import gc
import cv2
import numpy as np
from typing import Tuple, List, Optional, Dict, Any

# Maximum image dimension (pixels) before OCR — prevents OOM on Render 512MB RAM.
# 72 DPI standard page = 612×792 px, well under this limit.
# Scanned PDFs or high-res images get downscaled here before any OCR is run.
MAX_OCR_DIMENSION = 900

def rotate_image(image: np.ndarray, angle_deg: int) -> np.ndarray:
    """Rotate an image by 0, 90, 180, or 270 degrees clockwise."""
    if angle_deg == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif angle_deg == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    elif angle_deg == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image.copy()

def preprocess_image(image: np.ndarray) -> np.ndarray:
    """
    Preprocess image for OCR to enhance text contrast and line clarity.
    Memory-optimised: reuses buffers to avoid holding multiple full-image
    copies in RAM simultaneously (critical on Render's 512 MB free tier).
    """
    # Step 1: grayscale — reuse buffer immediately
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # Step 2: CLAHE contrast enhancement — write result back into gray buffer
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR, dst=None)  # keep scope small
    enhanced = clahe.apply(gray)
    del gray  # free grayscale buffer before sharpening allocates new memory

    # Step 3: lightweight sharpening kernel
    kernel = np.array([[0, -0.5, 0],
                       [-0.5, 3.0, -0.5],
                       [0, -0.5, 0]], dtype=np.float32)
    sharpened = cv2.filter2D(enhanced, -1, kernel)
    del enhanced  # free enhanced buffer

    # Step 4: back to BGR for RapidOCR
    result = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)
    del sharpened
    return result

def detect_orientation_and_ocr(image: np.ndarray, ocr_engine, preferred_angle: Optional[int] = None) -> Tuple[int, Optional[int], List[Dict[str, Any]]]:
    """
    High-performance orientation detection and single-pass OCR:
    1. If preferred_angle is provided (e.g. from document's Page 1), directly test it with full OCR.
       If valid text is found, return immediately without re-checking other orientations.
    2. Otherwise, use lightweight text_detector (~0.5s) to check bounding box aspect ratios:
       - If horizontal (horiz_ratio >= 0.5): candidates are [0, 180]
       - If vertical (horiz_ratio < 0.5): candidates are [90, 270]
    3. Run preprocessed OCR on candidate 1. If keyword count >= 2, accept immediately.
    4. Otherwise, test candidate 2 (the 180° flipped version).
    5. Returns (selected_angle, rotation_flag, precomputed_ocr_items).
       The precomputed OCR items are reused directly so the page is NEVER scanned twice!
    """
    rotation_flags = {
        0: None,
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE
    }

    target_keywords = [
        'overreader', 'patient id', 'patient full', 'date of birth',
        'visit number', 'order number', 'acquisition', 'test type',
        'resting ecg', 'ecg', 'cardiology',
        'patient', 'dob', 'assessment', 'assessments', 'visit code', 'medical'
    ]

    # ── Memory guard: cap longest edge to MAX_OCR_DIMENSION before any OCR ──
    # Medical PDFs at 72 DPI → 612×792 px — already under limit (no-op).
    # High-DPI or scanned PDFs get safely downscaled here to prevent OOM on Render.
    h_orig, w_orig = image.shape[:2]
    max_dim = max(h_orig, w_orig)
    if max_dim > MAX_OCR_DIMENSION:
        scale_factor = MAX_OCR_DIMENSION / max_dim
        image = cv2.resize(image, (int(w_orig * scale_factor), int(h_orig * scale_factor)), interpolation=cv2.INTER_AREA)

    # Fast path: preferred orientation already established for this document
    if preferred_angle is not None:
        rot_img = rotate_image(image, preferred_angle) if preferred_angle != 0 else image
        proc_img = preprocess_image(rot_img)
        ocr_items = ocr_engine.run_ocr(proc_img)
        del proc_img
        gc.collect()
        if len(ocr_items) > 5:
            return preferred_angle, rotation_flags[preferred_angle], ocr_items

    # Step 1: Sub-second text detection to determine primary axis (horizontal vs vertical)
    h, w = image.shape[:2]
    scale = min(1.0, 900.0 / max(h, w))
    preview_img = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else image

    try:
        boxes, _ = ocr_engine.engine.text_detector(preview_img)
    except Exception:
        boxes = None
    del preview_img
    gc.collect()

    if boxes is not None and len(boxes) > 0:
        w_h = []
        for b in boxes:
            bw = max(1.0, float(np.linalg.norm(b[1] - b[0])))
            bh = max(1.0, float(np.linalg.norm(b[3] - b[0])))
            w_h.append(bw / bh)
        horiz_ratio = sum(1 for r in w_h if r >= 1.2) / len(w_h)
    else:
        horiz_ratio = 0.5

    # If bounding boxes are predominantly horizontal, try [0, 180]; otherwise try [90, 270]
    candidates = [0, 180] if horiz_ratio >= 0.5 else [90, 270]

    # Test candidate 1 with preprocessing and OCR
    cand1 = candidates[0]
    rot1 = rotate_image(image, cand1) if cand1 != 0 else image
    proc1 = preprocess_image(rot1)
    ocr_items1 = ocr_engine.run_ocr(proc1)
    del proc1
    gc.collect()
    full_text1 = " ".join([it['text'].lower() for it in ocr_items1])
    kw1 = sum(1 for kw in target_keywords if kw in full_text1)

    if kw1 >= 2 or len(candidates) == 1:
        return cand1, rotation_flags[cand1], ocr_items1

    # Free candidate 1 images before testing candidate 2
    del rot1, full_text1
    gc.collect()

    # Test candidate 2 (180° flip)
    cand2 = candidates[1]
    rot2 = rotate_image(image, cand2) if cand2 != 0 else image
    proc2 = preprocess_image(rot2)
    ocr_items2 = ocr_engine.run_ocr(proc2)
    del proc2
    gc.collect()
    full_text2 = " ".join([it['text'].lower() for it in ocr_items2])
    kw2 = sum(1 for kw in target_keywords if kw in full_text2)

    if kw2 > kw1:
        del ocr_items1
        gc.collect()
        return cand2, rotation_flags[cand2], ocr_items2
    del ocr_items2
    gc.collect()
    return cand1, rotation_flags[cand1], ocr_items1


def detect_orientation(image: np.ndarray, ocr_engine, preferred_angle: Optional[int] = None) -> Tuple[int, Optional[int]]:
    """Backward compatible wrapper returning (angle, rot_flag)."""
    angle, rot_flag, _ = detect_orientation_and_ocr(image, ocr_engine, preferred_angle=preferred_angle)
    return angle, rot_flag

def draw_debug_annotations(image: np.ndarray, 
                           ocr_items: List[Dict[str, Any]], 
                           rows: List[List[Dict[str, Any]]], 
                           column_bounds: List[Tuple[float, float]], 
                           table_bounds: Optional[Dict[str, float]] = None) -> np.ndarray:
    """
    Draw debug visual annotations:
    - OCR bounding boxes: BLUE
    - Row boundaries: GREEN
    - Column boundaries: RED
    - Table region boundary: PURPLE
    """
    annotated = image.copy()
    h, w = annotated.shape[:2]
    
    # 1. OCR Bounding boxes (BLUE)
    for item in ocr_items:
        bbox = item['bbox']
        pt1 = (int(bbox[0]), int(bbox[1]))
        pt2 = (int(bbox[2]), int(bbox[3]))
        cv2.rectangle(annotated, pt1, pt2, (255, 120, 0), 1)  # Blue/Cyan
        
    # 2. Row boundaries (GREEN)
    for row in rows:
        if not row: continue
        y_min = min([it['bbox'][1] for it in row])
        y_max = max([it['bbox'][3] for it in row])
        cv2.line(annotated, (0, int(y_min)), (w, int(y_min)), (0, 220, 0), 1)
        cv2.line(annotated, (0, int(y_max)), (w, int(y_max)), (0, 220, 0), 1)
        
    # 3. Column boundaries (RED)
    for col_min, col_max in column_bounds:
        cv2.line(annotated, (int(col_min), 0), (int(col_min), h), (0, 0, 255), 1)
        cv2.line(annotated, (int(col_max), 0), (int(col_max), h), (0, 0, 255), 1)
        
    # 4. Table Region Boundary (PURPLE)
    if table_bounds:
        t_pt1 = (int(table_bounds['x_min']), int(table_bounds['y_min']))
        t_pt2 = (int(table_bounds['x_max']), int(table_bounds['y_max']))
        cv2.rectangle(annotated, t_pt1, t_pt2, (180, 0, 180), 3)  # Purple
        
    return annotated

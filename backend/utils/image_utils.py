import cv2
import numpy as np
from typing import Tuple, List, Optional, Dict, Any

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
    Preprocess image for OCR to enhance text contrast and line clarity
    without over-processing thin characters.
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
        
    # Contrast Limited Adaptive Histogram Equalization (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    
    # Slight sharpening to make text crisp
    kernel = np.array([[0, -0.5, 0], 
                       [-0.5, 3.0, -0.5], 
                       [0, -0.5, 0]], dtype=np.float32)
    sharpened = cv2.filter2D(enhanced, -1, kernel)
    
    # Convert back to BGR for OCR engine compatibility
    return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)

def detect_orientation(image: np.ndarray, ocr_engine) -> Tuple[int, Optional[int]]:
    """
    Detect page orientation. Check 0 deg first; if table headers or text are found,
    skip full multi-pass OCR on all 4 orientations to save CPU and memory.
    """
    rotation_flags = {
        0: None,
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE
    }
    
    target_keywords = ['overreader', 'patient id', 'patient full', 'visit number', 'order number', 'acquisition']

    # Downscale image copy for quick orientation testing to prevent high memory usage
    h, w = image.shape[:2]
    scale = min(1.0, 800.0 / max(h, w))
    preview_img = cv2.resize(image, (int(w * scale), int(h * scale))) if scale < 1.0 else image

    # Fast path: check 0 degrees first.
    # Require near-perfect keyword match (>= 5/6) to skip full angle sweep —
    # rotated PDFs can still match some keywords via flipped/mirrored OCR text.
    results_0 = ocr_engine.run_ocr(preview_img)
    if results_0:
        full_text_0 = " ".join([item['text'].lower() for item in results_0])
        kw_count_0 = sum(1 for kw in target_keywords if kw in full_text_0)
        if kw_count_0 >= 5:
            return 0, None

    best_angle = 0
    best_score = -1.0
    
    for angle in [0, 90, 180, 270]:
        flag = rotation_flags[angle]
        test_img = preview_img if flag is None else cv2.rotate(preview_img, flag)
        
        results = results_0 if angle == 0 else ocr_engine.run_ocr(test_img)
        if not results:
            continue
            
        w_h_ratios = []
        kw_count = 0
        
        full_text = " ".join([item['text'].lower() for item in results])
        for kw in target_keywords:
            if kw in full_text:
                kw_count += 1
                
        for item in results:
            bbox = item['bbox']
            w = max(1.0, bbox[2] - bbox[0])
            h = max(1.0, bbox[3] - bbox[1])
            w_h_ratios.append(w / h)
            
        avg_aspect_ratio = np.mean(w_h_ratios) if w_h_ratios else 0.0
        
        # Combined score: Header keywords get huge weight, aspect ratio breaks ties
        # Horizontal text in standard reports has aspect ratio > 2.0 (often > 5.0)
        score = (kw_count * 10.0) + avg_aspect_ratio
        
        if score > best_score:
            best_score = score
            best_angle = angle
            
    return best_angle, rotation_flags[best_angle]

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

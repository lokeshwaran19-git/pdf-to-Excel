import os
# Constrain thread count before any native libraries initialize
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import onnxruntime
import numpy as np
from typing import List, Dict, Any
import rapidocr_onnxruntime.utils as r_utils

# Patch SessionOptions in rapidocr_onnxruntime.utils so InferenceSession uses only 1 thread.
# On cloud hosts like Render (which have 32-64 host cores), ONNX defaults to 64 threads,
# exhausting 512MB RAM instantly during model load/inference.
def _create_low_mem_session_options():
    opt = onnxruntime.SessionOptions()
    opt.intra_op_num_threads = 1
    opt.inter_op_num_threads = 1
    opt.enable_cpu_mem_arena = False
    opt.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    return opt

r_utils.SessionOptions = _create_low_mem_session_options
onnxruntime.SessionOptions = _create_low_mem_session_options

from rapidocr_onnxruntime import RapidOCR

class OCRService:
    _instance = None

    def __init__(self):
        # Initialize RapidOCR without angle classifier to save ~25 MB RAM.
        # Orientation is handled via our own detect_orientation() in image_utils.
        self.engine = RapidOCR(use_angle_cls=False)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def run_ocr(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Run OCR on an image and return structured bounding boxes, text, and confidence.
        Each returned item contains:
        {
            'text': str,
            'confidence': float,
            'bbox': [x1, y1, x2, y2],
            'center_x': float,
            'center_y': float
        }
        """
        results, _ = self.engine(image)
        if not results:
            return []

        ocr_items = []
        for item in results:
            pts = np.array(item[0])
            x1, y1 = float(pts[:, 0].min()), float(pts[:, 1].min())
            x2, y2 = float(pts[:, 0].max()), float(pts[:, 1].max())
            text = str(item[1]).strip()
            conf = float(item[2])

            if text:
                ocr_items.append({
                    'text': text,
                    'confidence': conf,
                    'bbox': [x1, y1, x2, y2],
                    'center_x': (x1 + x2) / 2.0,
                    'center_y': (y1 + y2) / 2.0
                })

        return ocr_items

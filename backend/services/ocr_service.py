import numpy as np
from typing import List, Dict, Any
from rapidocr_onnxruntime import RapidOCR

class OCRService:
    _instance = None

    def __init__(self):
        # Initialize RapidOCR (ONNX engine for PaddleOCR PP-OCR models)
        self.engine = RapidOCR()

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

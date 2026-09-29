import pymupdf
import cv2
import numpy as np
from typing import List

class PDFService:
    @staticmethod
    def render_pdf_to_images(pdf_bytes: bytes, dpi: int = 150) -> List[np.ndarray]:
        """
        Render all pages of a PDF document into high-resolution NumPy images (BGR format).
        """
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        images = []
        
        # Calculate zoom factor based on DPI (72 is standard PDF DPI)
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        
        for page in doc:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            # Convert pixmap samples to numpy array
            img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
            # Convert RGB to BGR for OpenCV
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
            images.append(img_bgr)
            
        doc.close()
        return images

    @staticmethod
    def get_pdf_metadata(pdf_bytes: bytes) -> dict:
        """Get page count and basic metadata from PDF file bytes."""
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        doc.close()
        return {
            "page_count": page_count
        }

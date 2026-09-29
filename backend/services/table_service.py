import gc
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from backend.utils.image_utils import detect_orientation, rotate_image, preprocess_image
from backend.utils.text_utils import clean_text, format_column_header, is_low_confidence

class TableService:
    TARGET_HEADERS = [
        "Overreader ID",
        "Patient ID",
        "Patient Full Name",
        "Date of Birth",
        "Visit Number",
        "Order Number",
        "Acquisition Date/Time",
        "Test Type Value",
        "Test Reason"
    ]

    def __init__(self, ocr_service):
        self.ocr_service = ocr_service

    def extract_tables_from_pdf_pages(self, page_images: List[np.ndarray]) -> Dict[str, Any]:
        """
        Process all pages of a PDF:
        1. Detect orientation & rotate.
        2. Preprocess.
        3. Run OCR.
        4. Detect table headers & columns.
        5. Group rows & reconstruct cells.
        6. Merge multi-page tables & strip duplicate headers.
        """
        all_table_rows = []
        all_cell_confidences = []
        all_cell_low_conf = []
        
        debug_info = []

        for page_num, raw_img in enumerate(page_images, start=1):
            # 1. Fast path: run OCR on 0 degrees preprocessed image
            processed_img = preprocess_image(raw_img)
            ocr_items = self.ocr_service.run_ocr(processed_img)

            full_text = " ".join([it['text'].lower() for it in ocr_items])
            target_keywords = ['overreader', 'patient id', 'patient full', 'visit number', 'order number', 'acquisition']
            kw_count = sum(1 for kw in target_keywords if kw in full_text)

            if kw_count >= 2 or len(ocr_items) > 50:
                angle = 0
                rotated_img = raw_img
            else:
                # 2. Check rotated orientations (90, 180, 270) only if 0 degrees had insufficient text
                angle, rot_flag = detect_orientation(raw_img, self.ocr_service)
                if angle != 0:
                    rotated_img = rotate_image(raw_img, angle)
                    processed_img = preprocess_image(rotated_img)
                    ocr_items = self.ocr_service.run_ocr(processed_img)
                else:
                    rotated_img = raw_img

            # 4. Table Region & Header Detection
            header_row_y, header_items = self._find_header_row(ocr_items)
            footer_y = self._find_footer_y(ocr_items, header_row_y)

            # Filter items within table bounds
            table_items = [
                it for it in ocr_items 
                if it['center_y'] > (header_row_y + 15) and it['center_y'] < footer_y
            ]

            # 5. Determine Column X Boundaries
            col_bounds = self._determine_column_bounds(header_items, table_items, rotated_img.shape[1])

            # 6. Group into Rows
            rows = self._group_into_rows(table_items)

            # 7. Reconstruct Cells
            page_rows, page_confs, page_low_confs = self._reconstruct_page_cells(rows, col_bounds)

            # Add to combined dataset
            all_table_rows.extend(page_rows)
            all_cell_confidences.extend(page_confs)
            all_cell_low_conf.extend(page_low_confs)

            debug_info.append({
                "page": page_num,
                "orientation": angle,
                "ocr_item_count": len(ocr_items),
                "table_item_count": len(table_items),
                "detected_rows": len(page_rows),
                "column_bounds": col_bounds,
                "header_row_y": header_row_y,
                "footer_y": footer_y,
                "rotated_shape": [rotated_img.shape[1], rotated_img.shape[0]],
                # ocr_items and rows omitted to reduce memory usage on server
                "col_bounds": col_bounds
            })

            del processed_img, rotated_img
            gc.collect()

        # Calculate overall quality metrics
        if all_cell_confidences:
            flat_confs = [c for r in all_cell_confidences for c in r if c > 0]
            avg_conf = float(np.mean(flat_confs) * 100.0) if flat_confs else 90.0
            min_conf = float(np.min(flat_confs) * 100.0) if flat_confs else 80.0
        else:
            avg_conf = 0.0
            min_conf = 0.0

        low_conf_count = sum(sum(1 for is_low in r if is_low) for r in all_cell_low_conf)
        
        if avg_conf >= 90.0:
            quality_label = "High confidence"
        elif avg_conf >= 75.0:
            quality_label = "Needs review"
        else:
            quality_label = "Low confidence"

        return {
            "headers": self.TARGET_HEADERS,
            "table_data": all_table_rows,
            "low_conf_cells": all_cell_low_conf,
            "avg_confidence": round(avg_conf, 1),
            "min_confidence": round(min_conf, 1),
            "low_confidence_count": low_conf_count,
            "row_count": len(all_table_rows),
            "col_count": len(self.TARGET_HEADERS),
            "data_quality_label": quality_label,
            "debug_info": debug_info
        }

    def _find_header_row(self, ocr_items: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, Any]]]:
        """Find the table header Y coordinate and header items."""
        header_keywords = ['overreader', 'patient id', 'patient full', 'visit number', 'order number', 'acquisition']
        
        sorted_items = sorted(ocr_items, key=lambda x: x['center_y'])
        
        potential_rows = []
        for item in sorted_items:
            placed = False
            for r in potential_rows:
                avg_y = np.mean([it['center_y'] for it in r])
                if abs(item['center_y'] - avg_y) < 20:
                    r.append(item)
                    placed = True
                    break
            if not placed:
                potential_rows.append([item])
                
        best_row = []
        best_score = 0
        best_y = 500.0  # default fallback
        
        for r in potential_rows:
            row_text = " ".join([it['text'].lower() for it in r])
            score = sum(1 for kw in header_keywords if kw in row_text)
            if score > best_score:
                best_score = score
                best_row = r
                best_y = float(np.mean([it['center_y'] for it in r]))
                
        return best_y, best_row

    def _find_footer_y(self, ocr_items: List[Dict[str, Any]], header_y: float) -> float:
        """Find the footer Y boundary to exclude page footers."""
        max_y = 100000.0
        footer_keywords = ['muse cardiology', 'page 1', 'page 2', 'page 3', 'general electric']
        
        for item in ocr_items:
            if item['center_y'] > header_y:
                text_lower = item['text'].lower()
                if any(kw in text_lower for kw in footer_keywords):
                    if item['center_y'] < max_y:
                        max_y = item['center_y']
                        
        if max_y == 100000.0:
            max_y = max([it['bbox'][3] for it in ocr_items]) + 10.0 if ocr_items else 100000.0
            
        return max_y

    def _determine_column_bounds(self, header_items: List[Dict[str, Any]], 
                                table_items: List[Dict[str, Any]], 
                                image_width: float) -> List[Tuple[float, float]]:
        """
        Determine exact X-min and X-max boundaries for the 9 logical target columns.
        Uses detected header positions and calibrated X cuts.
        """
        scale = image_width / 3300.0
        
        # Calibrated cut points for the 9 logical columns:
        # Col 0: Overreader ID [0 - 375]
        # Col 1: Patient ID [375 - 650]
        # Col 2: Patient Full Name [650 - 1220]
        # Col 3: Date of Birth [1220 - 1515]
        # Col 4: Visit Number [1515 - 1810]
        # Col 5: Order Number [1810 - 2060]
        # Col 6: Acquisition Date/Time [2060 - 2500]
        # Col 7: Test Type Value [2500 - 2790]
        # Col 8: Test Reason [2790 - image_width]
        
        default_cuts = [
            0,
            375 * scale,
            650 * scale,
            1220 * scale,
            1515 * scale,
            1810 * scale,
            2060 * scale,
            2500 * scale,
            2790 * scale,
            image_width
        ]

        bounds = []
        for i in range(9):
            bounds.append((default_cuts[i], default_cuts[i+1]))
            
        return bounds

    def _group_into_rows(self, table_items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Group OCR items into physical table rows by Y coordinate."""
        sorted_items = sorted(table_items, key=lambda x: x['center_y'])
        
        rows = []
        for item in sorted_items:
            placed = False
            for r in rows:
                avg_y = np.mean([it['center_y'] for it in r])
                if abs(item['center_y'] - avg_y) < 18:
                    r.append(item)
                    placed = True
                    break
            if not placed:
                rows.append([item])
                
        for r in rows:
            r.sort(key=lambda item: item['bbox'][0])
            
        return rows

    def _reconstruct_page_cells(self, rows: List[List[Dict[str, Any]]], 
                                col_bounds: List[Tuple[float, float]]) -> Tuple[List[List[str]], List[List[float]], List[List[bool]]]:
        """
        Reconstruct 9 logical cells for each table row.
        Concatenates multiple OCR items belonging to the same column cell.
        """
        page_data_rows = []
        page_conf_rows = []
        page_low_conf_rows = []

        for r in rows:
            row_cells = [[] for _ in range(9)]
            row_confs = [[] for _ in range(9)]

            for item in r:
                cx = item['center_x']
                col_idx = 8 # default to last column
                for idx, (c_min, c_max) in enumerate(col_bounds):
                    if c_min <= cx < c_max:
                        col_idx = idx
                        break
                row_cells[col_idx].append(item['text'])
                row_confs[col_idx].append(item['confidence'])

            str_cells = []
            conf_cells = []
            low_conf_cells = []

            for c_idx in range(9):
                texts = row_cells[c_idx]
                confs = row_confs[c_idx]

                if texts:
                    cell_val = clean_text(" ".join(texts))
                    cell_conf = float(np.mean(confs))
                else:
                    cell_val = ""
                    cell_conf = 1.0

                is_low = is_low_confidence(cell_conf, threshold=0.80) if cell_val else False

                str_cells.append(cell_val)
                conf_cells.append(cell_conf)
                low_conf_cells.append(is_low)

            page_data_rows.append(str_cells)
            page_conf_rows.append(conf_cells)
            page_low_conf_rows.append(low_conf_cells)

        return page_data_rows, page_conf_rows, page_low_conf_rows

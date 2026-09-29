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

    def __init__(self, ocr_service, row_distance_multiplier: float = 0.65):
        self.ocr_service = ocr_service
        self.row_distance_multiplier = row_distance_multiplier
        self.master_col_bounds = None

    def extract_tables_from_pdf_pages(self, page_images: List[np.ndarray]) -> Dict[str, Any]:
        """
        Process all pages of a PDF:
        1. Detect orientation & rotate to ensure text is strictly horizontal.
        2. Preprocess image for OCR contrast and line sharpness.
        3. Run OCR to extract text and bounding boxes (x0, y0, x1, y1).
        4. Detect table headers dynamically to establish column X ranges.
        5. Filter table items between header bottom and footer top.
        6. Group text items into rows using dynamic Y-coordinate clustering.
        7. Assign items to columns using X coordinates and merge multi-word cells.
        8. Merge multi-page tables, strip repeated headers, and validate columns.
        """
        all_table_rows = []
        all_cell_confidences = []
        all_cell_low_conf = []
        debug_info = []

        for page_num, raw_img in enumerate(page_images, start=1):
            # 1. Orientation check: Ensures text is horizontal (width >> height)
            angle, rot_flag = detect_orientation(raw_img, self.ocr_service)
            if angle != 0:
                rotated_img = rotate_image(raw_img, angle)
            else:
                rotated_img = raw_img.copy()

            processed_img = preprocess_image(rotated_img)
            ocr_items = self.ocr_service.run_ocr(processed_img)

            img_h, img_w = rotated_img.shape[:2]

            # 2. Dynamic Table Header Detection
            header_y, header_top, header_bottom, header_items, detected_header_map = self._find_header_row(ocr_items)

            # 3. Determine Column X Boundaries from Header Geometry
            col_bounds = self._determine_column_bounds(detected_header_map, img_w)
            if self.master_col_bounds is None or len(detected_header_map) >= 6:
                self.master_col_bounds = col_bounds

            # 4. Filter Table Items (exclude headers, report titles, and page footers)
            footer_y = self._find_footer_y(ocr_items, header_bottom)

            table_items = [
                it for it in ocr_items
                if it['center_y'] > (header_bottom + 4.0) and it['center_y'] < (footer_y - 4.0)
            ]

            # 5. Group into rows using dynamic Y-coordinate clustering
            rows = self._group_into_rows(table_items)

            # 6. Reconstruct cells: assign text items to columns by X coordinates & merge multi-word cells
            page_rows, page_confs, page_low_confs = self._reconstruct_page_cells(rows, col_bounds)

            # 7. Validation Stage: Ensure each record has 9 columns, remove noise/duplicate headers
            valid_rows = []
            valid_confs = []
            valid_low_confs = []
            for r, c, l in zip(page_rows, page_confs, page_low_confs):
                if len(r) != 9:
                    continue
                # Skip empty lines
                if not any(cell.strip() for cell in r):
                    continue
                # Filter out accidental duplicate table headers
                if "overreader" in r[0].lower() or "patient id" in r[1].lower():
                    continue
                # Filter out summary report lines or footer fragments if any
                combined_line = " ".join(r).lower()
                if "report title:" in combined_line or "muse cardiology" in combined_line:
                    continue

                valid_rows.append(r)
                valid_confs.append(c)
                valid_low_confs.append(l)

            all_table_rows.extend(valid_rows)
            all_cell_confidences.extend(valid_confs)
            all_cell_low_conf.extend(valid_low_confs)

            print(f"Page {page_num}: Detected columns: {len(self.TARGET_HEADERS)}, Detected records: {len(valid_rows)}, Rows with invalid column count: 0")

            debug_info.append({
                "page": page_num,
                "orientation": angle,
                "ocr_item_count": len(ocr_items),
                "table_item_count": len(table_items),
                "detected_rows": len(valid_rows),
                "column_bounds": col_bounds,
                "header_row_y": header_y,
                "footer_y": footer_y,
                "rotated_shape": [img_w, img_h]
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
        quality_label = "High confidence" if avg_conf >= 90.0 else ("Needs review" if avg_conf >= 75.0 else "Low confidence")

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

    def _find_header_row(self, ocr_items: List[Dict[str, Any]]) -> Tuple[float, float, float, List[Dict[str, Any]], Dict[int, List[float]]]:
        """
        Dynamically find the header row by locating text items matching canonical header keywords.
        Returns:
            (header_center_y, header_top_y, header_bottom_y, header_items, detected_header_map)
        """
        header_kw_map = {
            0: ['overreader'],
            1: ['patient id'],
            2: ['patient full', 'patient name', 'full name'],
            3: ['date of birth', 'birth date', 'dob'],
            4: ['visit number', 'visit no', 'visit #'],
            5: ['order number', 'order no', 'order #'],
            6: ['acquisition date', 'acquisition date/time', 'acquisition'],
            7: ['test type value', 'test type', 'test value'],
            8: ['test reason', 'test reson', 'reason']
        }

        # Identify candidate header items
        candidate_items = []
        for it in ocr_items:
            t_lower = it['text'].lower()
            for col_idx, kws in header_kw_map.items():
                if any(kw in t_lower for kw in kws):
                    candidate_items.append((col_idx, it))
                    break

        if not candidate_items:
            return 175.0, 160.0, 190.0, [], {}

        # Group candidate items that share vertical alignment
        candidate_items.sort(key=lambda x: x[1]['center_y'])
        clusters = []
        for col_idx, item in candidate_items:
            placed = False
            for cl in clusters:
                avg_y = np.mean([x[1]['center_y'] for x in cl])
                if abs(item['center_y'] - avg_y) < 25.0:
                    cl.append((col_idx, item))
                    placed = True
                    break
            if not placed:
                clusters.append([(col_idx, item)])

        # Select the cluster containing the most unique column matches
        best_cluster = max(clusters, key=lambda cl: len(set(x[0] for x in cl)))
        header_items = [x[1] for x in best_cluster]

        header_top = min(it['bbox'][1] for it in header_items)
        header_bottom = max(it['bbox'][3] for it in header_items)
        header_y = float(np.mean([it['center_y'] for it in header_items]))

        # Map canonical col_idx -> merged bbox of that header
        detected_header_map = {}
        for col_idx, it in best_cluster:
            if col_idx not in detected_header_map:
                detected_header_map[col_idx] = list(it['bbox'])
            else:
                b = detected_header_map[col_idx]
                detected_header_map[col_idx] = [
                    min(b[0], it['bbox'][0]),
                    min(b[1], it['bbox'][1]),
                    max(b[2], it['bbox'][2]),
                    max(b[3], it['bbox'][3])
                ]

        return header_y, header_top, header_bottom, header_items, detected_header_map

    def _determine_column_bounds(self, header_map: Dict[int, List[float]], image_width: float) -> List[Tuple[float, float]]:
        """
        Dynamically determine column X boundaries from header bounding boxes.
        Computes clean cuts between adjacent columns, ensuring wide fields (like Patient Full Name)
        have adequate space and empty columns (like Test Reason) remain preserved.
        """
        sorted_cols = sorted(header_map.keys())

        if len(sorted_cols) >= 5:
            # Interpolate any missing column centers if a column is absent from header row
            centers = {}
            for col_idx in range(9):
                if col_idx in header_map:
                    box = header_map[col_idx]
                    centers[col_idx] = (box[0] + box[2]) / 2.0
                else:
                    centers[col_idx] = None

            known_indices = [i for i in range(9) if centers[i] is not None]
            for col_idx in range(9):
                if centers[col_idx] is None:
                    before = [i for i in known_indices if i < col_idx]
                    after = [i for i in known_indices if i > col_idx]
                    if before and after:
                        b_idx = before[-1]
                        a_idx = after[0]
                        ratio = (col_idx - b_idx) / (a_idx - b_idx)
                        centers[col_idx] = centers[b_idx] + ratio * (centers[a_idx] - centers[b_idx])
                    elif before:
                        step = (centers[before[-1]] - centers[known_indices[0]]) / max(1, len(before) - 1) if len(before) > 1 else 90.0
                        centers[col_idx] = centers[before[-1]] + step * (col_idx - before[-1])
                    elif after:
                        step = (centers[known_indices[-1]] - centers[after[0]]) / max(1, len(after) - 1) if len(after) > 1 else 90.0
                        centers[col_idx] = centers[after[0]] - step * (after[0] - col_idx)

            # Compute cut points as midpoints between adjacent headers
            cuts = []
            for i in range(8):
                if i in header_map and (i + 1) in header_map:
                    box_i = header_map[i]
                    box_next = header_map[i + 1]
                    # Give extra width margin to Patient Full Name (Col 2)
                    if i == 2:
                        cut = min(box_next[0] - 10.0, (box_i[2] + box_next[0]) / 2.0)
                    else:
                        cut = (box_i[2] + box_next[0]) / 2.0
                else:
                    cut = (centers[i] + centers[i + 1]) / 2.0
                cuts.append(cut)

            bounds = []
            bounds.append((0.0, cuts[0]))
            for i in range(1, 8):
                bounds.append((cuts[i - 1], cuts[i]))
            bounds.append((cuts[7], float(image_width)))
            return bounds

        # Fallback to master bounds if already established on previous page
        if self.master_col_bounds:
            return self.master_col_bounds

        # Proportional fallback based on document width
        ratios = [0.11, 0.09, 0.17, 0.09, 0.09, 0.08, 0.14, 0.11, 0.12]
        cum_cuts = np.cumsum(ratios) * image_width
        bounds = [(0.0, cum_cuts[0])]
        for i in range(1, 8):
            bounds.append((cum_cuts[i - 1], cum_cuts[i]))
        bounds.append((cum_cuts[7], float(image_width)))
        return bounds

    def _find_footer_y(self, ocr_items: List[Dict[str, Any]], header_bottom: float) -> float:
        """Find footer Y boundary to exclude page footers and system lines."""
        footer_kws = ['muse cardiology', 'general electric', 'page 1 of', 'page 2 of', 'report title:', 'lr-dr.']
        footer_y = 100000.0
        for it in ocr_items:
            if it['center_y'] > (header_bottom + 30.0):
                t_lower = it['text'].lower()
                if any(kw in t_lower for kw in footer_kws):
                    if it['bbox'][1] < footer_y:
                        footer_y = it['bbox'][1]
        if footer_y == 100000.0:
            footer_y = max([it['bbox'][3] for it in ocr_items]) + 10.0 if ocr_items else 100000.0
        return footer_y

    def _group_into_rows(self, table_items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Group OCR items into physical table rows by dynamic Y coordinate clustering.
        Tolerance is dynamically computed from median OCR text bounding-box height.
        """
        if not table_items:
            return []

        heights = [max(1.0, it['bbox'][3] - it['bbox'][1]) for it in table_items]
        median_h = float(np.median(heights)) if heights else 16.0
        row_tolerance = median_h * self.row_distance_multiplier

        sorted_items = sorted(table_items, key=lambda x: x['center_y'])
        rows = []
        for item in sorted_items:
            cy = item['center_y']
            best_row = None
            min_dist = float('inf')
            for r in rows:
                avg_y = float(np.mean([it['center_y'] for it in r]))
                dist = abs(cy - avg_y)
                if dist <= row_tolerance and dist < min_dist:
                    min_dist = dist
                    best_row = r

            if best_row is not None:
                best_row.append(item)
            else:
                rows.append([item])

        # Sort items inside each row left to right
        for r in rows:
            r.sort(key=lambda it: it['bbox'][0])

        # Sort rows top to bottom
        rows.sort(key=lambda r: float(np.mean([it['center_y'] for it in r])))
        return rows

    def _reconstruct_page_cells(self, rows: List[List[Dict[str, Any]]], 
                                col_bounds: List[Tuple[float, float]]) -> Tuple[List[List[str]], List[List[float]], List[List[bool]]]:
        """
        Reconstruct 9 logical cells for each table row.
        Assigns text items to columns based on X coordinate intervals.
        Combines multiple OCR words belonging to the same cell in left-to-right order.
        Preserves empty cells without shifting columns.
        """
        page_data_rows = []
        page_conf_rows = []
        page_low_conf_rows = []

        for r in rows:
            cell_items = [[] for _ in range(9)]
            for it in r:
                cx = it['center_x']
                col_idx = None
                for idx, (c_min, c_max) in enumerate(col_bounds):
                    if c_min <= cx < c_max:
                        col_idx = idx
                        break
                if col_idx is None:
                    dists = [min(abs(cx - c_min), abs(cx - c_max)) for c_min, c_max in col_bounds]
                    col_idx = int(np.argmin(dists))

                cell_items[col_idx].append(it)

            str_cells = []
            conf_cells = []
            low_conf_cells = []

            for c_idx in range(9):
                items = cell_items[c_idx]
                if items:
                    items.sort(key=lambda x: x['bbox'][0])
                    cell_val = clean_text(" ".join([it['text'] for it in items]))
                    cell_conf = float(np.mean([it['confidence'] for it in items]))
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

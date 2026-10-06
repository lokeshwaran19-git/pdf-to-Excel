import gc
import re
import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from backend.models.medical_models import PatientInfo, MedicalDocumentRecord
from backend.services.pdf_service import PDFService
from backend.utils.image_utils import detect_orientation_and_ocr
from backend.utils.text_utils import clean_text

class MedicalExtractor:
    """
    Production-grade multi-page medical report extractor.
    Scans PDF pages sequentially, identifies:
    1. Patient Information (Patient Name, DOB, Age, Gender, Account #, Patient ID, Visit #, Address, Phone, etc.)
    2. Assessment Section (Numbered items 1., 2., 3., 4., 5., stops before narrative doctor notes)
    3. Visit Code Section
    Combines all extracted sections into ONE document record.
    Releases memory immediately after each page to operate comfortably under 512 MB RAM.
    """

    TERMINATING_SECTION_REGEX = re.compile(
        r'^\s*(?:plan|treatment|after\s*rev|recommendations?|orders?|counseling|screening|'
        r'follow[\s\-]*up|provider|signed\s*by|signature|electronically\s*signed|rx|medications?|'
        r'patient\s*has\s*been|history\s*of\s*present)\b',
        re.IGNORECASE
    )

    VISIT_CODE_HEADING_REGEX = re.compile(
        r'^\s*(?:visit\s*c[o0]de|visitcode|billing\s*code|cpt\s*code|service\s*code)\s*[:\-]?\s*(.*)$',
        re.IGNORECASE
    )

    ASSESSMENT_HEADING_REGEX = re.compile(
        r'^\s*(?:#|\d+[\.\)]\s*)?(?:assessment|assessments)\s*[:\-\.]?\s*$',
        re.IGNORECASE
    )

    NUMBERED_ITEM_REGEX = re.compile(
        r'^\s*(\d+)[\.\)\]]\s*(.+)$'
    )

    ICD_REGEX = re.compile(
        r'\b[A-TV-Z]\d{2}(?:\.[0-9A-Za-z]+)?\b',
        re.IGNORECASE
    )

    HEADER_REGEX = re.compile(
        r'^\s*([A-Za-z\s,\.\'\-]+?)\s+(?:DOB|D0B|D\.O\.B\.?)\s*[:\-]?\s*([0-9\/\-\.]+)\s*'
        r'(?:\((\d{1,3})\s*(?:yo|y\.?o\.?|yr|years?)?\s*([MF]|Male|Female)\))?\s*'
        r'(?:Acc(?:ount)?\s*(?:No\.?|#)?\s*([A-Za-z0-9\-]+))?\s*'
        r'(?:DOS|D0S|D\.O\.S\.?)\s*[:\-]?\s*([0-9\/\-\.]+)?',
        re.IGNORECASE
    )

    DEMO_LINE_REGEX = re.compile(
        r'(\d{1,3})\s*Y(?:\s*old)?\s*(Male|Female|M|F)\b',
        re.IGNORECASE
    )

    ADDRESS_STREET_REGEX = re.compile(
        r'\b(?:\d+\s+[A-Za-z0-9\s,\.\'\-]+(?:\b(?:Dr|Drive|Ct|Court|St|Street|Rd|Road|Ave|Avenue|Blvd|Boulevard|Way|Lane|Ln|Apt|Suite|Ste)\b|\b[A-Z]{2}[-\s]*\d{5}\b))',
        re.IGNORECASE
    )

    CPT_REGEX = re.compile(
        r'\b99\d{3}\b'
    )

    @classmethod
    def extract_document(cls, pdf_bytes: bytes, filename: str, ocr_service) -> MedicalDocumentRecord:
        """
        Process a single PDF sequentially across all pages.
        Extracts Patient Information, Assessments, and Visit Code regardless of page location.
        """
        patient_info = PatientInfo()
        assessments: List[str] = []
        visit_code: str = ""
        source_pages: Dict[str, int] = {}
        confidences: Dict[str, float] = {}

        try:
            for page_num, raw_img in PDFService.iter_pdf_pages(pdf_bytes, dpi=96):
                # 1. Automatic Orientation Detection & Single-Pass OCR
                angle, rot_flag, ocr_items = detect_orientation_and_ocr(raw_img, ocr_service)
                del raw_img
                gc.collect()

                if not ocr_items:
                    continue

                # 2. Extract / update Patient Information
                p_info, p_confs = cls._extract_patient_info(ocr_items, patient_info)
                for fld in ['patient_name', 'date_of_birth', 'age', 'gender', 'account_number',
                            'patient_id', 'visit_number', 'address', 'phone', 'insurance', 'pcp']:
                    val = getattr(p_info, fld)
                    if val and not getattr(patient_info, fld):
                        setattr(patient_info, fld, val)
                        if fld in p_confs:
                            confidences[fld] = p_confs[fld]
                        if "patient_information" not in source_pages:
                            source_pages["patient_information"] = page_num

                # 3. Extract Assessments if not yet found
                if not assessments:
                    p_assessments, a_confs = cls._extract_assessments(ocr_items)
                    if p_assessments:
                        assessments = p_assessments
                        source_pages["assessment"] = page_num
                        confidences.update(a_confs)

                # 4. Extract Visit Code if not yet found
                if not visit_code:
                    v_code, v_conf = cls._extract_visit_code(ocr_items)
                    if v_code:
                        visit_code = v_code
                        source_pages["visit_code"] = page_num
                        confidences["visit_code"] = v_conf

                # Explicitly free OCR items for this page
                del ocr_items
                gc.collect()

                # If all three key target sections are found, early stop
                if (patient_info.patient_name and patient_info.date_of_birth and patient_info.address) and assessments and visit_code:
                    break

            # Fallbacks for identifiers:
            # If patient_id is not set but account_number is, use account_number
            if not patient_info.patient_id and patient_info.account_number:
                patient_info.patient_id = patient_info.account_number
                if 'account_number' in confidences and 'patient_id' not in confidences:
                    confidences['patient_id'] = confidences['account_number']

            if not patient_info.account_number and patient_info.patient_id:
                patient_info.account_number = patient_info.patient_id
                if 'patient_id' in confidences and 'account_number' not in confidences:
                    confidences['account_number'] = confidences['patient_id']

            # Handle missing data cleanly - never hallucinate
            if not assessments:
                assessments = ["N/A"]
            if not visit_code:
                visit_code = "N/A"

            # Check if any extracted field has low confidence (< 0.80)
            review_required = False
            for k, conf in confidences.items():
                if conf < 0.80:
                    review_required = True
                    break

            # If essential patient info is completely missing, flag for review
            if not patient_info.patient_name and not patient_info.date_of_birth:
                review_required = True

            return MedicalDocumentRecord(
                source_file=filename,
                patient_info=patient_info,
                assessments=assessments,
                visit_code=visit_code,
                confidence=confidences,
                source_pages=source_pages,
                review_required=review_required,
                success=True,
                error=None
            )

        except Exception as e:
            return MedicalDocumentRecord(
                source_file=filename,
                patient_info=PatientInfo(),
                assessments=["N/A"],
                visit_code="N/A",
                confidence={},
                source_pages={},
                review_required=True,
                success=False,
                error=str(e)
            )

    @classmethod
    def _extract_patient_info(cls, ocr_items: List[Dict[str, Any]], existing: Optional[PatientInfo] = None) -> Tuple[PatientInfo, Dict[str, float]]:
        """
        Extract structured patient information fields using OCR text, label matching,
        header format matching, and spatial proximity.
        """
        patient_info = PatientInfo(**(existing.dict() if existing else {}))
        confidences: Dict[str, float] = {}

        if not ocr_items:
            return patient_info, confidences

        # Calculate median text height for spatial distance thresholding
        heights = [max(1.0, it['bbox'][3] - it['bbox'][1]) for it in ocr_items]
        median_h = float(np.median(heights)) if heights else 16.0

        # Sort items top-to-bottom, left-to-right
        sorted_items = sorted(ocr_items, key=lambda it: (it['bbox'][1], it['bbox'][0]))

        # --- A. Check Header line (typically y < 150) ---
        for it in sorted_items:
            if it['bbox'][1] > 150:
                break
            t = it['text'].strip()
            m = cls.HEADER_REGEX.match(t)
            if m:
                h_name = clean_text(m.group(1))
                h_dob = clean_text(m.group(2))
                h_age = clean_text(m.group(3)) if m.group(3) else ""
                h_gender = clean_text(m.group(4)) if m.group(4) else ""
                h_acc = clean_text(m.group(5)) if m.group(5) else ""
                h_dos = clean_text(m.group(6)) if m.group(6) else ""

                if h_gender.upper() == 'M': h_gender = 'Male'
                elif h_gender.upper() == 'F': h_gender = 'Female'

                if not patient_info.patient_name and h_name:
                    patient_info.patient_name = h_name
                    confidences['patient_name'] = it['confidence']
                if not patient_info.date_of_birth and h_dob:
                    patient_info.date_of_birth = h_dob
                    confidences['date_of_birth'] = it['confidence']
                if not patient_info.age and h_age:
                    patient_info.age = h_age
                    confidences['age'] = it['confidence']
                if not patient_info.gender and h_gender:
                    patient_info.gender = h_gender
                    confidences['gender'] = it['confidence']
                if not patient_info.account_number and h_acc:
                    patient_info.account_number = h_acc
                    confidences['account_number'] = it['confidence']
                if not patient_info.visit_number and h_dos:
                    patient_info.visit_number = h_dos
                    confidences['visit_number'] = it['confidence']
                break

        # --- B. Demographic block on Page 1 (top right, y < 350) ---
        demo_idx = -1
        for idx, it in enumerate(sorted_items):
            if it['bbox'][1] > 350:
                break
            m = cls.DEMO_LINE_REGEX.search(it['text'])
            if m:
                demo_idx = idx
                if not patient_info.age:
                    patient_info.age = clean_text(m.group(1))
                    confidences['age'] = it['confidence']
                if not patient_info.gender:
                    g = m.group(2)
                    patient_info.gender = 'Male' if g.upper().startswith('M') else 'Female'
                    confidences['gender'] = it['confidence']
                dob_m = re.search(r'(?:DOB|D0B|Birth)\s*[:\-]?\s*([0-9\/\-\.]+)', it['text'], re.IGNORECASE)
                if dob_m and not patient_info.date_of_birth:
                    patient_info.date_of_birth = clean_text(dob_m.group(1))
                    confidences['date_of_birth'] = it['confidence']
                break

        if demo_idx > 0:
            cand_name_it = sorted_items[demo_idx - 1]
            cand_name = clean_text(cand_name_it['text'])
            if not re.search(r'(?:print|preview|am|pm|\d{1,2}\/\d{1,2}\/\d{2,4})', cand_name, re.IGNORECASE):
                patient_info.patient_name = cand_name
                confidences['patient_name'] = cand_name_it['confidence']

        # Address detection in demographic block
        if not patient_info.address:
            for it in sorted_items:
                if it['bbox'][1] > 350:
                    break
                t = it['text'].strip()
                if any(lbl in t.lower() for lbl in ['account', 'home:', 'phone:', 'insurance:', 'pcp:', 'facility:', 'progress', 'reason', 'current']):
                    continue
                if cls.ADDRESS_STREET_REGEX.search(t):
                    patient_info.address = clean_text(t)
                    confidences['address'] = it['confidence']
                    break

        # --- C. Inline label patterns ---
        inline_patterns = [
            ('patient_name', [
                re.compile(r'(?:patient\s*(?:full\s*)?name|pt\s*name|patient)\s*[:\-]\s*(.+)$', re.IGNORECASE),
                re.compile(r'^\s*name\s*[:\-]\s*(.+)$', re.IGNORECASE)
            ]),
            ('date_of_birth', [
                re.compile(r'(?:date\s*of\s*birth|birth\s*date|dob|d\.o\.b\.)\s*[:\-]?\s*([0-9A-Za-z\s,\/\-\.]{6,16})', re.IGNORECASE)
            ]),
            ('age', [
                re.compile(r'\bage\s*[:\-]\s*(\d{1,3}(?:\s*(?:yo|y\.?o\.?|yrs?|years?))?)\b', re.IGNORECASE),
                re.compile(r'\b(\d{1,3})\s*(?:yo|y\.?o\.?|yrs?\s*old|years?\s*old)\b', re.IGNORECASE)
            ]),
            ('gender', [
                re.compile(r'(?:gender|sex)\s*[:\-]\s*(Male|Female|M|F|Other|Non-Binary)\b', re.IGNORECASE)
            ]),
            ('account_number', [
                re.compile(r'(?:account\s*(?:number|no|#)|acct\s*(?:number|no|#)|account|acct)\s*[:\-]?\s*([A-Za-z0-9\-]+)', re.IGNORECASE)
            ]),
            ('patient_id', [
                re.compile(r'(?:patient\s*id\s*(?:#|no)?|patient\s*#|pat\s*id|mrn)\s*[:\-]?\s*([A-Za-z0-9\-]+)', re.IGNORECASE)
            ]),
            ('visit_number', [
                re.compile(r'(?:visit\s*(?:number|no|#)|encounter\s*(?:number|no|#)|visit\s*#)\s*[:\-]?\s*([A-Za-z0-9\-]+)', re.IGNORECASE),
                re.compile(r'(?:dos|date\s*of\s*service)\s*[:\-]?\s*([0-9\/\-\.]+)', re.IGNORECASE)
            ]),
            ('phone', [
                re.compile(r'(?:phone\s*(?:#|no)?|telephone|tel|cell|home\s*phone|home|mobile)\s*[:\-]\s*([\d\(\)\-\.\s\+]{7,20})', re.IGNORECASE)
            ]),
            ('insurance', [
                re.compile(r'(?:primary\s*insurance|insurance\s*(?:name|plan|carrier)?|payer)\s*[:\-]\s*(.+)$', re.IGNORECASE)
            ]),
            ('pcp', [
                re.compile(r'(?:pcp|primary\s*care\s*(?:provider|physician)|referring\s*provider|provider)\s*[:\-]\s*(.+)$', re.IGNORECASE)
            ]),
            ('address', [
                re.compile(r'(?:address|home\s*address|street)\s*[:\-]\s*(.+)$', re.IGNORECASE)
            ])
        ]

        # Scan each OCR item for inline matches
        for it in sorted_items:
            t = it['text'].strip()
            conf = it['confidence']

            for field_name, regex_list in inline_patterns:
                if getattr(patient_info, field_name):
                    continue
                for rgx in regex_list:
                    m = rgx.search(t)
                    if m:
                        val = clean_text(m.group(1))
                        val = re.split(r'\b(?:DOB|Age|Sex|Gender|Acct|Account|MRN|Visit|Phone|Insurance|PCP)\b', val, flags=re.IGNORECASE)[0].strip()
                        if val:
                            if field_name == 'gender':
                                if val.upper() == 'M': val = 'Male'
                                elif val.upper() == 'F': val = 'Female'
                            setattr(patient_info, field_name, val)
                            confidences[field_name] = conf
                            break

        # --- D. Standalone label spatial matching ---
        label_only_patterns = {
            'patient_name': re.compile(r'^(?:patient\s*(?:full\s*)?name|pt\s*name|patient|name)\s*[:\-]?$', re.IGNORECASE),
            'date_of_birth': re.compile(r'^(?:date\s*of\s*birth|birth\s*date|dob|d\.o\.b\.)\s*[:\-]?$', re.IGNORECASE),
            'age': re.compile(r'^(?:age)\s*[:\-]?$', re.IGNORECASE),
            'gender': re.compile(r'^(?:gender|sex)\s*[:\-]?$', re.IGNORECASE),
            'account_number': re.compile(r'^(?:account\s*(?:number|no|#)|acct\s*(?:number|no|#)|account|acct)\s*[:\-]?$', re.IGNORECASE),
            'patient_id': re.compile(r'^(?:patient\s*id\s*(?:#|no)?|pat\s*id|mrn)\s*[:\-]?$', re.IGNORECASE),
            'visit_number': re.compile(r'^(?:visit\s*(?:number|no|#)|encounter\s*(?:number|no|#)|visit)\s*[:\-]?$', re.IGNORECASE),
            'phone': re.compile(r'^(?:phone\s*(?:#|no)?|telephone|tel|cell|home)\s*[:\-]?$', re.IGNORECASE),
            'insurance': re.compile(r'^(?:primary\s*insurance|insurance|payer)\s*[:\-]?$', re.IGNORECASE),
            'pcp': re.compile(r'^(?:pcp|primary\s*care\s*(?:provider|physician)|provider)\s*[:\-]?$', re.IGNORECASE),
            'address': re.compile(r'^(?:address|home\s*address)\s*[:\-]?$', re.IGNORECASE)
        }

        for idx, it in enumerate(sorted_items):
            t = it['text'].strip()

            for field_name, label_rgx in label_only_patterns.items():
                if getattr(patient_info, field_name):
                    continue

                if label_rgx.match(t):
                    # Look for horizontal neighbor on the same line
                    best_cand = None
                    min_h_dist = float('inf')

                    for other in sorted_items:
                        if other is it:
                            continue
                        if abs(other['center_y'] - it['center_y']) < median_h * 0.8:
                            if other['bbox'][0] >= (it['bbox'][2] - 5.0):
                                dist = other['bbox'][0] - it['bbox'][2]
                                if dist < median_h * 12.0 and dist < min_h_dist:
                                    min_h_dist = dist
                                    best_cand = other

                    # If not found horizontally, look directly below (stacked layout)
                    if best_cand is None:
                        min_v_dist = float('inf')
                        for other in sorted_items:
                            if other is it:
                                continue
                            if other['bbox'][1] >= (it['bbox'][3] - 2.0):
                                v_dist = other['bbox'][1] - it['bbox'][3]
                                h_align = abs(other['bbox'][0] - it['bbox'][0])
                                if v_dist < median_h * 2.2 and h_align < median_h * 4.0 and v_dist < min_v_dist:
                                    min_v_dist = v_dist
                                    best_cand = other

                    if best_cand:
                        cand_val = clean_text(best_cand['text'])
                        is_other_label = any(rgx.match(cand_val) for rgx in label_only_patterns.values())
                        if cand_val and not is_other_label:
                            if field_name == 'gender':
                                if cand_val.upper() == 'M': cand_val = 'Male'
                                elif cand_val.upper() == 'F': cand_val = 'Female'
                            setattr(patient_info, field_name, cand_val)
                            confidences[field_name] = (it['confidence'] + best_cand['confidence']) / 2.0

        return patient_info, confidences

    @classmethod
    def _extract_assessments(cls, ocr_items: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, float]]:
        """
        Locate the Assessment section heading and extract all numbered assessment items.
        Handles multi-line items and terminates when the next section begins or when
        an unnumbered narrative note is encountered.
        """
        assessments: List[str] = []
        confidences: Dict[str, float] = {}

        if not ocr_items:
            return assessments, confidences

        # Step 1: Find the Assessment heading
        heading_item = None
        for it in ocr_items:
            t = it['text'].strip()
            if cls.ASSESSMENT_HEADING_REGEX.match(t) or t.lower() in ["assessment", "assessments", "assessment:", "assessments:"]:
                heading_item = it
                break

        # Also check for "ASSESSMENT & PLAN" or "ASSESSMENT/PLAN"
        if not heading_item:
            for it in ocr_items:
                t_lower = it['text'].strip().lower()
                if "assessment" in t_lower and ("plan" in t_lower or ":" in t_lower or len(t_lower) <= 25):
                    heading_item = it
                    break

        if not heading_item:
            return assessments, confidences

        heading_bottom_y = heading_item['bbox'][3]
        heights = [max(1.0, it['bbox'][3] - it['bbox'][1]) for it in ocr_items]
        median_h = float(np.median(heights)) if heights else 16.0

        # Step 2: Filter items below heading, sorted by vertical Y position
        candidate_items = [
            it for it in ocr_items
            if it['center_y'] > (heading_bottom_y + 2.0)
        ]
        candidate_items.sort(key=lambda x: (x['bbox'][1], x['bbox'][0]))

        current_num: Optional[int] = None
        current_text_parts: List[str] = []
        current_confs: List[float] = []
        last_item_bottom_y: float = heading_bottom_y

        for it in candidate_items:
            t = it['text'].strip()
            if not t:
                continue

            # Check if we hit a terminating section heading
            if cls.TERMINATING_SECTION_REGEX.match(t):
                break

            # Check if this item starts a new numbered assessment
            num_match = cls.NUMBERED_ITEM_REGEX.match(t)
            if num_match:
                # Save previously accumulated assessment item
                if current_text_parts:
                    item_str = clean_text(" ".join(current_text_parts))
                    assessments.append(item_str)
                    confidences[f"assessment_{len(assessments)}"] = float(np.mean(current_confs)) if current_confs else 0.90

                current_num = int(num_match.group(1))
                item_content = num_match.group(2).strip()
                current_text_parts = [f"{current_num}. {item_content}"]
                current_confs = [it['confidence']]
                last_item_bottom_y = it['bbox'][3]
            elif current_text_parts:
                # Unnumbered line below numbered assessments:
                # 1) If current item already has an ICD-10 code (e.g. - N18.30, - Z86.19),
                # the diagnosis is COMPLETE. Text under it is doctor discussion/notes!
                if cls.ICD_REGEX.search(" ".join(current_text_parts)):
                    break

                # 2) If there is a vertical gap > 0.7 * median_h:
                v_gap = it['bbox'][1] - last_item_bottom_y
                if v_gap > median_h * 0.7:
                    break

                # 3) If line looks like a narrative sentence (starts with capital and multiple words):
                if re.match(r'^(?:[A-Z][a-z]+(?:\s+[a-z]+){2,})', t):
                    break

                # 4) Page footer check:
                if re.search(r'\bpage\s*\d+\s*(?:of|\/)\s*\d+\b', t, re.IGNORECASE):
                    continue

                # Valid line continuation:
                current_text_parts.append(t)
                current_confs.append(it['confidence'])
                last_item_bottom_y = it['bbox'][3]

        # Save last accumulated item
        if current_text_parts:
            item_str = clean_text(" ".join(current_text_parts))
            assessments.append(item_str)
            confidences[f"assessment_{len(assessments)}"] = float(np.mean(current_confs)) if current_confs else 0.90

        return assessments, confidences

    @classmethod
    def _extract_visit_code(cls, ocr_items: List[Dict[str, Any]]) -> Tuple[str, float]:
        """
        Locate the Visit Code section heading and extract its corresponding value.
        Supports inline values, stacked values below heading, and prioritizes CPT codes.
        """
        if not ocr_items:
            return "", 0.0

        heights = [max(1.0, it['bbox'][3] - it['bbox'][1]) for it in ocr_items]
        median_h = float(np.median(heights)) if heights else 16.0

        for idx, it in enumerate(ocr_items):
            t = it['text'].strip()

            # Case A: Inline match (e.g. "Visit Code: 99204 Office Visit, New Pt., Level 4")
            m = cls.VISIT_CODE_HEADING_REGEX.match(t)
            if m:
                inline_val = clean_text(m.group(1))
                if inline_val:
                    inline_val = re.sub(r'^[\*\+\-•\s]+', '', inline_val).strip()
                    return inline_val, it['confidence']

                # Heading only inside this box - search for value in neighboring boxes
                heading_bottom = it['bbox'][3]
                heading_right = it['bbox'][2]

                # Look to the right first
                best_cand = None
                min_dist = float('inf')
                for other in ocr_items:
                    if other is it:
                        continue
                    if abs(other['center_y'] - it['center_y']) < median_h * 0.8:
                        if other['bbox'][0] >= (heading_right - 4.0):
                            d = other['bbox'][0] - heading_right
                            if d < median_h * 12.0 and d < min_dist:
                                min_dist = d
                                best_cand = other

                # If not to right, look below heading
                if best_cand is None:
                    sub_cands = []
                    for other in ocr_items:
                        if other is it:
                            continue
                        if other['center_y'] > it['center_y'] and other['bbox'][1] <= (heading_bottom + median_h * 3.0):
                            other_t = other['text'].strip().lower()
                            if any(other_t.startswith(sh) for sh in ['follow up', 'preventive', 'screening', 'counseling', 'progress note']):
                                continue
                            sub_cands.append(other)

                    if sub_cands:
                        cpt_cands = [c for c in sub_cands if cls.CPT_REGEX.search(c['text']) or 'office visit' in c['text'].lower()]
                        if cpt_cands:
                            best_cand = cpt_cands[0]
                        else:
                            best_cand = min(sub_cands, key=lambda c: c['bbox'][1])

                if best_cand:
                    val = clean_text(best_cand['text'])
                    val = re.sub(r'^[\*\+\-•\s]+', '', val).strip()
                    avg_conf = (it['confidence'] + best_cand['confidence']) / 2.0
                    return val, avg_conf

            # Case B: Fuzzy heading match for OCR imperfections
            t_norm = re.sub(r'[^a-z0-9]', '', t.lower())
            if t_norm in ["visitcode", "visitc0de", "billingcode", "cptcode", "servicecode"]:
                for other in ocr_items:
                    if other is it:
                        continue
                    if other['center_y'] > it['center_y'] and other['bbox'][1] <= (it['bbox'][3] + median_h * 3.0):
                        other_t = other['text'].strip().lower()
                        if any(other_t.startswith(sh) for sh in ['follow up', 'preventive', 'screening', 'counseling']):
                            continue
                        val = clean_text(other['text'])
                        val = re.sub(r'^[\*\+\-•\s]+', '', val).strip()
                        return val, (it['confidence'] + other['confidence']) / 2.0

        return "", 0.0

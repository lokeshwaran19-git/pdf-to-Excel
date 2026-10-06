import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import List, Dict, Any, Tuple
from backend.models.medical_models import MedicalDocumentRecord

class BatchExcelService:
    """
    Excel generator for Batch Medical Report extractions.
    Creates ONE formatted .xlsx file containing exactly one row per PDF.
    - Sheet name: Table 1
    - Freeze pane: A2
    - Dark navy header (#1F4E79), bold white text, centered, thin borders
    - Auto-filter on all columns
    - Alternating row shading (#F2F4F8)
    - Dynamic Assessment 1..N columns
    - Enforces '@' text format on identifiers to preserve leading zeros
    """

    BASE_PATIENT_HEADERS = [
        "Source PDF",
        "Patient Full Name",
        "Date of Birth",
        "Age",
        "Gender",
        "Account Number",
        "Patient ID",
        "Visit Number",
        "Address",
        "Phone",
        "Insurance",
        "PCP"
    ]

    @classmethod
    def build_headers_and_rows(cls, records: List[MedicalDocumentRecord]) -> Tuple[List[str], List[List[str]], List[List[bool]]]:
        """
        Convert a list of MedicalDocumentRecord objects into headers, rows, and low_conf_cells matrix.
        Calculates dynamic Assessment columns based on the max number of assessments found across all records.
        """
        # Determine max number of assessments across all records (at least 1)
        max_assessments = 1
        for rec in records:
            if rec.assessments:
                # Exclude trivial "N/A" only if there are other valid assessments
                valid_a = [a for a in rec.assessments if a and a != "N/A"]
                count = len(valid_a) if valid_a else 1
                if count > max_assessments:
                    max_assessments = count

        # Construct full header list
        headers = list(cls.BASE_PATIENT_HEADERS)
        for i in range(1, max_assessments + 1):
            headers.append(f"Assessment {i}")
        headers.append("Visit Code")
        headers.append("Review Required")

        table_data = []
        low_conf_cells = []

        for rec in records:
            p = rec.patient_info
            conf = rec.confidence

            # Base patient values
            row = [
                rec.source_file,
                p.patient_name or "N/A",
                p.date_of_birth or "N/A",
                p.age or "N/A",
                p.gender or "N/A",
                p.account_number or "N/A",
                p.patient_id or "N/A",
                p.visit_number or "N/A",
                p.address or "N/A",
                p.phone or "N/A",
                p.insurance or "N/A",
                p.pcp or "N/A"
            ]

            row_low_conf = [
                False,  # source_file
                conf.get('patient_name', 1.0) < 0.80 if p.patient_name else False,
                conf.get('date_of_birth', 1.0) < 0.80 if p.date_of_birth else False,
                conf.get('age', 1.0) < 0.80 if p.age else False,
                conf.get('gender', 1.0) < 0.80 if p.gender else False,
                conf.get('account_number', 1.0) < 0.80 if p.account_number else False,
                conf.get('patient_id', 1.0) < 0.80 if p.patient_id else False,
                conf.get('visit_number', 1.0) < 0.80 if p.visit_number else False,
                conf.get('address', 1.0) < 0.80 if p.address else False,
                conf.get('phone', 1.0) < 0.80 if p.phone else False,
                conf.get('insurance', 1.0) < 0.80 if p.insurance else False,
                conf.get('pcp', 1.0) < 0.80 if p.pcp else False
            ]

            # Populate assessment columns
            valid_assessments = [a for a in rec.assessments if a] if rec.assessments else ["N/A"]
            for i in range(max_assessments):
                if i < len(valid_assessments):
                    val = valid_assessments[i]
                    is_low = conf.get(f'assessment_{i+1}', 1.0) < 0.80 if val != "N/A" else False
                else:
                    val = "N/A"
                    is_low = False
                row.append(val)
                row_low_conf.append(is_low)

            # Visit code
            row.append(rec.visit_code or "N/A")
            row_low_conf.append(conf.get('visit_code', 1.0) < 0.80 if rec.visit_code and rec.visit_code != "N/A" else False)

            # Review Required
            review_str = "YES" if rec.review_required else "NO"
            row.append(review_str)
            row_low_conf.append(rec.review_required)

            table_data.append(row)
            low_conf_cells.append(row_low_conf)

        return headers, table_data, low_conf_cells

    @classmethod
    def create_batch_excel_file(cls, headers: List[str], table_data: List[List[str]], output_path: str) -> None:
        """
        Write combined batch extraction data into an openpyxl Excel spreadsheet.
        Enforces Table 1 title, A2 freeze pane, dark navy styling, alternating rows,
        and strict text formatting for ID/code columns to preserve leading zeros.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Table 1"

        # Styles
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid") # Dark Navy Blue
        data_font = Font(name="Calibri", size=11, color="000000")
        alt_row_fill = PatternFill(start_color="F2F4F8", end_color="F2F4F8", fill_type="solid") # Light Grey-Blue tint

        thin_border = Border(
            left=Side(style="thin", color="D9D9D9"),
            right=Side(style="thin", color="D9D9D9"),
            top=Side(style="thin", color="D9D9D9"),
            bottom=Side(style="thin", color="D9D9D9")
        )

        align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)

        # 1. Write Header Row
        ws.append(headers)
        ws.row_dimensions[1].height = 26
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center
            cell.border = thin_border

        # Columns that must strictly remain TEXT format to preserve leading zeros
        # e.g., Source PDF, Account Number, Patient ID, Visit Number, Phone, Visit Code
        text_column_names = {
            "source pdf", "account number", "patient id", "visit number", 
            "phone", "visit code", "date of birth", "dob"
        }
        text_col_indices = set()
        for idx, h in enumerate(headers, start=1):
            if h.lower() in text_column_names or "code" in h.lower() or "id" in h.lower() or "number" in h.lower():
                text_col_indices.add(idx)

        # 2. Write Data Rows
        for r_idx, row_values in enumerate(table_data, start=2):
            ws.append(row_values)
            ws.row_dimensions[r_idx].height = 22

            for c_idx in range(1, len(row_values) + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.font = data_font
                cell.border = thin_border

                # Alternating row shading
                if r_idx % 2 == 1:
                    cell.fill = alt_row_fill

                # Strict text formatting for ID columns
                if c_idx in text_col_indices or True:
                    cell.number_format = '@'
                    cell.data_type = 's'

                # Alignment: center dates, gender, age, review required; left-align names & text
                col_name = headers[c_idx - 1].lower() if c_idx <= len(headers) else ""
                if any(k in col_name for k in ["date", "age", "gender", "review", "dob"]):
                    cell.alignment = align_center
                else:
                    cell.alignment = align_left

        # 3. Freeze top row at A2
        ws.freeze_panes = "A2"

        # 4. Enable auto filter
        if ws.dimensions:
            ws.auto_filter.ref = ws.dimensions

        # 5. Intelligent column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(14, min(max_len + 5, 55))

        wb.save(output_path)

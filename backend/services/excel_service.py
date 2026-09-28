import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import List

class ExcelService:
    @staticmethod
    def create_excel_file(headers: List[str], data_rows: List[List[str]], output_path: str) -> None:
        """
        Generate a professional Excel file matching openpyxl standards:
        - Sheet name: Table 1
        - Freeze panes: A2
        - Auto-filter on all columns
        - Preserves text formatting for Patient IDs, Visit Numbers, and Order Numbers (leading zeros retained).
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Table 1"

        # Define Styles
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
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center
            cell.border = thin_border
            ws.row_dimensions[1].height = 26

        # Columns that must strictly remain TEXT format to preserve leading zeros
        # B: Patient ID, E: Visit Number, F: Order Number
        text_column_indices = {1, 2, 5, 6} # 1-based indices: A(1), B(2), E(5), F(6)

        # 2. Write Data Rows
        for r_idx, row_values in enumerate(data_rows, start=2):
            ws.append(row_values)
            ws.row_dimensions[r_idx].height = 20
            
            # Apply styling & text format enforcement
            for c_idx in range(1, len(row_values) + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.font = data_font
                cell.border = thin_border

                # Alternate row shading
                if r_idx % 2 == 1:
                    cell.fill = alt_row_fill

                # Enforce Text format for ID columns to prevent Excel from stripping leading zeros
                if c_idx in text_column_indices or True:
                    cell.number_format = '@'
                    cell.data_type = 's' # string format

                # Alignment: IDs, Dates, Test Types centered; Names left-aligned
                if c_idx in [1, 2, 4, 5, 6, 7, 8, 9]:
                    cell.alignment = align_center
                else:
                    cell.alignment = align_left

        # 3. Freeze top row (A2)
        ws.freeze_panes = "A2"

        # 4. Enable auto filter for the table
        if ws.dimensions:
            ws.auto_filter.ref = ws.dimensions

        # 5. Set intelligent column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if len(val_str) > max_len:
                    max_len = len(val_str)
            # Set padded width (min 14, max 40)
            ws.column_dimensions[col_letter].width = max(14, min(max_len + 5, 40))

        # Save workbook to destination path
        wb.save(output_path)

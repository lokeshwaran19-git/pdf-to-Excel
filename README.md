# PDF to Excel Table Extraction System

A production-quality full-stack application built with **FastAPI** (Python 3.11+) and a modern **Vanilla HTML5/CSS3/JS** frontend that converts structured, scanned, image-based, and rotated PDF reports into formatted Excel (`.xlsx`) files.

Specifically engineered to handle rotated table reports such as `AY(1).pdf` and reconstruct 9 target logical columns matching reference Excel schemas (`AY (1).xlsx`).

---

## 🌟 Architecture Overview

```text
                  USER
                    │
                    ▼
            HTML5/CSS3/JS UI
                    │
                    ▼  (POST /api/convert)
              FastAPI Backend
                    │
                    ▼
                 PyMuPDF
        (Render PDF Pages to 300 DPI)
                    │
                    ▼
           Orientation Detection
       (0°, 90°, 180°, 270° Auto-Rotate)
                    │
                    ▼
           OpenCV Preprocessing
          (CLAHE Contrast & Sharpening)
                    │
                    ▼
                 PaddleOCR
       (Bounding Boxes & Confidence)
                    │
                    ▼
             Table Detection
        ┌───────────┴───────────┐
        ▼                       ▼
   Row Grouping          Column Detection
        └───────────┬───────────┘
                    ▼
           Cell Reconstruction
       (Multi-line merging & ID format)
                    │
                    ▼
           OpenPyXL Excel Service
        (Sheet: Table 1, Freeze Pane A2,
         Auto-filter, Text format for IDs)
                    │
                    ▼
           Download Excel (.xlsx)
```

---

## ✨ Features

- **Rotated PDF Detection**: Automatically detects page orientation (0°, 90°, 180°, 270°) and corrects rotation before OCR processing.
- **Scanned & Image PDF Processing**: Uses high-resolution 300 DPI rendering via PyMuPDF and CLAHE contrast preprocessing via OpenCV.
- **PaddleOCR Bounding Boxes**: Captures spatial coordinates `[x1, y1, x2, y2]` and confidence scores for every text fragment.
- **9 Target Logical Columns**:
  1. Overreader ID
  2. Patient ID
  3. Patient Full Name
  4. Date of Birth
  5. Visit Number
  6. Order Number
  7. Acquisition Date/Time
  8. Test Type Value
  9. Test Reason
- **Multi-line Cell Reconstruction**: Merges names, dates, and order numbers into single intact cells rather than splitting across rows/columns.
- **Leading Zero Preservation**: Preserves Patient IDs, Visit Numbers, and Order Numbers strictly as **TEXT** strings to prevent Excel digit truncation (e.g. `0907-0064`, `0000G230772`).
- **Interactive Review Spreadsheet**: Full browser grid editor with row search, add/delete row, add column, undo, and live export.
- **Security & Privacy**: Uploaded PDFs and intermediate temporary processing files are securely deleted after conversion. `/uploads` and `/outputs` are not exposed as static directories.

---

## 🚀 Installation & Local Development Setup

### 1. Prerequisites

- Python 3.11+ (or Python 3.13 / 3.14)
- Web Browser (Chrome, Firefox, Edge, Safari)

### 2. Backend Setup

```bash
# Navigate to the backend directory
cd backend

# Create a virtual environment
python -m venv venv

# Activate virtual environment (Windows)
venv\Scripts\activate

# Install required dependencies
pip install -r requirements.txt

# Start the FastAPI Uvicorn server
uvicorn main:app --reload --port 8000
```

The backend server will run at `http://127.0.0.1:8000`. API docs are available at `http://127.0.0.1:8000/docs`.

### 3. Frontend Setup

The frontend is served directly by FastAPI at `http://127.0.0.1:8000/app` or can be opened by launching `frontend/index.html` with any local web server (e.g., Live Server or Python HTTP server):

```bash
# Optional standalone frontend server
cd frontend
python -m http.server 3000
```

---

## 🔬 How OCR & Table Detection Works

1. **PDF Page Rendering**: PyMuPDF converts vector/scanned PDF pages to 300 DPI images.
2. **Orientation Analysis**: Tests horizontal text aspect ratios and header keywords across 0°, 90°, 180°, and 270° rotations to identify the correct orientation.
3. **OpenCV Image Enhancement**: Converts image to grayscale, applies CLAHE contrast adjustment, and sharpens text outlines.
4. **Bounding Box OCR**: PaddleOCR detects text fragments and outputs bounding boxes `[x1, y1, x2, y2]`.
5. **Table Boundary Identification**: Finds top table header and bottom page footers to exclude report titles and page numbers.
6. **Column Boundaries**: Calculates exact horizontal X cuts for the 9 target columns.
7. **Row Grouping**: Groups text items into physical table rows by vertical center coordinate (`center_y`).
8. **Cell Concatenation**: Merges text items falling within the same cell bucket into clean formatted text strings.
9. **Excel Output**: OpenPyXL writes the dataset to sheet `Table 1`, freezes row `A2`, adds auto-filters, and enforces text formatting for identifier columns.

---

## 🛠 Developer Debug Mode

To inspect visual bounding boxes, row divisions, and column cut lines:

```http
GET http://127.0.0.1:8000/api/debug/{job_id}?page=1
```

- **Blue boxes**: Detected OCR text items
- **Green lines**: Row boundaries
- **Red lines**: Column boundaries
- **Purple box**: Table region boundary

---

## 🧪 Testing with `AY(1).pdf`

To run end-to-end verification via terminal command:

```bash
python -c "
from backend.services.pdf_service import PDFService
from backend.services.ocr_service import OCRService
from backend.services.table_service import TableService
from backend.services.excel_service import ExcelService

with open('AY(1).pdf', 'rb') as f:
    pdf_bytes = f.read()

images = PDFService.render_pdf_to_images(pdf_bytes, dpi=300)
ocr = OCRService.get_instance()
table = TableService(ocr)
res = table.extract_tables_from_pdf_pages(images)

print('Extracted rows:', res['row_count'])
print('Sample row 1:', res['table_data'][0])
ExcelService.create_excel_file(res['headers'], res['table_data'], 'output.xlsx')
"
```

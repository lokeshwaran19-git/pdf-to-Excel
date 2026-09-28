import os
import uuid
import shutil
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from backend.services.pdf_service import PDFService
from backend.services.ocr_service import OCRService
from backend.services.table_service import TableService
from backend.services.excel_service import ExcelService
from backend.utils.image_utils import draw_debug_annotations, rotate_image, preprocess_image
import cv2

app = FastAPI(title="PDF to Excel Extraction API", version="1.0.0")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global in-memory storage for active jobs (store intermediate table data for editing/download)
# Job directory stores output xlsx files securely, deleted upon download or after expiry
JOBS_CACHE: Dict[str, Dict[str, Any]] = {}

# Ensure working temp directories exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "uploads")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Serve frontend static assets if frontend directory exists
if os.path.exists(FRONTEND_DIR):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

def cleanup_file(filepath: str):
    """Background task helper to securely delete temporary files."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"Error cleaning up file {filepath}: {e}")

@app.get("/")
def root():
    """Redirect root to frontend application."""
    return RedirectResponse(url="/app/")

@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}

@app.post("/api/convert")
async def convert_pdf_to_excel(file: UploadFile = File(...)):
    """
    Core Conversion Endpoint:
    Upload PDF -> Render Pages -> Detect Rotation -> Run PaddleOCR -> Detect Table ->
    Reconstruct Rows/Cols/Cells -> Generate Excel -> Cache Job Data -> Return JSON.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF document.")

    job_id = str(uuid.uuid4())
    temp_pdf_path = os.path.join(UPLOADS_DIR, f"{job_id}_{file.filename}")

    try:
        # 1. Read PDF file contents into memory & save temporary file
        pdf_bytes = await file.read()
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_bytes)

        # 2. Render PDF pages to high-resolution images
        page_images = PDFService.render_pdf_to_images(pdf_bytes, dpi=300)
        page_count = len(page_images)

        if page_count == 0:
            raise HTTPException(status_code=400, detail="PDF contains no renderable pages.")

        # 3. Initialize OCR and Table Extraction Services
        ocr_service = OCRService.get_instance()
        table_service = TableService(ocr_service)

        # 4. Extract structured table data
        extraction_result = table_service.extract_tables_from_pdf_pages(page_images)

        headers = extraction_result["headers"]
        table_data = extraction_result["table_data"]
        low_conf_cells = extraction_result["low_conf_cells"]

        # 5. Generate Excel File using OpenPyXL
        out_filename = f"{os.path.splitext(file.filename)[0]}-converted.xlsx"
        excel_path = os.path.join(OUTPUTS_DIR, f"{job_id}_{out_filename}")

        ExcelService.create_excel_file(headers, table_data, excel_path)

        # 6. Cache job details for download & edit export
        JOBS_CACHE[job_id] = {
            "filename": file.filename,
            "out_filename": out_filename,
            "excel_path": excel_path,
            "headers": headers,
            "table_data": table_data,
            "low_conf_cells": low_conf_cells,
            "debug_info": extraction_result.get("debug_info", []),
            "page_images": page_images # Keep references in memory for debug visualization
        }

        # 7. Securely delete temporary PDF file
        cleanup_file(temp_pdf_path)

        return {
            "success": True,
            "job_id": job_id,
            "filename": file.filename,
            "pages": page_count,
            "rows": len(table_data),
            "columns": len(headers),
            "confidence": extraction_result["avg_confidence"],
            "extraction_method": "PaddleOCR",
            "data_quality_label": extraction_result["data_quality_label"],
            "headers": headers,
            "table_data": table_data,
            "low_conf_cells": low_conf_cells,
            "download_url": f"/api/download/{job_id}"
        }

    except Exception as e:
        cleanup_file(temp_pdf_path)
        print(f"Error during PDF conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Table extraction failed: {str(e)}")

class ExportRequest(BaseModel):
    headers: List[str]
    table_data: List[List[str]]

@app.post("/api/export/{job_id}")
def export_edited_table(job_id: str, payload: ExportRequest):
    """Update Excel file with user's manual edits from spreadsheet preview."""
    if job_id not in JOBS_CACHE:
        raise HTTPException(status_code=404, detail="Job ID not found or expired.")

    job_info = JOBS_CACHE[job_id]
    excel_path = job_info["excel_path"]

    # Re-generate Excel with updated user edited values
    ExcelService.create_excel_file(payload.headers, payload.table_data, excel_path)
    job_info["table_data"] = payload.table_data

    return {
        "success": True,
        "job_id": job_id,
        "download_url": f"/api/download/{job_id}"
    }

@app.get("/api/download/{job_id}")
def download_excel(job_id: str, background_tasks: BackgroundTasks):
    """
    Download generated XLSX file.
    Deletes the temporary output file after sending.
    """
    if job_id not in JOBS_CACHE:
        raise HTTPException(status_code=404, detail="File not found or expired.")

    job_info = JOBS_CACHE[job_id]
    excel_path = job_info["excel_path"]
    out_filename = job_info["out_filename"]

    if not os.path.exists(excel_path):
        raise HTTPException(status_code=404, detail="Generated Excel file missing.")

    # Schedule cleanup after download completes
    background_tasks.add_task(cleanup_file, excel_path)

    return FileResponse(
        path=excel_path,
        filename=out_filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/api/debug/{job_id}")
def get_debug_info(job_id: str, page: int = 1):
    """Developer debug endpoint to visualize bounding boxes, rows, columns, and table boundary."""
    if job_id not in JOBS_CACHE:
        raise HTTPException(status_code=404, detail="Job ID not found.")

    job_info = JOBS_CACHE[job_id]
    debug_list = job_info.get("debug_info", [])
    page_images = job_info.get("page_images", [])

    if page < 1 or page > len(debug_list):
        raise HTTPException(status_code=400, detail="Invalid page index.")

    p_info = debug_list[page - 1]
    raw_img = page_images[page - 1]

    # Rotate raw image to page orientation
    rot_img = rotate_image(raw_img, p_info["orientation"])

    # Draw debug annotations: Blue = OCR, Green = Rows, Red = Cols, Purple = Table
    debug_img = draw_debug_annotations(
        rot_img,
        p_info["ocr_items"],
        p_info["rows"],
        p_info["col_bounds"],
        table_bounds={"x_min": 0, "y_min": p_info["header_row_y"], "x_max": rot_img.shape[1], "y_max": p_info["footer_y"]}
    )

    # Encode debug image to PNG
    success, encoded_img = cv2.imencode(".png", debug_img)
    if not success:
        raise HTTPException(status_code=500, detail="Could not encode debug image.")

    temp_img_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_img_file.write(encoded_img.tobytes())
    temp_img_file.close()

    return FileResponse(path=temp_img_file.name, media_type="image/png")

import os
import gc
import uuid
import shutil
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body, Request
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

# Allowed origins — covers Cloudflare Workers frontend, local dev, and Render self-serving
ALLOWED_ORIGINS = [
    "https://pdf-to-excel.lokeshlap2828.workers.dev",  # Cloudflare Workers (production)
    "http://localhost:8000",                             # Local FastAPI dev server
    "http://127.0.0.1:8000",                            # Local FastAPI dev server (alias)
    "http://localhost:3000",                             # Local frontend dev (if applicable)
]

# Enable CORS for local development and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Custom exception handlers to ensure CORS headers survive error responses ──
# FastAPI's CORS middleware does NOT attach headers to unhandled exceptions;
# this means the browser sees a misleading CORS error instead of the real error.
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    origin = request.headers.get("origin", "")
    headers = {}
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "*"
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin", "")
    headers = {}
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "*"
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
        headers=headers,
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
def convert_pdf_to_excel(file: UploadFile = File(...)):
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
        pdf_bytes = file.file.read()
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_bytes)

        # 2. Render PDF pages to high-resolution images
        # DPI 130 balances OCR accuracy vs. memory usage on constrained hosts (Render free tier).
        page_images = PDFService.render_pdf_to_images(pdf_bytes, dpi=130)

        # Free raw PDF bytes from memory immediately after rendering;
        # only the NumPy image arrays are needed hereafter.
        del pdf_bytes
        gc.collect()

        page_count = len(page_images)

        if page_count == 0:
            raise HTTPException(status_code=400, detail="PDF contains no renderable pages.")

        # Guard against excessively large PDFs that would OOM the server
        MAX_PAGES = 20
        if page_count > MAX_PAGES:
            raise HTTPException(
                status_code=400,
                detail=f"PDF has {page_count} pages. Maximum supported is {MAX_PAGES} pages."
            )

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
        # Free large image arrays from memory before caching
        del page_images
        gc.collect()

        JOBS_CACHE[job_id] = {
            "filename": file.filename,
            "out_filename": out_filename,
            "excel_path": excel_path,
            "headers": headers,
            "table_data": table_data,
            "low_conf_cells": low_conf_cells,
            "debug_info": extraction_result.get("debug_info", [])
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

# Debug endpoint removed to avoid memory issues and missing page images.
# If needed, re-implement with on-demand PDF rendering.


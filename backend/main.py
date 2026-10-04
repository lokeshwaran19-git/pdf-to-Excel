import os
# Strictly constrain thread counts before any native C/C++ libraries load
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import gc
import uuid
import shutil
import tempfile
import asyncio
import time
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

# ── Allowed CORS origins ────────────────────────────────────────────────────
ALLOWED_ORIGINS = [
    "https://pdf-to-excel.lokeshlap2828.workers.dev",
    "https://pdf-to-excel-n2ho.onrender.com",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Prevent stale browser caching of frontend static assets ────────────────
@app.middleware("http")
async def add_cache_control_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/app") or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Custom exception handlers to ensure CORS headers survive error responses ──
def _get_cors_headers(request: Request) -> dict:
    """Return CORS headers matching the request's origin."""
    origin = request.headers.get("origin")
    allowed_origin = origin if origin else "*"
    return {
        "Access-Control-Allow-Origin": allowed_origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Vary": "Origin",
    }

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=_get_cors_headers(request),
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
        headers=_get_cors_headers(request),
    )

@app.on_event("startup")
def startup_event():
    """Warm up OCR service on startup to load ONNX models once into memory."""
    print("Pre-loading OCR engine...")
    try:
        OCRService.get_instance()
        print("OCR engine loaded successfully.")
    except Exception as e:
        print(f"Error pre-loading OCR engine: {e}")

# Global in-memory storage for active jobs (store intermediate table data for editing/download)
# Bounded to max 12 items to prevent memory buildup on Render's 512MB RAM tier
JOBS_CACHE: Dict[str, Dict[str, Any]] = {}

# Concurrency semaphore: strictly allow ONLY ONE PDF conversion at a time.
# Running concurrent ONNX OCR inferences blows past 512MB RAM and kills the process (502 Bad Gateway).
CONVERSION_SEMAPHORE = asyncio.Semaphore(1)

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

def _cleanup_old_files():
    """Remove files older than 30 minutes from uploads and outputs."""
    now = time.time()
    for directory in [UPLOADS_DIR, OUTPUTS_DIR]:
        try:
            for fname in os.listdir(directory):
                fpath = os.path.join(directory, fname)
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath) > 1800):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
        except Exception:
            pass

def _evict_oldest_jobs(max_jobs: int = 12):
    """Keep JOBS_CACHE bounded in memory to prevent memory leaks."""
    while len(JOBS_CACHE) > max_jobs:
        oldest_job_id = next(iter(JOBS_CACHE))
        old_job = JOBS_CACHE.pop(oldest_job_id, None)
        if old_job and "excel_path" in old_job:
            cleanup_file(old_job["excel_path"])

@app.get("/")
def root():
    """Redirect root to frontend application."""
    return RedirectResponse(url="/app/")

@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}

def _run_conversion_sync(pdf_bytes: bytes, filename: str, job_id: str, temp_pdf_path: str):
    """Synchronous CPU/memory intensive conversion task."""
    gc.collect()
    try:
        # 1. Save temporary PDF
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_bytes)

        # 2. Render PDF pages to high-resolution images
        page_images = PDFService.render_pdf_to_images(pdf_bytes, dpi=96)
        del pdf_bytes
        gc.collect()

        page_count = len(page_images)
        if page_count == 0:
            raise HTTPException(status_code=400, detail="PDF contains no renderable pages.")

        MAX_PAGES = 10
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
        out_filename = f"{os.path.splitext(filename)[0]}-converted.xlsx"
        excel_path = os.path.join(OUTPUTS_DIR, f"{job_id}_{out_filename}")
        ExcelService.create_excel_file(headers, table_data, excel_path)

        # Free image arrays immediately before caching
        del page_images
        gc.collect()

        # Cache job details with bounded memory
        _evict_oldest_jobs(12)
        JOBS_CACHE[job_id] = {
            "filename": filename,
            "out_filename": out_filename,
            "excel_path": excel_path,
            "headers": headers,
            "table_data": table_data,
            "low_conf_cells": low_conf_cells,
            "debug_info": extraction_result.get("debug_info", [])
        }

        cleanup_file(temp_pdf_path)

        return {
            "success": True,
            "job_id": job_id,
            "filename": filename,
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
    except HTTPException:
        cleanup_file(temp_pdf_path)
        raise
    except Exception as e:
        cleanup_file(temp_pdf_path)
        print(f"Error during PDF conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Table extraction failed: {str(e)}")
    finally:
        gc.collect()

@app.post("/api/convert")
async def convert_pdf_to_excel(file: UploadFile = File(...)):
    """
    Core Conversion Endpoint protected by concurrency semaphore:
    Only 1 conversion runs at a time to prevent 502 / OOM crashes on Render.
    Offloaded to thread pool so the async event loop stays completely responsive.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF document.")

    # If another conversion is already running, wait up to 15s or return 503 so frontend retries smoothly
    try:
        await asyncio.wait_for(CONVERSION_SEMAPHORE.acquire(), timeout=15.0)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Server is currently processing another document. Please wait a moment."
        )

    job_id = str(uuid.uuid4())
    temp_pdf_path = os.path.join(UPLOADS_DIR, f"{job_id}_{file.filename}")

    try:
        pdf_bytes = await file.read()
        _cleanup_old_files()

        # Run CPU-intensive conversion in threadpool so FastAPI event loop remains responsive
        result = await asyncio.to_thread(_run_conversion_sync, pdf_bytes, file.filename, job_id, temp_pdf_path)
        return result
    finally:
        CONVERSION_SEMAPHORE.release()
        gc.collect()

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


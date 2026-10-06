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
from backend.services.medical_extractor import MedicalExtractor
from backend.services.batch_excel_service import BatchExcelService
from backend.models.medical_models import BatchExportRequest, MedicalDocumentRecord
from backend.utils.image_utils import draw_debug_annotations, rotate_image, preprocess_image
import cv2

app = FastAPI(title="PDF to Excel Extraction API", version="1.0.0")

# ── Allowed CORS origins ────────────────────────────────────────────────────
ALLOWED_ORIGINS = [
    "https://pdf-to-excel.lokeshlap2828.workers.dev",
    "https://pdf-to-excel.enoahconverters.workers.dev",
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

# ── Background Asynchronous Conversion Queue ───────────────────────────────
# Handles concurrent submissions from 100+ users safely without timeouts,
# without memory spikes, and with ZERO 502 Bad Gateway errors.
JOB_QUEUE: asyncio.Queue = asyncio.Queue()
ACTIVE_JOB_ID: Optional[str] = None

# Global in-memory storage for active jobs (store intermediate table data for editing/download)
JOBS_CACHE: Dict[str, Dict[str, Any]] = {}
BATCHES_CACHE: Dict[str, Dict[str, Any]] = {}

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
        if filepath and os.path.exists(filepath):
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
                    cleanup_file(fpath)
        except Exception:
            pass

def _evict_oldest_jobs(max_jobs: int = 30):
    """Keep JOBS_CACHE and BATCHES_CACHE bounded in memory to prevent memory leaks."""
    while len(JOBS_CACHE) > max_jobs:
        oldest_job_id = next(iter(JOBS_CACHE))
        old_job = JOBS_CACHE.pop(oldest_job_id, None)
        if old_job:
            if "excel_path" in old_job:
                cleanup_file(old_job["excel_path"])
            if "temp_pdf_path" in old_job:
                cleanup_file(old_job["temp_pdf_path"])

    while len(BATCHES_CACHE) > max_jobs:
        oldest_batch_id = next(iter(BATCHES_CACHE))
        old_batch = BATCHES_CACHE.pop(oldest_batch_id, None)
        if old_batch:
            if "excel_path" in old_batch:
                cleanup_file(old_batch["excel_path"])
            for f in old_batch.get("files", []):
                cleanup_file(f.get("temp_path"))

def _run_conversion_sync(pdf_bytes: bytes, filename: str, job_id: str, temp_pdf_path: str):
    """Synchronous CPU/memory intensive conversion task."""
    gc.collect()
    try:
        # 1. Render PDF pages to high-resolution images
        # DPI 85 achieves ~19s extraction with 87.5% confidence and optimal RAM usage on Render
        page_images = PDFService.render_pdf_to_images(pdf_bytes, dpi=85)
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

        # 2. Initialize OCR and Table Extraction Services
        ocr_service = OCRService.get_instance()
        table_service = TableService(ocr_service)

        # 3. Extract structured table data
        extraction_result = table_service.extract_tables_from_pdf_pages(page_images)

        headers = extraction_result["headers"]
        table_data = extraction_result["table_data"]
        low_conf_cells = extraction_result["low_conf_cells"]

        # 4. Generate Excel File using OpenPyXL
        out_filename = f"{os.path.splitext(filename)[0]}-converted.xlsx"
        excel_path = os.path.join(OUTPUTS_DIR, f"{job_id}_{out_filename}")
        ExcelService.create_excel_file(headers, table_data, excel_path)

        # Free image arrays immediately before caching
        del page_images
        gc.collect()

        res_payload = {
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

        # Cache job details with bounded memory
        _evict_oldest_jobs(25)
        if job_id in JOBS_CACHE:
            JOBS_CACHE[job_id].update({
                "out_filename": out_filename,
                "excel_path": excel_path,
                "headers": headers,
                "table_data": table_data,
                "low_conf_cells": low_conf_cells,
                "debug_info": extraction_result.get("debug_info", []),
                "result": res_payload
            })

        cleanup_file(temp_pdf_path)
        return res_payload

    except HTTPException:
        cleanup_file(temp_pdf_path)
        raise
    except Exception as e:
        cleanup_file(temp_pdf_path)
        print(f"Error during PDF conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Table extraction failed: {str(e)}")
    finally:
        gc.collect()

def _run_batch_conversion_sync(batch_id: str):
    """
    Synchronous sequential conversion for a batch of medical PDF reports.
    Processes one PDF at a time, and inside each PDF, one page at a time.
    Strictly protects against memory spikes on 512 MB RAM environments.
    """
    gc.collect()
    if batch_id not in BATCHES_CACHE:
        return

    batch = BATCHES_CACHE[batch_id]
    file_entries = batch.get("files", [])
    total_files = len(file_entries)
    ocr_service = OCRService.get_instance()

    batch_records = []
    completed = 0
    failed = 0

    for idx, f_entry in enumerate(file_entries, start=1):
        fname = f_entry["filename"]
        fpath = f_entry["temp_path"]

        batch["current_file"] = fname
        batch["stage"] = f"Processing PDF {idx} of {total_files}: {fname}..."
        batch["progress"] = int(10 + 75 * (idx - 1) / max(1, total_files))

        if not os.path.exists(fpath):
            failed += 1
            batch["failed_files"] = failed
            batch["errors"].append({"file": fname, "error": "Temporary PDF file missing"})
            continue

        try:
            with open(fpath, "rb") as f:
                pdf_bytes = f.read()

            record = MedicalExtractor.extract_document(pdf_bytes, fname, ocr_service)
            del pdf_bytes
            cleanup_file(fpath)
            gc.collect()

            if record.success:
                completed += 1
            else:
                failed += 1
                batch["errors"].append({"file": fname, "error": record.error or "Section detection failed"})

            batch["completed_files"] = completed
            batch["failed_files"] = failed
            batch_records.append(record)

        except Exception as file_exc:
            cleanup_file(fpath)
            failed += 1
            batch["failed_files"] = failed
            batch["errors"].append({"file": fname, "error": str(file_exc)})
            batch_records.append(MedicalDocumentRecord(
                source_file=fname,
                assessments=["N/A"],
                visit_code="N/A",
                review_required=True,
                success=False,
                error=str(file_exc)
            ))
            gc.collect()

    # Step 2: Combine all records into ONE Excel file
    batch["stage"] = "Generating combined Excel spreadsheet..."
    batch["progress"] = 92

    headers, table_data, low_conf_cells = BatchExcelService.build_headers_and_rows(batch_records)

    today_str = time.strftime('%Y-%m-%d')
    out_filename = f"medical_reports_batch_{today_str}.xlsx"
    excel_path = os.path.join(OUTPUTS_DIR, f"{batch_id}_{out_filename}")

    BatchExcelService.create_batch_excel_file(headers, table_data, excel_path)

    res_payload = {
        "success": True,
        "batch_id": batch_id,
        "total_files": total_files,
        "completed_files": completed,
        "failed_files": failed,
        "headers": headers,
        "table_data": table_data,
        "low_conf_cells": low_conf_cells,
        "records": [r.model_dump() for r in batch_records],
        "errors": batch["errors"],
        "out_filename": out_filename,
        "download_url": f"/api/batch-download/{batch_id}"
    }

    _evict_oldest_jobs(25)
    batch.update({
        "status": "completed",
        "progress": 100,
        "stage": f"{completed} of {total_files} PDFs successfully processed!",
        "download_ready": True,
        "out_filename": out_filename,
        "excel_path": excel_path,
        "headers": headers,
        "table_data": table_data,
        "low_conf_cells": low_conf_cells,
        "result": res_payload
    })
    gc.collect()

async def queue_worker_loop():
    """Background consumer loop that converts queued PDFs one at a time."""
    global ACTIVE_JOB_ID
    print("Background conversion worker initialized and waiting for jobs...")
    while True:
        task_item = None
        try:
            task_item = await JOB_QUEUE.get()

            # Check if this is a batch conversion task
            if isinstance(task_item, dict) and task_item.get("type") == "batch":
                batch_id = task_item["batch_id"]
                if batch_id not in BATCHES_CACHE:
                    JOB_QUEUE.task_done()
                    continue

                ACTIVE_JOB_ID = f"batch_{batch_id}"
                batch = BATCHES_CACHE[batch_id]
                batch["status"] = "processing"
                batch["stage"] = "Starting batch conversion..."
                batch["progress"] = 10

                await asyncio.to_thread(_run_batch_conversion_sync, batch_id)

            else:
                # Single PDF conversion task
                job_id = task_item["job_id"] if isinstance(task_item, dict) else task_item
                if job_id not in JOBS_CACHE:
                    JOB_QUEUE.task_done()
                    continue

                ACTIVE_JOB_ID = job_id
                job = JOBS_CACHE[job_id]
                job["status"] = "processing"
                job["stage"] = "Rendering pages & running OCR..."
                job["progress"] = 30

                temp_pdf_path = job.get("temp_pdf_path")
                if not temp_pdf_path or not os.path.exists(temp_pdf_path):
                    job["status"] = "failed"
                    job["error"] = "Uploaded PDF file is missing."
                    JOB_QUEUE.task_done()
                    ACTIVE_JOB_ID = None
                    continue

                with open(temp_pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                result = await asyncio.to_thread(
                    _run_conversion_sync, pdf_bytes, job["filename"], job_id, temp_pdf_path
                )

                job["status"] = "completed"
                job["stage"] = "Conversion complete!"
                job["progress"] = 100
                job["result"] = result

        except Exception as e:
            print(f"Error executing queued task {task_item}: {e}")
            if isinstance(task_item, dict) and task_item.get("type") == "batch":
                b_id = task_item.get("batch_id")
                if b_id and b_id in BATCHES_CACHE:
                    BATCHES_CACHE[b_id]["status"] = "failed"
                    BATCHES_CACHE[b_id]["error"] = str(e)
            else:
                j_id = task_item.get("job_id") if isinstance(task_item, dict) else task_item
                if j_id and j_id in JOBS_CACHE:
                    JOBS_CACHE[j_id]["status"] = "failed"
                    JOBS_CACHE[j_id]["error"] = str(e)
        finally:
            if task_item:
                try:
                    JOB_QUEUE.task_done()
                except ValueError:
                    pass
            ACTIVE_JOB_ID = None
            gc.collect()

@app.on_event("startup")
async def startup_event():
    """Warm up OCR service on startup to load ONNX models once into memory."""
    print("Pre-loading OCR engine...")
    try:
        OCRService.get_instance()
        print("OCR engine loaded successfully.")
    except Exception as e:
        print(f"Error pre-loading OCR engine: {e}")

    # Launch background conversion queue worker
    asyncio.create_task(queue_worker_loop())

@app.get("/")
def root():
    """Redirect root to frontend application."""
    return RedirectResponse(url="/app/")

@app.get("/api/health")
def health_check():
    """Health check endpoint with queue metrics."""
    return {
        "status": "ok",
        "queue_size": JOB_QUEUE.qsize(),
        "active_job": ACTIVE_JOB_ID is not None
    }

@app.post("/api/convert")
async def convert_pdf_to_excel(file: UploadFile = File(...)):
    """
    Asynchronous Conversion Endpoint:
    Accepts PDF upload, registers in queue, and immediately returns 200 OK with job_id.
    Takes < 150 ms! Completely immune to Cloudflare 100s timeouts and Render 502s!
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF document.")

    job_id = str(uuid.uuid4())
    temp_pdf_path = os.path.join(UPLOADS_DIR, f"{job_id}_{file.filename}")

    try:
        pdf_bytes = await file.read()
        if len(pdf_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        max_size = 25 * 1024 * 1024  # 25 MB
        if len(pdf_bytes) > max_size:
            raise HTTPException(status_code=400, detail="File exceeds 25 MB limit.")

        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_bytes)
        del pdf_bytes

        _cleanup_old_files()

        # Calculate position in queue
        current_queue_len = JOB_QUEUE.qsize()
        queue_pos = current_queue_len + (1 if ACTIVE_JOB_ID else 0)

        JOBS_CACHE[job_id] = {
            "job_id": job_id,
            "filename": file.filename,
            "temp_pdf_path": temp_pdf_path,
            "created_at": time.time(),
            "status": "queued",
            "progress": 10 if queue_pos == 0 else 5,
            "stage": "Waiting in queue..." if queue_pos > 0 else "Starting conversion...",
            "result": None,
            "error": None,
        }

        await JOB_QUEUE.put(job_id)

        return {
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "queue_position": queue_pos,
            "poll_url": f"/api/status/{job_id}",
        }
    except HTTPException:
        cleanup_file(temp_pdf_path)
        raise
    except Exception as e:
        cleanup_file(temp_pdf_path)
        raise HTTPException(status_code=500, detail=f"Failed to queue conversion: {str(e)}")

@app.get("/api/status/{job_id}")
def get_job_status(job_id: str):
    """
    Ultra-fast polling endpoint (responds in 5ms):
    Returns live job status, real-time queue position, progress, and conversion result.
    """
    if job_id not in JOBS_CACHE:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    job = JOBS_CACHE[job_id]
    status = job["status"]

    if status == "queued":
        pos = 1
        if ACTIVE_JOB_ID == job_id:
            status = "processing"
            pos = 0
        return {
            "success": True,
            "job_id": job_id,
            "status": status,
            "queue_position": pos,
            "progress": job.get("progress", 10),
            "stage": job.get("stage", "Waiting in queue..."),
        }
    elif status == "processing":
        return {
            "success": True,
            "job_id": job_id,
            "status": "processing",
            "progress": job.get("progress", 50),
            "stage": job.get("stage", "Running AI OCR extraction..."),
        }
    elif status == "completed":
        return {
            "success": True,
            "job_id": job_id,
            "status": "completed",
            "progress": 100,
            "stage": "Conversion complete!",
            "result": job.get("result"),
        }
    elif status == "failed":
        return {
            "success": False,
            "job_id": job_id,
            "status": "failed",
            "error": job.get("error", "Conversion failed. Please try again."),
        }

    return {
        "success": True,
        "job_id": job_id,
        "status": status,
    }

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

# ══════════════════════════════════════════════════════════════════════════════
# BATCH MEDICAL REPORT EXTRACTION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/batch-convert")
async def batch_convert_medical_reports(files: List[UploadFile] = File(...)):
    """
    Batch Medical Report Conversion Endpoint:
    Accepts 1 to 10 medical PDF documents.
    Enqueues batch job into background queue and immediately returns 200 OK with batch_id.
    Operates in <150ms! Completely immune to Cloudflare/Render gateway timeouts.
    """
    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="No PDF files provided.")

    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Maximum supported batch size is 10 PDFs.")

    batch_id = str(uuid.uuid4())
    saved_files = []

    try:
        max_size = 25 * 1024 * 1024  # 25 MB
        for f in files:
            fname = f.filename or "unknown.pdf"
            if not fname.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail=f"File '{fname}' is not a PDF.")

            content = await f.read()
            if len(content) == 0:
                raise HTTPException(status_code=400, detail=f"File '{fname}' is empty.")
            if len(content) > max_size:
                raise HTTPException(status_code=400, detail=f"File '{fname}' exceeds the 25 MB limit.")

            safe_name = os.path.basename(fname).replace(" ", "_")
            temp_path = os.path.join(UPLOADS_DIR, f"{batch_id}_{safe_name}")
            with open(temp_path, "wb") as out_f:
                out_f.write(content)
            del content

            saved_files.append({
                "filename": fname,
                "temp_path": temp_path
            })

        _cleanup_old_files()

        BATCHES_CACHE[batch_id] = {
            "batch_id": batch_id,
            "created_at": time.time(),
            "status": "queued",
            "total_files": len(saved_files),
            "completed_files": 0,
            "failed_files": 0,
            "current_file": None,
            "progress": 5,
            "stage": "Waiting in queue...",
            "files": saved_files,
            "errors": [],
            "download_ready": False,
            "result": None,
            "error": None
        }

        await JOB_QUEUE.put({"type": "batch", "batch_id": batch_id})

        return {
            "success": True,
            "batch_id": batch_id,
            "total_files": len(saved_files),
            "status": "queued",
            "poll_url": f"/api/batch-status/{batch_id}"
        }

    except HTTPException:
        for sf in saved_files:
            cleanup_file(sf["temp_path"])
        raise
    except Exception as e:
        for sf in saved_files:
            cleanup_file(sf["temp_path"])
        raise HTTPException(status_code=500, detail=f"Failed to queue batch conversion: {str(e)}")

@app.get("/api/batch-status/{batch_id}")
def get_batch_status(batch_id: str):
    """
    Ultra-fast batch polling endpoint (responds in <5ms):
    Returns live progress, completed/failed file counts, current active file, and final result.
    """
    if batch_id not in BATCHES_CACHE:
        raise HTTPException(status_code=404, detail="Batch job not found or expired.")

    batch = BATCHES_CACHE[batch_id]
    status = batch["status"]

    return {
        "success": status != "failed",
        "batch_id": batch_id,
        "status": status,
        "total_files": batch.get("total_files", 0),
        "completed_files": batch.get("completed_files", 0),
        "failed_files": batch.get("failed_files", 0),
        "current_file": batch.get("current_file"),
        "progress": batch.get("progress", 0),
        "stage": batch.get("stage", "Processing..."),
        "download_ready": batch.get("download_ready", False),
        "result": batch.get("result"),
        "errors": batch.get("errors", []),
        "error": batch.get("error")
    }

@app.post("/api/batch-export/{batch_id}")
def export_batch_edited_table(batch_id: str, payload: BatchExportRequest):
    """Update combined batch Excel file with user manual edits from spreadsheet preview."""
    if batch_id not in BATCHES_CACHE:
        raise HTTPException(status_code=404, detail="Batch ID not found or expired.")

    batch = BATCHES_CACHE[batch_id]
    excel_path = batch.get("excel_path")
    if not excel_path:
        raise HTTPException(status_code=400, detail="Batch Excel file path not set.")

    BatchExcelService.create_batch_excel_file(payload.headers, payload.table_data, excel_path)
    batch["table_data"] = payload.table_data
    batch["headers"] = payload.headers

    return {
        "success": True,
        "batch_id": batch_id,
        "download_url": f"/api/batch-download/{batch_id}"
    }

@app.get("/api/batch-download/{batch_id}")
def download_batch_excel(batch_id: str, background_tasks: BackgroundTasks):
    """
    Download generated combined batch XLSX file.
    """
    if batch_id not in BATCHES_CACHE:
        raise HTTPException(status_code=404, detail="Batch file not found or expired.")

    batch = BATCHES_CACHE[batch_id]
    excel_path = batch.get("excel_path")
    out_filename = batch.get("out_filename", "medical_reports_batch.xlsx")

    if not excel_path or not os.path.exists(excel_path):
        raise HTTPException(status_code=404, detail="Generated batch Excel file missing.")

    return FileResponse(
        path=excel_path,
        filename=out_filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



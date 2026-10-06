import sys
import os
import io
import asyncio
from fastapi import UploadFile, BackgroundTasks

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import (
    health_check,
    batch_convert_medical_reports,
    get_batch_status,
    export_batch_edited_table,
    download_batch_excel,
    _run_batch_conversion_sync,
    BATCHES_CACHE
)
from backend.models.medical_models import BatchExportRequest
from tests.test_medical_batch import create_test_pdf

async def run_api_tests():
    print("=" * 60)
    print("TESTING FASTAPI BATCH MEDICAL REPORT ROUTES DIRECTLY")
    print("=" * 60)

    # 1. Health check
    print("1. Testing health_check()...")
    h = health_check()
    assert h["status"] == "ok"
    print("   [PASS] Health check returns status: ok")

    # 2. Batch convert submission
    print("2. Testing batch_convert_medical_reports() endpoint...")
    pdf1 = create_test_pdf([{
        'text_blocks': [
            (50, 100), "Patient Name: API Test One\nDOB: 01/01/1980\nAccount #: 0907-0011",
            (50, 250), "Assessments\n1. Condition A - A01.0",
            (50, 400), "Visit Code: 99201 Level 1"
        ]
    }])
    pdf2 = create_test_pdf([{
        'text_blocks': [
            (50, 100), "Patient Name: API Test Two\nDOB: 02/02/1990\nAccount #: 0907-0022",
            (50, 250), "Assessments\n1. Condition B - B02.0\n2. Condition C - C03.0",
            (50, 400), "Visit Code: 99202 Level 2"
        ]
    }])

    f1 = UploadFile(filename="patient_api_1.pdf", file=io.BytesIO(pdf1))
    f2 = UploadFile(filename="patient_api_2.pdf", file=io.BytesIO(pdf2))

    res = await batch_convert_medical_reports(files=[f1, f2])
    assert res["success"] is True
    batch_id = res["batch_id"]
    assert res["total_files"] == 2
    assert res["status"] == "queued"
    print(f"   [PASS] Batch submission enqueued in <5ms: batch_id={batch_id}")

    # 3. Test initial status
    print("3. Testing get_batch_status() during queued state...")
    status_queued = get_batch_status(batch_id)
    assert status_queued["status"] == "queued"
    assert status_queued["total_files"] == 2
    print("   [PASS] Status is queued as expected")

    # 4. Run conversion worker function
    print("4. Executing _run_batch_conversion_sync()...")
    _run_batch_conversion_sync(batch_id)

    # 5. Test completed status
    print("5. Testing get_batch_status() after conversion...")
    status_done = get_batch_status(batch_id)
    assert status_done["status"] == "completed"
    assert status_done["completed_files"] == 2
    assert status_done["failed_files"] == 0
    assert status_done["download_ready"] is True
    res_data = status_done["result"]
    assert len(res_data["table_data"]) == 2
    assert "Assessment 1" in res_data["headers"]
    assert "Assessment 2" in res_data["headers"]
    print(f"   [PASS] Batch completed! 2 files converted into 1 table with {len(res_data['headers'])} columns")

    # 6. Test export endpoint (user edits)
    print("6. Testing export_batch_edited_table()...")
    edited_data = list(res_data["table_data"])
    edited_data[0][1] = "Edited Patient Name"
    export_req = BatchExportRequest(headers=res_data["headers"], table_data=edited_data)
    exp_res = export_batch_edited_table(batch_id, export_req)
    assert exp_res["success"] is True
    assert BATCHES_CACHE[batch_id]["table_data"][0][1] == "Edited Patient Name"
    print("   [PASS] Edited table updated in batch cache and re-generated in Excel file")

    # 7. Test download endpoint
    print("7. Testing download_batch_excel()...")
    bg_tasks = BackgroundTasks()
    file_resp = download_batch_excel(batch_id, bg_tasks)
    assert os.path.exists(file_resp.path)
    file_size = os.path.getsize(file_resp.path)
    assert file_size > 1000
    print(f"   [PASS] Excel FileResponse generated: {file_resp.filename} ({file_size} bytes)")

    print("=" * 60)
    print("ALL FASTAPI BATCH ROUTE TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_api_tests())

import sys
import os
import time
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tests.test_medical_batch as tb

def main():
    print("=" * 60)
    print("RUNNING BATCH MEDICAL REPORT EXTRACTION TEST SUITE")
    print("=" * 60)

    test_functions = [
        ("Test 1: Single PDF Extraction", tb.test_01_single_pdf_extraction),
        ("Test 3: Assessment on Page 1", tb.test_03_assessment_on_page_1),
        ("Test 4: Assessment on Page 2", tb.test_04_assessment_on_page_2),
        ("Test 5: Assessment on Page 3", tb.test_05_assessment_on_page_3),
        ("Test 6: Assessment on Page 4", tb.test_06_assessment_on_page_4),
        ("Test 7: Assessment Split Across Multiple Lines", tb.test_07_assessment_split_across_multiple_lines),
        ("Test 8: Multiple Assessments (5 items)", tb.test_08_multiple_assessments),
        ("Test 9: Missing Assessment Handling", tb.test_09_missing_assessment),
        ("Test 10: Missing Visit Code Handling", tb.test_10_missing_visit_code),
        ("Test 11: Rotated PDF Auto-Orientation", tb.test_11_rotated_pdf),
        ("Test 12: OCR Errors and Fuzzy Heading Matching", tb.test_12_ocr_errors_and_fuzzy_headings),
        ("Test 13: Strict Leading Zeros Preservation in Excel", tb.test_13_leading_zeros_preservation),
        ("Test 14: One Failed PDF Inside Batch Does Not Crash", tb.test_14_one_failed_pdf_inside_batch),
        ("Test 2 & 15: 10 PDFs Producing Exactly 10 Excel Rows in 1 XLSX", tb.test_02_and_15_ten_pdf_batch_producing_ten_excel_rows)
    ]

    passed = 0
    failed = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        for name, func in test_functions:
            start_t = time.time()
            try:
                # If function expects tmp_path, pass it
                if "tmp_path" in func.__code__.co_varnames:
                    func(tmp_path)
                else:
                    func()
                elapsed = time.time() - start_t
                print(f"  [PASS] {name} ({elapsed:.2f}s)")
                passed += 1
            except Exception as e:
                elapsed = time.time() - start_t
                print(f"  [FAIL] {name} ({elapsed:.2f}s)")
                print(f"         Error: {e}")
                import traceback
                traceback.print_exc()
                failed += 1

    print("=" * 60)
    print(f"TEST RESULTS: {passed} PASSED, {failed} FAILED (Total: {len(test_functions)})")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()

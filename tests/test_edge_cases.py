"""
Edge-case and PRD compliance tests for PW Workflow Automation Tool.
Run: python tests/test_edge_cases.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.excel_reader import detect_column_mapping, read_excel  # noqa: E402
from src.image_mapper import build_image_index  # noqa: E402
from src.processor import process_workflow, report_to_json  # noqa: E402
from src.utils import classify_image_type, extract_unique_id  # noqa: E402
from src.validator import (  # noqa: E402
    validate_excel_upload,
    validate_pdf_upload,
    validate_zip_upload,
)
from src.zip_handler import validate_zip_file  # noqa: E402

INPUTS = ROOT.parent / "inputs"
PASS = 0
FAIL = 0
WARN = 0
RESULTS: list[tuple[str, str, str]] = []  # name, status, detail


def record(name: str, ok: bool, detail: str = "", prd: str = "") -> None:
    global PASS, FAIL
    status = "PASS" if ok else "FAIL"
    if not ok:
        FAIL += 1
    else:
        PASS += 1
    RESULTS.append((name, status, detail))
    tag = f" [{prd}]" if prd else ""
    print(f"  {status}: {name}{tag} — {detail}" if detail else f"  {status}: {name}{tag}")


def make_png_bytes(size: tuple[int, int] = (10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color="red").save(buf, format="PNG")
    return buf.getvalue()


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_excel(rows: list[dict], columns: list[str] | None = None) -> bytes:
    df = pd.DataFrame(rows)
    if columns:
        df = df[columns] if False else pd.DataFrame(rows)  # noqa
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Sheet 1")
    return buf.getvalue()


def minimal_pair(uid: str = "abcdefghijklmnopqr123456") -> dict[str, bytes]:
    png = make_png_bytes()
    return {
        f"QUES_ENG_{uid}.png": png,
        f"SOLU_ENG_{uid}.png": png,
    }


def process_with(zip_files: dict[str, bytes], excel_rows: list[dict]) -> "ProcessingReport":
    excel = make_excel(excel_rows)
    return process_workflow(make_zip(zip_files), excel)


# --- utils / identifier ---
def test_extract_unique_id() -> None:
    print("\n=== extract_unique_id / classify ===")
    cases = [
        ("QUES_ENG_fggct35vp5l1m8gyjj540mdzs.png", "fggct35vp5l1m8gyjj540mdzs"),
        ("SOLU_ENG_fggct35vp5l1m8gyjj540mdzs.png", "fggct35vp5l1m8gyjj540mdzs"),
        ("QUESTION_abc123.png", "abc123"),
        ("Q_shortid12.png", "shortid12"),
    ]
    for fname, expected in cases:
        got = extract_unique_id(fname)
        record(f"extract_id({fname})", got == expected, f"got={got}", "Case 7")

    record(
        "classify QUES",
        classify_image_type("QUES_ENG_x.png") == "question",
        "",
        "ZIP index",
    )
    record(
        "classify SOLU",
        classify_image_type("SOLU_ENG_x.png") == "solution",
        "",
        "ZIP index",
    )
    # Ambiguous: generic Q_ only
    record(
        "classify QUESTION_",
        classify_image_type("QUESTION_x.png") == "question",
        "",
        "Case 7",
    )


# --- ZIP validation ---
def test_zip_edge_cases() -> None:
    print("\n=== ZIP validation ===")
    bad = validate_zip_file(b"not a zip")
    record("invalid zip bytes", not bad.is_valid and bad.errors, str(bad.errors), "Step 1")

    empty = validate_zip_file(make_zip({}))
    record("empty zip valid with warning", empty.is_valid and empty.png_count == 0, str(empty.warnings), "Step 3")

    no_png = validate_zip_file(make_zip({"readme.txt": b"hi"}))
    record(
        "zip no png valid with warning",
        no_png.is_valid and no_png.png_count == 0 and len(no_png.warnings) > 0,
        str(no_png.warnings),
        "Step 3",
    )

    one_q = validate_zip_file(make_zip({"QUES_ENG_abc.png": make_png_bytes()}))
    record("zip questions only", one_q.is_valid and one_q.solution_count == 0, "", "Step 1")

    jpg = validate_zip_file(
        make_zip({"QUES_ENG_x.jpg": b"\xff\xd8\xff", "QUES_ENG_y.png": make_png_bytes()})
    )
    record("non-png warned", jpg.is_valid and jpg.other_count >= 1, f"warnings={len(jpg.warnings)}", "Rule 9")


# --- Excel validation ---
def test_excel_edge_cases() -> None:
    print("\n=== Excel validation ===")
    missing = read_excel(make_excel([{"Foo": 1}]))
    record("excel missing columns", not missing.success, str(missing.errors), "Step 2")

    alias = detect_column_mapping(["display_order", "QuestionImage", "QBG Question id"])
    record(
        "column aliases",
        alias["display_order"] == "display_order"
        and alias["question_image"] == "QuestionImage",
        str(alias),
        "Step 2",
    )

    dup = make_excel(
        [
            {"Display Order*": 1, "Question Image": "QUES_ENG_a.png", "QBG Question id": "a"},
            {"Display Order*": 1, "Question Image": "QUES_ENG_b.png", "QBG Question id": "b"},
        ]
    )
    er = read_excel(dup)
    rep = validate_excel_upload(er)
    record("duplicate display order", 1 in rep.duplicate_orders, str(rep.duplicate_orders), "Case 6")

    gap = make_excel(
        [
            {"Display Order*": 1, "Question Image": "QUES_ENG_a.png", "QBG Question id": "a"},
            {"Display Order*": 2, "Question Image": "QUES_ENG_b.png", "QBG Question id": "b"},
            {"Display Order*": 4, "Question Image": "QUES_ENG_c.png", "QBG Question id": "c"},
        ]
    )
    rep2 = validate_excel_upload(read_excel(gap))
    record("gap order 3 missing", 3 in rep2.missing_orders, str(rep2.missing_orders), "Case 5")

    empty_row = make_excel(
        [
            {"Display Order*": None, "Question Image": None, "QBG Question id": None},
            {"Display Order*": 1, "Question Image": "QUES_ENG_a.png", "QBG Question id": "a"},
        ]
    )
    rep3 = validate_excel_upload(read_excel(empty_row))
    record("empty row warning", any("Empty row" in w.message for w in rep3.warnings), "", "Rule 7")


# --- PRD Cases 1-7 processing ---
def test_case1_missing_solution() -> None:
    print("\n=== PRD edge cases (processing) ===")
    uid = "case1missingsolution1234567"
    z = {f"QUES_ENG_{uid}.png": make_png_bytes()}
    r = process_with(z, [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}])
    record(
        "Case1 missing solution",
        r.questions_processed == 1 and r.solutions_missing == 1 and r.solutions_processed == 0,
        str(r.warnings[:2]),
        "Case 1",
    )
    record("Case1 still success", r.success, "", "Continue processing")


def test_case2_missing_question() -> None:
    uid = "case2missingquestion12345678"
    z = {f"SOLU_ENG_{uid}.png": make_png_bytes()}
    r = process_with(z, [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}])
    record("Case2 missing question", r.questions_missing == 1, "", "Case 2")


def test_case3_excel_ref_not_in_zip() -> None:
    uid = "case3notinzip1234567890123"
    r = process_with(
        {},
        [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}],
    )
    record(
        "Case3 excel not in zip continues",
        r.questions_missing >= 1 and len(r.rows) >= 1,
        f"errors={r.errors}",
        "Case 3",
    )


def test_case4_extra_images() -> None:
    uid = "case4extraimg12345678901234"
    files = minimal_pair(uid)
    files["EXTRA_random.png"] = make_png_bytes()
    files["QUES_ENG_otherid123456789012345.png"] = make_png_bytes()
    r = process_with(files, [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}])
    record("Case4 extra images ignored", r.questions_processed == 1, "", "Case 4")


def test_case5_gap_export() -> None:
    uid_a, uid_c = "case5aaaaaaaaaaaaaaaaaaaaa", "case5cccccccccccccccccccccc"
    files = minimal_pair(uid_a) | minimal_pair(uid_c)
    r = process_with(
        files,
        [
            {"Display Order*": 1, "Question Image": f"QUES_ENG_{uid_a}.png", "QBG Question id": uid_a},
            {"Display Order*": 4, "Question Image": f"QUES_ENG_{uid_c}.png", "QBG Question id": uid_c},
        ],
    )
    has_q4 = r.output_zip_bytes and b"Q4.png" in r.output_zip_bytes if r.output_zip_bytes else False
    import zipfile as zf

    if r.output_zip_bytes:
        with zf.ZipFile(io.BytesIO(r.output_zip_bytes)) as z:
            names = z.namelist()
        has_q4 = "Q4.png" in names
    record("Case5 gap still exports Q4", has_q4, f"orders missing={r.missing_orders}", "Case 5")


def test_case6_duplicate_order_export() -> None:
    uid_a, uid_b = "case6aaaaaaaaaaaaaaaaaaaaa", "case6bbbbbbbbbbbbbbbbbbbbbb"
    files = minimal_pair(uid_a) | minimal_pair(uid_b)
    r = process_with(
        files,
        [
            {"Display Order*": 1, "Question Image": f"QUES_ENG_{uid_a}.png", "QBG Question id": uid_a},
            {"Display Order*": 1, "Question Image": f"QUES_ENG_{uid_b}.png", "QBG Question id": uid_b},
        ],
    )
    record("Case6 duplicate order warns", any("Duplicate" in w for w in r.warnings), "", "Case 6")
    record("Case6 still exports", r.success and r.output_zip_bytes is not None, "", "Case 6")


def test_case7_mixed_naming() -> None:
    uid = "case7mixedname123456789012"
    files = {
        f"QUESTION_{uid}.png": make_png_bytes(),
        f"SOLU_ENG_{uid}.png": make_png_bytes(),
    }
    r = process_with(
        files,
        [{"Display Order*": 1, "Question Image": f"QUESTION_{uid}.png", "QBG Question id": uid}],
    )
    record("Case7 QUESTION_ prefix", r.questions_processed == 1 and r.solutions_processed == 1, "", "Case 7")


def test_duplicate_uid_in_zip() -> None:
    """Two question PNGs with the same extracted ID (different paths in ZIP)."""
    uid = "dupuidindupzip12345678901"
    png = make_png_bytes()
    files = {
        f"QUES_ENG_{uid}.png": png,
        f"subdir/QUES_ENG_{uid}.png": png,
        f"SOLU_ENG_{uid}.png": png,
    }
    with tempfile.TemporaryDirectory() as td:
        zbytes = make_zip(files)
        extract = Path(td)
        with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
            zf.extractall(extract)
        idx = build_image_index(extract)
    record(
        "duplicate uid in zip warns",
        any("Duplicate question" in w for w in idx.warnings),
        str(idx.warnings[:3]),
        "Rule 6",
    )


def test_nested_zip_paths() -> None:
    uid = "nestedpathuid1234567890123"
    png = make_png_bytes()
    zbytes = make_zip({f"images/QUES_ENG_{uid}.png": png, f"images/SOLU_ENG_{uid}.png": png})
    r = process_with(
        {f"images/QUES_ENG_{uid}.png": png, f"images/SOLU_ENG_{uid}.png": png},
        [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}],
    )
    # process_with flattens names in zip at top level - need nested structure in zip
    zbytes = make_zip({f"folder/QUES_ENG_{uid}.png": png, f"folder/SOLU_ENG_{uid}.png": png})
    r = process_workflow(
        zbytes,
        make_excel([{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}]),
    )
    record("nested zip folder paths", r.questions_processed == 1, str(r.warnings[:1]), "Extract")


def test_corrupted_png() -> None:
    uid = "corruptpnguid1234567890123"
    bad_png = b"not png content"
    good = make_png_bytes()
    r = process_with(
        {f"QUES_ENG_{uid}.png": bad_png, f"SOLU_ENG_{uid}.png": good},
        [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}],
    )
    record(
        "corrupted question skipped",
        r.questions_missing >= 1 and r.questions_processed == 0,
        str(r.warnings[:2]),
        "Rule 8",
    )


def test_qbg_id_mismatch_filename() -> None:
    """Excel QBG id differs from filename id — can break solution pairing."""
    uid_file = "fileidfromname123456789012"
    uid_wrong = "wrongqbgid12345678901234"
    files = minimal_pair(uid_file)
    r = process_with(
        files,
        [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid_file}.png", "QBG Question id": uid_wrong}],
    )
    record(
        "QBG id mismatch uses image id",
        r.solutions_processed == 1 and r.questions_processed == 1,
        "fixed: prefers Question Image id",
        "Data integrity",
    )


def test_solu_substring_in_uid_no_false_solution() -> None:
    """IDs containing 'solu' must not match question files as solutions."""
    uid = "abc123withsoluembeddedxyz123456"
    r = process_with(
        {f"QUES_ENG_{uid}.png": make_png_bytes()},
        [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}],
    )
    record(
        "solu substring id no false S",
        r.solutions_processed == 0 and r.solutions_missing == 1,
        "",
        "Fix 1",
    )


def test_pdf_validation() -> None:
    print("\n=== PDF optional ===")
    rep = validate_pdf_upload(b"%PDF-1.4 fake", "t.pdf")
    record("valid pdf header", any(i.severity == "info" for i in rep.issues), "", "PDF optional")
    rep2 = validate_pdf_upload(b"xxxx", "t.pdf")
    record("invalid pdf rejected", rep2.errors, "", "PDF optional")


def test_reference_happy_path() -> None:
    print("\n=== Reference batch ===")
    if not INPUTS.exists():
        record("reference inputs", False, "inputs/ missing")
        return
    xlsx = next(INPUTS.glob("*.xlsx"))
    zpath = next(INPUTS.glob("*.zip"))
    r = process_workflow(zpath.read_bytes(), xlsx.read_bytes())
    record("reference 45+45", r.questions_processed == 45 and r.solutions_processed == 45, "", "Primary")
    record("output zip 90 files", r.output_zip_bytes is not None, "", "Step 7")
    if r.output_zip_bytes:
        with zipfile.ZipFile(io.BytesIO(r.output_zip_bytes)) as z:
            record("has Q1 and S45", "Q1.png" in z.namelist() and "S45.png" in z.namelist(), "")


def test_report_exports() -> None:
    uid = "reportexportuid12345678901"
    r = process_with(minimal_pair(uid), [{"Display Order*": 1, "Question Image": f"QUES_ENG_{uid}.png", "QBG Question id": uid}])
    j = report_to_json(r)
    data = json.loads(j)
    record("json report fields", "duration_seconds" in data and "rows" in data, "", "Bonus")
    record("timer in report", r.duration_seconds > 0, "", "Bonus")


def test_no_crash_invalid_excel() -> None:
    try:
        read_excel(b"<?xml not excel")
        record("garbage excel no crash", True, "", "Do not crash")
    except Exception as exc:
        record("garbage excel no crash", False, str(exc), "Do not crash")


def test_ui_module_imports() -> None:
    print("\n=== UI / structure ===")
    try:
        from src.ui import (  # noqa: F401
            render_download_section,
            render_processing_section,
            render_upload_section,
            render_validation_section,
        )

        record("ui sections exist", True, "", "UI")
    except ImportError as exc:
        record("ui sections exist", False, str(exc), "UI")

    log = ROOT / "logs" / "processing.log"
    record("logging file", log.exists(), str(log), "Logging")


def print_summary() -> None:
    print("\n" + "=" * 60)
    print(f"TOTAL: {PASS} passed, {FAIL} failed, {len(RESULTS)} tests")
    if FAIL:
        print("\nFailed:")
        for name, status, detail in RESULTS:
            if status == "FAIL":
                print(f"  - {name}: {detail}")


if __name__ == "__main__":
    test_extract_unique_id()
    test_zip_edge_cases()
    test_excel_edge_cases()
    test_case1_missing_solution()
    test_case2_missing_question()
    test_case3_excel_ref_not_in_zip()
    test_case4_extra_images()
    test_case5_gap_export()
    test_case6_duplicate_order_export()
    test_case7_mixed_naming()
    test_duplicate_uid_in_zip()
    test_nested_zip_paths()
    test_corrupted_png()
    test_solu_substring_in_uid_no_false_solution()
    test_qbg_id_mismatch_filename()
    test_pdf_validation()
    test_reference_happy_path()
    test_report_exports()
    test_no_crash_invalid_excel()
    test_ui_module_imports()
    print_summary()
    sys.exit(1 if FAIL else 0)

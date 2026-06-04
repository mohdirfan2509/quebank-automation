"""Core processing pipeline: extract, map, rename, package."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .excel_reader import ExcelReadResult, ExcelRow, read_excel
from .image_mapper import (
    ImageIndexResult,
    build_image_index,
    find_file_by_name,
    find_solution_by_uid,
)
from .logger import get_logger
from .utils import (
    extract_unique_id,
    get_output_dir,
    get_temp_dir,
    is_question_filename,
    normalize_filename,
)
from .zip_handler import create_output_zip, extract_zip, validate_zip_file

logger = get_logger("processor")


@dataclass
class MappingPreview:
    """Preview row for UI display."""

    question_number: int
    output_question: str
    output_solution: str
    source_question: Optional[str]
    source_solution: Optional[str]
    unique_id: Optional[str]
    status: str
    notes: str = ""


@dataclass
class ProcessedRow:
    """Result of processing a single question."""

    question_number: int
    display_order: int
    question_output: str
    solution_output: str
    unique_id: Optional[str] = None
    question_source: Optional[str] = None
    solution_source: Optional[str] = None
    question_copied: bool = False
    solution_copied: bool = False
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class ProcessingReport:
    """Final processing statistics and row-level details."""

    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    questions_processed: int = 0
    solutions_processed: int = 0
    questions_missing: int = 0
    solutions_missing: int = 0
    duplicate_orders: List[int] = field(default_factory=list)
    missing_orders: List[int] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rows: List[ProcessedRow] = field(default_factory=list)
    output_zip_bytes: Optional[bytes] = None
    success: bool = False


def build_mapping_previews(
    excel_result: ExcelReadResult,
    index_result: ImageIndexResult,
    limit: int = 5,
) -> List[MappingPreview]:
    """Build preview mappings for the first N valid rows."""
    previews: List[MappingPreview] = []
    active = [
        r
        for r in excel_result.rows
        if not r.is_empty and r.display_order is not None
    ]
    active.sort(key=lambda r: r.display_order or 0)

    for row in active[:limit]:
        qnum = row.display_order
        uid = row.unique_id or (
            extract_unique_id(row.question_image) if row.question_image else None
        )
        source_q = row.question_image
        source_s = None
        status = "ok"
        notes = ""

        if row.question_image:
            q_path = find_file_by_name(index_result, row.question_image)
            if not q_path and uid and uid in index_result.index:
                q_path = index_result.index[uid].question_file
            if not q_path:
                status = "warning"
                notes = "Question image not found in ZIP."

        if uid and uid in index_result.index:
            pair = index_result.index[uid]
            source_s = pair.solution_filename
        elif uid:
            source_s = f"SOLU_ENG_{uid}.png"

        previews.append(
            MappingPreview(
                question_number=qnum,
                output_question=f"Q{qnum}.png",
                output_solution=f"S{qnum}.png",
                source_question=source_q,
                source_solution=source_s,
                unique_id=uid,
                status=status,
                notes=notes,
            )
        )
    return previews


def _resolve_question_path(
    row: ExcelRow,
    index_result: ImageIndexResult,
) -> Optional[Path]:
    """Resolve question image path for an Excel row."""
    if row.question_image:
        path = find_file_by_name(index_result, row.question_image)
        if path and is_question_filename(path.name):
            return path

    uid = row.unique_id or (
        extract_unique_id(row.question_image) if row.question_image else None
    )
    if uid and uid in index_result.index:
        return index_result.index[uid].question_file
    return None


def _resolve_solution_path(
    row: ExcelRow,
    index_result: ImageIndexResult,
) -> Optional[Path]:
    """Resolve solution image path for an Excel row."""
    uid = row.unique_id or (
        extract_unique_id(row.question_image) if row.question_image else None
    )
    if not uid:
        return None
    return find_solution_by_uid(index_result, uid)


def process_workflow(
    zip_bytes: bytes,
    excel_bytes: bytes,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> ProcessingReport:
    """
    Execute the full rename workflow.

    Args:
        zip_bytes: Uploaded ZIP file contents.
        excel_bytes: Uploaded Excel file contents.
        progress_callback: Optional callback(progress_fraction, status_message).

    Returns:
        ProcessingReport with statistics and output ZIP bytes.
    """
    report = ProcessingReport()
    start_time = time.perf_counter()
    report.started_at = datetime.now(timezone.utc).isoformat()

    def update(progress: float, message: str) -> None:
        logger.info("%s (%.0f%%)", message, progress * 100)
        if progress_callback:
            progress_callback(progress, message)

    temp_root = get_temp_dir() / f"session_{int(time.time() * 1000)}"
    extract_dir = temp_root / "extracted"
    output_dir = get_output_dir()

    try:
        update(0.05, "Validating ZIP archive...")
        zip_validation = validate_zip_file(zip_bytes, "upload.zip")
        if not zip_validation.is_valid:
            report.errors.extend(zip_validation.errors)
            return report
        if zip_validation.png_count == 0:
            report.warnings.append(
                "ZIP contains no PNG images; all Excel rows will be reported as missing."
            )

        update(0.15, "Reading Excel spreadsheet...")
        excel_result = read_excel(excel_bytes)
        if not excel_result.success:
            report.errors.extend(excel_result.errors)
            return report

        update(0.25, "Extracting ZIP contents...")
        extraction = extract_zip(zip_bytes, extract_dir)
        if not extraction.success:
            report.errors.extend(extraction.errors)
            return report

        update(0.40, "Building image index...")
        index_result = build_image_index(extract_dir)
        report.warnings.extend(index_result.warnings)

        update(0.50, "Preparing output directory...")
        for f in output_dir.glob("*.png"):
            f.unlink()

        active_rows = [
            r
            for r in excel_result.rows
            if not r.is_empty and r.display_order is not None
        ]
        active_rows.sort(key=lambda r: r.display_order or 0)

        order_counts: Dict[int, int] = {}
        for row in active_rows:
            order = row.display_order
            if order is not None:
                order_counts[order] = order_counts.get(order, 0) + 1

        total = max(len(active_rows), 1)
        processed_rows: List[ProcessedRow] = []

        for idx, row in enumerate(active_rows):
            qnum = row.display_order
            if qnum is None:
                continue

            fraction = 0.50 + (0.40 * (idx + 1) / total)
            update(fraction, f"Processing question {qnum}...")

            proc = ProcessedRow(
                question_number=qnum,
                display_order=qnum,
                unique_id=row.unique_id,
                question_source=row.question_image,
                solution_source=None,
                question_output=f"Q{qnum}.png",
                solution_output=f"S{qnum}.png",
            )

            if order_counts.get(qnum, 0) > 1:
                msg = f"Duplicate display order {qnum}; last row wins for output files."
                proc.warnings.append(msg)
                report.warnings.append(msg)
                if qnum not in report.duplicate_orders:
                    report.duplicate_orders.append(qnum)

            q_path = _resolve_question_path(row, index_result)
            s_path = _resolve_solution_path(row, index_result)

            if q_path:
                proc.question_source = q_path.name
                try:
                    shutil.copy2(q_path, output_dir / proc.question_output)
                    proc.question_copied = True
                    report.questions_processed += 1
                except OSError as exc:
                    proc.errors.append(f"Failed to copy question: {exc}")
                    report.errors.append(str(exc))
            else:
                proc.warnings.append("Question image not found.")
                report.questions_missing += 1
                report.warnings.append(
                    f"Q{qnum}: question image not found ({row.question_image})."
                )

            if s_path:
                proc.solution_source = s_path.name
                try:
                    shutil.copy2(s_path, output_dir / proc.solution_output)
                    proc.solution_copied = True
                    report.solutions_processed += 1
                except OSError as exc:
                    proc.warnings.append(f"Failed to copy solution: {exc}")
                    report.warnings.append(str(exc))
            else:
                proc.warnings.append("Solution image not found.")
                report.solutions_missing += 1
                report.warnings.append(f"Q{qnum}: solution image not found.")

            processed_rows.append(proc)

        report.rows = processed_rows

        if active_rows:
            orders = [r.display_order for r in active_rows if r.display_order]
            if orders:
                expected = set(range(min(orders), max(orders) + 1))
                gaps = sorted(expected - set(orders))
                report.missing_orders = gaps

        update(0.92, "Creating downloadable ZIP...")
        report.output_zip_bytes = create_output_zip(output_dir)

        report.success = report.questions_processed > 0
        if not report.output_zip_bytes:
            report.warnings.append("Output ZIP could not be created (no files).")

    except Exception as exc:
        logger.exception("Processing failed")
        report.errors.append(f"Unexpected error: {exc}")
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
        report.finished_at = datetime.now(timezone.utc).isoformat()
        report.duration_seconds = round(time.perf_counter() - start_time, 2)

    logger.info(
        "Processing complete: %d questions, %d solutions in %.2fs",
        report.questions_processed,
        report.solutions_processed,
        report.duration_seconds,
    )
    return report


def report_to_dataframe(report: ProcessingReport) -> pd.DataFrame:
    """Convert processing report rows to a pandas DataFrame."""
    records = []
    for row in report.rows:
        records.append(
            {
                "question_number": row.question_number,
                "display_order": row.display_order,
                "unique_id": row.unique_id,
                "question_source": row.question_source,
                "solution_source": row.solution_source,
                "question_output": row.question_output,
                "solution_output": row.solution_output,
                "question_copied": row.question_copied,
                "solution_copied": row.solution_copied,
                "warnings": "; ".join(row.warnings),
                "errors": "; ".join(row.errors),
            }
        )
    summary = {
        "question_number": "SUMMARY",
        "display_order": "",
        "unique_id": "",
        "question_source": "",
        "solution_source": "",
        "question_output": f"questions_processed={report.questions_processed}",
        "solution_output": f"solutions_processed={report.solutions_processed}",
        "question_copied": f"questions_missing={report.questions_missing}",
        "solution_copied": f"solutions_missing={report.solutions_missing}",
        "warnings": "; ".join(report.warnings[:20]),
        "errors": "; ".join(report.errors),
    }
    df = pd.DataFrame(records)
    if not df.empty:
        df = pd.concat([df, pd.DataFrame([summary])], ignore_index=True)
    return df


def report_to_json(report: ProcessingReport) -> str:
    """Serialize processing report to JSON."""
    payload: Dict[str, Any] = {
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "duration_seconds": report.duration_seconds,
        "success": report.success,
        "questions_processed": report.questions_processed,
        "solutions_processed": report.solutions_processed,
        "questions_missing": report.questions_missing,
        "solutions_missing": report.solutions_missing,
        "duplicate_orders": report.duplicate_orders,
        "missing_orders": report.missing_orders,
        "warnings": report.warnings,
        "errors": report.errors,
        "rows": [
            {
                "question_number": r.question_number,
                "display_order": r.display_order,
                "unique_id": r.unique_id,
                "question_source": r.question_source,
                "solution_source": r.solution_source,
                "question_output": r.question_output,
                "solution_output": r.solution_output,
                "question_copied": r.question_copied,
                "solution_copied": r.solution_copied,
                "warnings": r.warnings,
                "errors": r.errors,
            }
            for r in report.rows
        ],
    }
    return json.dumps(payload, indent=2)

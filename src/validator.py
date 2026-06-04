"""Validation rules for ZIP, Excel, and processing readiness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .excel_reader import COL_DISPLAY_ORDER, COL_QUESTION_IMAGE, ExcelReadResult, ExcelRow
from .image_mapper import ImageIndexResult, find_file_by_name, find_solution_by_uid
from .utils import extract_unique_id
from .zip_handler import ZipValidationResult

from .logger import get_logger

logger = get_logger("validator")


@dataclass
class ValidationIssue:
    """A single validation warning or error."""

    severity: str  # 'error', 'warning', 'info'
    category: str
    message: str
    row_index: Optional[int] = None
    display_order: Optional[int] = None


@dataclass
class ValidationReport:
    """Aggregated validation results."""

    issues: List[ValidationIssue] = field(default_factory=list)
    is_ready: bool = True
    duplicate_orders: List[int] = field(default_factory=list)
    missing_orders: List[int] = field(default_factory=list)
    duplicate_unique_ids: List[str] = field(default_factory=list)

    def add(self, severity: str, category: str, message: str, **kwargs) -> None:
        self.issues.append(
            ValidationIssue(severity=severity, category=category, message=message, **kwargs)
        )
        if severity == "error":
            self.is_ready = False

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_zip_upload(zip_result: ZipValidationResult) -> ValidationReport:
    """Validate ZIP upload results."""
    report = ValidationReport()
    if not zip_result.is_valid:
        for err in zip_result.errors:
            report.add("error", "zip", err)
    for warn in zip_result.warnings:
        report.add("warning", "zip", warn)
    if zip_result.is_valid and zip_result.question_count == 0:
        report.add("warning", "zip", "No question images detected in ZIP.")
    if zip_result.is_valid and zip_result.solution_count == 0:
        report.add("warning", "zip", "No solution images detected in ZIP.")
    return report


def validate_excel_upload(excel_result: ExcelReadResult) -> ValidationReport:
    """Validate Excel structure and row data."""
    report = ValidationReport()

    if not excel_result.success:
        for err in excel_result.errors:
            report.add("error", "excel", err)
        return report

    mapping = excel_result.column_mapping
    if mapping.get(COL_DISPLAY_ORDER):
        report.add(
            "info",
            "excel",
            f"Mapped Display Order -> '{mapping[COL_DISPLAY_ORDER]}'",
        )
    if mapping.get(COL_QUESTION_IMAGE):
        report.add(
            "info",
            "excel",
            f"Mapped Question Image -> '{mapping[COL_QUESTION_IMAGE]}'",
        )

    orders_seen: dict[int, List[int]] = {}
    ids_seen: dict[str, List[int]] = {}
    active_rows = [r for r in excel_result.rows if not r.is_empty]

    for row in excel_result.rows:
        if row.is_empty:
            report.add(
                "warning",
                "excel",
                "Empty row skipped.",
                row_index=row.row_index,
            )
            continue

        if row.display_order is None:
            report.add(
                "warning",
                "excel",
                "Missing display order.",
                row_index=row.row_index,
            )
        else:
            orders_seen.setdefault(row.display_order, []).append(row.row_index)

        if not row.question_image:
            report.add(
                "warning",
                "excel",
                "Missing question image filename.",
                row_index=row.row_index,
                display_order=row.display_order,
            )
        elif row.qbg_question_id:
            image_id = extract_unique_id(row.question_image)
            if image_id and row.qbg_question_id.lower() != image_id:
                report.add(
                    "warning",
                    "excel",
                    f"QBG Question id '{row.qbg_question_id}' does not match "
                    f"Question Image id '{image_id}'.",
                    row_index=row.row_index,
                    display_order=row.display_order,
                )

        if row.unique_id:
            ids_seen.setdefault(row.unique_id, []).append(row.row_index)

    for order, rows in orders_seen.items():
        if len(rows) > 1:
            report.duplicate_orders.append(order)
            report.add(
                "warning",
                "excel",
                f"Duplicate display order {order} (rows: {rows}).",
                display_order=order,
            )

    for uid, rows in ids_seen.items():
        if len(rows) > 1:
            report.duplicate_unique_ids.append(uid)
            report.add(
                "warning",
                "excel",
                f"Duplicate unique ID '{uid}' (rows: {rows}).",
            )

    if active_rows:
        orders = [r.display_order for r in active_rows if r.display_order is not None]
        if orders:
            min_order, max_order = min(orders), max(orders)
            expected = set(range(min_order, max_order + 1))
            actual = set(orders)
            gaps = sorted(expected - actual)
            report.missing_orders = gaps
            for gap in gaps:
                report.add(
                    "warning",
                    "excel",
                    f"Gap in display order: question {gap} is missing.",
                    display_order=gap,
                )

    return report


def validate_against_index(
    excel_result: ExcelReadResult,
    index_result: ImageIndexResult,
) -> ValidationReport:
    """Cross-validate Excel rows against the ZIP image index."""
    report = ValidationReport()

    for row in excel_result.rows:
        if row.is_empty:
            continue

        if not row.question_image:
            continue

        path = find_file_by_name(index_result, row.question_image)
        if path is None:
            report.add(
                "warning",
                "mapping",
                f"Question image not found in ZIP: {row.question_image}",
                row_index=row.row_index,
                display_order=row.display_order,
            )
        elif row.question_image in index_result.corrupted_files:
            report.add(
                "warning",
                "mapping",
                f"Question image corrupted: {row.question_image}",
                row_index=row.row_index,
                display_order=row.display_order,
            )

        uid = row.unique_id or extract_unique_id(row.question_image)
        if uid and uid in index_result.index:
            pair = index_result.index[uid]
            if not pair.solution_file:
                report.add(
                    "warning",
                    "mapping",
                    f"Solution image missing in ZIP for ID {uid}.",
                    row_index=row.row_index,
                    display_order=row.display_order,
                )
            if not pair.question_file:
                report.add(
                    "warning",
                    "mapping",
                    f"Question image missing in ZIP for ID {uid}.",
                    row_index=row.row_index,
                    display_order=row.display_order,
                )
        elif uid:
            sol_path = find_solution_by_uid(index_result, uid)
            if sol_path is None:
                report.add(
                    "warning",
                    "mapping",
                    f"No solution image found for ID {uid}.",
                    row_index=row.row_index,
                    display_order=row.display_order,
                )

    return report


def validate_pdf_upload(pdf_bytes: Optional[bytes], filename: str = "") -> ValidationReport:
    """Basic PDF validation (optional upload)."""
    report = ValidationReport()
    if pdf_bytes is None:
        return report

    if len(pdf_bytes) < 5:
        report.add("error", "pdf", "PDF file is empty or too small.")
        return report

    if not pdf_bytes[:4] == b"%PDF":
        report.add("error", "pdf", "File does not appear to be a valid PDF.")
        return report

    report.add("info", "pdf", f"PDF accepted for reference: {filename or 'upload.pdf'}")
    logger.info("PDF validated: %s (%d bytes)", filename, len(pdf_bytes))
    return report


def merge_reports(*reports: ValidationReport) -> ValidationReport:
    """Merge multiple validation reports into one."""
    merged = ValidationReport()
    for rep in reports:
        merged.issues.extend(rep.issues)
        merged.duplicate_orders.extend(rep.duplicate_orders)
        merged.missing_orders.extend(rep.missing_orders)
        merged.duplicate_unique_ids.extend(rep.duplicate_unique_ids)
        if not rep.is_ready:
            merged.is_ready = False
    merged.duplicate_orders = sorted(set(merged.duplicate_orders))
    merged.missing_orders = sorted(set(merged.missing_orders))
    merged.duplicate_unique_ids = sorted(set(merged.duplicate_unique_ids))
    return merged

"""Excel spreadsheet reading and column mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

import pandas as pd

from .logger import get_logger
from .utils import extract_unique_id, normalize_filename

logger = get_logger("excel_reader")

# Canonical column keys used internally.
COL_DISPLAY_ORDER = "display_order"
COL_QUESTION_IMAGE = "question_image"
COL_QBG_QUESTION_ID = "qbg_question_id"

# Patterns for automatic column detection (normalized header -> canonical key).
_COLUMN_ALIASES: Dict[str, List[str]] = {
    COL_DISPLAY_ORDER: [
        "display order",
        "display order*",
        "display_order",
        "displayorder",
        "order",
        "question order",
        "question number",
        "sr no",
        "sno",
        "serial",
    ],
    COL_QUESTION_IMAGE: [
        "question image",
        "questionimage",
        "question_image",
        "q image",
        "ques image",
        "image",
    ],
    COL_QBG_QUESTION_ID: [
        "qbg question id",
        "qbg questionid",
        "qbg_question_id",
        "question id",
        "questionid",
        "unique id",
        "uniqueid",
    ],
}


def _normalize_header(header: str) -> str:
    """Normalize a column header for fuzzy matching."""
    text = str(header).strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_column_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    """
    Map spreadsheet columns to canonical internal keys.

    Args:
        columns: Raw column names from the Excel file.

    Returns:
        Dict mapping canonical keys to actual column names (or None).
    """
    normalized_map = {_normalize_header(c): c for c in columns}
    mapping: Dict[str, Optional[str]] = {
        COL_DISPLAY_ORDER: None,
        COL_QUESTION_IMAGE: None,
        COL_QBG_QUESTION_ID: None,
    }

    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_map:
                mapping[canonical] = normalized_map[alias]
                break

    # Prefer "question image" over generic "image" if multiple matches
    if mapping[COL_QUESTION_IMAGE] is None:
        for col in columns:
            norm = _normalize_header(col)
            if "question" in norm and "image" in norm:
                mapping[COL_QUESTION_IMAGE] = col
                break

    logger.info("Detected column mapping: %s", mapping)
    return mapping


@dataclass
class ExcelRow:
    """A single question row from the spreadsheet."""

    row_index: int
    display_order: Optional[int]
    question_image: Optional[str]
    qbg_question_id: Optional[str]
    unique_id: Optional[str]
    is_empty: bool = False


@dataclass
class ExcelReadResult:
    """Result of reading and parsing an Excel file."""

    success: bool
    sheet_name: Optional[str] = None
    column_mapping: Dict[str, Optional[str]] = field(default_factory=dict)
    rows: List[ExcelRow] = field(default_factory=list)
    raw_row_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _parse_display_order(value: Any) -> Optional[int]:
    """Parse display order to a positive integer."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        order = int(float(value))
        if order < 1:
            return None
        return order
    except (TypeError, ValueError):
        return None


def _parse_string(value: Any) -> Optional[str]:
    """Parse a cell value to a stripped string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text if text else None


def read_excel(
    excel_source: Union[Path, BinaryIO, bytes],
    sheet_name: Optional[Union[str, int]] = None,
) -> ExcelReadResult:
    """
    Read an Excel file and produce structured question rows.

    Args:
        excel_source: Path, file-like, or bytes.
        sheet_name: Optional sheet name or index; uses first sheet if None.

    Returns:
        ExcelReadResult with parsed rows and column mapping.
    """
    result = ExcelReadResult(success=False)
    logger.info("Reading Excel file")

    try:
        if isinstance(excel_source, bytes):
            source: Any = BytesIO(excel_source)
        else:
            source = excel_source
            if hasattr(source, "seek"):
                source.seek(0)

        xl = pd.ExcelFile(source)
        if not xl.sheet_names:
            result.errors.append("Excel workbook contains no sheets.")
            return result

        chosen_sheet = sheet_name if sheet_name is not None else xl.sheet_names[0]
        if hasattr(source, "seek"):
            source.seek(0)
        df = pd.read_excel(
            BytesIO(excel_source) if isinstance(excel_source, bytes) else source,
            sheet_name=chosen_sheet,
        )
        result.sheet_name = str(chosen_sheet)
        result.raw_row_count = len(df)

        if df.empty:
            result.errors.append("Excel sheet is empty.")
            return result

        mapping = detect_column_mapping(list(df.columns))
        result.column_mapping = mapping

        if mapping[COL_DISPLAY_ORDER] is None:
            result.errors.append(
                "Required column not found: Display Order "
                "(expected names like 'Display Order*', 'display_order')."
            )
        if mapping[COL_QUESTION_IMAGE] is None:
            result.errors.append(
                "Required column not found: Question Image "
                "(expected names like 'Question Image', 'QuestionImage')."
            )

        if result.errors:
            return result

        order_col = mapping[COL_DISPLAY_ORDER]
        image_col = mapping[COL_QUESTION_IMAGE]
        qbg_col = mapping[COL_QBG_QUESTION_ID]

        for idx, row in df.iterrows():
            display_order = _parse_display_order(row[order_col])
            question_image = _parse_string(row[image_col])
            qbg_id = _parse_string(row[qbg_col]) if qbg_col else None

            is_empty = display_order is None and question_image is None

            if question_image:
                question_image = normalize_filename(question_image)

            image_id = (
                extract_unique_id(question_image) if question_image else None
            )
            qbg_normalized = qbg_id.lower() if qbg_id else None
            unique_id = None

            if qbg_normalized and image_id and qbg_normalized != image_id:
                result.warnings.append(
                    f"Row {int(idx) + 2}: QBG Question id '{qbg_id}' does not match "
                    f"Question Image id '{image_id}'; using image id for file matching."
                )
                unique_id = image_id
            elif qbg_normalized:
                unique_id = qbg_normalized
            elif image_id:
                unique_id = image_id

            excel_row = ExcelRow(
                row_index=int(idx) + 2,  # 1-based + header row
                display_order=display_order,
                question_image=question_image,
                qbg_question_id=qbg_id,
                unique_id=unique_id,
                is_empty=is_empty,
            )
            result.rows.append(excel_row)

        result.success = True
        logger.info("Read %d rows from sheet '%s'", len(result.rows), result.sheet_name)

    except ValueError as exc:
        result.errors.append(f"Invalid Excel file: {exc}")
        logger.exception("Excel read error")
    except Exception as exc:
        result.errors.append(f"Failed to read Excel: {exc}")
        logger.exception("Excel read error")

    return result

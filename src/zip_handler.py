"""ZIP archive validation, extraction, and output packaging."""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, List, Optional, Union

from .logger import get_logger
from .utils import classify_image_type, is_png_filename, normalize_filename

logger = get_logger("zip_handler")


@dataclass
class ZipValidationResult:
    """Result of ZIP file validation."""

    is_valid: bool
    total_files: int = 0
    question_count: int = 0
    solution_count: int = 0
    png_count: int = 0
    other_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ZipExtractionResult:
    """Result of ZIP extraction."""

    success: bool
    extract_dir: Optional[Path] = None
    extracted_files: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def validate_zip_file(
    zip_source: Union[Path, BinaryIO, bytes],
    filename: str = "upload.zip",
) -> ZipValidationResult:
    """
    Validate that the source is a readable, non-corrupted ZIP archive.

    Args:
        zip_source: Path, file-like object, or raw bytes.
        filename: Display name for logging.

    Returns:
        ZipValidationResult with counts and any errors.
    """
    result = ZipValidationResult(is_valid=False)
    logger.info("Validating ZIP: %s", filename)

    try:
        if isinstance(zip_source, bytes):
            zf = zipfile.ZipFile(BytesIO(zip_source), "r")
        elif isinstance(zip_source, Path):
            if not zip_source.exists():
                result.errors.append(f"ZIP file not found: {zip_source}")
                return result
            zf = zipfile.ZipFile(zip_source, "r")
        else:
            zf = zipfile.ZipFile(zip_source, "r")

        with zf:
            if zf.testzip() is not None:
                result.errors.append("ZIP archive contains corrupted entries.")
                return result

            names = [n for n in zf.namelist() if not n.endswith("/")]
            result.total_files = len(names)

            for name in names:
                basename = normalize_filename(name)
                if is_png_filename(basename):
                    result.png_count += 1
                    img_type = classify_image_type(basename)
                    if img_type == "question":
                        result.question_count += 1
                    elif img_type == "solution":
                        result.solution_count += 1
                else:
                    result.other_count += 1
                    if basename:
                        result.warnings.append(
                            f"Non-PNG entry ignored: {basename}"
                        )

            if result.png_count == 0:
                result.warnings.append(
                    "No PNG images found in ZIP archive; processing will produce warnings only."
                )

            result.is_valid = True
            logger.info(
                "ZIP valid: %d files, %d questions, %d solutions",
                result.total_files,
                result.question_count,
                result.solution_count,
            )

    except zipfile.BadZipFile:
        result.errors.append("Invalid or corrupted ZIP file.")
        logger.exception("Bad ZIP file: %s", filename)
    except Exception as exc:
        result.errors.append(f"ZIP validation failed: {exc}")
        logger.exception("ZIP validation error: %s", filename)

    return result


def extract_zip(
    zip_source: Union[Path, BinaryIO, bytes],
    extract_dir: Path,
) -> ZipExtractionResult:
    """
    Extract all files from a ZIP archive into extract_dir.

    Args:
        zip_source: Path, file-like object, or raw bytes.
        extract_dir: Target directory for extracted files.

    Returns:
        ZipExtractionResult with paths to extracted files.
    """
    result = ZipExtractionResult(success=False)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        if isinstance(zip_source, bytes):
            zf = zipfile.ZipFile(BytesIO(zip_source), "r")
        elif isinstance(zip_source, Path):
            zf = zipfile.ZipFile(zip_source, "r")
        else:
            zf = zipfile.ZipFile(zip_source, "r")

        with zf:
            zf.extractall(extract_dir)
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                path = extract_dir / normalize_filename(name)
                if path.exists():
                    result.extracted_files.append(path)

        result.extract_dir = extract_dir
        result.success = True
        logger.info("Extracted %d files to %s", len(result.extracted_files), extract_dir)

    except zipfile.BadZipFile:
        result.errors.append("Cannot extract: invalid ZIP file.")
        logger.exception("Extraction failed: bad ZIP")
    except Exception as exc:
        result.errors.append(f"Extraction failed: {exc}")
        logger.exception("Extraction error")

    return result


def create_output_zip(output_dir: Path, zip_name: str = "renamed_output.zip") -> Optional[bytes]:
    """
    Create a ZIP archive from all files in output_dir.

    Args:
        output_dir: Directory containing renamed PNG files.
        zip_name: Internal reference name for logging.

    Returns:
        ZIP file contents as bytes, or None on failure.
    """
    if not output_dir.exists():
        logger.error("Output directory does not exist: %s", output_dir)
        return None

    files = sorted(output_dir.glob("*.png"))
    if not files:
        logger.warning("No PNG files to package in %s", output_dir)
        return None

    buffer = BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in files:
                zf.write(file_path, arcname=file_path.name)
        buffer.seek(0)
        data = buffer.getvalue()
        logger.info("Created output ZIP %s with %d files", zip_name, len(files))
        return data
    except Exception as exc:
        logger.exception("Failed to create output ZIP: %s", exc)
        return None


def cleanup_directory(directory: Path) -> None:
    """Remove a directory tree if it exists."""
    if directory.exists():
        shutil.rmtree(directory)
        logger.info("Cleaned up directory: %s", directory)

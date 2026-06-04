"""Build an index of question/solution images from extracted ZIP files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from .logger import get_logger
from .utils import (
    classify_image_type,
    extract_unique_id,
    is_png_filename,
    is_solution_filename,
    normalize_filename,
)

logger = get_logger("image_mapper")


@dataclass
class ImagePair:
    """Question and solution file paths for a unique identifier."""

    unique_id: str
    question_file: Optional[Path] = None
    solution_file: Optional[Path] = None
    question_filename: Optional[str] = None
    solution_filename: Optional[str] = None


@dataclass
class ImageIndexResult:
    """Result of building the image index from extracted files."""

    index: Dict[str, ImagePair] = field(default_factory=dict)
    question_files: Dict[str, Path] = field(default_factory=dict)
    solution_files: Dict[str, Path] = field(default_factory=dict)
    filename_lookup: Dict[str, Path] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    extra_files: List[str] = field(default_factory=list)
    corrupted_files: List[str] = field(default_factory=list)


def _verify_png(path: Path) -> bool:
    """Return True if the file is a valid, readable PNG image."""
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            img.load()
        return True
    except Exception:
        return False


def build_image_index(extract_dir: Path) -> ImageIndexResult:
    """
    Scan extracted directory and build unique_id -> {question, solution} index.

    Args:
        extract_dir: Root directory containing extracted ZIP contents.

    Returns:
        ImageIndexResult with lookup dictionaries and warnings.
    """
    result = ImageIndexResult()
    logger.info("Building image index from %s", extract_dir)

    if not extract_dir.exists():
        result.errors.append(f"Extract directory not found: {extract_dir}")
        return result

    all_files: List[Path] = []
    for path in extract_dir.rglob("*"):
        if path.is_file():
            all_files.append(path)

    referenced_ids: Dict[str, List[str]] = {}

    for path in all_files:
        basename = normalize_filename(path.name)

        if not is_png_filename(basename):
            result.extra_files.append(basename)
            continue

        if not _verify_png(path):
            result.corrupted_files.append(basename)
            result.warnings.append(f"Corrupted or unreadable PNG: {basename}")
            continue

        # Only valid PNGs are resolvable by filename during processing.
        result.filename_lookup[basename] = path
        result.filename_lookup[basename.lower()] = path

        unique_id = extract_unique_id(basename)
        if not unique_id:
            result.warnings.append(f"Could not extract ID from: {basename}")
            continue

        img_type = classify_image_type(basename)

        if img_type == "question":
            if unique_id in result.question_files:
                result.warnings.append(
                    f"Duplicate question image for ID {unique_id}: {basename}"
                )
            result.question_files[unique_id] = path
            referenced_ids.setdefault(unique_id, []).append("question")
        elif img_type == "solution":
            if unique_id in result.solution_files:
                result.warnings.append(
                    f"Duplicate solution image for ID {unique_id}: {basename}"
                )
            result.solution_files[unique_id] = path
            referenced_ids.setdefault(unique_id, []).append("solution")
        else:
            result.extra_files.append(basename)
            result.warnings.append(f"Unclassified PNG (ignored): {basename}")

    all_ids = set(result.question_files) | set(result.solution_files)
    for uid in sorted(all_ids):
        pair = ImagePair(
            unique_id=uid,
            question_file=result.question_files.get(uid),
            solution_file=result.solution_files.get(uid),
            question_filename=(
                result.question_files[uid].name if uid in result.question_files else None
            ),
            solution_filename=(
                result.solution_files[uid].name if uid in result.solution_files else None
            ),
        )
        if not pair.question_file:
            result.warnings.append(f"Solution without question in ZIP: ID {uid}")
        if not pair.solution_file:
            result.warnings.append(f"Question without solution in ZIP: ID {uid}")
        result.index[uid] = pair

    logger.info(
        "Image index: %d unique IDs, %d questions, %d solutions",
        len(result.index),
        len(result.question_files),
        len(result.solution_files),
    )
    return result


def find_file_by_name(
    index_result: ImageIndexResult,
    filename: str,
) -> Optional[Path]:
    """Locate a valid (non-corrupted) extracted file by exact filename."""
    name = normalize_filename(filename)
    if name in index_result.corrupted_files:
        return None
    if name in index_result.filename_lookup:
        return index_result.filename_lookup[name]
    if name.lower() in index_result.filename_lookup:
        return index_result.filename_lookup[name.lower()]
    return None


def solution_filename_candidates(unique_id: str) -> List[str]:
    """Return expected solution filenames for a unique identifier."""
    uid = unique_id.lower()
    return [
        f"SOLU_ENG_{uid}.png",
        f"SOLU_{uid}.png",
        f"SOLUTION_{uid}.png",
        f"SOLUTION_ENG_{uid}.png",
    ]


def find_solution_by_uid(
    index_result: ImageIndexResult,
    unique_id: str,
) -> Optional[Path]:
    """
    Resolve a solution image path using index and solution filename prefixes only.

    Never matches question filenames (avoids false positives when ID contains 'solu').
    """
    uid = unique_id.lower()
    if uid in index_result.index:
        path = index_result.index[uid].solution_file
        if path:
            return path

    for name in solution_filename_candidates(uid):
        path = find_file_by_name(index_result, name)
        if path and is_solution_filename(path.name):
            return path
    return None

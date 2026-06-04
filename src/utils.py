"""Shared utilities for identifier extraction and path handling."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Question/solution filename prefixes (case-insensitive matching on basename).
_QUESTION_PREFIXES = (
    r"QUES(?:TION)?(?:_[A-Z]{2,3})?",  # QUES_ENG, QUESTION, QUES
    r"Q",
)
_SOLUTION_PREFIXES = (
    r"SOLU(?:TION)?(?:_[A-Z]{2,3})?",  # SOLU_ENG, SOLUTION, SOLU
    r"S",
)

_PREFIX_PATTERN = re.compile(
    rf"^({'|'.join(_QUESTION_PREFIXES + _SOLUTION_PREFIXES)})_",
    re.IGNORECASE,
)

# Fallback: capture trailing alphanumeric id before extension.
_ID_FALLBACK = re.compile(r"([a-z0-9]{20,})$", re.IGNORECASE)


def normalize_filename(name: str) -> str:
    """Return basename with forward slashes normalized."""
    return Path(name.replace("\\", "/")).name.strip()


def extract_unique_id(filename: str) -> Optional[str]:
    """
    Extract the shared unique identifier from a question or solution image filename.

    Examples:
        QUES_ENG_fggct35vp5l1m8gyjj540mdzs.png -> fggct35vp5l1m8gyjj540mdzs
        SOLU_ENG_fggct35vp5l1m8gyjj540mdzs.png -> fggct35vp5l1m8gyjj540mdzs
        QUESTION_abc123.png -> abc123

    Args:
        filename: Image filename or path.

    Returns:
        Unique identifier string, or None if extraction fails.
    """
    basename = normalize_filename(filename)
    stem = Path(basename).stem
    if not stem:
        return None

    without_prefix = _PREFIX_PATTERN.sub("", stem)
    if without_prefix and without_prefix != stem:
        return without_prefix.lower()

    match = _ID_FALLBACK.search(stem)
    if match:
        return match.group(1).lower()

    parts = stem.split("_")
    if len(parts) >= 2:
        return parts[-1].lower()

    return stem.lower() if stem else None


def is_png_filename(filename: str) -> bool:
    """Return True if the filename has a .png extension."""
    return normalize_filename(filename).lower().endswith(".png")


def is_solution_filename(filename: str) -> bool:
    """Return True if the basename is classified as a solution image."""
    return classify_image_type(filename) == "solution"


def is_question_filename(filename: str) -> bool:
    """Return True if the basename is classified as a question image."""
    return classify_image_type(filename) == "question"


def classify_image_type(filename: str) -> Optional[str]:
    """
    Classify an image as 'question' or 'solution' based on filename prefix.

    Returns:
        'question', 'solution', or None if unrecognized.
    """
    basename = normalize_filename(filename).upper()
    if basename.startswith("QUES") or basename.startswith("QUESTION"):
        return "question"
    if basename.startswith("Q_"):
        return "question"
    if basename.startswith("SOLU") or basename.startswith("SOLUTION"):
        return "solution"
    if basename.startswith("S_") and "SOLU" not in basename:
        return "solution"
    return None


def get_output_dir() -> Path:
    """Return (and create) the output directory."""
    from .logger import get_project_root

    output_dir = get_project_root() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_temp_dir() -> Path:
    """Return (and create) a temporary working directory."""
    from .logger import get_project_root

    temp_dir = get_project_root() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def clear_directory(directory: Path, keep: bool = True) -> None:
    """Remove all files in a directory; optionally recreate the directory."""
    if directory.exists():
        for item in directory.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                import shutil

                shutil.rmtree(item)
    if keep:
        directory.mkdir(parents=True, exist_ok=True)

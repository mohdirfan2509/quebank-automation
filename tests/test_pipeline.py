"""Integration test against reference inputs (run from repo root)."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# pw_workflow_tool on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.processor import process_workflow  # noqa: E402


def test_reference_batch() -> None:
    inputs = ROOT.parent / "inputs"
    xlsx = next(inputs.glob("*.xlsx"))
    zpath = next(inputs.glob("*.zip"))

    zip_bytes = zpath.read_bytes()
    excel_bytes = xlsx.read_bytes()

    report = process_workflow(zip_bytes, excel_bytes)
    assert report.success, report.errors
    assert report.questions_processed == 45, report.questions_processed
    assert report.solutions_processed == 45, report.solutions_processed
    assert report.output_zip_bytes is not None

    import io

    with zipfile.ZipFile(io.BytesIO(report.output_zip_bytes)) as zf:
        names = sorted(zf.namelist())
    assert "Q1.png" in names
    assert "S45.png" in names
    assert len(names) == 90
    print("OK:", report.questions_processed, "questions,", report.duration_seconds, "s")


if __name__ == "__main__":
    test_reference_batch()

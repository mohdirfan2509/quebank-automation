"""Streamlit UI components for PW Workflow Automation Tool."""

from __future__ import annotations


import pandas as pd
import streamlit as st

from .excel_reader import read_excel
from .image_mapper import build_image_index
from .logger import setup_logging
from .processor import (
    build_mapping_previews,
    process_workflow,
    report_to_dataframe,
    report_to_json,
)
from .validator import (
    merge_reports,
    validate_against_index,
    validate_excel_upload,
    validate_pdf_upload,
    validate_zip_upload,
)
from .zip_handler import extract_zip, validate_zip_file

from .logger import get_logger

logger = get_logger("ui")

PAGE_TITLE = "PW Workflow Automation Tool"
PAGE_ICON = "📋"


def init_session_state() -> None:
    """Initialize Streamlit session state keys."""
    defaults = {
        "zip_bytes": None,
        "zip_name": None,
        "excel_bytes": None,
        "excel_name": None,
        "pdf_bytes": None,
        "pdf_name": None,
        "zip_validation": None,
        "excel_result": None,
        "validation_report": None,
        "mapping_previews": None,
        "processing_report": None,
        "processing_done": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def configure_page() -> None:
    """Configure Streamlit page layout and title."""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    setup_logging()


def render_header() -> None:
    """Render application header."""
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")
    st.markdown(
        "Automate renaming of question and solution images from a test paper batch. "
        "Upload a **ZIP** of images and an **Excel** metadata file to generate "
        "`Q1.png`, `S1.png`, `Q2.png`, `S2.png`, … without manual screenshots."
    )


def render_upload_section() -> None:
    """Section 1: File uploads."""
    st.header("1. Upload Files")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("ZIP Archive")
        zip_file = st.file_uploader(
            "Question & solution images (ZIP)",
            type=["zip"],
            key="zip_uploader",
            help="ZIP containing QUES_ENG_*.png and SOLU_ENG_*.png files.",
        )
        if zip_file is not None:
            st.session_state.zip_bytes = zip_file.getvalue()
            st.session_state.zip_name = zip_file.name
            logger.info("ZIP uploaded: %s", zip_file.name)
            _validate_zip_upload()

    with col2:
        st.subheader("Excel Metadata")
        excel_file = st.file_uploader(
            "Question metadata (Excel)",
            type=["xlsx"],
            key="excel_uploader",
            help="Excel .xlsx with Display Order and Question Image columns.",
        )
        if excel_file is not None:
            st.session_state.excel_bytes = excel_file.getvalue()
            st.session_state.excel_name = excel_file.name
            logger.info("Excel uploaded: %s", excel_file.name)
            _validate_excel_upload()

    with col3:
        st.subheader("PDF (Optional)")
        pdf_file = st.file_uploader(
            "Question paper PDF (validation only)",
            type=["pdf"],
            key="pdf_uploader",
            help="Optional. Used for basic format validation only.",
        )
        if pdf_file is not None:
            st.session_state.pdf_bytes = pdf_file.getvalue()
            st.session_state.pdf_name = pdf_file.name
            logger.info("PDF uploaded: %s", pdf_file.name)

    if st.session_state.zip_bytes and st.session_state.excel_bytes:
        if st.button("Run cross-validation & preview", type="primary", key="validate_btn"):
            _run_cross_validation()


def _validate_zip_upload() -> None:
    """Validate uploaded ZIP and store results."""
    result = validate_zip_file(st.session_state.zip_bytes, st.session_state.zip_name or "upload.zip")
    st.session_state.zip_validation = result


def _validate_excel_upload() -> None:
    """Validate uploaded Excel and store results."""
    result = read_excel(st.session_state.excel_bytes)
    st.session_state.excel_result = result


def _run_cross_validation() -> None:
    """Run full cross-validation and build mapping preview."""
    reports = []

    zip_val = validate_zip_file(
        st.session_state.zip_bytes,
        st.session_state.zip_name or "upload.zip",
    )
    st.session_state.zip_validation = zip_val
    reports.append(validate_zip_upload(zip_val))

    excel_result = read_excel(st.session_state.excel_bytes)
    st.session_state.excel_result = excel_result
    reports.append(validate_excel_upload(excel_result))

    if st.session_state.pdf_bytes:
        reports.append(
            validate_pdf_upload(
                st.session_state.pdf_bytes,
                st.session_state.pdf_name or "upload.pdf",
            )
        )

    if zip_val.is_valid and excel_result.success:
        from .utils import get_temp_dir
        import shutil
        import time

        temp_dir = get_temp_dir() / f"preview_{int(time.time() * 1000)}"
        try:
            extraction = extract_zip(st.session_state.zip_bytes, temp_dir)
            if extraction.success and extraction.extract_dir:
                index = build_image_index(extraction.extract_dir)
                reports.append(validate_against_index(excel_result, index))
                st.session_state.mapping_previews = build_mapping_previews(
                    excel_result, index, limit=5
                )
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    st.session_state.validation_report = merge_reports(*reports)


def render_validation_section() -> None:
    """Section 2: Validation results."""
    st.header("2. Validation Results")

    zip_val = st.session_state.zip_validation
    if zip_val:
        if zip_val.is_valid:
            st.success(
                f"ZIP valid — **{zip_val.total_files}** files | "
                f"**{zip_val.question_count}** question images | "
                f"**{zip_val.solution_count}** solution images | "
                f"**{zip_val.png_count}** PNG files"
            )
        else:
            st.error("ZIP validation failed.")
        for err in zip_val.errors:
            st.error(err)
        for warn in zip_val.warnings[:10]:
            st.warning(warn)

    excel_result = st.session_state.excel_result
    if excel_result:
        if excel_result.success:
            mapping = excel_result.column_mapping
            st.success(
                f"Excel valid — sheet **{excel_result.sheet_name}** | "
                f"**{len([r for r in excel_result.rows if not r.is_empty])}** data rows"
            )
            st.info(
                f"Columns: Display Order → `{mapping.get('display_order')}` | "
                f"Question Image → `{mapping.get('question_image')}` | "
                f"QBG Question id → `{mapping.get('qbg_question_id') or '—'}`"
            )
        else:
            st.error("Excel validation failed.")
        for err in excel_result.errors:
            st.error(err)
        for warn in excel_result.warnings:
            st.warning(warn)

    report = st.session_state.validation_report
    if report:
        if report.errors:
            with st.expander(f"Validation errors ({len(report.errors)})", expanded=True):
                for issue in report.errors:
                    st.error(f"[{issue.category}] {issue.message}")

        if report.warnings:
            with st.expander(f"Validation warnings ({len(report.warnings)})", expanded=False):
                for issue in report.warnings:
                    st.warning(f"[{issue.category}] {issue.message}")

        if report.missing_orders:
            st.warning(
                f"Gaps in display order: missing question(s) {report.missing_orders}"
            )
        if report.duplicate_orders:
            st.warning(f"Duplicate display orders: {report.duplicate_orders}")

    previews = st.session_state.mapping_previews
    if previews:
        st.subheader("Mapping preview (first 5)")
        preview_data = [
            {
                "Output Q": p.output_question,
                "Source Q": p.source_question or "—",
                "Output S": p.output_solution,
                "Source S": p.source_solution or "—",
                "Unique ID": p.unique_id or "—",
                "Status": p.status,
                "Notes": p.notes,
            }
            for p in previews
        ]
        st.dataframe(pd.DataFrame(preview_data), use_container_width=True, hide_index=True)


def render_processing_section() -> None:
    """Section 3: Processing status and execution."""
    st.header("3. Processing Status")

    ready = (
        st.session_state.zip_bytes is not None
        and st.session_state.excel_bytes is not None
    )

    if not ready:
        st.info("Upload both ZIP and Excel files to enable processing.")
        return

    if st.button("Process and generate output", type="primary", key="process_btn"):
        progress = st.progress(0.0, text="Starting...")
        status = st.status("Processing workflow...", expanded=True)

        def on_progress(value: float, message: str) -> None:
            progress.progress(min(value, 1.0), text=message)
            status.update(label=message)

        with status:
            st.write("Extracting, mapping, and renaming images...")
            report = process_workflow(
                st.session_state.zip_bytes,
                st.session_state.excel_bytes,
                progress_callback=on_progress,
            )

        st.session_state.processing_report = report
        st.session_state.processing_done = True
        progress.progress(1.0, text="Complete")
        status.update(label="Processing complete", state="complete")

        if report.success:
            st.success(
                f"Processed **{report.questions_processed}** questions and "
                f"**{report.solutions_processed}** solutions in "
                f"**{report.duration_seconds}s**."
            )
        else:
            st.error("Processing completed with errors.")

        for err in report.errors:
            st.error(err)
        for warn in report.warnings[:15]:
            st.warning(warn)
        if len(report.warnings) > 15:
            st.caption(f"… and {len(report.warnings) - 15} more warnings (see report export).")

    report = st.session_state.processing_report
    if report and st.session_state.processing_done:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Questions", report.questions_processed)
        col2.metric("Solutions", report.solutions_processed)
        col3.metric("Missing Q", report.questions_missing)
        col4.metric("Missing S", report.solutions_missing)

        st.caption(
            f"Duration: {report.duration_seconds}s | "
            f"Started: {report.started_at} | Finished: {report.finished_at}"
        )


def render_download_section() -> None:
    """Section 4: Download results and reports."""
    st.header("4. Download Results")

    report = st.session_state.processing_report
    if not report or not st.session_state.processing_done:
        st.info("Run processing to download renamed images and reports.")
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        if report.output_zip_bytes:
            st.download_button(
                label="Download renamed_output.zip",
                data=report.output_zip_bytes,
                file_name="renamed_output.zip",
                mime="application/zip",
                type="primary",
            )
        else:
            st.warning("Output ZIP not available.")

    with col2:
        csv_df = report_to_dataframe(report)
        st.download_button(
            label="Download report (CSV)",
            data=csv_df.to_csv(index=False).encode("utf-8"),
            file_name="processing_report.csv",
            mime="text/csv",
        )

    with col3:
        st.download_button(
            label="Download report (JSON)",
            data=report_to_json(report).encode("utf-8"),
            file_name="processing_report.json",
            mime="application/json",
        )

    with st.expander("Processing report details"):
        if report.rows:
            st.dataframe(
                report_to_dataframe(report),
                use_container_width=True,
                hide_index=True,
            )


def render_sidebar() -> None:
    """Render sidebar with help and session controls."""
    with st.sidebar:
        st.header("About")
        st.markdown(
            """
            **Workflow**
            1. Upload ZIP + Excel
            2. Review validation
            3. Preview mappings
            4. Process & download

            **Expected ZIP names**
            - `QUES_ENG_{id}.png`
            - `SOLU_ENG_{id}.png`

            **Expected Excel columns**
            - `Display Order*`
            - `Question Image`
            - `QBG Question id` (optional)
            """
        )
        if st.button("Clear session", key="clear_session"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            init_session_state()
            st.rerun()


def run_app() -> None:
    """Main UI entry point."""
    configure_page()
    init_session_state()
    render_sidebar()
    render_header()
    st.divider()
    render_upload_section()
    st.divider()
    render_validation_section()
    st.divider()
    render_processing_section()
    st.divider()
    render_download_section()

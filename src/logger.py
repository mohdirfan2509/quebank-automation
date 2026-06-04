"""Application logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def get_project_root() -> Path:
    """Return the pw_workflow_tool package root directory."""
    return Path(__file__).resolve().parent.parent


def get_logs_dir() -> Path:
    """Return (and create) the logs directory."""
    logs_dir = get_project_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def setup_logging(log_level: int = logging.INFO) -> logging.Logger:
    """
    Configure root application logger with file and console handlers.

    Args:
        log_level: Logging level for the application logger.

    Returns:
        Configured application logger instance.
    """
    global _initialized
    logger = logging.getLogger("pw_workflow")
    if _initialized:
        return logger

    logger.setLevel(log_level)
    logger.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    log_file = get_logs_dir() / "processing.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    _initialized = True
    logger.info("Logging initialized. Log file: %s", log_file)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a child logger under the application namespace."""
    setup_logging()
    if name:
        return logging.getLogger(f"pw_workflow.{name}")
    return logging.getLogger("pw_workflow")

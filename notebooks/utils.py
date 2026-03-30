"""
Shared logging utility for the document-redaction pipeline.

Usage in any notebook:
    from utils import get_logger
    logger = get_logger(__name__)   # or get_logger("01_setup")
"""

import logging
import sys
from pathlib import Path


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Keep track of loggers we've already configured so reconfiguration
# (e.g. re-running a notebook cell) doesn't add duplicate handlers.
_configured: set[str] = set()


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Return a named logger that writes to stdout with a consistent format.

    Parameters
    ----------
    name  : Logical name shown in every log line (e.g. "02_pdf_to_images").
    level : Minimum log level (default DEBUG so all messages surface in notebooks).
    """
    logger = logging.getLogger(name)

    if name in _configured:
        return logger

    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    logger.addHandler(handler)
    logger.propagate = False   # prevent duplicate output from root logger

    _configured.add(name)
    return logger

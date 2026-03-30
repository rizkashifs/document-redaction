"""
Shared utilities for the document-redaction pipeline.

Usage in any notebook:
    from utils import get_logger, extract_json, validate_mapping
    logger = get_logger("01_setup")
"""

import json
import logging
import re
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


def extract_json(raw: str) -> dict:
    """
    Robustly parse a JSON object from a model response that may contain
    markdown fences, leading/trailing prose, or extra whitespace.

    Strategy (each step tried in order):
    1. Direct parse after stripping whitespace.
    2. Strip ```json ... ``` or ``` ... ``` fences, then parse.
    3. Regex-extract the first {...} block that spans the whole depth, then parse.

    Raises json.JSONDecodeError if all strategies fail, with the raw
    response logged so callers can debug.
    """
    # Step 1 — direct parse
    cleaned = raw.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Step 2 — strip markdown fences
    fenced = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced).strip()
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        pass

    # Step 3 — extract first complete {...} block
    match = re.search(r"\{.*\}", fenced, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # All strategies exhausted — raise with context
    preview = raw[:300].replace("\n", "\\n")
    raise json.JSONDecodeError(
        f"Could not extract JSON from model response. Raw (first 300 chars): {preview}",
        raw, 0
    )


_STOP_WORDS = {"", "mr", "dr", "ms", "jr", "sr", "i", "ii", "iii"}


def validate_mapping(result: dict) -> list[dict]:
    """
    Check each mapping row for word-overlap between original_masked and replacement.

    Words are extracted by stripping asterisks and splitting on whitespace/hyphens.
    Common titles and suffixes are excluded as stop words.

    Returns the list of rows that are violations (original and replacement share a word).
    """
    violations = []
    for row in result.get("mapping", []):
        orig_words = (
            {w.lower() for w in re.split(r"[\s*\-]+", row.get("original_masked", ""))}
            - _STOP_WORDS
        )
        repl_words = (
            {w.lower() for w in re.split(r"[\s\-]+", row.get("replacement", ""))}
            - _STOP_WORDS
        )
        if orig_words & repl_words:
            violations.append(row)
    return violations

"""
Shared utilities for the document-redaction pipeline.

Usage in any notebook:
    from utils import get_logger, extract_json, validate_mapping
    logger = get_logger("01_setup")
"""

import json
import logging
import random
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


# ── Synthetic replacement fallback ─────────────────────────────

_FIRST_NAMES = [
    "Alex", "Diana", "James", "Sofia", "Marcus", "Elena", "Ryan", "Priya",
    "Luca", "Mei", "Carlos", "Nora", "Dmitri", "Zara", "Felix", "Amara",
    "Owen", "Yuki", "Hassan", "Clara", "Tobias", "Ines", "Rohan", "Vera",
]
_LAST_NAMES = [
    "Chen", "Rivera", "Patel", "Kim", "Santos", "Novak", "Okafor", "Berg",
    "Tanaka", "Dubois", "Walsh", "Reyes", "Larsen", "Bakshi", "Cruz", "Holm",
    "Quinn", "Sato", "Ghosh", "Voss", "Marin", "Falk", "Zheng", "Byrne",
]


def generate_replacement(row_type: str) -> str:
    """Generate a random fictitious replacement value for the given PII type."""
    if row_type == "Name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    elif row_type == "SSN":
        return f"{random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(1000, 9999)}"
    elif row_type == "DOB":
        return f"{random.randint(1, 12):02d}/{random.randint(1, 28):02d}/{random.randint(1950, 2000)}"
    elif row_type == "Phone":
        return f"({random.randint(200, 999)}) {random.randint(200, 999)}-{random.randint(1000, 9999)}"
    elif row_type == "Email":
        return f"{random.choice(_FIRST_NAMES).lower()}.{random.choice(_LAST_NAMES).lower()}@example.com"
    elif row_type == "MRN":
        return f"MRN-{random.randint(100000, 999999)}"
    elif row_type == "CreditCard":
        return f"XXXX-XXXX-XXXX-{random.randint(1000, 9999)}"
    elif row_type == "Diagnosis":
        return "[Redacted diagnosis]"
    else:
        return f"[REDACTED-{random.randint(1000, 9999)}]"


def fix_remaining_violations(result: dict, violations: list[dict], logger=None) -> None:
    """
    Last-resort fix: generate synthetic replacements for mapping rows that
    still violate the no-word-overlap rule after the model retry.

    Modifies result["mapping"] and result["sanitized_text"] in place.
    """
    for row in violations:
        old_val = row["replacement"]
        row_type = row.get("type", "")

        # Generate a candidate that doesn't overlap with original_masked words
        new_val = None
        for _ in range(10):
            candidate = generate_replacement(row_type)
            test = {"mapping": [{"original_masked": row["original_masked"],
                                 "replacement": candidate, "type": row_type}]}
            if not validate_mapping(test):
                new_val = candidate
                break
        if new_val is None:
            new_val = generate_replacement(row_type)  # use anyway

        if old_val and old_val in result.get("sanitized_text", ""):
            result["sanitized_text"] = result["sanitized_text"].replace(old_val, new_val)
        row["replacement"] = new_val
        if logger:
            logger.warning("Synthetic fix: %s → %s (%s)",
                           row["original_masked"], new_val, row_type)

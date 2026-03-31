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


_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Return a named logger that writes to both stdout and a log file.

    Log files are written to ``logs/{name}.log`` (one per notebook/module).
    The file handler appends so logs accumulate across runs.

    Parameters
    ----------
    name  : Logical name shown in every log line (e.g. "02_pdf_to_images").
    level : Minimum log level (default DEBUG so all messages surface in notebooks).
    """
    logger = logging.getLogger(name)

    if name in _configured:
        return logger

    logger.setLevel(level)

    # Console handler (stdout)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(console)

    # File handler (logs/{name}_{timestamp}.log, one file per run)
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = _LOG_DIR / f"{name}_{timestamp}.log"
    file_h = logging.FileHandler(str(log_file), mode="w", encoding="utf-8")
    file_h.setLevel(level)
    file_h.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(file_h)

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


def _unmask_matches(masked: str, candidate: str) -> bool:
    """
    Check if `candidate` could be the unmasked version of `masked`.

    Uses _build_mask_regex to generate a pattern from the mask, then
    checks if the candidate matches it exactly (full-string match).

    Examples:
      "J*** S***"    matches "John Smith"     → True
      "***-**-6789"  matches "123-45-6789"    → True
      "J*** S***"    matches "Diana Chen"     → False
    """
    pattern = _build_mask_regex(masked)
    if pattern is None:
        return False
    return pattern.fullmatch(candidate) is not None


def validate_mapping(result: dict) -> list[dict]:
    """
    Check each mapping row for:
    1. Word-overlap between original_masked and replacement (after stripping asterisks).
    2. Replacement that looks like the unmasked original (model echoed the real value).

    Returns the list of rows that are violations.
    """
    violations = []
    for row in result.get("mapping", []):
        orig_masked = row.get("original_masked", "")
        replacement = row.get("replacement", "")

        # Check 0: exact or case-insensitive match (most obvious violation)
        if orig_masked.lower().replace("*", "") == replacement.lower().replace("*", ""):
            violations.append(row)
            continue

        # Check 1: word overlap (existing logic)
        orig_words = (
            {w.lower() for w in re.split(r"[\s*\-]+", orig_masked)}
            - _STOP_WORDS
        )
        repl_words = (
            {w.lower() for w in re.split(r"[\s\-]+", replacement)}
            - _STOP_WORDS
        )
        if orig_words & repl_words:
            violations.append(row)
            continue

        # Check 2: replacement matches the mask pattern (e.g. "J*** S***" → "John Smith")
        if "*" in orig_masked and _unmask_matches(orig_masked, replacement):
            violations.append(row)
            continue

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


def _case_insensitive_replace(text: str, old: str, new: str) -> str:
    """Replace all occurrences of `old` in `text` case-insensitively."""
    if not old:
        return text
    pattern = re.compile(re.escape(old), re.IGNORECASE)
    return pattern.sub(new, text)


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

        if old_val:
            result["sanitized_text"] = _case_insensitive_replace(
                result.get("sanitized_text", ""), old_val, new_val
            )
        row["replacement"] = new_val
        if logger:
            logger.warning("Synthetic fix: %s → %s (%s)",
                           row["original_masked"], new_val, row_type)


def _build_mask_regex(masked: str) -> re.Pattern | None:
    """
    Build a regex from a masked string that matches the original value.

    Works character-by-character:
    - Consecutive '*'s become [\\w]{n,} (n or more word chars — lenient because
      models often write fewer asterisks than the actual character count)
    - Literal characters are escaped and matched exactly
    - Whitespace in the mask matches flexible whitespace (\\s+)

    Examples:
      "J*** S***"        matches "John Smith" and "JONATHAN SMITHSON"
      "***-**-6789"      matches "123-45-6789"
      "**/**/1972"       matches "03/14/1972"

    Returns None if the mask has no asterisks.
    """
    if "*" not in masked:
        return None

    regex = ""
    i = 0
    while i < len(masked):
        ch = masked[i]
        if ch == "*":
            # Count consecutive asterisks
            star_start = i
            while i < len(masked) and masked[i] == "*":
                i += 1
            count = i - star_start
            # Use {n,} (n or more) — models often undercount asterisks
            regex += rf"[\w]{{{count},}}"
        elif ch in " \t":
            # Whitespace — match flexible whitespace
            regex += r"\s+"
            i += 1
            while i < len(masked) and masked[i] in " \t":
                i += 1
        else:
            regex += re.escape(ch)
            i += 1

    try:
        return re.compile(regex, re.IGNORECASE)
    except re.error:
        return None


def enforce_replacements_in_text(result: dict, logger=None) -> int:
    """
    Check that each mapping replacement actually appears in sanitized_text.
    If a replacement is missing, the model likely left the original in the text.

    Strategy (tried in order for each missing replacement):
    1. Use the mask pattern regex to find the leaked original in the text.
    2. If mask has no asterisks (model wrote full original), use the mask
       value itself as a case-insensitive search term.

    Returns the number of fixes applied.
    """
    text = result.get("sanitized_text", "")
    fixes = 0

    for row in result.get("mapping", []):
        replacement = row.get("replacement", "")
        orig_masked = row.get("original_masked", "")

        if not replacement:
            continue

        # Case-insensitive check for replacement presence
        if re.search(re.escape(replacement), text, re.IGNORECASE):
            continue  # replacement is present — nothing to fix

        # Strategy 1: mask pattern regex (works when mask has asterisks)
        pattern = _build_mask_regex(orig_masked)
        if pattern is not None:
            match = pattern.search(text)
            if match:
                original_found = match.group()
                text = _case_insensitive_replace(text, original_found, replacement)
                fixes += 1
                if logger:
                    logger.warning(
                        "Text fix (mask regex): replaced '%s' with '%s' (mask: %s)",
                        original_found, replacement, orig_masked
                    )
                continue

        # Strategy 2: no asterisks — model wrote the full original in original_masked
        # Use it directly as a case-insensitive search term
        if orig_masked and "*" not in orig_masked:
            if re.search(re.escape(orig_masked), text, re.IGNORECASE):
                text = _case_insensitive_replace(text, orig_masked, replacement)
                fixes += 1
                if logger:
                    logger.warning(
                        "Text fix (literal): replaced '%s' with '%s'",
                        orig_masked, replacement
                    )
                continue

    if fixes:
        result["sanitized_text"] = text
    return fixes


# ── LLM-based PII leak audit ─────────────────────────────────

_AUDIT_PROMPT = """You are a PII/PHI leak auditor. You will receive:
1. A sanitized document page (sanitized_text)
2. A replacement mapping showing what was redacted

Your job is to find PII/PHI that LEAKED through — values that should have been replaced but weren't.

IMPORTANT: The replacement values in the mapping are INTENTIONAL fictitious values. Do NOT flag them.
The mapping tells you exactly which values are legitimate replacements — treat them as safe.

Here are the known replacement values (do NOT flag these):
<<REPLACEMENT_LIST>>

Check for these specific leak types:

A) MISSED ORIGINALS: Values in the text that match an original_masked pattern from the mapping \
but were NOT replaced. Example: mapping has "J*** S***" → "Alex Rivera", but text still \
contains "John Smith" somewhere.

B) IDENTITY REPLACEMENTS: Mapping rows where the replacement is identical or nearly identical \
to the original_masked (ignoring asterisks). Example: "J*** S***" → "John Smith" \
means the "replacement" IS the original — it was not actually replaced.

C) UNMAPPED PII: PII-shaped values in the text that are NOT in the replacement list above, \
meaning the primary model missed them entirely. Only flag these categories:
- Full names of INDIVIDUAL PEOPLE (not organizations, companies, churches, or facilities)
- SSN patterns (XXX-XX-XXXX)
- DOB patterns (MM/DD/YYYY or similar date-of-birth values)
- Email addresses
- Phone/fax numbers
- MRN/medical record numbers
- Credit card numbers

DO NOT flag: addresses, facility/hospital/company/church/organization names, insurance/policy/claim \
numbers, employer names, dates of service, or other non-PII values. \
"Pacific Ridge Casualty Company" is an organization — NOT a person's name. \
"Diocese of Brooklyn" is an organization — NOT a person's name.

Return ONLY valid JSON (no markdown fences):
{
  "leaks": [
    {"value": "<the leaked value>", "type": "<Name|SSN|DOB|Email|Phone|MRN|Diagnosis|CreditCard>",
     "reason": "<brief explanation>"}
  ],
  "clean": false
}

If no leaks found, return: {"leaks": [], "clean": true}

--- MAPPING ---
<<MAPPING>>

--- SANITIZED TEXT ---
<<SANITIZED_TEXT>>"""


def audit_sanitized_text(result, bedrock_client, audit_model_id, logger=None):
    """
    Second Bedrock call using a cheaper model to audit sanitized_text for
    leaked PII that the programmatic checks missed.

    Checks for: missed originals, identity replacements, and unmapped PII.
    Modifies result in place if leaks are found.

    Returns the number of leaks fixed.
    """
    mapping = result.get("mapping", [])
    text = result.get("sanitized_text", "")

    if not text or not mapping:
        return 0

    # Build replacement list so audit model knows which values are intentional
    replacement_list = "\n".join(
        f"  - \"{r['replacement']}\" ({r['type']})" for r in mapping
    )

    # Build mapping section
    mapping_section = "\n".join(
        f"  {r['original_masked']} → {r['replacement']} ({r['type']})"
        for r in mapping
    )

    prompt = (_AUDIT_PROMPT
              .replace("<<REPLACEMENT_LIST>>", replacement_list)
              .replace("<<MAPPING>>", mapping_section)
              .replace("<<SANITIZED_TEXT>>", text))

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    }

    try:
        resp = bedrock_client.invoke_model(
            modelId=audit_model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(resp["body"].read())["content"][0]["text"]
        audit_result = extract_json(raw)
    except Exception as exc:
        if logger:
            logger.warning("[audit] Audit call failed (non-blocking): %s", exc)
        return 0

    leaks = audit_result.get("leaks", [])
    if not leaks or audit_result.get("clean", False):
        if logger:
            logger.info("[audit] Clean — no leaks detected")
        return 0

    if logger:
        logger.warning("[audit] %d leak(s) detected — fixing", len(leaks))

    fixes = 0
    for leak in leaks:
        leaked_value = leak.get("value", "")
        leak_type = leak.get("type", "Name")
        reason = leak.get("reason", "")

        if not leaked_value:
            continue

        # Check if this leaked value is actually in the text
        if not re.search(re.escape(leaked_value), text, re.IGNORECASE):
            if logger:
                logger.debug("[audit] Reported leak '%s' not found in text — skipping", leaked_value)
            continue

        # Try to match to an existing mapping row by mask pattern
        matched_replacement = None
        for row in mapping:
            pattern = _build_mask_regex(row.get("original_masked", ""))
            if pattern and pattern.fullmatch(leaked_value):
                matched_replacement = row["replacement"]
                break

        if matched_replacement:
            # Leaked original found — replace with the correct replacement
            text = _case_insensitive_replace(text, leaked_value, matched_replacement)
            fixes += 1
            if logger:
                logger.warning("[audit] Fixed leak: '%s' → '%s' (matched mask)",
                               leaked_value, matched_replacement)
        else:
            # Unmapped PII — generate synthetic replacement
            new_val = generate_replacement(leak_type)
            text = _case_insensitive_replace(text, leaked_value, new_val)
            mapping.append({
                "original_masked": leaked_value[:1] + "*" * (len(leaked_value) - 1),
                "replacement": new_val,
                "type": leak_type,
            })
            fixes += 1
            if logger:
                logger.warning("[audit] Fixed unmapped leak: '%s' → '%s' (%s)",
                               leaked_value, new_val, leak_type)

    if fixes:
        result["sanitized_text"] = text

    return fixes

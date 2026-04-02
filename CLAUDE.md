# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

JupyterLab pipeline that redacts PII/PHI from PDFs using AWS Bedrock (Claude claude-3-7-sonnet). Each PDF page is rendered as an image, sent to the vision model, and the model returns sanitized text with realistic fictitious dummy values (not blank redactions) plus a mapping table. Output is three files per source document: a redacted content PDF, a standalone summary PDF listing every replacement made, and a governance JSON log with per-page redaction details for audit/compliance.

## AWS configuration

- **Region:** `us-east-2` (configurable via `AWS_REGION` env var)
- **Default model:** `us.anthropic.claude-3-7-sonnet-20250219-v1:0` (configurable in `config/models.json`)
- **Required IAM permission:** `bedrock:InvokeModel`
- Credentials are resolved by `models/bedrock_client.py` in this order:
  1. STS assume-role if `BEDROCK_ROLE_ARN` is set (recommended for Lambda / SageMaker)
  2. Explicit `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` from `config/env`
  3. boto3 default chain (env vars, `~/.aws/credentials`, instance role)
- See `config/env.example` for configuration template

## Model selection

Available models are defined in `config/models.json`. To switch models, either:
- Change `"default"` in `config/models.json`, or
- Set `SELECTED_MODEL = "haiku 4.5"` (or any key) in the notebook config cell

All notebooks import the Bedrock client from `models/bedrock_client.py` — they do not create their own.

## Folder layout

```
input_folder/     ← drop source PDFs here (contents gitignored)
output_folder/    ← redacted PDFs + governance JSONs written here (contents gitignored)
temp_images/      ← per-page PNGs during processing (contents gitignored)
redacted_text/    ← per-page JSON cache created at runtime (not committed)
config/
  models.json     ← model catalogue + default selection
models/
  bedrock_client.py ← centralized Bedrock client (STS assume-role, .env, default chain)
  __init__.py
notebooks/
  utils.py        ← shared logger + JSON parser (imported by all notebooks)
  01_setup.ipynb  ← install deps, test Bedrock connection, smoke test
  02_pdf_to_images.ipynb
  03_redact_via_bedrock.ipynb
  04_reconstruct_pdf.ipynb
  05_pipeline.ipynb  ← end-to-end; run this for normal use
config/env.example      ← template for AWS credentials / role ARN
PRODUCTION_PLAN.md      ← Phase 2 architecture (S3 + Step Functions + Lambda)
```

## Running the pipeline

**Normal use:** open `05_pipeline.ipynb`, drop PDFs into `input_folder/`, optionally set `SELECTED_MODEL` in the config cell, then Run All. Notebook 05 is fully self-contained — it installs its own dependencies and does not require running any other notebook first.

**Optional pre-flight check:** run `01_setup.ipynb` to verify AWS credentials and Bedrock connectivity before committing to a full pipeline run. This is a diagnostic sanity check, not a prerequisite.

**Step-by-step / debugging:** run notebooks `01` → `02` → `03` → `04` in order. Each notebook is self-contained (installs its own deps at the top). This path is useful for inspecting intermediate outputs or resuming after a failure at a specific stage.

## Key design decisions

### Model response format
The Bedrock prompt instructs the model to return strict JSON:
```json
{ "sanitized_text": "...", "mapping": [{"original_masked": "...", "replacement": "...", "type": "..."}] }
```
Because the model sometimes wraps output in markdown fences or adds preamble, all parsing goes through `extract_json()` in `utils.py` which tries three fallback strategies before raising.

### Replacement formatting
Replacement values in `sanitized_text` are wrapped in `@` delimiters (e.g. `@Alex Rivera@`) for easy visual identification and search. The `mapping` rows, summary PDF, and governance JSON store clean values without `@`. The `extract_json()` parser strips `@` from mapping replacement fields if the model includes them.

### Replacement uniqueness
Every distinct original value must receive a unique replacement — the same replacement is never assigned to two different people/values. This is enforced at two levels: the prompt explicitly instructs the model, and `check_duplicate_replacements()` + `fix_duplicate_replacements()` in `utils.py` catch and fix any violations programmatically. `generate_replacement()` accepts an `existing` set to avoid collisions.

### Cross-page consistency
Within a single PDF, the accumulated `mapping` list from all previously processed pages is injected into each subsequent page's prompt. This ensures the same original value always gets the same dummy replacement across pages.

### Page caching
Each page's JSON response is saved to `redacted_text/{stem}_page_{n}.json`. Re-running a notebook skips pages that already have a cache file — safe to resume after failures.

### Governance JSON
Each processed PDF produces `output_folder/governance_{stem}.json` — a machine-readable audit log containing: source filename, processing timestamp, model IDs (redaction + audit), `total_pages`, `total_redactions` (sum of per-page entries, counts duplicates across pages), `categories_found` (deduplicated PII types), `pages` (per-page redaction breakdown), and `consolidated_mapping` (unique entries with a `pages` array showing which pages each appeared on).

### Parallel processing
`05_pipeline.ipynb` processes multiple PDFs concurrently via `ThreadPoolExecutor` (configurable `MAX_WORKERS`, default 3). Pages within each PDF remain sequential for cross-page mapping consistency. Each thread shares the module-level Bedrock client.

### Per-file logging
Each document gets its own log file at `logs/{stem}.log` with complete processing history (rendering, page-by-page redaction, violations, audit fixes, output generation, timing). The per-file logger is created via `get_file_logger(stem)` in the pipeline cell and passed through all functions via the `log=` parameter. Console output still shows INFO-level progress for all documents.

### Production plan
`PRODUCTION_PLAN.md` describes the Phase 2 architecture: S3 + Step Functions + Lambda. Event-driven, serverless, with S3-based page caching for fault-tolerant resume on Lambda timeout. Three Lambdas (render, process-page, reconstruct), DynamoDB status tracking, SQS-based concurrency control. See `PRODUCTION_PLAN.md` for full details.

### Cleanup
`CLEAN_UP = True` in `05_pipeline.ipynb` deletes `temp_images/` PNGs and `redacted_text/` JSONs after each PDF is successfully written. Set to `False` to keep intermediates for debugging.

### PII/PHI categories
The sanitization prompt targets exactly these categories:
- Full names (any format including "Last, First", with role labels like "Claimant:", "Patient:", etc.)
- Email addresses
- Phone and fax numbers
- SSNs / national identifiers
- Dates of Birth (DOB only — not date of service, date of injury, etc.)
- Medical record numbers (MRN, patient ID, chart number)
- Medical diagnoses / conditions tied to individuals
- Credit card details (card numbers, expiration dates, CVVs)

All other data (addresses, insurance/policy/claim numbers, non-DOB dates, facility names, etc.) is explicitly left unchanged.

### Checkbox / form field handling
The prompt explicitly instructs the model to preserve checkbox states: `[X]` for checked boxes, `[ ]` for unchecked. Checkmarks (✓, ☑) are transcribed as `[X]`; empty boxes (☐, ○) as `[ ]`. The model must not leave all boxes unchecked.

### Multi-column layout handling
For scanned forms with side-by-side sections (left/right columns), the prompt instructs the model to transcribe each column completely before moving to the next, using separators like `--- LEFT COLUMN ---` / `--- RIGHT COLUMN ---`. This prevents interleaving lines from different columns in the output.

### Replacement validation
After each Bedrock response, `validate_mapping()` checks every mapping row for two kinds of violations:
1. **Word overlap** — the replacement shares a word with the masked original (e.g. "M*** Holmes" → "Margaret Holmes").
2. **Echo detection** — the replacement matches the unmasked pattern of `original_masked` (e.g. "J*** S***" → "John Smith"), caught by `_unmask_matches()`.

When violations are found, a targeted retry asks Bedrock to fix just the bad rows. If violations persist after the retry, `fix_remaining_violations()` generates synthetic random replacements as a last resort and patches both the mapping and `sanitized_text` in place.

As a final safety net, `enforce_replacements_in_text()` checks that every mapping replacement actually appears in `sanitized_text`. If a replacement is missing (meaning the model left the original in the text despite reporting a correct mapping), it uses the mask pattern to regex-find the leaked original and substitutes the replacement.

### LLM-based PII leak audit
After programmatic validation, a second Bedrock call using a cheaper model (Haiku 4.5, configurable via `audit_model` in `config/models.json`) audits the `sanitized_text` against the mapping. The audit model receives the full text, the mapping, and a list of known replacements (so it doesn't flag intentional fictitious values). It checks for:
1. **Missed originals** — values matching a mask pattern that weren't replaced
2. **Identity replacements** — mapping rows where replacement ≈ original
3. **Unmapped PII** — PII-shaped values not accounted for in any mapping row

Organization/company/church names are explicitly excluded from audit flags. If leaks are found, they are auto-fixed (matched to existing mapping or given synthetic replacements). The audit is best-effort — if the call fails, the pipeline continues.

## Shared utilities (`notebooks/utils.py`)

- `get_logger(name)` — returns a named `logging.Logger` writing to stdout; idempotent (safe to call in re-run cells)
- `extract_json(raw)` — parses JSON from model response with fence-stripping and regex fallback
- `validate_mapping(result)` — checks mapping rows for word-overlap and echo violations; returns list of bad rows
- `fix_remaining_violations(result, violations, logger)` — last-resort synthetic replacement generator; modifies result in place
- `enforce_replacements_in_text(result, logger)` — verifies each mapping replacement actually appears in `sanitized_text`; if missing, uses the mask pattern to find and replace the leaked original
- `audit_sanitized_text(result, bedrock_client, audit_model_id, logger)` — second Bedrock call (Haiku 4.5) to audit for leaked PII; auto-fixes any leaks found

Import pattern — non-redaction notebooks:
```python
from utils import get_logger, extract_json
logger = get_logger("02_pdf_to_images")
```

Import pattern — redaction notebooks (03, 05):
```python
from utils import get_logger, extract_json, validate_mapping, fix_remaining_violations, enforce_replacements_in_text, audit_sanitized_text
from models import get_bedrock_client, resolve_model_id, get_audit_model_id
```

## Git

- `input_folder/`, `output_folder/`, `temp_images/` contents are gitignored; only `.gitkeep` files are tracked
- Git user: `kashif` / `rizkashifs@gmail.com` (set locally)
- Remote: `https://github.com/rizkashifs/document-redaction`

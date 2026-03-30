# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

JupyterLab pipeline that redacts PII/PHI from PDFs using AWS Bedrock (Claude claude-3-7-sonnet). Each PDF page is rendered as an image, sent to the vision model, and the model returns sanitized text with realistic fictitious dummy values (not blank redactions) plus a mapping table. Output is two PDFs per source document: a redacted content PDF and a standalone summary PDF listing every replacement made.

## AWS configuration

- **Region:** `us-east-2`
- **Model:** `us.anthropic.claude-3-7-sonnet-20250219-v1:0`
- **Required IAM permission:** `bedrock:InvokeModel`
- Credentials are resolved via the standard boto3 chain (env vars, `~/.aws/credentials`, instance role)

## Folder layout

```
input_folder/     ← drop source PDFs here (contents gitignored)
output_folder/    ← redacted PDFs written here (contents gitignored)
temp_images/      ← per-page PNGs during processing (contents gitignored)
redacted_text/    ← per-page JSON cache created at runtime (not committed)
notebooks/
  utils.py        ← shared logger + JSON parser (imported by all notebooks)
  01_setup.ipynb  ← install deps, test Bedrock connection, smoke test
  02_pdf_to_images.ipynb
  03_redact_via_bedrock.ipynb
  04_reconstruct_pdf.ipynb
  05_pipeline.ipynb  ← end-to-end; run this for normal use
```

## Running the pipeline

**Normal use:** open `05_pipeline.ipynb`, drop PDFs into `input_folder/`, set `AWS_REGION` / `BEDROCK_MODEL` in the config cell, then Run All. Notebook 05 is fully self-contained — it installs its own dependencies, creates its own Bedrock client, and does not require running any other notebook first.

**Optional pre-flight check:** run `01_setup.ipynb` to verify AWS credentials and Bedrock connectivity before committing to a full pipeline run. This is a diagnostic sanity check, not a prerequisite.

**Step-by-step / debugging:** run notebooks `01` → `02` → `03` → `04` in order. Each notebook is self-contained (installs its own deps at the top). This path is useful for inspecting intermediate outputs or resuming after a failure at a specific stage.

## Key design decisions

### Model response format
The Bedrock prompt instructs the model to return strict JSON:
```json
{ "sanitized_text": "...", "mapping": [{"original_masked": "...", "replacement": "...", "type": "..."}] }
```
Because the model sometimes wraps output in markdown fences or adds preamble, all parsing goes through `extract_json()` in `utils.py` which tries three fallback strategies before raising.

### Cross-page consistency
Within a single PDF, the accumulated `mapping` list from all previously processed pages is injected into each subsequent page's prompt. This ensures the same original value always gets the same dummy replacement across pages.

### Page caching
Each page's JSON response is saved to `redacted_text/{stem}_page_{n}.json`. Re-running a notebook skips pages that already have a cache file — safe to resume after failures.

### Cleanup
`CLEAN_UP = True` in `05_pipeline.ipynb` deletes `temp_images/` PNGs and `redacted_text/` JSONs after each PDF is successfully written. Set to `False` to keep intermediates for debugging.

### PII/PHI categories (v2)
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

## Shared utilities (`notebooks/utils.py`)

- `get_logger(name)` — returns a named `logging.Logger` writing to stdout; idempotent (safe to call in re-run cells)
- `extract_json(raw)` — parses JSON from model response with fence-stripping and regex fallback

Import pattern used in every notebook:
```python
from utils import get_logger, extract_json
logger = get_logger("02_pdf_to_images")
```

## Git

- `input_folder/`, `output_folder/`, `temp_images/` contents are gitignored; only `.gitkeep` files are tracked
- Git user: `kashif` / `rizkashifs@gmail.com` (set locally)
- Remote: `https://github.com/rizkashifs/document-redaction`

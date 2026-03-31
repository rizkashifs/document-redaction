# Document Redaction Pipeline

Automated PII/PHI redaction for PDF documents using AWS Bedrock (via Claude).

---

## Phase 1 — Local JupyterLab Pipeline

### Overview

Process PDFs from a local `input_folder`, redact all PII/PHI using Claude 3.7 Sonnet vision via AWS Bedrock, and write clean PDFs to `output_folder`. Non-sensitive text is preserved verbatim. Each source document produces **three output files**: a sanitized content PDF, a standalone summary PDF listing every replacement made, and a governance JSON log with per-page redaction details for audit/compliance.

```
input_folder/
  ├── document_a.pdf
  └── document_b.pdf

temp_images/                        ← auto-created, auto-cleaned
  ├── document_a_page_1.png
  ├── document_a_page_2.png
  └── document_b_page_1.png

output_folder/
  ├── redacted_document_a.pdf       ← sanitized content only
  ├── summary_document_a.pdf        ← PII/PHI replacement table
  ├── governance_document_a.json    ← per-page redaction audit log
  ├── redacted_document_b.pdf
  ├── summary_document_b.pdf
  └── governance_document_b.json
```

---

### Pipeline Steps

#### Step 1 — Environment Setup
Install required libraries and configure AWS credentials for Bedrock access.

**Dependencies:**
- `pymupdf` (fitz) — PDF to image conversion
- `boto3` — AWS Bedrock API calls
- `fpdf2` or `reportlab` — Reconstruct redacted PDF from text
- `Pillow` — Image handling

#### Step 2 — PDF to Images
For each `.pdf` in `input_folder`:
- Open the PDF with PyMuPDF
- Render each page as a PNG (230 DPI in notebook 02; 230 DPI in notebook 05 — higher DPI improves accuracy on complex or small-text documents)
- Save to `temp_images/` as `{filename}_page_{n}.png`

#### Step 3 — Vision-Based Sanitization (Bedrock)
For each page image:
- Send the image to Claude 3.7 Sonnet via `bedrock-runtime` (`InvokeModel`)
- The model replaces every PII/PHI item with a **realistic, fictitious dummy value** — not a blank or `[REDACTED]` tag
- The same original value gets the **same replacement** throughout the entire document (cross-page consistency enforced by carrying the mapping forward with each page call)
- Model responds as JSON: `{ "sanitized_text": "...", "mapping": [{...}] }`
- Cached per page as `redacted_text/{stem}_page_{n}.json`

**PII/PHI categories:** full names, email addresses, phone/fax numbers, SSNs, dates of birth, medical record numbers, medical diagnoses, credit card details

**Bedrock model:** configurable via `config/models.json` (default: Claude 3.7 Sonnet). Available: Sonnet 3.7, Haiku 4.5, Sonnet 4.5, Opus 4.6

#### Step 4 — PDF Reconstruction
For each source PDF, three files are written to `output_folder/`:
- `redacted_{stem}.pdf` — sanitized content only, one page per original page
- `summary_{stem}.pdf` — standalone PII/PHI replacement table listing every original→replacement mapping
- `governance_{stem}.json` — machine-readable audit log (see schema below)

**Governance JSON schema (`governance_{stem}.json`):**

```json
{
  "source_file": "claim_001.pdf",
  "processed_at": "2026-03-31T14:22:05Z",
  "model_id": "us.anthropic.claude-3-7-sonnet-...",
  "audit_model_id": "us.anthropic.claude-haiku-4-5-...",
  "total_pages": 3,
  "total_redactions": 8,
  "categories_found": ["DOB", "Name", "Phone", "SSN"],
  "pages": [
    {
      "page_number": 1,
      "redactions": [
        {"original_masked": "J*** S***", "replacement": "Alex Rivera", "type": "Name"},
        {"original_masked": "***-**-6789", "replacement": "456-78-9012", "type": "SSN"}
      ]
    }
  ],
  "consolidated_mapping": [
    {"original_masked": "J*** S***", "replacement": "Alex Rivera", "type": "Name", "pages": [1, 3]}
  ]
}
```

| Field | Description |
|---|---|
| `source_file` | Original PDF filename |
| `processed_at` | UTC timestamp of processing |
| `model_id` / `audit_model_id` | Bedrock model IDs used for redaction and leak audit |
| `total_pages` | Number of pages in the source PDF |
| `total_redactions` | Sum of redaction entries across all pages (counts per-page occurrences, not unique values) |
| `categories_found` | Deduplicated list of PII/PHI types detected |
| `pages` | Per-page breakdown — each page lists its own redaction entries |
| `consolidated_mapping` | Deduplicated entries with a `pages` array showing which pages each unique redaction appeared on |

#### Step 5 — Cleanup
Delete all files in `temp_images/` after the PDF is successfully written.

---

### Running the Pipeline

There are three usage paths:

**Quick / normal use — run `05_pipeline.ipynb` alone.**
Notebook 05 is a fully self-contained end-to-end pipeline. It installs its own dependencies, creates its own Bedrock client, and handles PDF rendering, redaction, and reconstruction internally. You do **not** need to run notebooks 01–04 first.

**Optional pre-flight check — run `01_setup.ipynb` before notebook 05.**
Use this if you want to verify AWS credentials and Bedrock connectivity before committing to a full pipeline run. It's a diagnostic sanity check, not a prerequisite — notebook 05 does not depend on anything notebook 01 produces.

**Step-by-step / debugging — run `01` → `02` → `03` → `04` in order.**
Useful for inspecting intermediate outputs (page images, per-page JSON responses) or resuming after a failure at a specific stage.

### Notebook Structure

| Notebook | Purpose |
|---|---|
| `01_setup.ipynb` | Install deps, verify Bedrock access, smoke-test with a synthetic image |
| `02_pdf_to_images.ipynb` | Batch convert all PDFs to page images (230 DPI) |
| `03_redact_via_bedrock.ipynb` | Run vision redaction on all images, cache per-page JSON |
| `04_reconstruct_pdf.ipynb` | Collate cached JSON and write redacted + summary PDFs |
| `05_pipeline.ipynb` | **Self-contained end-to-end pipeline** — runs steps 02–04 internally |

---

### Sanitization Prompt (v4)

The prompt instructs the model to transcribe each page image and replace PII/PHI inline with fictitious values. Key features:

- **Checkbox / form field preservation** — checked boxes are transcribed as `[X]`, unchecked as `[ ]`. The model must faithfully reproduce which option is selected.
- **No-echo rule** — the replacement must share NO words with the original. An explicit "Wrong" example shows that echoing the original name is not allowed.
- **Name attention** — special emphasis on names in table cells, form fields, inverted formats ("Last, First"), and after role labels ("Claimant:", "Patient:", etc.).
- **Masked originals** — the mapping never contains the full original value, only a partially masked hint (e.g. "J\*\*\* S\*\*\*").

**PII/PHI categories redacted:** full names, email addresses, phone/fax numbers, SSNs, dates of birth, medical record numbers, medical diagnoses, credit card details.

**Left unchanged:** addresses, insurance/policy/claim numbers, non-DOB dates, driver's licenses, bank accounts, employer names, facility names.

Cross-page consistency is enforced by injecting the accumulated mapping from prior pages into each subsequent page call.

### Replacement Validation

After each Bedrock response, `validate_mapping()` in `utils.py` runs two automated checks:

1. **Word overlap** — flags rows where the replacement shares a word with the masked original (after stripping asterisks).
2. **Echo detection** — flags rows where the replacement matches the unmasked pattern of `original_masked` (e.g. "J\*\*\* S\*\*\*" → "John Smith").

Violations trigger a targeted Bedrock retry to fix just the bad rows. If that also fails, `fix_remaining_violations()` generates synthetic random replacements as a last resort.

3. **Text enforcement** — as a final safety net, `enforce_replacements_in_text()` verifies each replacement actually appears in `sanitized_text`. If a replacement is missing (model left the original in the text despite reporting a correct mapping), the mask pattern is used to regex-find the leaked original and substitute the replacement.

4. **LLM leak audit** — a second Bedrock call using a cheaper model (Haiku 4.5 by default, configurable via `audit_model` in `config/models.json`) audits the final `sanitized_text` against the mapping. It checks for missed originals, identity replacements, and unmapped PII — while explicitly ignoring the intentional fictitious replacement values and organization/company names. Any leaks found are auto-fixed.

---

### Folder Structure

```
document-redaction/
  ├── README.md
  ├── PRODUCTION_PLAN.md   ← Phase 2 architecture (S3 + Step Functions + Lambda)
  ├── config/env.example           ← AWS credentials template
  ├── config/
  │     └── models.json      ← model catalogue + default selection
  ├── models/
  │     ├── __init__.py
  │     └── bedrock_client.py ← centralized Bedrock client
  ├── input_folder/          ← place source PDFs here
  ├── output_folder/         ← redacted PDFs + governance JSONs written here
  ├── temp_images/           ← auto-managed, not committed
  └── notebooks/
        ├── utils.py
        ├── 01_setup.ipynb
        ├── 02_pdf_to_images.ipynb
        ├── 03_redact_via_bedrock.ipynb
        ├── 04_reconstruct_pdf.ipynb
        └── 05_pipeline.ipynb
```

---

### AWS Requirements

- IAM role/user with `bedrock:InvokeModel` permission
- Bedrock model access enabled for the selected model in your AWS region
- Region: `us-east-2` (configurable via `AWS_REGION` env var or `config/env` file)
- Credentials: copy `config/env.example` to `.env` and configure. Supports STS assume-role (`BEDROCK_ROLE_ARN`), explicit keys, or the default boto3 chain
- All notebooks use a centralized Bedrock client from `models/bedrock_client.py`

---

### Known Limitations (Phase 1)

| Limitation | Phase 2 Mitigation |
|---|---|
| Text-only output — original layout/fonts not preserved | Use PDF overlay / bounding-box redaction |
| ~~No confidence scoring on redactions~~ | ✅ Resolved — LLM leak audit via secondary model (Haiku 4.5) |
| ~~Single-threaded per page~~ | ✅ Resolved — parallel document processing via `ThreadPoolExecutor` (configurable `MAX_WORKERS`) |
| ~~No audit log of what was redacted~~ | ✅ Resolved — `governance_{stem}.json` logs per-page redactions |
| Scanned PDFs with poor image quality may degrade accuracy | Pre-process with image enhancement |

---

### Phase 2 — Production Architecture

Full production plan: **[PRODUCTION_PLAN.md](PRODUCTION_PLAN.md)**

S3 + Step Functions + Lambda pipeline — event-driven, serverless, with automatic resume on failure. Drop a PDF into S3 → get redacted output automatically. Handles thousands of documents with DynamoDB status tracking, Bedrock concurrency control, and S3-based page caching for fault tolerance.

**Remaining items:**

| Item | Status |
|---|---|
| S3-based pipeline (Step Functions + Lambda) | Designed — see [PRODUCTION_PLAN.md](PRODUCTION_PLAN.md) |
| Layout-preserving redaction using bounding boxes | Not started |
| ~~Parallel processing across documents~~ | ✅ Done — `ThreadPoolExecutor` with `MAX_WORKERS` |
| ~~Audit trail JSON per document~~ | ✅ Done — `governance_{stem}.json` |
| ~~Secondary model check~~ | ✅ Done — LLM leak audit via Haiku 4.5 |
| CI/CD deployment via SAM/CloudFormation | Designed — see [PRODUCTION_PLAN.md](PRODUCTION_PLAN.md) |

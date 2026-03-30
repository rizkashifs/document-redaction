# Document Redaction Pipeline

Automated PII/PHI redaction for PDF documents using AWS Bedrock (Claude 3.7 Sonnet vision).

---

## Phase 1 — Local JupyterLab Pipeline

### Overview

Process PDFs from a local `input_folder`, redact all PII/PHI using Claude 3.7 Sonnet vision via AWS Bedrock, and write clean PDFs to `output_folder`. Non-sensitive text is preserved verbatim. Each source document produces **two output files**: a sanitized content PDF and a standalone summary PDF listing every replacement made.

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
  ├── redacted_document_b.pdf
  └── summary_document_b.pdf
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
- Render each page as a PNG (230 DPI in notebook 02; 150 DPI in notebook 05 for faster processing — higher DPI improves accuracy on complex or small-text documents)
- Save to `temp_images/` as `{filename}_page_{n}.png`

#### Step 3 — Vision-Based Sanitization (Bedrock)
For each page image:
- Send the image to Claude 3.7 Sonnet via `bedrock-runtime` (`InvokeModel`)
- The model replaces every PII/PHI item with a **realistic, fictitious dummy value** — not a blank or `[REDACTED]` tag
- The same original value gets the **same replacement** throughout the entire document (cross-page consistency enforced by carrying the mapping forward with each page call)
- Model responds as JSON: `{ "sanitized_text": "...", "mapping": [{...}] }`
- Cached per page as `redacted_text/{stem}_page_{n}.json`

**PII/PHI categories:** full names, email addresses, phone numbers, SSNs, mailing addresses, dates of birth, medical record numbers, individual-linked diagnoses

**Bedrock model ID:** `us.anthropic.claude-3-7-sonnet-20250219-v1:0`

#### Step 4 — PDF Reconstruction
For each source PDF, two files are written to `output_folder/`:
- `redacted_{stem}.pdf` — sanitized content only, one page per original page
- `summary_{stem}.pdf` — standalone PII/PHI replacement table listing every original→replacement mapping

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

### Sanitization Prompt (v2)

```
You are a data-sanitization assistant. Your task is to take the provided document
and produce a new, clean version in which all PII and PHI are fully redacted and
replaced with realistic but completely fictitious dummy records.

Identify and replace:
- Full names in ANY format or order: "First Last", "Last, First", "Last, First Middle",
  titles (Mr., Dr., etc.), suffixes (Jr., Sr., III), and initials. This includes names
  preceded by role labels such as "Claimant:", "Patient:", "Provider:", "Insured:",
  "Applicant:", "Member:", "Beneficiary:", etc.
- Email addresses
- Phone and fax numbers
- Social Security Numbers (SSN) or national identifiers
- Driver's license or state ID numbers
- Physical mailing addresses
- Dates of Birth (DOB) and any other individual-linked dates: date of injury, date of service,
  admission/discharge dates, appointment dates
- Medical record numbers or identifiers
- Insurance, policy, group, claim, and member ID numbers
- Bank account numbers, routing numbers, or credit card numbers
- Any specific medical diagnoses or conditions tied to individuals

Requirements:
1. Identify every occurrence of PII/PHI within the document.
2. Replace each sensitive item with a consistent, fictitious dummy value.
   If the same value appears multiple times, use the SAME replacement throughout.
3. Dummy values must follow valid formats but must NOT correspond to real individuals.
4. Maintain the original meaning, readability, and structure — only sensitive data is substituted.
5. Pay special attention to names in inverted "Last, First" format, names inside table cells
   or form fields, and names preceded by role/label prefixes. These are all PII regardless
   of formatting or context.

Return valid JSON (no markdown fences):
{
  "sanitized_text": "<full sanitized page text>",
  "mapping": [
    {"original_masked": "J*** S***", "replacement": "Alex Carter", "type": "Name"},
    ...
  ]
}
```

Cross-page consistency is enforced by injecting the accumulated mapping from prior pages into each subsequent page call.

---

### Folder Structure

```
document-redaction/
  ├── README.md
  ├── input_folder/          ← place source PDFs here
  ├── output_folder/         ← redacted PDFs written here
  ├── temp_images/           ← auto-managed, not committed
  └── notebooks/
        ├── 01_setup.ipynb
        ├── 02_pdf_to_images.ipynb
        ├── 03_redact_via_bedrock.ipynb
        ├── 04_reconstruct_pdf.ipynb
        └── 05_pipeline.ipynb
```

---

### AWS Requirements

- IAM role/user with `bedrock:InvokeModel` permission
- Bedrock model access enabled for `us.anthropic.claude-3-7-sonnet-20250219-v1:0` in your AWS region
- Region: `us-east-2` (configured in each notebook's config cell)

---

### Known Limitations (Phase 1)

| Limitation | Phase 2 Mitigation |
|---|---|
| Text-only output — original layout/fonts not preserved | Use PDF overlay / bounding-box redaction |
| No confidence scoring on redactions | Add a review pass or secondary model check |
| Single-threaded per page | Parallelize with `concurrent.futures` |
| No audit log of what was redacted | Log redacted spans per page to JSON |
| Scanned PDFs with poor image quality may degrade accuracy | Pre-process with image enhancement |

---

### Phase 2 Preview

- S3-based pipeline (replace local folders with buckets — see `notebooks/s3-pipeline-code.py`)
- Layout-preserving redaction using bounding boxes
- Parallel processing across documents
- Audit trail JSON per document
- CI/CD deployment via Lambda or ECS

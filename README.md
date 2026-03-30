# Document Redaction Pipeline

Automated PII/PHI redaction for PDF documents using AWS Bedrock (Claude claude-opus-4-6 vision).

---

## Phase 1 — Local JupyterLab Pipeline

### Overview

Process PDFs from a local `input_folder`, redact all PII/PHI using Claude claude-opus-4-6 vision via AWS Bedrock, and write clean PDFs to `output_folder`. Non-sensitive text is preserved verbatim.

```
input_folder/
  ├── document_a.pdf
  └── document_b.pdf

temp_images/                        ← auto-created, auto-cleaned
  ├── document_a_page_1.png
  ├── document_a_page_2.png
  └── document_b_page_1.png

output_folder/
  ├── redacted_document_a.pdf
  └── redacted_document_b.pdf
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
- Render each page as a PNG at 150–200 DPI
- Save to `temp_images/` as `{filename}_page_{n}.png`

#### Step 3 — Vision-Based Sanitization (Bedrock)
For each page image:
- Send the image to Claude claude-opus-4-6 via `bedrock-runtime` (`InvokeModel`)
- The model replaces every PII/PHI item with a **realistic, fictitious dummy value** — not a blank or `[REDACTED]` tag
- The same original value gets the **same replacement** throughout the entire document (cross-page consistency enforced by carrying the mapping forward with each page call)
- Model responds as JSON: `{ "sanitized_text": "...", "mapping": [{...}] }`
- Cached per page as `redacted_text/{stem}_page_{n}.json`

**PII/PHI categories:** full names, email addresses, phone numbers, SSNs, mailing addresses, dates of birth, medical record numbers, individual-linked diagnoses

**Bedrock model ID:** `us.anthropic.claude-3-7-sonnet-20250219-v1:0`

#### Step 4 — PDF Reconstruction
For each source PDF:
- Collate the sanitized text from all pages in order (one PDF page per original page)
- Append a final **Summary Table** page listing every original→replacement mapping
- Save as `output_folder/redacted_{original_filename}.pdf`

#### Step 5 — Cleanup
Delete all files in `temp_images/` after the PDF is successfully written.

---

### Notebook Structure

| Notebook | Purpose |
|---|---|
| `01_setup.ipynb` | Install deps, verify Bedrock access, test with a single image |
| `02_pdf_to_images.ipynb` | Batch convert all PDFs to page images |
| `03_redact_via_bedrock.ipynb` | Run vision redaction on all images |
| `04_reconstruct_pdf.ipynb` | Collate text and write output PDFs |
| `05_pipeline.ipynb` | End-to-end single notebook combining all steps |

---

### Sanitization Prompt (v1)

```
You are a data-sanitization assistant. Your task is to take the provided document
and produce a new, clean version in which all PII and PHI are fully redacted and
replaced with realistic but completely fictitious dummy records.

Identify and replace:
- Full names (first, last, middle, initials)
- Email addresses
- Phone numbers
- Social Security Numbers (SSN) or national identifiers
- Physical mailing addresses
- Dates of Birth (DOB)
- Medical record numbers or identifiers
- Any specific medical diagnoses or conditions tied to individuals

Requirements:
1. Replace each sensitive item with a consistent, fictitious dummy value.
   If the same value appears multiple times, use the SAME replacement throughout.
2. Dummy values must follow valid formats but must NOT correspond to real individuals.
3. Maintain the original meaning, readability, and structure.

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
- Bedrock model access enabled for `claude-opus-4-6` in your AWS region
- Region: configure in notebook (e.g. `us-east-2`)

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

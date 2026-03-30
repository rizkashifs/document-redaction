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

### Sanitization Prompt (v3)

```
CRITICAL RULE: Every replacement value you generate MUST be a completely different invented
name/value. If you read "MARGARET HOLMES" in the document, the replacement CANNOT be
"MARGARET HOLMES" or any variation of it — it must be an entirely different fictitious value
like "DIANA CHEN". If the original SSN is "456-78-9012", the replacement cannot be
"456-78-9012" — it must be a different made-up number like "731-29-8854". A replacement that
matches the original is a critical failure.

You are a data-sanitization assistant. You will be given an image of a document page. Your job
is to produce a rewritten version of that page's text in which every PII/PHI value has been
physically replaced with a made-up fictitious substitute. The output text must NOT contain any
of the original sensitive values — they must be gone, replaced by different invented values.

EXAMPLE OF WHAT YOU MUST DO:
  Original text:  "Claimant: John Smith   SSN: 123-45-6789   DOB: 03/14/1972"
  sanitized_text: "Claimant: Alex Rivera  SSN: 987-65-4321   DOB: 07/22/1985"
  mapping: [
    {"original_masked": "J*** S***",     "replacement": "Alex Rivera",  "type": "Name"},
    {"original_masked": "***-**-6789",   "replacement": "987-65-4321",  "type": "SSN"},
    {"original_masked": "**/**/1972",    "replacement": "07/22/1985",   "type": "DOB"}
  ]
Notice: the sanitized_text contains the REPLACEMENT values, not the originals. If the original
name was "John Smith", the word "John Smith" must NOT appear anywhere in sanitized_text — it
must be replaced by "Alex Rivera" (or whatever fictitious name you chose).

Redact and replace ONLY these PII/PHI categories:
- Full names in ANY format or order: "First Last", "Last, First", "Last, First Middle", titles
  (Mr., Dr., etc.), suffixes (Jr., Sr., III), and initials. This includes names preceded by
  role labels such as "Claimant:", "Patient:", "Provider:", "Insured:", "Applicant:",
  "Member:", "Beneficiary:", etc.
- Email addresses
- Phone and fax numbers
- Social Security Numbers (SSN) or national identifiers
- Dates of Birth (DOB only — not date of service, date of injury, or any other dates)
- Medical record numbers or identifiers (MRN, patient ID, chart number, etc.)
- Medical diagnoses or conditions tied to individuals (disease names, ICD codes, clinical descriptions)
- Credit card details (card numbers, expiration dates, CVVs, cardholder names alongside card data)

Do NOT redact or replace anything outside those categories. Leave unchanged:
- Physical mailing addresses
- Insurance, policy, group, or claim numbers
- Dates other than DOB (date of service, date of injury, admission/discharge dates, etc.)
- Driver's license or state ID numbers
- Bank account or routing numbers
- Employer names or job titles
- Facility names, hospital names, or clinic names

Requirements:
1. Transcribe ALL text from the page — including headings, labels, table contents, and footers.
2. As you transcribe, substitute every PII/PHI value with your invented replacement. The
   replacement MUST be a completely different made-up value that shares NO words with the
   original. "MARGARET HOLMES" → "DIANA CHEN" is correct. "MARGARET HOLMES" → "MARGARET
   HOLMES" is a critical failure. "MARGARET HOLMES" → "MARGARET CHEN" is also wrong — no
   words may overlap.
3. If the same original value appears multiple times, always use the same replacement for it.
4. Replacements must follow realistic formats (names look like names, SSNs look like SSNs,
   etc.) but must not correspond to real individuals.
5. Pay special attention to names inside table cells or form fields, names in "Last, First"
   inverted format, and names following role labels (e.g. "Claimant:", "Patient:"). These are
   PII regardless of formatting or context.
6. In the mapping, "original_masked" is a partially obscured hint of the original (e.g.
   "J*** S***" for "John Smith") — never write the full original value there. "replacement"
   is the invented value you wrote into sanitized_text. They must always be different.

SELF-CHECK — do this before returning your response:
1. For every row in your mapping, verify that "replacement" is completely different from the
   original value you saw in the document. If they match or closely resemble each other,
   discard that replacement and invent a new one.
2. Scan your sanitized_text and confirm that NONE of the original PII/PHI values from the
   document appear anywhere in it. If any original value leaked through, replace it with the
   corresponding fictitious value from your mapping.

Return ONLY valid JSON with exactly this structure (no markdown fences, no extra keys):
{
  "sanitized_text": "<complete transcription of the page with all PII/PHI replaced>",
  "mapping": [
    {
      "original_masked": "<partially obscured original, e.g. J*** S***>",
      "replacement": "<the invented value written into sanitized_text>",
      "type": "<Name | SSN | DOB | Email | Phone | MRN | Diagnosis | CreditCard>"
    }
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

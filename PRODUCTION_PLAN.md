# Production Plan — S3 + Step Functions + Lambda

> **Status:** Design phase — not yet implemented.
> See the [main README](README.md) for the current POC pipeline.

---

## Overview

The redaction pipeline currently runs in a Jupyter notebook (POC). This document describes the production architecture for processing thousands of documents at scale on AWS — event-driven, serverless, with automatic resume on failure.

**Core idea:** Drop a PDF into an S3 bucket → get redacted output automatically.

---

## Architecture

```
S3 (input/)  →  SQS (FIFO)  →  Trigger Lambda  →  Step Functions
                                                      │
                                                      ├── DynamoDB: status = "processing"
                                                      ├── Lambda A: PDF → page PNGs (S3 cache)
                                                      ├── Map (sequential, MaxConcurrency: 1):
                                                      │     └── Lambda B: Bedrock redact per page
                                                      ├── Lambda C: Build PDFs + governance JSON → S3
                                                      └── DynamoDB: status = "done"
```

---

## Lambda Functions

### Lambda A: `redaction-render`

| Setting | Value |
|---------|-------|
| Timeout | 5 min |
| Memory | 2048 MB |
| Layer | PyMuPDF |

- Downloads PDF from S3, renders each page at 230 DPI
- Uploads PNGs to `s3://bucket/cache/{doc_id}/page_{n}.png`
- **Idempotent:** skips pages whose PNGs already exist in S3
- Returns `{doc_id, total_pages}`

### Lambda B: `redaction-process-page`

| Setting | Value |
|---------|-------|
| Timeout | 5 min |
| Memory | 1024 MB |
| ReservedConcurrency | 8 (caps Bedrock calls) |
| Layer | utils.py, models.json |

- Processes **one page** per invocation (called sequentially by Step Functions Map state)
- **Resume support:** checks `s3://cache/{doc_id}/page_{n}.json` first — if exists, skips
- Downloads prior page JSONs to build `accumulated_mapping` (cross-page consistency)
- Bedrock vision call → validate → enforce replacements → LLM audit
- Uploads result JSON to S3 cache
- Reuses existing `utils.py` functions (`validate_mapping`, `fix_remaining_violations`, `enforce_replacements_in_text`, `audit_sanitized_text`)

### Lambda C: `redaction-reconstruct`

| Setting | Value |
|---------|-------|
| Timeout | 10 min |
| Memory | 2048 MB |
| Layer | ReportLab |

- Downloads all page JSONs from S3 cache
- Builds `redacted_{stem}.pdf`, `summary_{stem}.pdf`, `governance_{stem}.json`
- Uploads outputs to `s3://bucket/output/{doc_id}/`
- Uploads per-document log to `s3://bucket/logs/`
- Deletes cache prefix (`cache/{doc_id}/`) on success

---

## Step Functions Workflow

```
Start
  → DynamoDB: status = "processing"
  → Lambda A: Render (with retry)
  → Pass: generate page array [1, 2, ..., N]
  → Map (MaxConcurrency: 1):          ← pages MUST be sequential
      → Lambda B: Process Page
  → Lambda C: Reconstruct
  → DynamoDB: status = "done"
End

Catch (any state failure):
  → DynamoDB: status = "failed", error details
  → Cache NOT deleted (enables retry/resume)
```

**Type:** Standard (not Express) — supports long-running executions with built-in retry/catch.

**Retry config on Lambda B:**

| Error | Retries | Initial wait | Backoff |
|-------|---------|-------------|---------|
| ThrottlingException | 5 | 15s | 2x |
| General failure | 2 | 5s | 2x |

---

## Resume-on-Timeout

Lambda has a 15-minute maximum timeout. For large documents (e.g. 20+ pages), a single Lambda invocation may not be enough. The design handles this through **S3-based page caching:**

1. Lambda B times out at page 8 of 20 → Map state fails → Catch → status = "failed"
2. Retry starts a **new execution** of the entire workflow
3. Lambda A: sees all PNGs exist in S3 → skips rendering (instant)
4. Lambda B pages 1–8: sees JSON cache exists in S3 → skips (instant)
5. Lambda B pages 9–20: processes normally via Bedrock
6. Lambda C: builds outputs from all 20 cached page JSONs

**No Bedrock calls are wasted on retry.** Only uncached pages are processed.

---

## S3 Bucket Structure

```
s3://doc-redaction-{env}/
  input/                          ← PDFs land here (triggers processing)
  processing/                     ← moved here during processing (prevents re-trigger)
  cache/{doc_id}/                 ← ephemeral per-page artifacts
    page_1.png
    page_1.json
    page_2.png
    page_2.json
    ...
  output/{doc_id}/                ← final deliverables
    redacted_{stem}.pdf
    summary_{stem}.pdf
    governance_{stem}.json
  logs/
    {doc_id}.log                  ← per-document processing log
  dead-letter/                    ← poison docs moved here after 3 failures
  config/
    models.json                   ← model config (or bundle in Lambda layer)
```

---

## DynamoDB Status Table

**Table:** `DocRedactionStatus`

| Attribute | Type | Description |
|-----------|------|-------------|
| `doc_id` (PK) | String | UUID assigned at intake |
| `source_key` | String | Original S3 key of the PDF |
| `status` | String | `queued` / `processing` / `done` / `failed` |
| `total_pages` | Number | Set after render step |
| `pages_completed` | Number | Incremented per page (progress tracking) |
| `execution_arn` | String | Step Functions execution ARN |
| `created_at` | String | ISO 8601 timestamp |
| `updated_at` | String | ISO 8601 timestamp |
| `error` | String | Error message if failed |
| `output_prefix` | String | S3 prefix for outputs (e.g. `output/{doc_id}/`) |
| `ttl` | Number | Epoch seconds — auto-delete after 90 days |

**GSI:** `status-index` on `status` + `created_at` — for querying all failed or in-progress docs.

DynamoDB updates use **Step Functions direct SDK integration** (no Lambda needed for status writes).

---

## Duplicate Prevention

Three layers to prevent a PDF from being processed twice:

1. **SQS FIFO** with `MessageDeduplicationId` = S3 ETag + key (absorbs duplicate S3 event notifications within a 5-min window)
2. **DynamoDB conditional write** — trigger Lambda does `PutItem` with `attribute_not_exists(source_key)`. If item already exists with status `processing` or `done`, skip.
3. **S3 prefix move** — after triggering, the input PDF is moved from `input/` to `processing/`, so no new S3 event fires for it.

---

## Concurrency Control

| Control | Value | Purpose |
|---------|-------|---------|
| SQS trigger Lambda ReservedConcurrency | 6 | Max 6 parallel Step Functions executions |
| Lambda B ReservedConcurrency | 8 | Hard cap on concurrent Bedrock calls |
| Map state MaxConcurrency | 1 | Pages sequential within a document |
| Step Functions retry backoff | exponential | Handles Bedrock throttling gracefully |

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| **Bedrock throttling** | Step Functions retry with exponential backoff (15s → 30s → 60s → 120s → 240s) |
| **Document failure** | Catch block → DynamoDB `status = "failed"`, cache preserved for manual retry |
| **Poison documents** | After 3 full-workflow failures → move PDF to `dead-letter/` prefix + SNS alert |
| **Partial page failure** | Retry resumes from last cached page — no wasted Bedrock calls |
| **Lambda timeout** | Same as partial failure — cache-based resume |

---

## Code Changes Required

### New files

```
lambda/
  trigger/handler.py              ← SQS → DynamoDB + start Step Functions
  render/handler.py               ← Lambda A: PDF → page PNGs to S3
  process_page/handler.py         ← Lambda B: Bedrock redact, S3 cache
  reconstruct/handler.py          ← Lambda C: build PDFs + governance → S3
  shared/                         ← Lambda layer (shared across all functions)
    utils.py                        (copied from notebooks/utils.py — unchanged)
    models.json                     (copied from config/models.json)
    bedrock_client.py               (simplified — IAM role, no dotenv)
infra/
  template.yaml                   ← SAM/CloudFormation (all resources)
```

### Existing code reused in Lambda layer

| Source | Destination | Changes |
|--------|-------------|---------|
| `notebooks/utils.py` | `lambda/shared/utils.py` | None — all functions are pure/stateless |
| `config/models.json` | `lambda/shared/models.json` | None |
| `models/bedrock_client.py` | `lambda/shared/bedrock_client.py` | Simplified: remove dotenv/file loading, use Lambda IAM role |

### IAM Role

Single execution role for all Lambdas:
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:CopyObject` on the bucket
- `bedrock:InvokeModel` on the redaction + audit model ARNs
- `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:GetItem` on the status table
- `states:StartExecution` (trigger Lambda only)

Step Functions execution role:
- `lambda:InvokeFunction` on all three Lambdas
- `dynamodb:UpdateItem` for direct SDK integration steps

---

## Verification Checklist

- [ ] Deploy via SAM/CloudFormation
- [ ] Upload a test PDF to `s3://bucket/input/`
- [ ] Confirm SQS message → Step Functions execution starts
- [ ] Check DynamoDB status: `queued` → `processing` → `done`
- [ ] Verify all 3 outputs in `s3://bucket/output/{doc_id}/`
- [ ] Test resume: upload a large PDF, kill Lambda B mid-execution, retry and verify it resumes from cache
- [ ] Test throttling: upload 20 PDFs simultaneously, verify no unrecoverable Bedrock errors
- [ ] Test poison doc: upload a corrupt PDF, verify it moves to `dead-letter/` after 3 failures

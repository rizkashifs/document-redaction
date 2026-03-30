# =============================================================
# Cell 1: Setup & Configuration
# =============================================================
import boto3
import json
import os
from io import BytesIO

s3 = boto3.client("s3")

SOURCE_BUCKET = "my-source-bucket"
SOURCE_PREFIX = "raw-docs/"

DEST_BUCKET = "my-dest-bucket"
DEST_PREFIX = "processed-docs/"


# =============================================================
# Cell 2: List all files in the source bucket
# =============================================================
def list_s3_files(bucket, prefix):
    """List all object keys under a given prefix (handles pagination)."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("/"):  # skip folder markers
                keys.append(obj["Key"])
    return keys

file_keys = list_s3_files(SOURCE_BUCKET, SOURCE_PREFIX)
print(f"Found {len(file_keys)} files in s3://{SOURCE_BUCKET}/{SOURCE_PREFIX}")
for k in file_keys:
    print(f"  - {k}")


# =============================================================
# Cell 3: Read documents from source bucket
# =============================================================
def read_s3_file(bucket, key):
    """Download a single file from S3 into memory."""
    response = s3.get_object(Bucket=bucket, Key=key)
    content = response["Body"].read()
    return content

documents = []
for key in file_keys:
    content = read_s3_file(SOURCE_BUCKET, key)
    documents.append({"key": key, "content": content})
    print(f"Read: {key} ({len(content)} bytes)")

print(f"\nTotal documents loaded: {len(documents)}")


# =============================================================
# Cell 4: Process / Transform documents
# =============================================================
# ── Replace this with your actual processing logic ──

def process_document(raw_content, key):
    """
    Your transformation logic goes here.
    
    Examples:
      - Parse JSON/CSV and filter rows
      - Extract text from PDFs (using PyPDF2 or pdfplumber)
      - Clean and normalize text
      - Run ML inference
      - Convert file formats
    """
    # Example: decode text, strip whitespace, add metadata header
    text = raw_content.decode("utf-8")
    processed = f"--- Processed: {key} ---\n{text.strip()}\n"
    return processed.encode("utf-8")


processed_docs = []
for doc in documents:
    try:
        result = process_document(doc["content"], doc["key"])
        processed_docs.append({"key": doc["key"], "content": result})
        print(f"Processed: {doc['key']}")
    except Exception as e:
        print(f"ERROR processing {doc['key']}: {e}")

print(f"\nSuccessfully processed: {len(processed_docs)}/{len(documents)}")


# =============================================================
# Cell 5: Upload processed documents to destination bucket
# =============================================================
def upload_to_s3(bucket, key, content):
    """Upload a single file to S3."""
    s3.put_object(Bucket=bucket, Key=key, Body=content)

for doc in processed_docs:
    # Build destination key: swap source prefix for dest prefix
    filename = doc["key"].replace(SOURCE_PREFIX, "", 1)
    dest_key = DEST_PREFIX + filename

    upload_to_s3(DEST_BUCKET, dest_key, doc["content"])
    print(f"Uploaded: s3://{DEST_BUCKET}/{dest_key}")

print(f"\nDone! {len(processed_docs)} files uploaded.")


# =============================================================
# Cell 6: Verify output
# =============================================================
output_keys = list_s3_files(DEST_BUCKET, DEST_PREFIX)
print(f"Files in s3://{DEST_BUCKET}/{DEST_PREFIX}:")
for k in output_keys:
    meta = s3.head_object(Bucket=DEST_BUCKET, Key=k)
    size = meta["ContentLength"]
    print(f"  {k}  ({size} bytes)")

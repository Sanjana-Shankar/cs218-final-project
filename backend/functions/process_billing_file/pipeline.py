"""Local and Lambda-compatible processing entry points."""
# Conencts parsing, analysis and persistence

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .analytics import analyze_records
from .parser import load_billing_csv, parse_billing_rows


def process_csv_file(file_path: str | Path, analysis_id: str | None = None, repository=None) -> dict:
    records = load_billing_csv(file_path)
    analysis = analyze_records(records, analysis_id=analysis_id)
    if repository is not None:
        repository.save_records(records, analysis["analysisId"])
        repository.save_analysis(analysis)
    return analysis


def process_csv_text(csv_text: str, analysis_id: str | None = None, repository=None) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        from .errors import BillingValidationError

        raise BillingValidationError("The billing file is missing a header row.")
    records = parse_billing_rows(reader)
    analysis = analyze_records(records, analysis_id=analysis_id)
    if repository is not None:
        repository.save_records(records, analysis["analysisId"])
        repository.save_analysis(analysis)
    return analysis


def process_s3_event(event: dict, repository) -> dict:
    """Process the first S3 object in an S3 notification event."""
    import boto3

    s3 = boto3.client("s3")
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]
    response = s3.get_object(Bucket=bucket, Key=key)
    csv_text = response["Body"].read().decode("utf-8-sig")
    return process_csv_text(csv_text, repository=repository)

"""Local and Lambda-compatible processing entry points."""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from urllib.parse import unquote_plus

try:
    from .analytics import analyze_records
    from .errors import BillingValidationError
    from .explanations import add_explanations
    from .parser import load_billing_csv, parse_billing_rows
except ImportError:  # pragma: no cover
    from analytics import analyze_records
    from errors import BillingValidationError
    from explanations import add_explanations
    from parser import load_billing_csv, parse_billing_rows

# Written by the upload Lambda and delivered by S3, sometimes URL-encoded.
_UPLOAD_KEY = re.compile(r"^uploads/([^/]+)/([^/]+)\.csv$")

_UNEXPECTED_FAILURE = "Processing failed. Please try again."


def _with_organization(records: list[dict], organization_id: str | None) -> None:
    """The authenticated identity wins over the organization column in the CSV."""
    if organization_id is None:
        return
    for record in records:
        record["organizationId"] = organization_id


def _analyze_and_store(
    records: list[dict],
    analysis_id: str | None,
    repository,
    organization_id: str | None,
) -> dict:
    _with_organization(records, organization_id)
    analysis = analyze_records(records, analysis_id=analysis_id)
    add_explanations(analysis)
    if repository is not None:
        repository.save_records(records, analysis["analysisId"])
        repository.save_analysis(analysis)
    return analysis


def process_csv_file(
    file_path: str | Path,
    analysis_id: str | None = None,
    repository=None,
    organization_id: str | None = None,
) -> dict:
    records = load_billing_csv(file_path)
    return _analyze_and_store(records, analysis_id, repository, organization_id)


def process_csv_text(
    csv_text: str,
    analysis_id: str | None = None,
    repository=None,
    organization_id: str | None = None,
) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise BillingValidationError("The billing file is missing a header row.")
    records = parse_billing_rows(reader)
    return _analyze_and_store(records, analysis_id, repository, organization_id)


def _bucket_and_key(event: dict) -> tuple[str, str]:
    """Accept an S3 notification or a plain ``{"bucket", "key"}`` task payload."""
    records = event.get("Records")
    if isinstance(records, list) and records and isinstance(records[0], dict) and "s3" in records[0]:
        try:
            bucket = records[0]["s3"]["bucket"]["name"]
            key = records[0]["s3"]["object"]["key"]
        except (KeyError, TypeError) as exc:
            raise ValueError("S3 event is missing the object bucket or key.") from exc
        return bucket, key
    bucket = event.get("bucket")
    key = event.get("key")
    if isinstance(bucket, str) and isinstance(key, str):
        return bucket, key
    raise ValueError("Event must be an S3 notification or an object with 'bucket' and 'key'.")


def _organization_and_analysis(key: str) -> tuple[str, str]:
    decoded = unquote_plus(key)
    match = _UPLOAD_KEY.fullmatch(decoded)
    if not match:
        raise ValueError(
            f"S3 object key {decoded!r} does not match uploads/{{organizationId}}/{{analysisId}}.csv."
        )
    return match.group(1), match.group(2)


def process_s3_event(event: dict, repository, s3_client=None) -> dict:
    """Process one uploaded billing CSV.

    The object key is ``uploads/{organizationId}/{analysisId}.csv``. S3 event
    notifications URL-encode the key, so it is decoded before it is parsed.
    ``organizationId`` from the key replaces the organization column in the file.
    """
    bucket, raw_key = _bucket_and_key(event)
    key = unquote_plus(raw_key)
    organization_id, analysis_id = _organization_and_analysis(raw_key)
    repository.save_status(organization_id, analysis_id, "PROCESSING")

    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        csv_text = response["Body"].read().decode("utf-8-sig")
        return process_csv_text(
            csv_text,
            analysis_id=analysis_id,
            repository=repository,
            organization_id=organization_id,
        )
    except BillingValidationError as exc:
        # A bad file will never become valid on retry. Record the failure and stop.
        repository.save_status(organization_id, analysis_id, "FAILED", str(exc))
        return {
            "analysisId": analysis_id,
            "organizationId": organization_id,
            "status": "FAILED",
            "errorMessage": str(exc),
        }
    except Exception:
        repository.save_status(organization_id, analysis_id, "FAILED", _UNEXPECTED_FAILURE)
        logging.exception(
            "Billing file processing failed for %s/%s", organization_id, analysis_id
        )
        raise

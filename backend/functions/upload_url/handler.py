"""AWS Lambda entry point for issuing billing-file upload URLs.

Routes (Cognito authorization is required in API Gateway):
    POST /uploads    body {"fileName", "contentType"} returns {"uploadUrl", "analysisId"}

Environment variables:
    UPLOAD_BUCKET, ANALYSIS_RESULTS_TABLE
    UPLOAD_URL_EXPIRES_SECONDS (optional, default 300), CORS_ORIGIN (optional, default *)

The organization is ALWAYS taken from the Cognito token, never from the request body.
A QUEUED analysis record is written before the presigned URL is returned, so the
dashboard can poll immediately.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ALLOWED_CONTENT_TYPES = (
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
)


class UploadValidationError(ValueError):
    """Raised when the upload request itself is invalid."""


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.getenv("CORS_ORIGIN", "*"),
        },
        "body": json.dumps(body),
    }


def _organization_id(event: dict) -> str:
    """Same rule as process_billing_file/handler.py so both Lambdas agree on identity."""
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("claims", {}) or authorizer.get("jwt", {}).get("claims", {})
    organization_id = claims.get("custom:organizationId") or claims.get("sub")
    if not organization_id:
        raise PermissionError("Authenticated organization identity was not found.")
    return organization_id


def _request_method(event: dict) -> str | None:
    method = event.get("httpMethod")
    if method:
        return method
    request_context = event.get("requestContext") or {}
    http = request_context.get("http") or {}
    return http.get("method")


def _request_path(event: dict) -> str:
    return event.get("resource") or event.get("rawPath") or event.get("path") or ""


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if isinstance(raw, dict):
        return raw
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UploadValidationError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise UploadValidationError("Request body must be a JSON object.")
    return body


def _validated_upload(body: dict) -> tuple[str, str]:
    file_name = body.get("fileName")
    if not isinstance(file_name, str) or not file_name.strip():
        raise UploadValidationError("fileName is required.")
    if not file_name.lower().endswith(".csv"):
        raise UploadValidationError("fileName must end in .csv.")
    content_type = body.get("contentType")
    if content_type not in ALLOWED_CONTENT_TYPES:
        allowed = ", ".join(ALLOWED_CONTENT_TYPES)
        raise UploadValidationError(f"contentType must be one of {allowed}.")
    return file_name, content_type


def _expires_in() -> int:
    raw = os.getenv("UPLOAD_URL_EXPIRES_SECONDS", "300")
    try:
        expires = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("UPLOAD_URL_EXPIRES_SECONDS must be an integer.") from exc
    if expires <= 0:
        raise RuntimeError("UPLOAD_URL_EXPIRES_SECONDS must be positive.")
    return expires


def _s3_client(s3_client):
    if s3_client is not None:
        return s3_client
    import boto3

    return boto3.client("s3", region_name=os.getenv("AWS_REGION"))


def _analysis_table(table):
    if table is not None:
        return table
    import boto3

    dynamodb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION"))
    return dynamodb.Table(os.environ["ANALYSIS_RESULTS_TABLE"])


def lambda_handler(event, context, s3_client=None, table=None):
    """Return a presigned PUT URL and record the analysis as QUEUED.

    ``s3_client`` and ``table`` are optional so tests can inject fakes.
    """
    try:
        method = _request_method(event)
        path = _request_path(event)
        if method != "POST" or not (path == "/uploads" or path.endswith("/uploads")):
            return _response(400, {"error": "Unsupported upload request."})

        organization_id = _organization_id(event)
        _file_name, content_type = _validated_upload(_parse_body(event))

        analysis_id = f"analysis-{uuid4().hex[:12]}"
        key = f"uploads/{organization_id}/{analysis_id}.csv"
        now = datetime.now(timezone.utc).isoformat()
        _analysis_table(table).put_item(Item={
            "organizationId": organization_id,
            "analysisId": analysis_id,
            "status": "QUEUED",
            "errorMessage": None,
            "createdAt": now,
            "updatedAt": now,
        })

        upload_url = _s3_client(s3_client).generate_presigned_url(
            "put_object",
            Params={
                "Bucket": os.environ["UPLOAD_BUCKET"],
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=_expires_in(),
        )
        return _response(200, {"uploadUrl": upload_url, "analysisId": analysis_id})
    except PermissionError as exc:
        return _response(401, {"error": str(exc)})
    except UploadValidationError as exc:
        return _response(400, {"error": str(exc)})
    except Exception:
        logger.exception("Upload URL request failed")
        return _response(500, {"error": "Could not create the upload URL."})

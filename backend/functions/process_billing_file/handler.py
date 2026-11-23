"""AWS Lambda entry point for billing analysis.

Routes:
    GET /analyses/{analysisId}   the analysis record (QUEUED, PROCESSING, COMPLETED, or FAILED)
    GET /health                  {"status": "ok"} with no authorization
    POST /internal/process-csv   synchronous CSV processing for development
    S3 ObjectCreated, or {"bucket", "key"}   process an uploaded billing file

Environment variables:
    ANALYSIS_RESULTS_TABLE, BILLING_RECORDS_TABLE, RECOMMENDATIONS_TABLE
    CORS_ORIGIN (optional), ENABLE_AI_EXPLANATIONS (optional), BEDROCK_MODEL_ID (optional)
    Lambda timeout: at least 120 seconds. Explanations make one Bedrock call per recommendation, and each call can wait up to 60 seconds before falling back.

The organization is ALWAYS taken from the Cognito token (or, for an S3 upload,
from the object key the upload Lambda wrote), never from the CSV.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from urllib.parse import unquote

try:
    from .pipeline import process_csv_text, process_s3_event
    from .storage import DynamoRepository, json_safe
except ImportError:  # pragma: no cover
    from pipeline import process_csv_text, process_s3_event
    from storage import DynamoRepository, json_safe


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.getenv("CORS_ORIGIN", "*"),
        },
        "body": json.dumps(json_safe(body)),
    }


def _organization_id(event: dict) -> str:
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


def _is_health_request(event: dict) -> bool:
    if _request_method(event) != "GET":
        return False
    if event.get("resource") == "/health":
        return True
    path = event.get("rawPath") or event.get("path") or ""
    return isinstance(path, str) and path.endswith("/health")


def _is_processing_event(event: dict) -> bool:
    records = event.get("Records")
    if isinstance(records, list) and records and isinstance(records[0], dict):
        if records[0].get("eventSource") == "aws:s3":
            return True
    if _request_method(event):
        return False
    return isinstance(event.get("bucket"), str) and isinstance(event.get("key"), str)


def _analysis_id(event: dict) -> str | None:
    path_parameters = event.get("pathParameters") or {}
    analysis_id = path_parameters.get("analysisId")
    if analysis_id:
        return analysis_id
    path = event.get("rawPath") or event.get("path") or ""
    marker = "/analyses/"
    if marker not in path:
        return None
    segment = path.split(marker, 1)[1].split("/", 1)[0]
    return unquote(segment) or None


def lambda_handler(event, context, repository=None, s3_client=None):
    """Handle S3 processing events and API Gateway analysis retrieval.

    ``repository`` and ``s3_client`` are optional so tests can inject fakes.
    The health check runs before either one is constructed.
    """
    if _is_health_request(event):
        return _response(200, {"status": "ok"})

    if _is_processing_event(event):
        # Unexpected failures propagate so S3 or Step Functions can retry.
        # Invalid billing files are recorded as FAILED and do not raise.
        repository = repository or DynamoRepository()
        analysis = process_s3_event(event, repository, s3_client=s3_client)
        return {"analysisId": analysis["analysisId"], "status": analysis["status"]}

    repository = repository or DynamoRepository()
    try:
        method = _request_method(event)
        analysis_id = _analysis_id(event)
        organization_id = _organization_id(event)

        if method == "GET" and analysis_id:
            analysis = repository.get_analysis(organization_id, analysis_id)
            if not analysis:
                return _response(404, {"error": "Analysis not found."})
            return _response(200, analysis)

        if method == "POST" and event.get("resource") == "/internal/process-csv":
            raw_body = event.get("body") or "{}"
            if event.get("isBase64Encoded"):
                raw_body = base64.b64decode(raw_body).decode("utf-8")
            body = json.loads(raw_body)
            csv_text = body["csvText"]
            # The token's organization overwrites the CSV column before anything is stored.
            analysis = process_csv_text(
                csv_text, repository=repository, organization_id=organization_id
            )
            return _response(201, analysis)

        return _response(400, {"error": "Unsupported analysis request."})
    except PermissionError as exc:
        return _response(401, {"error": str(exc)})
    except (KeyError, ValueError) as exc:
        return _response(400, {"error": str(exc)})
    except Exception:
        # Keep internal details out of the client response; use CloudWatch logs for the traceback.
        logging.exception("Analysis request failed")
        return _response(500, {"error": "Analysis processing failed."})

"""AWS Lambda handlers for processing and retrieving analysis results."""

from __future__ import annotations

import base64
import json
import os

from .pipeline import process_csv_text, process_s3_event
from .storage import DynamoRepository, json_safe


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


def lambda_handler(event, context):
    """Handle S3 processing events and API Gateway analysis retrieval."""
    repository = DynamoRepository()
    try:
        if event.get("Records") and event["Records"][0].get("eventSource") == "aws:s3":
            analysis = process_s3_event(event, repository)
            return {"analysisId": analysis["analysisId"], "status": analysis["status"]}

        method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
        path_parameters = event.get("pathParameters") or {}
        analysis_id = path_parameters.get("analysisId")
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
            analysis = process_csv_text(csv_text, repository=repository)
            if analysis["organizationId"] != organization_id:
                return _response(403, {"error": "Organization does not match the billing data."})
            return _response(201, analysis)

        return _response(400, {"error": "Unsupported analysis request."})
    except PermissionError as exc:
        return _response(401, {"error": str(exc)})
    except (KeyError, ValueError) as exc:
        return _response(400, {"error": str(exc)})
    except Exception:
        # Keep internal details out of the client response; use CloudWatch logs for the traceback.
        import logging

        logging.exception("Analysis request failed")
        return _response(500, {"error": "Analysis processing failed."})

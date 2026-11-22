"""Workstream 4: AWS Lambda entry point for the chatbot.

Routes (both require Cognito authorization in API Gateway):
    POST /chat           body matches data/chat_request.json, returns data/chat_response.json
    GET  /chat/history   optional ?conversationId=...  returns {"items": [...]}

Environment variables:
    ANALYSIS_RESULTS_TABLE, RECOMMENDATIONS_TABLE, CHAT_HISTORY_TABLE
    BEDROCK_MODEL_ID (optional), CORS_ORIGIN (optional)

The organization is ALWAYS taken from the Cognito token, never from the request body,
so one user can never read another organization's data.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

try:  # works both as a package (tests) and as a flat Lambda folder
    from .chat import ChatValidationError, answer_question, to_plain
except ImportError:  # pragma: no cover
    from chat import ChatValidationError, answer_question, to_plain

logger = logging.getLogger()
logger.setLevel(logging.INFO)

HISTORY_TURNS_FOR_MODEL = 3
HISTORY_PAGE_SIZE = 50


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.getenv("CORS_ORIGIN", "*"),
        },
        "body": json.dumps(to_plain(body)),
    }


def _organization_id(event: dict) -> str:
    """Same rule as process_billing_file/handler.py so both Lambdas agree on identity."""
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = authorizer.get("claims") or (authorizer.get("jwt") or {}).get("claims") or {}
    organization_id = claims.get("custom:organizationId") or claims.get("sub")
    if not organization_id:
        raise PermissionError("Authenticated organization identity was not found.")
    return organization_id


class ChatRepository:
    """All DynamoDB access for the chatbot. Every query is scoped to one organizationId."""

    def __init__(self, dynamodb_resource=None):
        if dynamodb_resource is None:
            import boto3

            dynamodb_resource = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION"))
        self.analysis_table = dynamodb_resource.Table(os.environ["ANALYSIS_RESULTS_TABLE"])
        self.recommendation_table = dynamodb_resource.Table(os.environ["RECOMMENDATIONS_TABLE"])
        self.chat_table = dynamodb_resource.Table(os.environ["CHAT_HISTORY_TABLE"])

    def get_analysis(self, organization_id: str, analysis_id: str) -> dict | None:
        item = self.analysis_table.get_item(
            Key={"organizationId": organization_id, "analysisId": analysis_id}
        ).get("Item")
        if not item:
            return None
        recs = self.recommendation_table.query(
            KeyConditionExpression="organizationId = :org",
            ExpressionAttributeValues={":org": organization_id},
        ).get("Items", [])
        item["recommendations"] = [r for r in recs if r.get("analysisId") == analysis_id]
        return to_plain(item)

    def get_history(self, organization_id: str, conversation_id: str | None = None,
                    limit: int = HISTORY_PAGE_SIZE) -> list[dict]:
        """Newest-first query, returned oldest-first."""
        items = self.chat_table.query(
            KeyConditionExpression="organizationId = :org",
            ExpressionAttributeValues={":org": organization_id},
            ScanIndexForward=False,
            Limit=200,
        ).get("Items", [])
        if conversation_id:
            items = [i for i in items if i.get("conversationId") == conversation_id]
        return list(reversed(to_plain(items[:limit])))

    def save_turn(self, organization_id: str, response: dict, question: str) -> None:
        # Unique sort key: ISO time + short id, so two messages in the same instant don't collide.
        timestamp = f"{datetime.now(timezone.utc).isoformat()}#{uuid.uuid4().hex[:6]}"
        self.chat_table.put_item(Item={
            "organizationId": organization_id,
            "timestamp": timestamp,
            "conversationId": response["conversationId"],
            "analysisId": response["analysisId"],
            "question": question,
            "answer": response["answer"],
            "confidence": Decimal(str(response["confidence"])),  # DynamoDB needs Decimal, not float
            "modelUsed": response.get("modelUsed"),
        })


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChatValidationError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise ChatValidationError("Request body must be a JSON object.")
    return body


def handle_chat(event: dict, repository: ChatRepository, client=None) -> dict:
    organization_id = _organization_id(event)
    body = _parse_body(event)
    analysis_id = body.get("analysisId")
    if not analysis_id or not isinstance(analysis_id, str):
        raise ChatValidationError("analysisId is required.")
    question = body.get("question")
    conversation_id = body.get("conversationId")

    analysis = repository.get_analysis(organization_id, analysis_id)
    if not analysis:
        # Same message whether it doesn't exist or belongs to someone else (no information leak).
        return _response(404, {"error": "Analysis not found."})

    history = []
    if conversation_id and body.get("includeHistory", True):
        history = repository.get_history(organization_id, conversation_id)[-HISTORY_TURNS_FOR_MODEL:]

    response = answer_question(question, analysis, history=history,
                               conversation_id=conversation_id, client=client)
    try:
        repository.save_turn(organization_id, response, question.strip())
    except Exception:
        # Losing history should not lose the answer.
        logger.exception("Could not save chat history")
    return _response(200, response)


def handle_history(event: dict, repository: ChatRepository) -> dict:
    organization_id = _organization_id(event)
    params = event.get("queryStringParameters") or {}
    items = repository.get_history(organization_id, params.get("conversationId"))
    return _response(200, {"items": items})


def lambda_handler(event, context, repository=None, client=None):
    try:
        method = event.get("httpMethod") or (event.get("requestContext") or {}).get("http", {}).get("method")
        path = event.get("resource") or event.get("rawPath") or event.get("path") or ""
        repository = repository or ChatRepository()

        if method == "POST" and path.endswith("/chat"):
            return handle_chat(event, repository, client=client)
        if method == "GET" and path.endswith("/chat/history"):
            return handle_history(event, repository)
        return _response(400, {"error": "Unsupported chat request."})
    except PermissionError as exc:
        return _response(401, {"error": str(exc)})
    except ChatValidationError as exc:
        return _response(400, {"error": str(exc)})
    except Exception:
        logger.exception("Chat request failed")
        return _response(500, {"error": "The assistant could not process this request."})

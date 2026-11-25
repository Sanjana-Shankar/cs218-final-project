"""Optional DynamoDB persistence used by the deployed Lambda functions."""
# Provides local in-memory storage and DynamoDB storage

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def dynamodb_safe(value: Any) -> Any:
    """Return a copy of ``value`` that DynamoDB can store.

    boto3 rejects Python floats. A JSON round trip with ``parse_float=Decimal``
    converts every float and leaves strings, ints, bools, and nulls unchanged.
    """
    return json.loads(json.dumps(value), parse_float=Decimal)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InMemoryRepository:
    """Small repository for local tests and frontend contract development."""

    def __init__(self):
        self.analyses: dict[tuple[str, str], dict] = {}
        self.records: dict[tuple[str, str], list[dict]] = {}

    def save_records(self, records: list[dict], analysis_id: str) -> None:
        organization_id = records[0]["organizationId"]
        self.records[(organization_id, analysis_id)] = records

    def save_analysis(self, analysis: dict) -> None:
        key = (analysis["organizationId"], analysis["analysisId"])
        self.analyses[key] = analysis

    def save_status(
        self,
        organization_id: str,
        analysis_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Merge a status update. ``createdAt`` is set only when it is missing."""
        now = _now()
        key = (organization_id, analysis_id)
        existing = self.analyses.get(key)
        if existing is None:
            existing = {"organizationId": organization_id, "analysisId": analysis_id}
            self.analyses[key] = existing
        existing["status"] = status
        existing["errorMessage"] = error_message
        existing["updatedAt"] = now
        if not existing.get("createdAt"):
            existing["createdAt"] = now

    def get_analysis(self, organization_id: str, analysis_id: str) -> dict | None:
        item = self.analyses.get((organization_id, analysis_id))
        if item is None:
            return None
        if "recommendations" not in item:
            return {**item, "recommendations": []}
        return item


class DynamoRepository:
    """DynamoDB adapter. Table names are supplied by environment variables."""

    def __init__(self, dynamodb_resource=None):
        if dynamodb_resource is None:
            import boto3

            dynamodb_resource = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION"))
        self.analysis_table = dynamodb_resource.Table(
            os.environ["ANALYSIS_RESULTS_TABLE"]
        )
        self.billing_table = dynamodb_resource.Table(
            os.environ["BILLING_RECORDS_TABLE"]
        )
        self.recommendation_table = dynamodb_resource.Table(
            os.environ["RECOMMENDATIONS_TABLE"]
        )

    def save_records(self, records: list[dict], analysis_id: str) -> None:
        for index, record in enumerate(records):
            item = dict(record)
            item["recordId"] = f"{analysis_id}-{index + 1:06d}"
            item["analysisId"] = analysis_id
            self.billing_table.put_item(Item=dynamodb_safe(item))

    def save_analysis(self, analysis: dict) -> None:
        item = {
            "organizationId": analysis["organizationId"],
            "analysisId": analysis["analysisId"],
            "status": analysis["status"],
            "errorMessage": analysis.get("errorMessage"),
            "currency": analysis["currency"],
            "period": analysis["period"],
            "summary": analysis["summary"],
            "serviceCosts": analysis["serviceCosts"],
            "projectCosts": analysis["projectCosts"],
            "teamCosts": analysis["teamCosts"],
            "dailyCosts": analysis["dailyCosts"],
            "topResources": analysis["topResources"],
            "anomalies": analysis["anomalies"],
            "createdAt": analysis["createdAt"],
            "updatedAt": analysis.get("updatedAt"),
        }
        self.analysis_table.put_item(Item=dynamodb_safe(item))
        for recommendation in analysis["recommendations"]:
            self.recommendation_table.put_item(Item=dynamodb_safe(recommendation))

    def save_status(
        self,
        organization_id: str,
        analysis_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Set status fields. ``createdAt`` is written only if the item does not have one."""
        now = _now()
        self.analysis_table.update_item(
            Key={"organizationId": organization_id, "analysisId": analysis_id},
            UpdateExpression=(
                "SET #status = :status, errorMessage = :errorMessage, "
                "updatedAt = :updatedAt, createdAt = if_not_exists(createdAt, :createdAt)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":errorMessage": error_message,
                ":updatedAt": now,
                ":createdAt": now,
            },
        )

    def get_analysis(self, organization_id: str, analysis_id: str) -> dict | None:
        response = self.analysis_table.get_item(
            Key={"organizationId": organization_id, "analysisId": analysis_id}
        )
        item = response.get("Item")
        if not item:
            return None
        recommendation_response = self.recommendation_table.query(
            KeyConditionExpression="organizationId = :organizationId",
            ExpressionAttributeValues={":organizationId": organization_id},
        )
        recommendations = [
            rec for rec in recommendation_response.get("Items", []) if rec.get("analysisId") == analysis_id
        ]
        # Status-only items (QUEUED, PROCESSING, FAILED) have no recommendation rows.
        item["recommendations"] = recommendations
        return item


def json_safe(value: Any) -> Any:
    """Convert DynamoDB Decimal values before returning API JSON."""
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return float(value)
    except ImportError:
        pass
    return value

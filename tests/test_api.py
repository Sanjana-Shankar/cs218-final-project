"""API and persistence tests. Fakes only: no AWS, network, or moto."""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from backend.functions.process_billing_file.handler import lambda_handler as process_lambda
from backend.functions.process_billing_file.pipeline import process_csv_file
from backend.functions.process_billing_file.storage import DynamoRepository, InMemoryRepository
from backend.functions.upload_url.handler import lambda_handler as upload_lambda

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BYTES = (ROOT / "data" / "sample_billing.csv").read_bytes()
INVALID_BYTES = (ROOT / "data" / "sample_billing_invalid.csv").read_bytes()
ANALYSIS_ID_RE = re.compile(r"analysis-[0-9a-f]{12}")


@pytest.fixture(autouse=True)
def _disable_ai_explanations(monkeypatch):
    """Keep these tests off Bedrock. test_ai_assistant.py still sees the default."""
    monkeypatch.setenv("ENABLE_AI_EXPLANATIONS", "false")


class FakeBody:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}


class FakePresign:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, ClientMethod, Params=None, ExpiresIn=None, HttpMethod=None):
        self.calls.append({
            "method": ClientMethod,
            "params": Params,
            "expires": ExpiresIn,
            "http_method": HttpMethod,
        })
        return f"https://uploads.example/{Params['Key']}?signed=1"


class FakeUploadTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


class CapturingTable:
    def __init__(self):
        self.items = []
        self.updates = []

    def put_item(self, Item):
        self.items.append(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(key) == value for key, value in Key.items()):
                return {"Item": copy.deepcopy(item)}
        return {}

    def query(self, KeyConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        values = ExpressionAttributeValues or {}
        organization_id = values.get(":organizationId", values.get(":org"))
        found = [
            copy.deepcopy(item)
            for item in self.items
            if item.get("organizationId") == organization_id
        ]
        return {"Items": found}


class FakeDynamo:
    def __init__(self):
        self.tables = {}

    def Table(self, name):
        table = self.tables.get(name)
        if table is None:
            table = CapturingTable()
            self.tables[name] = table
        return table


class RecordingRepository(InMemoryRepository):
    def __init__(self):
        super().__init__()
        self.statuses = []

    def save_status(self, organization_id, analysis_id, status, error_message=None):
        self.statuses.append((status, error_message))
        super().save_status(organization_id, analysis_id, status, error_message)


def _upload_event(body, claims=None, **extra):
    event = {
        "httpMethod": "POST",
        "resource": "/uploads",
        "body": json.dumps(body) if isinstance(body, dict) else body,
        "requestContext": {"authorizer": {"claims": claims or {"custom:organizationId": "org-acme"}}},
    }
    event.update(extra)
    return event


def _invoke_upload(monkeypatch, body, claims=None, **extra):
    monkeypatch.setenv("UPLOAD_BUCKET", "billing-uploads")
    table = FakeUploadTable()
    s3 = FakePresign()
    result = upload_lambda(
        _upload_event(body, claims=claims, **extra),
        None,
        s3_client=s3,
        table=table,
    )
    return result, table, s3


def _s3_records(bucket, key):
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
            }
        ]
    }


def _analysis_event(method, analysis_id, organization_id="org-acme"):
    return {
        "httpMethod": method,
        "resource": "/analyses/{analysisId}",
        "pathParameters": {"analysisId": analysis_id},
        "requestContext": {"authorizer": {"claims": {"custom:organizationId": organization_id}}},
    }


def _assert_no_float(value, path="item"):
    if isinstance(value, float):
        raise AssertionError(f"float at {path}: {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_float(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_float(item, f"{path}[{index}]")


# ---------- upload ----------

@pytest.mark.parametrize(
    ("file_name", "content_type"),
    [
        ("october.csv", "text/csv"),
        ("October.CSV", "application/vnd.ms-excel"),
        ("export.Csv", "application/csv"),
    ],
)
def test_upload_returns_a_signed_url_and_queues_the_analysis(monkeypatch, file_name, content_type):
    result, table, s3 = _invoke_upload(
        monkeypatch,
        {"fileName": file_name, "contentType": content_type, "organizationId": "org-demo"},
    )
    assert result["statusCode"] == 200
    assert result["headers"]["Content-Type"] == "application/json"
    assert result["headers"]["Access-Control-Allow-Origin"] == "*"
    body = json.loads(result["body"])
    assert ANALYSIS_ID_RE.fullmatch(body["analysisId"])
    assert set(body) == {"uploadUrl", "analysisId"}

    call = s3.calls[0]
    assert call["method"] == "put_object"
    assert call["expires"] == 300
    assert call["params"] == {
        "Bucket": "billing-uploads",
        "Key": f"uploads/org-acme/{body['analysisId']}.csv",
        "ContentType": content_type,
    }
    assert body["uploadUrl"].endswith(f"{call['params']['Key']}?signed=1")

    assert len(table.items) == 1
    item = table.items[0]
    assert item["organizationId"] == "org-acme"
    assert item["analysisId"] == body["analysisId"]
    assert item["status"] == "QUEUED"
    assert item["errorMessage"] is None
    assert item["createdAt"]
    assert item["updatedAt"]
    assert set(item) == {
        "organizationId",
        "analysisId",
        "status",
        "errorMessage",
        "createdAt",
        "updatedAt",
    }
    _assert_no_float(item)


def test_upload_uses_sub_when_organization_claim_is_absent(monkeypatch):
    result, table, s3 = _invoke_upload(
        monkeypatch,
        {"fileName": "billing.csv", "contentType": "text/csv"},
        claims={"sub": "user-123"},
    )
    assert result["statusCode"] == 200
    analysis_id = json.loads(result["body"])["analysisId"]
    assert s3.calls[0]["params"]["Key"] == f"uploads/user-123/{analysis_id}.csv"
    assert table.items[0]["organizationId"] == "user-123"


def test_upload_accepts_a_base64_body(monkeypatch):
    raw = json.dumps({"fileName": "billing.csv", "contentType": "text/csv"}).encode()
    result, table, _s3 = _invoke_upload(
        monkeypatch,
        base64.b64encode(raw).decode("ascii"),
        isBase64Encoded=True,
    )
    assert result["statusCode"] == 200
    assert table.items[0]["status"] == "QUEUED"


def test_upload_rejects_a_non_csv_name(monkeypatch):
    result, table, s3 = _invoke_upload(
        monkeypatch,
        {"fileName": "notes.txt", "contentType": "text/csv"},
    )
    assert result["statusCode"] == 400
    assert "csv" in json.loads(result["body"])["error"].lower()
    assert table.items == []
    assert s3.calls == []


def test_upload_rejects_a_disallowed_content_type(monkeypatch):
    result, table, s3 = _invoke_upload(
        monkeypatch,
        {"fileName": "billing.csv", "contentType": "application/json"},
    )
    assert result["statusCode"] == 400
    assert "contentType" in json.loads(result["body"])["error"]
    assert table.items == []
    assert s3.calls == []


def test_upload_rejects_missing_claims(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "billing-uploads")
    event = _upload_event({"fileName": "billing.csv", "contentType": "text/csv"})
    event["requestContext"] = {}
    table = FakeUploadTable()
    result = upload_lambda(event, None, s3_client=FakePresign(), table=table)
    assert result["statusCode"] == 401
    assert table.items == []


def test_upload_rejects_invalid_json(monkeypatch):
    result, table, _s3 = _invoke_upload(monkeypatch, "{not json")
    assert result["statusCode"] == 400
    assert table.items == []


# ---------- processing ----------

def test_s3_event_completes_under_the_key_organization_not_the_csv(monkeypatch):
    organization_id = "org acme"
    analysis_id = "analysis-abc123def456"
    decoded_key = f"uploads/{organization_id}/{analysis_id}.csv"
    encoded_key = "uploads/org%20acme/analysis-abc123def456.csv"
    repository = RecordingRepository()
    s3 = FakeS3({("billing-bucket", decoded_key): SAMPLE_BYTES})

    result = process_lambda(
        _s3_records("billing-bucket", encoded_key),
        None,
        repository=repository,
        s3_client=s3,
    )

    assert result == {"analysisId": analysis_id, "status": "COMPLETED"}
    assert s3.calls == [("billing-bucket", decoded_key)]
    assert repository.statuses == [("PROCESSING", None)]
    stored = repository.get_analysis(organization_id, analysis_id)
    assert stored["status"] == "COMPLETED"
    assert stored["organizationId"] == organization_id
    assert stored["summary"]["totalCost"] == 2840
    assert repository.get_analysis("org-demo", analysis_id) is None
    assert repository.records[(organization_id, analysis_id)][0]["organizationId"] == organization_id
    assert stored["recommendations"]
    for recommendation in stored["recommendations"]:
        assert "aiExplanation" in recommendation
        assert recommendation["organizationId"] == organization_id


def test_invalid_sample_fails_without_raising():
    analysis_id = "analysis-badfile0001"
    key = f"uploads/org-acme/{analysis_id}.csv"
    repository = RecordingRepository()
    s3 = FakeS3({("billing-bucket", key): INVALID_BYTES})
    result = process_lambda(
        _s3_records("billing-bucket", key),
        None,
        repository=repository,
        s3_client=s3,
    )
    assert result["status"] == "FAILED"
    assert result["analysisId"] == analysis_id
    stored = repository.analyses[("org-acme", analysis_id)]
    assert stored["status"] == "FAILED"
    assert stored["errorMessage"]
    assert "usageDate" in stored["errorMessage"]
    assert "YYYY-MM-DD" in stored["errorMessage"]
    assert stored["errorMessage"] != "Processing failed. Please try again."
    assert [status for status, _message in repository.statuses] == ["PROCESSING", "FAILED"]

    fetched = process_lambda(
        _analysis_event("GET", analysis_id),
        None,
        repository=repository,
    )
    assert fetched["statusCode"] == 200
    body = json.loads(fetched["body"])
    assert body["status"] == "FAILED"
    assert body["errorMessage"] == stored["errorMessage"]


def test_s3_plus_encoded_key_is_decoded():
    analysis_id = "analysis-plus0000001"
    decoded_key = f"uploads/org acme/{analysis_id}.csv"
    repository = InMemoryRepository()
    process_lambda(
        _s3_records("billing-bucket", f"uploads/org+acme/{analysis_id}.csv"),
        None,
        repository=repository,
        s3_client=FakeS3({("billing-bucket", decoded_key): SAMPLE_BYTES}),
    )
    stored = repository.get_analysis("org acme", analysis_id)
    assert stored["status"] == "COMPLETED"
    assert stored["organizationId"] == "org acme"


def test_plain_bucket_key_event_completes():
    analysis_id = "analysis-plain000001"
    key = f"uploads/org-plain/{analysis_id}.csv"
    repository = InMemoryRepository()
    result = process_lambda(
        {"bucket": "billing-bucket", "key": key},
        None,
        repository=repository,
        s3_client=FakeS3({("billing-bucket", key): SAMPLE_BYTES}),
    )
    assert result == {"analysisId": analysis_id, "status": "COMPLETED"}
    stored = repository.get_analysis("org-plain", analysis_id)
    assert stored["organizationId"] == "org-plain"
    assert stored["status"] == "COMPLETED"


def test_unexpected_processing_error_is_saved_and_reraised():
    class ExplodingS3:
        def get_object(self, **_kwargs):
            raise RuntimeError("s3 unavailable")

    repository = RecordingRepository()
    event = {"bucket": "billing-bucket", "key": "uploads/org-acme/analysis-boom000001.csv"}
    with pytest.raises(RuntimeError, match="s3 unavailable"):
        process_lambda(event, None, repository=repository, s3_client=ExplodingS3())
    stored = repository.analyses[("org-acme", "analysis-boom000001")]
    assert stored["status"] == "FAILED"
    assert stored["errorMessage"] == "Processing failed. Please try again."
    assert [status for status, _message in repository.statuses] == ["PROCESSING", "FAILED"]


def test_unrecognized_key_raises_before_a_status_write():
    repository = RecordingRepository()
    event = {"bucket": "billing-bucket", "key": "incoming/not-our-layout.csv"}
    with pytest.raises(ValueError, match="uploads/"):
        process_lambda(event, None, repository=repository, s3_client=FakeS3({}))
    assert repository.statuses == []
    assert repository.analyses == {}


def test_get_queued_analysis_and_unknown_id():
    repository = InMemoryRepository()
    repository.save_status("org-acme", "analysis-queued0001", "QUEUED")
    queued = process_lambda(
        _analysis_event("GET", "analysis-queued0001"),
        None,
        repository=repository,
    )
    assert queued["statusCode"] == 200
    body = json.loads(queued["body"])
    assert body["status"] == "QUEUED"
    assert body["analysisId"] == "analysis-queued0001"
    assert body["organizationId"] == "org-acme"
    assert body["errorMessage"] is None
    assert body["recommendations"] == []

    missing = process_lambda(
        _analysis_event("GET", "analysis-missing001"),
        None,
        repository=repository,
    )
    assert missing["statusCode"] == 404

    other_org = process_lambda(
        _analysis_event("GET", "analysis-queued0001", organization_id="org-other"),
        None,
        repository=repository,
    )
    assert other_org["statusCode"] == 404


def test_in_memory_status_keeps_created_at_and_plain_values():
    repository = InMemoryRepository()
    repository.save_status("org-acme", "analysis-status0001", "QUEUED")
    created_at = repository.analyses[("org-acme", "analysis-status0001")]["createdAt"]
    repository.save_status("org-acme", "analysis-status0001", "PROCESSING", None)
    stored = repository.analyses[("org-acme", "analysis-status0001")]
    assert stored["createdAt"] == created_at
    assert stored["status"] == "PROCESSING"
    assert set(stored) == {
        "organizationId",
        "analysisId",
        "status",
        "errorMessage",
        "createdAt",
        "updatedAt",
    }

    analysis = process_csv_file(ROOT / "data" / "sample_billing.csv", repository=repository)
    saved = repository.analyses[(analysis["organizationId"], analysis["analysisId"])]
    assert isinstance(saved["summary"]["totalCost"], float)
    assert isinstance(repository.records[(analysis["organizationId"], analysis["analysisId"])][0]["monthlyCost"], float)


def test_internal_process_overwrites_the_csv_organization():
    repository = InMemoryRepository()
    event = {
        "httpMethod": "POST",
        "resource": "/internal/process-csv",
        "body": json.dumps({"csvText": SAMPLE_BYTES.decode("utf-8")}),
        "requestContext": {"authorizer": {"claims": {"custom:organizationId": "org-token"}}},
    }
    result = process_lambda(event, None, repository=repository)
    assert result["statusCode"] == 201
    body = json.loads(result["body"])
    assert body["organizationId"] == "org-token"
    assert body["status"] == "COMPLETED"
    assert repository.get_analysis("org-demo", body["analysisId"]) is None


# ---------- health ----------

@pytest.mark.parametrize(
    "event",
    [
        {"httpMethod": "GET", "resource": "/health"},
        {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/prod/health"},
        {"httpMethod": "GET", "path": "/health"},
    ],
)
def test_health_does_not_require_auth_or_aws(event):
    result = process_lambda(event, None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"status": "ok"}
    assert result["headers"]["Content-Type"] == "application/json"


# ---------- DynamoDB types ----------

def test_dynamodb_writes_use_decimals_and_partial_reads_work(monkeypatch):
    monkeypatch.setenv("ANALYSIS_RESULTS_TABLE", "AnalysisResults")
    monkeypatch.setenv("BILLING_RECORDS_TABLE", "BillingRecords")
    monkeypatch.setenv("RECOMMENDATIONS_TABLE", "Recommendations")
    fake = FakeDynamo()
    repository = DynamoRepository(dynamodb_resource=fake)
    analysis = process_csv_file(ROOT / "data" / "sample_billing.csv", repository=repository)

    assert len(fake.tables["BillingRecords"].items) == 10
    assert len(fake.tables["AnalysisResults"].items) == 1
    assert len(fake.tables["Recommendations"].items) == 4
    for table in fake.tables.values():
        for item in table.items:
            _assert_no_float(item)

    stored = fake.tables["AnalysisResults"].items[0]
    assert stored["status"] == "COMPLETED"
    assert stored["errorMessage"] is None
    assert isinstance(stored["summary"]["totalCost"], Decimal)
    assert isinstance(fake.tables["BillingRecords"].items[0]["monthlyCost"], Decimal)
    assert isinstance(fake.tables["Recommendations"].items[0]["estimatedMonthlySavings"], Decimal)

    fake.tables["AnalysisResults"].items.append({
        "organizationId": "org-1",
        "analysisId": "analysis-queued0001",
        "status": "QUEUED",
        "errorMessage": None,
        "createdAt": "2026-11-22T00:00:00+00:00",
        "updatedAt": "2026-11-22T00:00:00+00:00",
    })
    partial = repository.get_analysis("org-1", "analysis-queued0001")
    assert partial["status"] == "QUEUED"
    assert partial["recommendations"] == []
    assert "currency" not in partial

    repository.save_status("org-1", "analysis-queued0001", "PROCESSING")
    update = fake.tables["AnalysisResults"].updates[-1]
    assert "if_not_exists(createdAt, :createdAt)" in update["UpdateExpression"]
    assert update["ExpressionAttributeNames"]["#status"] == "status"
    assert update["ExpressionAttributeValues"][":status"] == "PROCESSING"
    assert update["Key"] == {"organizationId": "org-1", "analysisId": "analysis-queued0001"}

    fetched = process_lambda(
        _analysis_event("GET", analysis["analysisId"], organization_id="org-demo"),
        None,
        repository=repository,
    )
    assert fetched["statusCode"] == 200
    payload = json.loads(fetched["body"])
    assert payload["summary"]["totalCost"] == 2840
    assert isinstance(payload["summary"]["totalCost"], float)
    assert payload["recommendations"]


def test_handler_imports_when_the_lambda_root_is_its_own_folder():
    folder = ROOT / "backend" / "functions" / "process_billing_file"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", "import handler; assert callable(handler.lambda_handler)"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr

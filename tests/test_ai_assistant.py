"""Workstream 4 tests: explanations, chatbot grounding, fallbacks, and organization isolation.

No AWS account needed: Bedrock and DynamoDB are replaced with small fakes.
Run from the repository's top folder:
    python3 -m pytest tests/test_ai_assistant.py -v
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend.functions.chatbot import chat
from backend.functions.chatbot.handler import lambda_handler
from backend.functions.process_billing_file.analytics import analyze_records
from backend.functions.process_billing_file.explanations import add_explanations, explain_recommendation
from backend.functions.process_billing_file.parser import load_billing_csv

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = analyze_records(load_billing_csv(ROOT / "data" / "sample_billing.csv"))


# ---------- fakes ----------

class FakeBedrock:
    """Returns a fixed reply and remembers what it was sent."""

    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.reply}]}}}


class BrokenBedrock:
    def converse(self, **kwargs):
        raise RuntimeError("Bedrock is down")


class FakeTable:
    def __init__(self, key_names):
        self.key_names = key_names
        self.items = []

    def put_item(self, Item):
        for value in Item.values():
            assert not isinstance(value, float), "DynamoDB rejects floats; use Decimal"
        self.items.append(Item)

    def get_item(self, Key):
        for item in self.items:
            if all(item.get(k) == v for k, v in Key.items()):
                return {"Item": copy.deepcopy(item)}
        return {}

    def query(self, KeyConditionExpression, ExpressionAttributeValues, ScanIndexForward=True, Limit=None):
        org = ExpressionAttributeValues[":org"]
        found = [copy.deepcopy(i) for i in self.items if i["organizationId"] == org]
        found.sort(key=lambda i: i[self.key_names[1]], reverse=not ScanIndexForward)
        return {"Items": found[:Limit] if Limit else found}


class FakeRepository:
    """Uses the real ChatRepository code against fake tables."""

    def __init__(self):
        from backend.functions.chatbot.handler import ChatRepository

        self.repo = ChatRepository.__new__(ChatRepository)
        self.repo.analysis_table = FakeTable(("organizationId", "analysisId"))
        self.repo.recommendation_table = FakeTable(("organizationId", "recommendationId"))
        self.repo.chat_table = FakeTable(("organizationId", "timestamp"))

    def store_analysis(self, analysis):
        item = {k: v for k, v in analysis.items() if k != "recommendations"}
        self.repo.analysis_table.items.append(_to_decimal(item))
        for rec in analysis["recommendations"]:
            self.repo.recommendation_table.items.append(_to_decimal(rec))

    def __getattr__(self, name):
        return getattr(self.repo, name)


def _to_decimal(value):
    """Mimic what DynamoDB returns (Decimal instead of float)."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def _event(method, path, org="org-demo", body=None, query=None):
    return {
        "httpMethod": method,
        "resource": path,
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
        "requestContext": {"authorizer": {"claims": {"custom:organizationId": org}}},
    }


# ---------- explanations ----------

def test_explanation_uses_bedrock_and_sends_only_validated_facts():
    rec = ANALYSIS["recommendations"][0]
    fake = FakeBedrock("This GPU is mostly idle.")
    text, status = explain_recommendation(rec, client=fake)
    assert (text, status) == ("This GPU is mostly idle.", "generated")
    sent = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert rec["issue"] in sent
    assert "organizationId" not in sent and "recommendationId" not in sent


def test_explanation_falls_back_when_bedrock_fails():
    rec = ANALYSIS["recommendations"][0]
    text, status = explain_recommendation(rec, client=BrokenBedrock())
    assert status == "fallback"
    assert rec["issue"] in text and rec["verificationStep"] in text


def test_add_explanations_fills_every_recommendation_without_new_fields():
    analysis = copy.deepcopy(ANALYSIS)
    add_explanations(analysis, client=FakeBedrock("Plain explanation."))
    for rec, original in zip(analysis["recommendations"], ANALYSIS["recommendations"]):
        assert rec["aiExplanation"] == "Plain explanation."
        assert set(rec) == set(original)  # schema has additionalProperties: false


# ---------- chatbot grounding ----------

def test_chat_sends_only_this_analysis_and_matches_response_schema():
    fake = FakeBedrock("gpu-research-001 is your highest-cost resource.")
    response = chat.answer_question("Which resources cost the most?", ANALYSIS, client=fake)
    system_text = fake.calls[0]["system"][0]["text"]
    assert "gpu-research-001" in system_text
    for field in ("conversationId", "analysisId", "answer", "confidence", "sources", "createdAt"):
        assert field in response
    assert response["citedResources"] == ["gpu-research-001"]
    assert any(s["sourceType"] == "RECOMMENDATION" for s in response["sources"])
    assert response["confidence"] == 0.9


def test_invented_numbers_lower_confidence_and_add_disclaimer():
    total = ANALYSIS["summary"]["totalCost"]
    fake = FakeBedrock(f"Your total is ${total:,.2f}, but you could save $999,999.")
    response = chat.answer_question("How much can I save?", ANALYSIS, client=fake)
    assert response["confidence"] == 0.6
    assert "do not appear directly" in response["disclaimer"]


def test_chat_falls_back_when_bedrock_fails():
    response = chat.answer_question("Which resources cost the most?", ANALYSIS, client=BrokenBedrock())
    assert response["confidence"] == 0.3
    assert response["modelUsed"] is None
    assert "gpu-research-001" in response["answer"]


@pytest.mark.parametrize("question", ["", "hi", "x" * 2001, None])
def test_invalid_questions_are_rejected(question):
    with pytest.raises(chat.ChatValidationError):
        chat.answer_question(question, ANALYSIS, client=FakeBedrock())


def test_history_is_sent_to_the_model():
    fake = FakeBedrock("ok")
    history = [{"question": "Which costs most?", "answer": "gpu-research-001."}]
    chat.answer_question("Why?", ANALYSIS, history=history, client=fake)
    roles = [m["role"] for m in fake.calls[0]["messages"]]
    assert roles == ["user", "assistant", "user"]


# ---------- Lambda handler ----------

def test_handler_answers_and_saves_history():
    repo = FakeRepository()
    repo.store_analysis(ANALYSIS)
    body = {"analysisId": ANALYSIS["analysisId"], "question": "Which resources cost the most?"}
    result = lambda_handler(_event("POST", "/chat", body=body), None, repository=repo,
                            client=FakeBedrock("gpu-research-001 costs the most."))
    assert result["statusCode"] == 200
    assert len(repo.chat_table.items) == 1

    history = lambda_handler(_event("GET", "/chat/history"), None, repository=repo)
    items = json.loads(history["body"])["items"]
    assert items[0]["question"] == "Which resources cost the most?"


def test_other_organization_cannot_read_this_analysis_or_history():
    repo = FakeRepository()
    repo.store_analysis(ANALYSIS)
    body = {"analysisId": ANALYSIS["analysisId"], "question": "Tell me about gpu-research-001"}
    lambda_handler(_event("POST", "/chat", body=body), None, repository=repo, client=FakeBedrock("ok"))

    fake = FakeBedrock("should not be called")
    result = lambda_handler(_event("POST", "/chat", org="org-other", body=body), None,
                            repository=repo, client=fake)
    assert result["statusCode"] == 404
    assert fake.calls == []  # the model never saw the other organization's data

    history = lambda_handler(_event("GET", "/chat/history", org="org-other"), None, repository=repo)
    assert json.loads(history["body"])["items"] == []


def test_organization_in_request_body_is_ignored():
    repo = FakeRepository()
    repo.store_analysis(ANALYSIS)
    body = {"analysisId": ANALYSIS["analysisId"], "question": "What is my total?", "organizationId": "org-demo"}
    result = lambda_handler(_event("POST", "/chat", org="org-other", body=body), None,
                            repository=repo, client=FakeBedrock("ok"))
    assert result["statusCode"] == 404


def test_missing_token_is_rejected():
    event = _event("POST", "/chat", body={"analysisId": "a", "question": "hello"})
    event["requestContext"] = {}
    result = lambda_handler(event, None, repository=FakeRepository(), client=FakeBedrock())
    assert result["statusCode"] == 401


def test_bad_json_is_rejected():
    event = _event("POST", "/chat")
    event["body"] = "{not json"
    result = lambda_handler(event, None, repository=FakeRepository(), client=FakeBedrock())
    assert result["statusCode"] == 400

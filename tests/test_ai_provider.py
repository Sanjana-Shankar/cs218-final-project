"""OpenAI provider tests. No network and no AWS: HTTP and SSM are fakes."""

from __future__ import annotations

import copy
import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from backend.functions.chatbot import ai_provider as chatbot_provider
from backend.functions.chatbot import chat
from backend.functions.process_billing_file import ai_provider
from backend.functions.process_billing_file.analytics import analyze_records
from backend.functions.process_billing_file.explanations import add_explanations
from backend.functions.process_billing_file.parser import load_billing_csv

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = analyze_records(load_billing_csv(ROOT / "data" / "sample_billing.csv"))
PROCESSING_PROVIDER = ROOT / "backend" / "functions" / "process_billing_file" / "ai_provider.py"
CHATBOT_PROVIDER = ROOT / "backend" / "functions" / "chatbot" / "ai_provider.py"


class _Response:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSSM:
    def __init__(self, value="sk-from-ssm"):
        self.value = value
        self.calls = []

    def get_parameter(self, **kwargs):
        self.calls.append(kwargs)
        return {"Parameter": {"Value": self.value}}


def _openai_payload(text="OpenAI says the GPU is idle."):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    for name in (
        "AI_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_PARAMETER",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_TIMEOUT_SECONDS",
        "BEDROCK_MODEL_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    ai_provider._cached_api_key = None
    chatbot_provider._cached_api_key = None
    yield
    ai_provider._cached_api_key = None
    chatbot_provider._cached_api_key = None


def test_provider_copies_are_identical():
    assert PROCESSING_PROVIDER.read_bytes() == CHATBOT_PROVIDER.read_bytes()


def test_default_provider_is_bedrock():
    assert ai_provider.provider() == "bedrock"
    assert ai_provider.model_id() == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_bedrock_model_id_can_be_overridden(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "custom-bedrock-model")
    assert ai_provider.model_id() == "custom-bedrock-model"


def test_openai_provider_selects_openai_model_and_client(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "OpenAI")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")
    assert ai_provider.provider() == "openai"
    assert ai_provider.model_id() == "gpt-test-model"
    assert isinstance(ai_provider.get_client(), ai_provider.OpenAIConverseClient)


def test_openai_model_defaults_to_gpt_4o_mini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert ai_provider.model_id() == "gpt-4o-mini"


def test_invalid_provider_raises(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "azure")
    with pytest.raises(ValueError):
        ai_provider.provider()


def test_invalid_provider_falls_back(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "azure")
    monkeypatch.setenv("ENABLE_AI_EXPLANATIONS", "true")
    analysis = copy.deepcopy(ANALYSIS)
    add_explanations(analysis)
    first = analysis["recommendations"][0]
    assert first["issue"] in first["aiExplanation"]
    response = chat.answer_question("Which resources cost the most?", ANALYSIS)
    assert response["confidence"] == 0.3
    assert response["modelUsed"] is None


def test_env_api_key_wins_over_ssm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_API_KEY_PARAMETER", "/cloud-cost-optimizer/openai-api-key")
    ssm = _FakeSSM()
    assert ai_provider._resolve_api_key(ssm_client=ssm) == "sk-env"
    assert ssm.calls == []


def test_ssm_key_is_decrypted_and_cached(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY_PARAMETER", "/cloud-cost-optimizer/openai-api-key")
    ssm = _FakeSSM("sk-from-ssm")
    assert ai_provider._resolve_api_key(ssm_client=ssm) == "sk-from-ssm"
    assert ai_provider._resolve_api_key(ssm_client=ssm) == "sk-from-ssm"
    assert ssm.calls == [
        {"Name": "/cloud-cost-optimizer/openai-api-key", "WithDecryption": True}
    ]


def test_missing_api_key_raises():
    with pytest.raises(RuntimeError, match="OpenAI API key is not configured"):
        ai_provider._resolve_api_key(ssm_client=_FakeSSM())


def test_converse_translates_request_and_response(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "9")
    captured = {}

    def urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(_openai_payload("Because the GPU is idle."))

    client = ai_provider.OpenAIConverseClient(api_key="sk-test-key", urlopen=urlopen)
    result = client.converse(
        modelId="gpt-4o-mini",
        system=[{"text": "Be brief."}, {"text": "Use only the facts."}],
        messages=[
            {"role": "user", "content": [{"text": "Hello"}, {"text": "there"}]},
            {"role": "assistant", "content": [{"text": "Hi"}]},
            {"role": "user", "content": [{"text": "Why?"}]},
        ],
        inferenceConfig={"maxTokens": 300, "temperature": 0.2},
    )

    request = captured["request"]
    assert request.full_url == "https://example.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-test-key"
    assert request.get_method() == "POST"
    assert captured["timeout"] == 9.0
    body = json.loads(request.data.decode())
    assert body["model"] == "gpt-4o-mini"
    assert body["max_completion_tokens"] == 300
    assert body["temperature"] == 0.2
    assert body["messages"] == [
        {"role": "system", "content": "Be brief.\nUse only the facts."},
        {"role": "user", "content": "Hello\nthere"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Why?"},
    ]
    assert result == {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "Because the GPU is idle."}],
            }
        },
        "stopReason": "stop",
        "usage": {"inputTokens": 12, "outputTokens": 7},
    }
    assert "sk-test-key" not in json.dumps({k: v for k, v in result.items()})


def test_http_error_hides_the_api_key():
    secret = "sk-live-secret-value"

    def urlopen(request, timeout=None):
        body = json.dumps({"error": {"message": "Incorrect API key provided"}}).encode()
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            Message(),
            io.BytesIO(body),
        )

    client = ai_provider.OpenAIConverseClient(api_key=secret, urlopen=urlopen)
    with pytest.raises(RuntimeError) as exc_info:
        client.converse(
            modelId="gpt-4o-mini",
            system=[{"text": "Be brief."}],
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
            inferenceConfig={"maxTokens": 20, "temperature": 0.1},
        )
    message = str(exc_info.value)
    assert message == "OpenAI request failed (401): Incorrect API key provided"
    assert secret not in message
    assert "Authorization" not in message
    assert exc_info.value.__cause__ is None


def test_openai_fills_explanations_and_answers(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")

    def urlopen(request, timeout=None):
        assert request.get_header("Authorization") == "Bearer sk-test-key"
        body = json.loads(request.data.decode())
        assert body["model"] == "gpt-test-model"
        return _Response(_openai_payload("OpenAI says the GPU is idle."))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    analysis = copy.deepcopy(ANALYSIS)
    add_explanations(analysis)
    assert analysis["recommendations"]
    for recommendation in analysis["recommendations"]:
        assert recommendation["aiExplanation"] == "OpenAI says the GPU is idle."

    response = chat.answer_question("Which resources cost the most?", ANALYSIS)
    assert response["answer"] == "OpenAI says the GPU is idle."
    assert response["modelUsed"] == "gpt-test-model"
    assert response["confidence"] == 0.9


def test_openai_failures_fall_back(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")

    def urlopen(request, timeout=None):
        body = json.dumps({"error": {"message": "server exploded"}}).encode()
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "Error",
            Message(),
            io.BytesIO(body),
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    analysis = copy.deepcopy(ANALYSIS)
    add_explanations(analysis)
    first = analysis["recommendations"][0]
    assert first["issue"] in first["aiExplanation"]
    assert "server exploded" not in first["aiExplanation"]

    response = chat.answer_question("Which resources cost the most?", ANALYSIS)
    assert response["confidence"] == 0.3
    assert response["modelUsed"] is None
    assert "temporarily unavailable" in response["answer"]
    assert "gpu-research-001" in response["answer"]

"""Select Amazon Bedrock or OpenAI behind one Bedrock-shaped converse() client.

AI_PROVIDER defaults to bedrock. Set it to openai to call OpenAI instead.
Each Lambda is packaged from its own folder, so this file is copied into both
function directories and the two copies must stay identical.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_VALID_PROVIDERS = ("bedrock", "openai")

# SSM lookups are cached after the first success. An OPENAI_API_KEY in the
# environment is checked on every call and is never cached, so it always wins.
_cached_api_key: str | None = None


def provider() -> str:
    selected = os.getenv("AI_PROVIDER", "bedrock").lower()
    if selected not in _VALID_PROVIDERS:
        raise ValueError(f"Unsupported AI_PROVIDER: {selected}")
    return selected


def model_id() -> str:
    if provider() == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return os.getenv("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID)


def get_client():
    if provider() == "openai":
        return OpenAIConverseClient()
    import boto3

    return boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _resolve_api_key(ssm_client=None) -> str:
    """Return the OpenAI API key from the environment, or from SSM once."""
    global _cached_api_key
    env_key = os.getenv("OPENAI_API_KEY")
    if env_key:
        return env_key
    if _cached_api_key:
        return _cached_api_key
    parameter_name = os.getenv("OPENAI_API_KEY_PARAMETER")
    if not parameter_name:
        raise RuntimeError("OpenAI API key is not configured.")
    if ssm_client is None:
        import boto3

        ssm_client = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-east-1"))
    response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
    value = (response.get("Parameter") or {}).get("Value") or ""
    if not value:
        raise RuntimeError("OpenAI API key is not configured.")
    _cached_api_key = value
    return value


def _joined_text(blocks) -> str:
    parts: list[str] = []
    for block in blocks or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("text") is not None:
            parts.append(str(block["text"]))
    return "\n".join(parts)


def _error_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return text or "unknown error"
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return text or "unknown error"


def _without_secret(message: str, api_key: str) -> str:
    if api_key and api_key in message:
        return message.replace(api_key, "[redacted]")
    return message


class OpenAIConverseClient:
    """Calls OpenAI chat completions and returns a Bedrock converse() shape."""

    def __init__(self, api_key=None, urlopen=None):
        self._api_key = api_key
        self._urlopen = urlopen

    def converse(self, modelId, system, messages, inferenceConfig):
        api_key = self._api_key or _resolve_api_key()
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured.")
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
        body = {
            "model": modelId,
            "messages": [{"role": "system", "content": _joined_text(system)}]
            + [
                {"role": message["role"], "content": _joined_text(message.get("content"))}
                for message in messages
            ],
            "max_completion_tokens": inferenceConfig["maxTokens"],
            "temperature": inferenceConfig["temperature"],
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = self._urlopen or urllib.request.urlopen
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = b""
            if exc.fp is not None:
                detail = exc.read()
            message = _without_secret(_error_text(detail), api_key)
            raise RuntimeError(f"OpenAI request failed ({exc.code}): {message}") from None
        payload = json.loads(raw.decode("utf-8"))
        choice = payload["choices"][0]
        usage = payload.get("usage") or {}
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": choice["message"]["content"]}],
                }
            },
            "stopReason": choice.get("finish_reason"),
            "usage": {
                "inputTokens": usage.get("prompt_tokens"),
                "outputTokens": usage.get("completion_tokens"),
            },
        }

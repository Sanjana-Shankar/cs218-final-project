"""Workstream 4: Bedrock plain-language explanations for rule-based recommendations.

Each recommendation produced by analytics.py already contains validated facts
(issue, action, savings, risks, verification step, evidence). This module asks
Amazon Bedrock to rewrite those facts for a non-expert and stores the result in
the recommendation's existing ``aiExplanation`` field.

If Bedrock fails for any reason, a template explanation is used instead, so the
analysis still completes and the dashboard still works.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

try:
    from . import ai_provider
except ImportError:  # pragma: no cover
    import ai_provider

EXPLANATION_SYSTEM_PROMPT = """You explain cloud cost findings to people who are not cloud experts.

Rules:
- Use ONLY the facts and numbers in the finding. Never invent numbers.
- Call savings "estimated" or "potential", never guaranteed.
- Write 3 to 4 short sentences: what the problem is, what to do, one risk to consider,
  and one thing to check before acting.
- Do not say any action has been taken. You only make suggestions.
- Plain text only: no headings, bullet points, or markdown symbols."""

# Only these fields are sent to the model: validated facts, no identifiers it doesn't need.
FIELDS_FOR_MODEL = (
    "resourceId",
    "service",
    "resourceType",
    "project",
    "team",
    "category",
    "severity",
    "issue",
    "action",
    "estimatedMonthlySavings",
    "savingsCurrency",
    "performanceRisk",
    "reliabilityRisk",
    "verificationStep",
    "evidence",
)


def _model_id() -> str:
    return ai_provider.model_id()


def _bedrock_client():
    return ai_provider.get_client()


def fallback_explanation(recommendation: dict) -> str:
    """Template explanation used when Bedrock is unavailable."""
    savings = float(recommendation.get("estimatedMonthlySavings", 0))
    return (
        f"{recommendation['issue']} Suggested action: {recommendation['action']} "
        f"Estimated potential savings: ${savings:.2f} per month. "
        f"Before acting: {recommendation['verificationStep']}"
    )


def explain_recommendation(recommendation: dict, client=None) -> tuple[str, str]:
    """Return (explanation_text, status) where status is 'generated' or 'fallback'."""
    facts = {key: recommendation[key] for key in FIELDS_FOR_MODEL if key in recommendation}
    try:
        client = client or _bedrock_client()
        response = client.converse(
            modelId=_model_id(),
            system=[{"text": EXPLANATION_SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Finding:\n" + json.dumps(facts, indent=2, default=float)}],
                }
            ],
            inferenceConfig={"maxTokens": 300, "temperature": 0.2},
        )
        text = response["output"]["message"]["content"][0]["text"].strip()
        if not text:
            raise ValueError("Bedrock returned an empty explanation.")
        return text, "generated"
    except Exception:
        logger.exception("Bedrock explanation failed for %s; using fallback", recommendation.get("resourceId"))
        return fallback_explanation(recommendation), "fallback"


def add_explanations(analysis: dict, client=None) -> dict:
    """Fill ``aiExplanation`` on every recommendation in an analysis (in place) and return it."""
    if os.getenv("ENABLE_AI_EXPLANATIONS", "true").lower() != "true":
        return analysis
    if analysis.get("recommendations"):
        client = client or _safe_client()
    for recommendation in analysis.get("recommendations", []):
        # The schema allows only aiExplanation (no extra fields), so the status goes to the logs.
        text, status = explain_recommendation(recommendation, client=client)
        recommendation["aiExplanation"] = text
        logger.info("aiExplanation for %s: %s", recommendation.get("resourceId"), status)
    return analysis


def _safe_client():
    """Create the Bedrock client, or a stand-in that fails so each call falls back cleanly."""
    try:
        return _bedrock_client()
    except Exception:
        logger.exception("Could not create Bedrock client")
        return _FailingClient()


class _FailingClient:
    def converse(self, **_kwargs):
        raise RuntimeError("Bedrock client unavailable")

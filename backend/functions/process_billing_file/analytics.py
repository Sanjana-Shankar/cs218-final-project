"""Deterministic cost aggregation, anomaly detection, and recommendation rules."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4


def money(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _breakdown(records: list[dict], field: str, total: float) -> list[dict]:
    values: dict[str, float] = defaultdict(float)
    for record in records:
        values[record[field]] += record["monthlyCost"]
    return [
        {
            "name": name,
            "cost": money(cost),
            "percentageOfTotal": money((cost / total * 100) if total else 0),
        }
        for name, cost in sorted(values.items(), key=lambda item: item[1], reverse=True)
    ]


def _period_totals(records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for record in records:
        totals[record["usageDate"][:7]] += record["monthlyCost"]
    return dict(sorted(totals.items()))

def _recommendation(
    record: dict,
    *,
    analysis_id: str,
    category: str,
    severity: str,
    rule_id: str,
    savings_rate: float,
    issue: str,
    action: str,
    performance_risk: str,
    reliability_risk: str,
    verification_step: str,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "recommendationId": f"rec-{uuid4().hex[:12]}",
        "organizationId": record["organizationId"],
        "analysisId": analysis_id,
        "category": category,
        "severity": severity,
        "status": "NEW",
        "resourceId": record["resourceId"],
        "resourceType": record["resourceType"],
        "service": record["service"],
        "project": record["project"],
        "team": record["team"],
        "issue": issue,
        "action": action,
        "estimatedMonthlySavings": money(record["monthlyCost"] * savings_rate),
        "savingsCurrency": record["currency"],
        "performanceRisk": performance_risk,
        "reliabilityRisk": reliability_risk,
        "verificationStep": verification_step,
        "confidence": 0.9,
        "ruleId": rule_id,
        "evidence": {
            "monthlyCost": record["monthlyCost"],
            "utilizationPercent": record["utilizationPercent"],
            "usageHours": record["usageHours"],
            "storageGb": record["storageGb"],
            "accessFrequency": record["accessFrequency"],
        },
        "aiExplanation": None,
        "createdAt": now,
        "updatedAt": None,
    }


def detect_recommendations(records: list[dict], analysis_id: str) -> list[dict]:
    """Apply deterministic rules to the latest record for each resource."""

    latest_by_resource: dict[str, dict] = {}
    for record in sorted(records, key=lambda item: item["usageDate"]):
        latest_by_resource[record["resourceId"]] = record

    recommendations: list[dict] = []
    for record in latest_by_resource.values():
        resource_type = record["resourceType"]
        utilization = record["utilizationPercent"]
        cost = record["monthlyCost"]

        if "gpu" in resource_type and record["usageHours"] > 650 and utilization < 20:
            recommendations.append(
                _recommendation(
                    record,
                    analysis_id=analysis_id,
                    category="UNDERUTILIZED_GPU",
                    severity="CRITICAL",
                    rule_id="always-on-gpu-under-20-percent",
                    savings_rate=0.60,
                    issue=f"The GPU ran for {record['usageHours']:.1f} hours with {utilization:.1f}% utilization.",
                    action="Schedule the GPU for expected training windows or stop it when idle.",
                    performance_risk="Scheduled execution may delay jobs submitted outside the planned window.",
                    reliability_risk="Stopping an active training job may lose progress if checkpoints are not configured.",
                    verification_step="Confirm checkpointing, job schedules, and active workloads before changing it.",
                )
            )
        elif record["service"].lower() == "ec2" and utilization < 10 and cost > 50:
            recommendations.append(
                _recommendation(
                    record,
                    analysis_id=analysis_id,
                    category="IDLE_COMPUTE",
                    severity="HIGH",
                    rule_id="idle-ec2-under-10-percent",
                    savings_rate=0.95,
                    issue=f"The compute resource averaged {utilization:.1f}% utilization.",
                    action="Stop or schedule the instance when it is not needed, or evaluate a smaller instance.",
                    performance_risk="Stopping the instance may interrupt workloads that still require it.",
                    reliability_risk="Scheduled shutdowns may affect availability if workload windows are misunderstood.",
                    verification_step="Confirm workload schedules, dependencies, and recovery procedures before changing it.",
                )
            )
        elif record["service"].lower() == "ec2" and utilization < 25 and cost > 200:
            recommendations.append(
                _recommendation(
                    record,
                    analysis_id=analysis_id,
                    category="OVERSIZED_COMPUTE",
                    severity="MEDIUM",
                    rule_id="oversized-ec2-under-25-percent",
                    savings_rate=0.40,
                    issue=f"The instance costs ${cost:.2f} per period but averages only {utilization:.1f}% utilization.",
                    action="Evaluate a smaller instance type using recent CPU, memory, network, and latency metrics.",
                    performance_risk="A smaller instance may increase response time or cause resource contention.",
                    reliability_risk="Insufficient capacity could cause errors during workload peaks.",
                    verification_step="Review peak utilization and load-test a smaller instance before changing production.",
                )
            )

        if record["service"].lower() == "s3" and record["accessFrequency"] == "rare" and record["storageGb"] > 100:
            recommendations.append(
                _recommendation(
                    record,
                    analysis_id=analysis_id,
                    category="RARELY_ACCESSED_STORAGE",
                    severity="MEDIUM",
                    rule_id="rare-s3-storage-over-100gb",
                    savings_rate=0.30,
                    issue=f"The storage contains {record['storageGb']:.1f} GB and is marked as rarely accessed.",
                    action="Apply a lifecycle policy or evaluate a lower-cost storage class.",
                    performance_risk="Retrieval may be slower or incur additional access charges.",
                    reliability_risk="Incorrect lifecycle rules could make frequently needed data harder to retrieve.",
                    verification_step="Confirm retention, retrieval, and compliance requirements before changing storage class.",
                )
            )

    return sorted(
        recommendations,
        key=lambda item: (item["severity"] not in {"CRITICAL", "HIGH"}, -item["estimatedMonthlySavings"]),
    )

def detect_anomalies(records: list[dict]) -> list[dict]:
    periods = _period_totals(records)
    if len(periods) < 2:
        return []
    ordered = list(periods.items())
    anomalies = []
    for (previous_period, previous_cost), (current_period, current_cost) in zip(ordered, ordered[1:]):
        if previous_cost > 0 and current_cost > previous_cost * 1.20:
            change = (current_cost - previous_cost) / previous_cost * 100
            anomalies.append(
                {
                    "anomalyId": f"anomaly-{current_period}",
                    "type": "COST_SPIKE",
                    "severity": "HIGH" if change >= 50 else "MEDIUM",
                    "message": f"Cost increased {change:.1f}% from {previous_period} to {current_period}.",
                    "observedValue": money(current_cost),
                    "expectedValue": money(previous_cost),
                    "resourceId": None,
                }
            )
    return anomalies


def analyze_records(records: list[dict], analysis_id: str | None = None) -> dict:
    """Return a dashboard-ready analysis response."""

    if not records:
        raise ValueError("At least one normalized billing record is required.")
    analysis_id = analysis_id or f"analysis-{uuid4().hex[:12]}"
    total = money(sum(record["monthlyCost"] for record in records))
    periods = _period_totals(records)
    dates = [record["usageDate"] for record in records]
    recommendations = detect_recommendations(records, analysis_id)
    anomalies = detect_anomalies(records)
    previous_cost = None
    percent_change = None
    if len(periods) >= 2:
        values = list(periods.values())
        previous_cost = money(values[-2])
        if previous_cost:
            percent_change = money((values[-1] - values[-2]) / previous_cost * 100)

    daily = defaultdict(float)
    resource_costs = defaultdict(float)
    resource_metadata = {}
    for record in records:
        daily[record["usageDate"]] += record["monthlyCost"]
        resource_costs[record["resourceId"]] += record["monthlyCost"]
        resource_metadata[record["resourceId"]] = record

    top_resources = []
    for resource_id, cost in sorted(resource_costs.items(), key=lambda item: item[1], reverse=True)[:10]:
        record = resource_metadata[resource_id]
        top_resources.append(
            {
                "resourceId": resource_id,
                "service": record["service"],
                "resourceType": record["resourceType"],
                "cost": money(cost),
                "utilizationPercent": record["utilizationPercent"],
            }
        )

    return {
        "analysisId": analysis_id,
        "organizationId": records[0]["organizationId"],
        "status": "COMPLETED",
        "errorMessage": None,
        "currency": records[0]["currency"],
        "period": {"startDate": min(dates), "endDate": max(dates)},
        "summary": {
            "totalCost": total,
            "estimatedSavings": money(sum(item["estimatedMonthlySavings"] for item in recommendations)),
            "recommendationCount": len(recommendations),
            "anomalyCount": len(anomalies),
            "recordCount": len(records),
            "previousPeriodCost": previous_cost,
            "percentChangeFromPreviousPeriod": percent_change,
        },
        "serviceCosts": _breakdown(records, "service", total),
        "projectCosts": _breakdown(records, "project", total),
        "teamCosts": _breakdown(records, "team", total),
        "dailyCosts": [
            {"date": day, "cost": money(cost)} for day, cost in sorted(daily.items())
        ],
        "topResources": top_resources,
        "recommendations": recommendations,
        "anomalies": anomalies,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": None,
    }
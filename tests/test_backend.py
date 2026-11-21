from pathlib import Path

import pytest

from backend.analytics import analyze_records
from backend.errors import BillingValidationError
from backend.parser import load_billing_csv, parse_billing_rows
from backend.pipeline import process_csv_file
from backend.storage import InMemoryRepository


ROOT = Path(__file__).resolve().parents[1]


def test_valid_sample_is_parsed_and_normalized():
    records = load_billing_csv(ROOT / "sample_billing.csv")
    assert len(records) == 10
    assert records[0]["resourceType"] == "small-instance"
    assert records[0]["monthlyCost"] == 144.00
    assert records[0]["currency"] == "USD"


def test_invalid_sample_returns_useful_validation_error():
    with pytest.raises(BillingValidationError) as error:
        load_billing_csv(ROOT / "sample_billing_invalid.csv")
    assert "usageDate" in str(error.value)


def test_missing_required_column_is_rejected():
    rows = [{"organizationId": "org-demo", "usageDate": "2026-11-01"}]
    with pytest.raises(BillingValidationError) as error:
        parse_billing_rows(rows)
    assert "service" in str(error.value)


def test_sample_generates_expected_costs_and_recommendations():
    analysis = process_csv_file(ROOT / "sample_billing.csv")
    assert analysis["summary"]["totalCost"] == 2840.00
    assert analysis["summary"]["recommendationCount"] == 4
    assert analysis["summary"]["anomalyCount"] == 0
    categories = {item["category"] for item in analysis["recommendations"]}
    assert categories == {
        "IDLE_COMPUTE",
        "OVERSIZED_COMPUTE",
        "RARELY_ACCESSED_STORAGE",
        "UNDERUTILIZED_GPU",
    }


def test_cost_breakdowns_and_top_resource_are_sorted():
    analysis = process_csv_file(ROOT / "sample_billing.csv")
    assert analysis["serviceCosts"][0]["name"] == "EC2"
    assert analysis["topResources"][0]["resourceId"] == "gpu-research-001"
    assert analysis["topResources"][0]["cost"] == 1800.00


def test_cost_spike_anomaly_is_detected():
    records = load_billing_csv(ROOT / "sample_billing.csv")
    records[-1]["monthlyCost"] = 100.00
    records[-1]["usageDate"] = "2026-11-01"
    records[-2]["monthlyCost"] = 1300.00
    records[-2]["usageDate"] = "2026-11-01"
    analysis = analyze_records(records)
    # The sample's two periods are normally equal; this change makes the
    # November total larger than October by enough to trigger the rule.
    assert analysis["summary"]["anomalyCount"] == 1
    assert analysis["anomalies"][0]["type"] == "COST_SPIKE"


def test_in_memory_repository_supports_local_contract_testing():
    repository = InMemoryRepository()
    analysis = process_csv_file(ROOT / "sample_billing.csv", repository=repository)
    stored = repository.get_analysis("org-demo", analysis["analysisId"])
    assert stored["summary"]["totalCost"] == 2840.00
    assert len(repository.records[("org-demo", analysis["analysisId"])]) == 10

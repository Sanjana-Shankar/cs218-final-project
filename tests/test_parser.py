"""Parser behavior that tests/test_backend.py does not already cover."""

from pathlib import Path

import pytest

from backend.functions.process_billing_file.errors import BillingValidationError
from backend.functions.process_billing_file.parser import load_billing_csv, parse_billing_rows

ROOT = Path(__file__).resolve().parents[1]


def billing_row(**overrides):
    row = {
        "organizationId": "org-demo",
        "usageDate": "2026-10-01",
        "service": "EC2",
        "resourceId": "i-1",
        "resourceType": "small-instance",
        "project": "Website",
        "team": "Engineering",
        "region": "us-east-1",
        "usageHours": "1.5",
        "usageAmount": "2",
        "monthlyCost": "10.126",
        "currency": "usd",
        "utilizationPercent": "40",
        "storageGb": "0",
        "accessFrequency": "High",
    }
    for key, value in overrides.items():
        if value is None:
            row.pop(key, None)
        else:
            row[key] = value
    return row


def test_row_is_normalized():
    record = parse_billing_rows([
        billing_row(resourceType="  GPU_Instance ", service="  EC2  ")
    ])[0]
    assert record["usageDate"] == "2026-10-01"
    assert record["resourceType"] == "gpu-instance"
    assert record["service"] == "EC2"
    assert record["currency"] == "USD"
    assert record["accessFrequency"] == "high"
    assert record["monthlyCost"] == 10.13
    assert record["usageHours"] == 1.5
    assert record["utilizationPercent"] == 40


def test_missing_currency_defaults_to_usd():
    omitted = parse_billing_rows([billing_row(currency=None)])[0]
    empty = parse_billing_rows([billing_row(currency="")])[0]
    assert omitted["currency"] == "USD"
    assert empty["currency"] == "USD"


def test_spaces_and_underscores_in_resource_type_become_hyphens():
    record = parse_billing_rows([billing_row(resourceType="Small   Instance")])[0]
    assert record["resourceType"] == "small-instance"


@pytest.mark.parametrize(
    ("field", "value", "snippet"),
    [
        ("usageHours", "n/a", "usageHours"),
        ("monthlyCost", "-1", "negative"),
        ("utilizationPercent", "140", "utilizationPercent"),
        ("currency", "US", "currency"),
        ("accessFrequency", "weekly", "accessFrequency"),
        ("usageDate", "11/01/2026", "usageDate"),
    ],
)
def test_bad_values_are_rejected_with_the_field_name(field, value, snippet):
    with pytest.raises(BillingValidationError) as error:
        parse_billing_rows([billing_row(**{field: value})])
    assert snippet in str(error.value)


def test_utilization_of_100_and_zero_cost_are_accepted():
    record = parse_billing_rows([
        billing_row(utilizationPercent="100", monthlyCost="0", storageGb="0")
    ])[0]
    assert record["utilizationPercent"] == 100
    assert record["monthlyCost"] == 0


def test_a_file_must_contain_one_organization():
    rows = [
        billing_row(organizationId="org-a", resourceId="i-a"),
        billing_row(organizationId="org-b", resourceId="i-b"),
    ]
    with pytest.raises(BillingValidationError) as error:
        parse_billing_rows(rows)
    assert "exactly one organization" in str(error.value)


def test_header_only_file_is_rejected():
    with pytest.raises(BillingValidationError) as error:
        parse_billing_rows([])
    assert "does not contain any data" in str(error.value)


def test_load_rejects_missing_column_missing_header_and_missing_file(tmp_path):
    missing_column = tmp_path / "missing.csv"
    missing_column.write_text("organizationId,usageDate\norg-demo,2026-10-01\n", encoding="utf-8")
    with pytest.raises(BillingValidationError) as error:
        load_billing_csv(missing_column)
    assert "service" in str(error.value)

    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(BillingValidationError) as error:
        load_billing_csv(empty)
    assert "header" in str(error.value)

    with pytest.raises(BillingValidationError) as error:
        load_billing_csv(tmp_path / "nope.csv")
    assert "not found" in str(error.value)


def test_sample_invalid_file_names_the_bad_date():
    with pytest.raises(BillingValidationError) as error:
        load_billing_csv(ROOT / "data" / "sample_billing_invalid.csv")
    message = str(error.value)
    assert "usageDate" in message
    assert "YYYY-MM-DD" in message

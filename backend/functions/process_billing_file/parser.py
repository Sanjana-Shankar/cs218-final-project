"""CSV parsing and normalization for the shared billing schema."""
# Validates and normalizes billing CSV records 

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from .errors import BillingValidationError

REQUIRED_COLUMNS = {
    "organizationId",
    "usageDate",
    "service",
    "resourceId",
    "resourceType",
    "project",
    "team",
    "region",
    "usageHours",
    "usageAmount",
    "monthlyCost",
    "utilizationPercent",
    "storageGb",
    "accessFrequency",
}

NUMERIC_FIELDS = {
    "usageHours": float,
    "usageAmount": float,
    "monthlyCost": float,
    "utilizationPercent": float,
    "storageGb": float,
}

ALLOWED_ACCESS_FREQUENCIES = {"high", "medium", "low", "rare", "unknown"}


def _required_text(row: Mapping[str, str], field: str, row_number: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise BillingValidationError(f"Row {row_number}: '{field}' is required.")
    return value


def _number(row: Mapping[str, str], field: str, row_number: int) -> float:
    raw = (row.get(field) or "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise BillingValidationError(
            f"Row {row_number}: '{field}' must be numeric; received {raw!r}."
        ) from exc
    if value < 0:
        raise BillingValidationError(f"Row {row_number}: '{field}' cannot be negative.")
    if field == "utilizationPercent" and value > 100:
        raise BillingValidationError(
            f"Row {row_number}: 'utilizationPercent' must be between 0 and 100."
        )
    return value


def _normalize_resource_type(value: str) -> str:
    return "-".join(value.lower().strip().replace("_", " ").split())


def parse_billing_rows(rows: Iterable[Mapping[str, str]]) -> list[dict]:
    """Validate and normalize rows from the project billing CSV schema."""

    normalized: list[dict] = []
    organization_ids: set[str] = set()

    for row_number, row in enumerate(rows, start=2):
        if not row:
            raise BillingValidationError(f"Row {row_number}: empty row.")

        organization_id = _required_text(row, "organizationId", row_number)
        usage_date_raw = _required_text(row, "usageDate", row_number)
        try:
            usage_date = date.fromisoformat(usage_date_raw).isoformat()
        except ValueError as exc:
            raise BillingValidationError(
                f"Row {row_number}: 'usageDate' must use YYYY-MM-DD format."
            ) from exc

        access_frequency = _required_text(row, "accessFrequency", row_number).lower()
        if access_frequency not in ALLOWED_ACCESS_FREQUENCIES:
            raise BillingValidationError(
                f"Row {row_number}: unsupported accessFrequency {access_frequency!r}."
            )

        currency = (row.get("currency") or "USD").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise BillingValidationError(
                f"Row {row_number}: 'currency' must be a three-letter code.")

        record = {
            "organizationId": organization_id,
            "usageDate": usage_date,
            "service": _required_text(row, "service", row_number),
            "resourceId": _required_text(row, "resourceId", row_number),
            "resourceType": _normalize_resource_type(
                _required_text(row, "resourceType", row_number)
            ),
            "project": _required_text(row, "project", row_number),
            "team": _required_text(row, "team", row_number),
            "region": _required_text(row, "region", row_number),
            "usageHours": _number(row, "usageHours", row_number),
            "usageAmount": _number(row, "usageAmount", row_number),
            "monthlyCost": round(_number(row, "monthlyCost", row_number), 2),
            "currency": currency,
            "utilizationPercent": _number(row, "utilizationPercent", row_number),
            "storageGb": _number(row, "storageGb", row_number),
            "accessFrequency": access_frequency,
        }
        organization_ids.add(organization_id)
        normalized.append(record)

    if not normalized:
        raise BillingValidationError("The billing file does not contain any data rows.")
    if len(organization_ids) != 1:
        raise BillingValidationError(
            "A billing file must contain records for exactly one organization."
        )
    return normalized


def load_billing_csv(file_path: str | Path) -> list[dict]:
    """Load, validate, and normalize a CSV file from disk."""

    path = Path(file_path)
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames:
                raise BillingValidationError("The billing file is missing a header row.")
            missing = REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                missing_names = ", ".join(sorted(missing))
                raise BillingValidationError(
                    f"The billing file is missing required columns: {missing_names}."
                )
            return parse_billing_rows(reader)
    except FileNotFoundError as exc:
        raise BillingValidationError(f"Billing file not found: {path}") from exc

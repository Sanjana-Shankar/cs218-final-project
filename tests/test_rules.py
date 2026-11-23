"""Each recommendation rule fires only for the record shape analytics.py describes."""

from backend.functions.process_billing_file.analytics import detect_recommendations


def record(**overrides):
    base = {
        "organizationId": "org-demo",
        "usageDate": "2026-11-01",
        "service": "EC2",
        "resourceId": "res-1",
        "resourceType": "small-instance",
        "project": "Website",
        "team": "Engineering",
        "region": "us-east-1",
        "usageHours": 24.0,
        "usageAmount": 24.0,
        "monthlyCost": 80.0,
        "currency": "USD",
        "utilizationPercent": 50.0,
        "storageGb": 0.0,
        "accessFrequency": "high",
    }
    base.update(overrides)
    return base


def categories(*records):
    found = detect_recommendations(list(records), "analysis-rules0001")
    return [item["category"] for item in found]


def test_idle_compute():
    # EC2, utilization under 10 percent, cost over $50. Not a GPU.
    assert categories(record(utilizationPercent=4, monthlyCost=80)) == ["IDLE_COMPUTE"]


def test_oversized_compute():
    # Utilization is at least 10, so the idle rule does not take this branch.
    assert categories(record(
        resourceType="large-instance",
        utilizationPercent=18,
        monthlyCost=300,
    )) == ["OVERSIZED_COMPUTE"]


def test_rarely_accessed_storage():
    assert categories(record(
        service="S3",
        resourceType="object-storage",
        accessFrequency="rare",
        storageGb=250,
        monthlyCost=40,
        utilizationPercent=0,
        usageHours=0,
    )) == ["RARELY_ACCESSED_STORAGE"]


def test_underutilized_gpu():
    assert categories(record(
        resourceType="gpu-instance",
        usageHours=700,
        utilizationPercent=12,
        monthlyCost=40,
    )) == ["UNDERUTILIZED_GPU"]


def test_healthy_record_has_no_recommendation():
    assert categories(record()) == []


def test_rules_respect_exclusive_thresholds():
    assert categories(record(utilizationPercent=10, monthlyCost=80)) == []
    assert categories(record(utilizationPercent=4, monthlyCost=50)) == []
    assert categories(record(utilizationPercent=25, monthlyCost=300, resourceType="large-instance")) == []
    assert categories(record(utilizationPercent=18, monthlyCost=200, resourceType="large-instance")) == []
    assert categories(record(
        service="S3",
        resourceType="object-storage",
        accessFrequency="rare",
        storageGb=100,
    )) == []
    assert categories(record(
        service="S3",
        resourceType="object-storage",
        accessFrequency="medium",
        storageGb=250,
    )) == []
    assert categories(record(resourceType="gpu-instance", usageHours=650, utilizationPercent=12)) == []
    assert categories(record(resourceType="gpu-instance", usageHours=700, utilizationPercent=20)) == []


def test_only_the_latest_record_for_a_resource_is_judged():
    idle = record(usageDate="2026-10-01", utilizationPercent=1, monthlyCost=90)
    busy = record(usageDate="2026-11-01", utilizationPercent=80, monthlyCost=90)
    assert categories(idle, busy) == []

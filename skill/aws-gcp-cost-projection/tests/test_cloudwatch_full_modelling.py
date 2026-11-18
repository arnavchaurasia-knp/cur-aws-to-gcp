"""Tests for CloudWatch full modelling (§4.17).

Verifies:
1. PutLogEvents (log ingestion) → Cloud Logging log ingestion ("Log Storage cost", 143F-A1B0-E0BE, $0.50/GiB, 50 GiB free).
2. DataIngestion bytes variant → Cloud Logging log ingestion ("Log Storage cost", 143F-A1B0-E0BE).
3. TimedStorage-ByteHrs (log storage/retention) → Cloud Logging Log Retention cost ("Log Retention cost", F4AE-5A52-ACE3, $0.01/GiBy-mo).
4. Logs Insights (data scanned) → Cloud Logging Log Analytics ($0/query on bundled tier, ignore).
5. Log delivery to S3 / Firehose → Cloud Logging Log Router free export ($0, ignore).
6. Alarms (standard / composite) → Cloud Monitoring alerting ($0 on GCP, with 2027-09-01 charging caveat in projection_note).
7. Dashboards (DashboardHour) → Cloud Monitoring dashboards ($0 on GCP, ignore).
8. Custom Metrics (low volume <= 1000) → $0 on GCP within 150 MiB account-wide free tier.
9. Custom Metrics (high volume > 1000) → Cloud Monitoring "Metric Volume" (A924-09D0-8854) via 8-bytes/datapoint assumption.
10. API requests (per 1,000 requests) → $0 on GCP (Cloud Monitoring write API free / read free tier).
11. Database Insights (vCPU-Hours) → $0 on GCP (Cloud SQL Query Insights bundled).
12. Synthetics (canary runs) → Cloud Monitoring uptime/synthetic checks (passthrough, unit_model_mismatch).
13. Other CloudWatch (Contributor Insights / RUM) → passthrough (unit_model_mismatch).
14. Invariant: no mapped CloudWatch row ever has strategy="map" with an empty or None gcp_sku_id.
15. Invariant: all passthrough CloudWatch rows declare an intentional passthrough_reason.
16. Service usage catalog completeness: all 12 CloudWatch dimensions dispatch correctly via spec_dispatch.
"""

import json
import os
import pytest

from scripts.apply_static_mappings import map_cloudwatch
import scripts.passthrough_reasons as pr
import scripts.spec_dispatch as sd


HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CATALOG_PATH = os.path.join(ROOT, "data", "service_usage_catalog.json")


def _cw_row(
    usage_type: str,
    operation: str = "",
    unit: str = "GB",
    total_usage: float = 100.0,
    cost: float = 50.0,
    product: str = "AmazonCloudWatch",
    region: str = "us-central1",
    key: str = "k_cw_test",
) -> dict:
    return {
        "aws_li_key": key,
        "product": product,
        "product_code": "AmazonCloudWatch",
        "canonical": "cloudwatch",
        "usage_type": usage_type,
        "operation": operation,
        "pricing_unit": unit,
        "unit": unit,
        "line_item_type": "Usage",
        "pricing_model": "OnDemand",
        "aws_amortized_cost": cost,
        "total_usage": total_usage,
        "gcp_region": region,
        "aws_region": "us-east-1",
        "_bill_has_discounted_usage": True,
    }


def test_putlogevents_maps_to_cloud_logging_ingestion():
    row = _cw_row("USE1-PutLogEvents-Bytes", operation="PutLogEvents", unit="GB", total_usage=100.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Logging"
    assert m["gcp_sku_name"] == "Log Storage cost"
    assert m["gcp_sku_id"] == "143F-A1B0-E0BE"
    assert abs(m["unit_multiplier"] - (1.0 / 1.073741824)) < 1e-6
    assert m["component"] == "logs"
    assert m["mapping_confidence"] >= 0.90


def test_log_data_ingestion_variant_maps_to_ingestion():
    row = _cw_row("USE1-DataIngestion-Bytes", operation="DataIngestion", unit="GB", total_usage=200.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Logging"
    assert m["gcp_sku_name"] == "Log Storage cost"
    assert m["gcp_sku_id"] == "143F-A1B0-E0BE"
    assert m["component"] == "logs"


def test_log_storage_timedstorage_maps_to_retention():
    row = _cw_row("USE1-TimedStorage-ByteHrs", operation="TimedStorage", unit="GB-Mo", total_usage=500.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Logging"
    assert m["gcp_sku_name"] == "Log Retention cost"
    assert m["gcp_sku_id"] == "F4AE-5A52-ACE3"
    assert abs(m["unit_multiplier"] - (1.0 / 1.073741824)) < 1e-6
    assert m["component"] == "logs"


def test_log_insights_scanned_maps_to_ignore():
    row = _cw_row("USE1-DataScanned-Bytes", operation="StartQuery", unit="GB", total_usage=50.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Logging"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "log-insights"
    assert "Log Analytics" in m["projection_note"]


def test_log_export_s3_maps_to_ignore():
    row = _cw_row("USE1-Delivered to S3", operation="Export", unit="GB", total_usage=100.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Logging"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "log-export"
    assert "Log Router" in m["projection_note"]


def test_alarms_maps_to_ignore_with_2027_caveat():
    row = _cw_row("USE1-AlarmMonitorUsage", operation="", unit="Count", total_usage=20.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "alarms"
    assert "2027-09-01" in m["projection_note"]


def test_dashboards_maps_to_ignore():
    row = _cw_row("USE1-DashboardHour", operation="", unit="Hrs", total_usage=720.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "dashboard"


def test_custom_metrics_low_volume_maps_to_ignore():
    row = _cw_row("USE1-CW:MetricMonitorUsage", operation="MetricStorage", unit="Count", total_usage=50.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "custom-metrics"
    assert "150 MiB" in m["projection_note"]


def test_custom_metrics_high_volume_maps_to_metric_volume():
    row = _cw_row("USE1-CW:MetricMonitorUsage", operation="MetricStorage", unit="Count", total_usage=5000.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_name"] == "Metric Volume"
    assert m["gcp_sku_id"] == "A924-09D0-8854"
    assert m["component"] == "custom-metrics"


def test_api_requests_maps_to_ignore():
    row = _cw_row("USE1-Requests", operation="per 1,000 requests", unit="Requests", total_usage=500.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "api-requests"


def test_db_insights_maps_to_ignore():
    row = _cw_row("USE1-CW:DatabaseInsights-vCPU-Hours", operation="", unit="Hrs", total_usage=720.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "ignore"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 0.0
    assert m["component"] == "db-insights"


def test_synthetics_maps_to_passthrough():
    row = _cw_row("USE1-CW:Canary-Runs", operation="CanaryRun", unit="Count", total_usage=1000.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "passthrough"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["gcp_sku_id"] is None
    assert m["passthrough_reason"] == "unit_model_mismatch"
    assert m["component"] == "synthetics"
    assert "Synthetics" in m["projection_note"]


def test_contributor_insights_other_maps_to_passthrough():
    row = _cw_row("USE1-ContributorInsights-Events", operation="", unit="Count", total_usage=500.0)
    out = map_cloudwatch([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "passthrough"
    assert m["gcp_service"] == "Cloud Monitoring"
    assert m["passthrough_reason"] == "unit_model_mismatch"
    assert m["component"] == "monitoring"


def test_invariant_no_map_with_empty_sku_id():
    test_rows = [
        _cw_row("USE1-PutLogEvents-Bytes", operation="PutLogEvents", unit="GB"),
        _cw_row("USE1-DataIngestion-Bytes", operation="DataIngestion", unit="GB"),
        _cw_row("USE1-TimedStorage-ByteHrs", operation="TimedStorage", unit="GB-Mo"),
        _cw_row("USE1-CW:MetricMonitorUsage", operation="MetricStorage", unit="Count", total_usage=5000.0),
        _cw_row("USE1-DataScanned-Bytes", operation="StartQuery", unit="GB"),
        _cw_row("USE1-Delivered to S3", operation="Export", unit="GB"),
        _cw_row("USE1-AlarmMonitorUsage", operation="", unit="Count"),
        _cw_row("USE1-DashboardHour", operation="", unit="Hrs"),
        _cw_row("USE1-CW:Canary-Runs", operation="CanaryRun", unit="Count"),
        _cw_row("USE1-Requests", operation="per 1,000 requests", unit="Requests"),
        _cw_row("USE1-CW:DatabaseInsights-vCPU-Hours", operation="", unit="Hrs"),
        _cw_row("USE1-ContributorInsights-Events", operation="", unit="Count"),
    ]
    out = map_cloudwatch(test_rows)
    for m in out:
        if m["strategy"] == "map":
            assert m["gcp_sku_id"] is not None and len(m["gcp_sku_id"]) > 0, (
                f"Row {m['aws_li_key']} strategy=map but empty gcp_sku_id"
            )


def test_invariant_passthrough_rows_carry_intentional_reason():
    test_rows = [
        _cw_row("USE1-CW:Canary-Runs", operation="CanaryRun", unit="Count"),
        _cw_row("USE1-ContributorInsights-Events", operation="", unit="Count"),
    ]
    out = map_cloudwatch(test_rows)
    for m in out:
        if m["strategy"] == "passthrough":
            reason = m.get("passthrough_reason")
            assert reason is not None, f"Passthrough row {m['aws_li_key']} missing passthrough_reason"
            assert pr.is_valid(reason), f"Invalid passthrough_reason: {reason}"
            assert pr.kind(reason) == "intentional", f"Expected intentional passthrough, got: {reason}"


def test_service_usage_catalog_completeness_cloudwatch():
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    cw_entries = catalog.get("cloudwatch", [])
    assert len(cw_entries) >= 12, f"Expected at least 12 CloudWatch dimensions, got {len(cw_entries)}"

    failures = []
    for entry in cw_entries:
        ut = entry["usage_type"]
        row = _cw_row(ut, operation=entry.get("operation", ""), unit=entry.get("unit", "GB"))
        res = sd.dispatch(row)
        assert res is not None, f"cloudwatch/{ut}: dispatch returned None"

        charge_type = res.get("charge_type")
        mechanic = res.get("mechanic")
        expected_ct = entry.get("expected_charge_type")
        expected_mech = entry.get("expected_mechanic")

        if expected_ct and charge_type != expected_ct:
            failures.append(f"cloudwatch/{ut}: charge_type={charge_type!r}, expected {expected_ct!r}")
        if expected_mech and mechanic != expected_mech:
            failures.append(f"cloudwatch/{ut}: mechanic={mechanic!r}, expected {expected_mech!r}")

    assert not failures, "Completeness gate violations:\n  " + "\n  ".join(failures)


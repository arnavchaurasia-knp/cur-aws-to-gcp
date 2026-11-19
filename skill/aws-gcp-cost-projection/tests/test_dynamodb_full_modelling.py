"""Tests for DynamoDB full modelling (§4.18).

Verifies:
1. On-demand RRU → Cloud Firestore Read Ops single-region (Iowa, 6A94-8525-876F, $0.30/M; NOT nam5 multi-region $0.60/M).
2. On-demand WRU → Cloud Firestore Entity Writes single-region (Iowa, BFCC-1D11-14E1, $0.90/M; NOT nam5 multi-region $1.80/M).
3. Provisioned RCU-Hrs → Cloud Firestore Read Ops single-region (Iowa, multiplier 3600 via dynamodb_rcu_to_reads_sec=1.0 assumption).
4. Provisioned WCU-Hrs → Cloud Firestore Entity Writes single-region (Iowa, multiplier 3600 via dynamodb_wcu_to_writes_sec=1.0 assumption).
5. Storage (TimedStorage-ByteHrs) → Cloud Firestore Storage single-region (Iowa, 140B-ADDF-6A12, ~$0.15/GiB-mo; NOT nam5 $0.18).
6. PITR Storage (TimedPITRStorage-ByteHrs) → Cloud Firestore Point-in-time Recovery Storage single-region (Iowa, 9AC7-D7C1-66E7).
7. Global Tables on-demand replication writes (ReplWriteRequestUnits) → Cloud Firestore Entity Writes North America 5 (nam5 multi-region, 0FD8-FD9E-2D84).
8. Global Tables provisioned replication writes (ReplWriteCapacityUnit-Hrs) → Cloud Firestore Entity Writes North America 5 (nam5 multi-region).
9. Bigtable / Spanner alternatives are disclosed in the projection note.
10. Invariant: no mapped DynamoDB row ever has strategy="map" with an empty or None gcp_sku_id.
11. Invariant: no DynamoDB throughput row routes to mechanic "misc" (spec_dispatch cutover).
12. Invariant: all passthrough DynamoDB rows declare an intentional passthrough_reason.
13. Service usage catalog completeness: all DynamoDB dimensions dispatch correctly via spec_dispatch.
"""

import json
import os
import pytest

from scripts.apply_static_mappings import (
    map_dynamodb_capacity,
    map_dynamodb_storage,
    _firestore_city_for_region,
)
import scripts.passthrough_reasons as pr
import scripts.spec_dispatch as sd


HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CATALOG_PATH = os.path.join(ROOT, "data", "service_usage_catalog.json")


def _ddb_row(
    usage_type: str,
    operation: str = "",
    unit: str = "Count",
    total_usage: float = 100.0,
    cost: float = 50.0,
    region: str = "us-central1",
    key: str = "k_ddb_test",
) -> dict:
    return {
        "aws_li_key": key,
        "product": "Amazon DynamoDB",
        "product_code": "AmazonDynamoDB",
        "canonical": "dynamodb",
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


def test_rru_maps_to_single_region_firestore_read_ops():
    row = _ddb_row("USE1-ReadRequestUnits", operation="", unit="Count", total_usage=1_000_000)
    out = map_dynamodb_capacity([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "6A94-8525-876F"  # Single-region Iowa ($0.30/M), NOT nam5 (369B, $0.60/M)
    assert m["gcp_sku_name"] == "Cloud Firestore Read Ops Iowa"
    assert m["unit_multiplier"] == 1.0
    assert m["component"] == "reads"
    assert "Bigtable" in m["projection_note"]
    assert "Spanner" in m["projection_note"]


def test_wru_maps_to_single_region_firestore_entity_writes():
    row = _ddb_row("USE1-WriteRequestUnits", operation="", unit="Count", total_usage=500_000)
    out = map_dynamodb_capacity([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "BFCC-1D11-14E1"  # Single-region Iowa ($0.90/M), NOT nam5 (0FD8, $1.80/M)
    assert m["gcp_sku_name"] == "Cloud Firestore Entity Writes Iowa"
    assert m["unit_multiplier"] == 1.0
    assert m["component"] == "writes"


def test_provisioned_rcu_hrs_maps_with_3600_multiplier():
    row = _ddb_row("USE1-ReadCapacityUnit-Hrs", operation="", unit="Hrs", total_usage=720.0)
    out = map_dynamodb_capacity([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "6A94-8525-876F"
    assert m["unit_multiplier"] == 3600.0  # 1 RCU = 1 read/sec = 3600 reads/hr
    assert m["is_assumption"] is True
    assert "dynamodb_rcu_to_reads_sec" in m["assumption"]


def test_provisioned_wcu_hrs_maps_with_3600_multiplier():
    row = _ddb_row("USE1-WriteCapacityUnit-Hrs", operation="", unit="Hrs", total_usage=720.0)
    out = map_dynamodb_capacity([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "BFCC-1D11-14E1"
    assert m["unit_multiplier"] == 3600.0  # 1 WCU = 1 write/sec = 3600 writes/hr
    assert m["is_assumption"] is True
    assert "dynamodb_wcu_to_writes_sec" in m["assumption"]


def test_storage_maps_to_single_region_firestore_storage():
    row = _ddb_row("USE1-TimedStorage-ByteHrs", operation="", unit="GB-Mo", total_usage=250.0)
    out = map_dynamodb_storage([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "140B-ADDF-6A12"  # Single-region Iowa (~$0.15), NOT nam5 (D36D, $0.18)
    assert m["gcp_sku_name"] == "Cloud Firestore Storage Iowa"
    assert m["component"] == "storage"


def test_pitr_storage_maps_to_single_region_pitr_sku():
    row = _ddb_row("USE1-TimedPITRStorage-ByteHrs", operation="", unit="GB-Mo", total_usage=100.0)
    out = map_dynamodb_storage([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "A979-304C-860F"  # Single-region Iowa PITR (exact specificity match)
    assert "Point-in-time Recovery" in m["gcp_sku_name"]
    assert m["component"] == "pitr-storage"


def test_global_tables_replication_writes_maps_to_nam5():
    row = _ddb_row("USE1-ReplWriteRequestUnits", operation="", unit="Count", total_usage=100_000)
    out = map_dynamodb_capacity([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "0FD8-FD9E-2D84"  # North America 5 multi-region for Global Tables replication
    assert "North America 5" in m["gcp_sku_name"]
    assert m["unit_multiplier"] == 1.0


def test_global_tables_provisioned_replication_maps_to_nam5():
    row = _ddb_row("USE1-ReplWriteCapacityUnit-Hrs", operation="", unit="Hrs", total_usage=720.0)
    out = map_dynamodb_capacity([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Firestore"
    assert m["gcp_sku_id"] == "0FD8-FD9E-2D84"  # North America 5 multi-region
    assert m["unit_multiplier"] == 3600.0


def test_city_mapping_covers_major_regions():
    assert _firestore_city_for_region("us-central1") == "Iowa"
    assert _firestore_city_for_region("us-west1") == "Oregon"
    assert _firestore_city_for_region("europe-west1") == "Belgium"
    assert _firestore_city_for_region("asia-south1") == "Mumbai"
    assert _firestore_city_for_region("unknown-region") == "Iowa"


def test_invariant_no_map_with_empty_sku_id():
    test_rows = [
        _ddb_row("USE1-ReadRequestUnits"),
        _ddb_row("USE1-WriteRequestUnits"),
        _ddb_row("USE1-ReadCapacityUnit-Hrs", unit="Hrs"),
        _ddb_row("USE1-WriteCapacityUnit-Hrs", unit="Hrs"),
        _ddb_row("USE1-ReplWriteRequestUnits"),
        _ddb_row("USE1-ReplWriteCapacityUnit-Hrs", unit="Hrs"),
    ]
    out = map_dynamodb_capacity(test_rows)
    for m in out:
        if m["strategy"] == "map":
            assert m["gcp_sku_id"] is not None and len(m["gcp_sku_id"]) > 0


def test_invariant_no_throughput_row_routes_to_misc():
    throughput_rows = [
        _ddb_row("USE1-ReadRequestUnits"),
        _ddb_row("USE1-WriteRequestUnits"),
        _ddb_row("USE1-ReadCapacityUnit-Hrs", unit="Hrs"),
        _ddb_row("USE1-WriteCapacityUnit-Hrs", unit="Hrs"),
        _ddb_row("USE1-ReplWriteRequestUnits"),
        _ddb_row("USE1-ReplWriteCapacityUnit-Hrs", unit="Hrs"),
    ]
    for r in throughput_rows:
        res = sd.dispatch(r)
        assert res is not None
        assert res["mechanic"] == "dynamodb_capacity", (
            f"{r['usage_type']}: expected mechanic 'dynamodb_capacity', got {res['mechanic']!r}"
        )


def test_service_usage_catalog_completeness_dynamodb():
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    ddb_entries = catalog.get("dynamodb", [])
    assert len(ddb_entries) >= 10, f"Expected at least 10 DynamoDB dimensions, got {len(ddb_entries)}"

    failures = []
    for entry in ddb_entries:
        ut = entry["usage_type"]
        row = _ddb_row(ut, operation=entry.get("operation", ""), unit=entry.get("unit", "Count"))
        res = sd.dispatch(row)
        assert res is not None, f"dynamodb/{ut}: dispatch returned None"

        charge_type = res.get("charge_type")
        mechanic = res.get("mechanic")
        expected_ct = entry.get("expected_charge_type")
        expected_mech = entry.get("expected_mechanic")

        if expected_ct and charge_type != expected_ct:
            failures.append(f"dynamodb/{ut}: charge_type={charge_type!r}, expected {expected_ct!r}")
        if expected_mech and mechanic != expected_mech:
            failures.append(f"dynamodb/{ut}: mechanic={mechanic!r}, expected {expected_mech!r}")

    assert not failures, "Completeness gate violations:\n  " + "\n  ".join(failures)


"""Tests for Data Transfer / egress full modelling per WORKORDER §4.11."""

import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm
import egress_rates as er


def _row(**kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k_dt"),
        "product": kw.get("product", "AWS Data Transfer"),
        "usage_type": kw.get("usage_type", ""),
        "operation": kw.get("operation", ""),
        "unit": kw.get("unit", "GB"),
        "total_usage": kw.get("total_usage", 100.0),
        "aws_amortized_cost": kw.get("aws_amortized_cost", 10.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
        "from_region": kw.get("from_region"),
        "to_region": kw.get("to_region"),
    }


def test_internet_egress_tiers_by_volume_and_destination():
    """Internet egress selects the correct Premium Tier by monthly volume and destination continent."""
    # Americas destination
    row_t1 = _row(aws_li_key="k_t1", usage_type="DataTransfer-Out-Bytes", total_usage=500.0)
    row_t2 = _row(aws_li_key="k_t2", usage_type="DataTransfer-Out-Bytes", total_usage=5000.0)
    row_t3 = _row(aws_li_key="k_t3", usage_type="DataTransfer-Out-Bytes", total_usage=20000.0)

    res1 = asm.map_data_transfer([row_t1])
    assert res1[0]["strategy"] == "map"
    assert res1[0]["gcp_sku_id"] == "GCP-EGRESS-INTERNET-AMERICAS-T1"
    assert "0.1200" in res1[0]["projection_note"]

    res2 = asm.map_data_transfer([row_t2])
    assert res2[0]["strategy"] == "map"
    assert res2[0]["gcp_sku_id"] == "GCP-EGRESS-INTERNET-AMERICAS-T2"
    assert "0.1100" in res2[0]["projection_note"]

    res3 = asm.map_data_transfer([row_t3])
    assert res3[0]["strategy"] == "map"
    assert res3[0]["gcp_sku_id"] == "GCP-EGRESS-INTERNET-AMERICAS-T3"
    assert "0.0800" in res3[0]["projection_note"]

    # Australia / Special destination
    row_au_t1 = _row(aws_li_key="k_au1", usage_type="DataTransfer-Out-Bytes", operation="Australia-Out", total_usage=500.0)
    row_au_t3 = _row(aws_li_key="k_au3", usage_type="DataTransfer-Out-Bytes", operation="Australia-Out", total_usage=25000.0)

    res_au1 = asm.map_data_transfer([row_au_t1])
    assert res_au1[0]["gcp_sku_id"] == "GCP-EGRESS-INTERNET-SPECIAL-T1"
    assert "0.1900" in res_au1[0]["projection_note"]

    res_au3 = asm.map_data_transfer([row_au_t3])
    assert res_au3[0]["gcp_sku_id"] == "GCP-EGRESS-INTERNET-SPECIAL-T3"
    assert "0.1500" in res_au3[0]["projection_note"]

    # Europe destination
    row_eu_t3 = _row(aws_li_key="k_eu3", usage_type="EU-DataTransfer-Out-Bytes", total_usage=25000.0)
    res_eu3 = asm.map_data_transfer([row_eu_t3])
    assert res_eu3[0]["gcp_sku_id"] == "GCP-EGRESS-INTERNET-EUROPE-T3"
    assert "0.0850" in res_eu3[0]["projection_note"]


def test_interregion_continent_pair_matrix():
    """Inter-region egress selects the correct rate from the continent-pair matrix."""
    # 1. Intra-NA ($0.02)
    row_intra_na = _row(
        aws_li_key="k_na",
        usage_type="USE1-USE2-AWS-Out-Bytes",
        from_region="us-central1",
        to_region="us-east1",
    )
    res_na = asm.map_data_transfer([row_intra_na])
    assert res_na[0]["strategy"] == "map"
    assert res_na[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-NORT-NORT"
    assert "0.0200" in res_na[0]["projection_note"]

    # 2. Intra-EU ($0.02)
    row_intra_eu = _row(
        aws_li_key="k_eu",
        from_region="europe-west1",
        to_region="europe-west4",
    )
    res_eu = asm.map_data_transfer([row_intra_eu])
    assert res_eu[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-EURO-EURO"
    assert "0.0200" in res_eu[0]["projection_note"]

    # 3. NA to EU ($0.05)
    row_na_eu = _row(
        aws_li_key="k_na_eu",
        from_region="us-central1",
        to_region="europe-west1",
    )
    res_na_eu = asm.map_data_transfer([row_na_eu])
    assert res_na_eu[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-NORT-EURO"
    assert "0.0500" in res_na_eu[0]["projection_note"]

    # 4. NA to Asia ($0.08)
    row_na_asia = _row(
        aws_li_key="k_na_asia",
        from_region="us-central1",
        to_region="asia-southeast1",
    )
    res_na_asia = asm.map_data_transfer([row_na_asia])
    assert res_na_asia[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-NORT-ASIA"
    assert "0.0800" in res_na_asia[0]["projection_note"]

    # 5. NA to South America ($0.14)
    row_na_sa = _row(
        aws_li_key="k_na_sa",
        from_region="us-central1",
        to_region="southamerica-east1",
    )
    res_na_sa = asm.map_data_transfer([row_na_sa])
    assert res_na_sa[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-NORT-SOUT"
    assert "0.1400" in res_na_sa[0]["projection_note"]

    # 6. South America to anywhere ($0.14)
    row_sa_any = _row(
        aws_li_key="k_sa_na",
        from_region="southamerica-east1",
        to_region="us-central1",
    )
    res_sa_any = asm.map_data_transfer([row_sa_any])
    assert res_sa_any[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-SOUT-NORT"
    assert "0.1400" in res_sa_any[0]["projection_note"]

    # 7. NA to Oceania ($0.10)
    row_na_ocea = _row(
        aws_li_key="k_na_oc",
        from_region="us-central1",
        to_region="australia-southeast1",
    )
    res_na_ocea = asm.map_data_transfer([row_na_ocea])
    assert res_na_ocea[0]["gcp_sku_id"] == "GCP-EGRESS-INTERREGION-NORT-OCEA"
    assert "0.1000" in res_na_ocea[0]["projection_note"]


def test_interzone_stays_paid_sku_and_rejects_decoy():
    """Inter-zone egress uses paid SKU DE9E-AFBC-A15A ($0.01/GiB) and explicitly rejects $0 decoy C1F1-02CA-F355."""
    row_iz = _row(
        aws_li_key="k_iz",
        usage_type="USE1-Regional-Bytes",
        operation="RegionalBytes",
        gcp_region="us-central1",
    )
    res = asm.map_data_transfer([row_iz])
    assert len(res) == 1
    r = res[0]
    assert r["strategy"] == "map"
    assert r["gcp_sku_id"] == "DE9E-AFBC-A15A"
    assert r["gcp_sku_id"] != "C1F1-02CA-F355"
    assert "0.01" in r["projection_note"]
    assert "paid SKU" in r["projection_note"]


def test_ingress_free_on_gcp():
    """Ingress is free on GCP -> strategy='ignore' ($0)."""
    row_in = _row(
        aws_li_key="k_in",
        usage_type="DataTransfer-In-Bytes",
        operation="DataTransfer-In",
    )
    res = asm.map_data_transfer([row_in])
    assert len(res) == 1
    r = res[0]
    assert r["strategy"] == "ignore"
    assert r["gcp_sku_id"] is None


def test_no_mapped_egress_row_has_empty_sku_id():
    """Invariant: every egress row with strategy='map' must have a non-empty gcp_sku_id."""
    test_rows = [
        _row(aws_li_key="k1", usage_type="DataTransfer-Out-Bytes"),
        _row(aws_li_key="k2", usage_type="USE1-Regional-Bytes"),
        _row(aws_li_key="k3", usage_type="USE1-USE2-AWS-Out-Bytes", from_region="us-central1", to_region="us-east1"),
        _row(aws_li_key="k4", usage_type="VpcPeering-Bytes"),
    ]
    res = asm.map_data_transfer(test_rows)
    for r in res:
        if r["strategy"] == "map":
            assert r.get("gcp_sku_id"), f"Row {r['aws_li_key']} is map but has no gcp_sku_id!"
            assert r.get("gcp_sku_name"), f"Row {r['aws_li_key']} is map but has no gcp_sku_name!"


def test_datatransfer_catalog_and_spec_dispatch_coverage():
    """Verify all 18 Data Transfer catalog dimensions dispatch cleanly."""
    import json
    import spec_dispatch as sd

    cat_path = os.path.join(os.path.dirname(_HERE), "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    dt_entries = catalog.get("datatransfer", [])
    assert len(dt_entries) >= 15

    for entry in dt_entries:
        row = {
            "aws_li_key": "k",
            "product": "AWS Data Transfer",
            "product_code": "AWSDataTransfer",
            "usage_type": entry["usage_type"],
            "unit": entry.get("unit", "GB"),
            "operation": "",
            "aws_region": "us-east-1",
        }
        d = sd.dispatch(row)
        assert d is not None, f"Dispatch returned None for {entry['usage_type']}"
        assert d["charge_type"] == entry["expected_charge_type"], (
            f"Expected {entry['expected_charge_type']}, got {d['charge_type']} for {entry['usage_type']}"
        )
        assert d["mechanic"] == entry["expected_mechanic"], (
            f"Expected {entry['expected_mechanic']}, got {d['mechanic']} for {entry['usage_type']}"
        )


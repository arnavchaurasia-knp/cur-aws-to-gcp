#!/usr/bin/env python3
"""
test_region_fidelity.py — Unit tests for Phase 2 region fidelity preservation at ingest.

Phase 2 Workorder:
- Add from_region + to_region (GCP-decoded) columns to aws_li_catalog alongside aws_region.
- Source priority:
    from_region = product/fromRegionCode -> region_prefix token 1 -> aws_region
    to_region = product/toRegionCode -> region_prefix token 2 -> NULL
- Extend region_prefix.py: add decode_region_pair() and missing codes (WAW1 -> europe-central2).
- STOP GATE: every data-transfer row has non-null from_region; inter-region rows have
  from_region != to_region; WAW1 -> europe-central2; Phase-0 invariant holds.
"""

import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from region_prefix import decode_region_prefix, decode_region_pair, UT_PREFIX_TO_GCP


def test_decode_region_pair_variations():
    """Verify decode_region_pair correctly decodes 2-token region pairs and single prefixes."""
    # WAW1 Warsaw -> europe-central2
    from_r, to_r = decode_region_pair("APS1-WAW1-AWS-Out-Bytes")
    assert from_r == "asia-southeast1"
    assert to_r == "europe-central2"

    # APS3 Mumbai -> APS5 Hyderabad/Delhi
    from_r, to_r = decode_region_pair("APS3-APS5-AWS-Out-Bytes")
    assert from_r == "asia-south1"
    assert to_r == "asia-south2"

    # APS5 -> EU generic (europe-west1)
    from_r, to_r = decode_region_pair("APS5-EU-AWS-Out-Bytes")
    assert from_r == "asia-south2"
    assert to_r == "europe-west1"

    # Single prefix (EBS volume usage) -> from_region set, to_region is None/fallback
    from_r, to_r = decode_region_pair("APS3-EBS:VolumeUsage.gp3")
    assert from_r == "asia-south1"
    assert to_r is None

    # No prefix in usage_type -> fallbacks returned
    from_r, to_r = decode_region_pair("DataTransfer-Out-Bytes", fallback_from="us-east4", fallback_to=None)
    assert from_r == "us-east4"
    assert to_r is None

    # Flat/PDF bill where usage_type is empty and pair is embedded in product description
    from_r, to_r = decode_region_pair("", product="AWS Data Transfer APS1-WAW1-AWS-Out-Bytes")
    assert from_r == "asia-southeast1"
    assert to_r == "europe-central2"


def test_waw1_code_mapping():
    """Verify WAW1 is mapped to europe-central2 in UT_PREFIX_TO_GCP."""
    assert "waw1" in UT_PREFIX_TO_GCP
    assert UT_PREFIX_TO_GCP["waw1"] == "europe-central2"


def test_ingest_step5_region_fidelity():
    """Verify from_region and to_region resolution in catalog schema."""
    con = duckdb.connect(":memory:")
    
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            from_region VARCHAR,
            to_region VARCHAR,
            from_region_raw VARCHAR,
            to_region_raw VARCHAR
        );
    """)

    # Row 1: CUR row with explicit from/to region codes
    # Row 2: Flat/PDF row with region pair in usage_type
    # Row 3: PDF row with region pair in product
    # Row 4: Single-region row
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'AWS Data Transfer', 'APS3-APS5-AWS-Out-Bytes', 'ap-south-1', NULL, NULL, NULL, 'ap-south-1', 'ap-south-2'),
            ('k2', 'AWS Data Transfer', 'APS1-WAW1-AWS-Out-Bytes', 'ap-southeast-1', NULL, NULL, NULL, '', ''),
            ('k3', 'AWS Data Transfer APS1-WAW1-AWS-Out-Bytes', '', 'ap-southeast-1', NULL, NULL, NULL, '', ''),
            ('k4', 'Amazon EC2', 'APS3-BoxUsage:t3.medium', 'ap-south-1', NULL, NULL, NULL, '', '');
    """)

    from ingest import REGION_MAP, DESCRIPTIVE_REGION_MAP

    catalog_rows = con.execute("""
        SELECT aws_li_key, aws_region, usage_type,
               COALESCE(from_region_raw, ''), COALESCE(to_region_raw, ''),
               COALESCE(product, '')
        FROM aws_li_catalog
    """).fetchall()

    region_updates = []
    for row in catalog_rows:
        key, aws_r, ut, from_raw, to_raw, prod = row
        aws_r_clean = aws_r.strip().lower() if aws_r else ""
        gcp_r = REGION_MAP.get(aws_r_clean) or DESCRIPTIVE_REGION_MAP.get(aws_r_clean, "global")
        if gcp_r == "global":
            gcp_r = decode_region_prefix(ut or prod, gcp_r)

        pair_from, pair_to = decode_region_pair(ut, prod)

        from_gcp = None
        if from_raw:
            from_clean = from_raw.strip().lower()
            from_gcp = REGION_MAP.get(from_clean) or DESCRIPTIVE_REGION_MAP.get(from_clean)
        if not from_gcp:
            from_gcp = pair_from
        if not from_gcp:
            from_gcp = gcp_r

        to_gcp = None
        if to_raw:
            to_clean = to_raw.strip().lower()
            to_gcp = REGION_MAP.get(to_clean) or DESCRIPTIVE_REGION_MAP.get(to_clean)
        if not to_gcp:
            to_gcp = pair_to

        region_updates.append((gcp_r, from_gcp, to_gcp, key))

    con.executemany("""
        UPDATE aws_li_catalog
        SET gcp_region = ?, from_region = ?, to_region = ?
        WHERE aws_li_key = ?
    """, region_updates)

    res = {r[0]: r for r in con.execute("SELECT aws_li_key, gcp_region, from_region, to_region FROM aws_li_catalog").fetchall()}

    # k1: CUR explicit from/to
    assert res["k1"][2] == "asia-south1"
    assert res["k1"][3] == "asia-south2"
    assert res["k1"][2] != res["k1"][3]

    # k2: decoded from usage_type pair
    assert res["k2"][2] == "asia-southeast1"
    assert res["k2"][3] == "europe-central2"

    # k3: decoded from product text (PDF format)
    assert res["k3"][2] == "asia-southeast1"
    assert res["k3"][3] == "europe-central2"

    # k4: single-region EC2
    assert res["k4"][2] == "asia-south1"
    assert res["k4"][3] is None

    con.close()

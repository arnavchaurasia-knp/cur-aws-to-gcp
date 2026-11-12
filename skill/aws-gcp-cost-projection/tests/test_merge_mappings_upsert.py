#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import merge_mappings

def test_merge_mappings_upsert_preserves_retry_rows():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        mappings_dir = os.path.join(tmp_dir, "mappings")
        os.makedirs(mappings_dir, exist_ok=True)

        con = duckdb.connect(db_path)
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key TEXT PRIMARY KEY,
                product TEXT,
                mechanic_group TEXT
            )
        """)
        con.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'compute_breakdown')")
        con.execute("INSERT INTO aws_li_catalog VALUES ('k_retry', 'AmazonEC2', 'compute_breakdown')")
        merge_mappings.ensure_table(con)

        # Pre-populate aws_li_to_gcp_li with a direct INSERT from an LLM retry
        con.execute("""
            INSERT INTO aws_li_to_gcp_li (aws_li_key, gcp_service, strategy, unit_multiplier, decided_by)
            VALUES ('k_retry', 'Compute Engine', 'map', 1.0, 'Phase-2 retry prompt')
        """)
        con.close()

        # Write normal mapping file for k1
        mapping_content = [
            {
                "aws_li_key": "k1",
                "gcp_service": "Compute Engine",
                "strategy": "map",
                "unit_multiplier": 1.0,
                "decided_by": "compute_breakdown_mappings.json"
            }
        ]
        with open(os.path.join(mappings_dir, "compute_breakdown_mappings.json"), "w") as f:
            json.dump(mapping_content, f)

        sys.argv = ["merge_mappings.py", db_path, mappings_dir]
        try:
            merge_mappings.main()
        except SystemExit as e:
            assert e.code == 0

        con = duckdb.connect(db_path)
        rows = con.execute("SELECT aws_li_key, decided_by FROM aws_li_to_gcp_li ORDER BY aws_li_key").fetchall()
        con.close()

        # Both k1 and k_retry must be present! The retry row must NOT be deleted.
        keys = [r[0] for r in rows]
        assert "k1" in keys
        assert "k_retry" in keys
        retry_decided = [r[1] for r in rows if r[0] == "k_retry"][0]
        assert retry_decided == "Phase-2 retry prompt"



def test_merge_mappings_upsert_protects_retry_row_even_when_in_incoming_json():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        mappings_dir = os.path.join(tmp_dir, "mappings")
        os.makedirs(mappings_dir, exist_ok=True)

        con = duckdb.connect(db_path)
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key TEXT PRIMARY KEY,
                product TEXT,
                mechanic_group TEXT
            )
        """)
        con.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'compute_breakdown')")
        merge_mappings.ensure_table(con)

        # Pre-populate aws_li_to_gcp_li with a direct INSERT from an LLM retry for k1
        con.execute("""
            INSERT INTO aws_li_to_gcp_li (aws_li_key, gcp_service, strategy, unit_multiplier, decided_by, gcp_sku_name)
            VALUES ('k1', 'Compute Engine', 'map', 1.0, 'llm_retry_direct', 'Retry Winner SKU')
        """)
        con.close()

        # Write incoming JSON that ALSO re-asserts k1
        mapping_content = [
            {
                "aws_li_key": "k1",
                "gcp_service": "Compute Engine",
                "strategy": "map",
                "unit_multiplier": 1.0,
                "decided_by": "compute_breakdown_mappings.json",
                "gcp_sku_name": "Old Bulk SKU"
            }
        ]
        with open(os.path.join(mappings_dir, "compute_breakdown_mappings.json"), "w") as f:
            json.dump(mapping_content, f)

        sys.argv = ["merge_mappings.py", db_path, mappings_dir]
        try:
            merge_mappings.main()
        except SystemExit as e:
            assert e.code == 0

        con = duckdb.connect(db_path)
        row = con.execute("SELECT aws_li_key, decided_by, gcp_sku_name FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
        con.close()

        # The retry row must be preserved and NOT overwritten by the JSON bulk mapping!
        assert row is not None
        assert row[0] == "k1"
        assert row[1] == "llm_retry_direct"
        assert row[2] == "Retry Winner SKU"

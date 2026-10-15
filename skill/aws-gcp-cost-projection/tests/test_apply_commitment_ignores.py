import json
import os
import subprocess
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import apply_commitment_ignores as aci


def test_apply_commitment_ignores_structured_fields_and_null_cost(tmp_path):
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            mechanic_group TEXT,
            product TEXT,
            product_code TEXT,
            aws_amortized_cost DOUBLE,
            line_item_type TEXT
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('c1', 'commitment_discount', 'Amazon Elastic Compute Cloud', 'AmazonEC2', 150.50, 'RIFee'),
            ('c2', 'commitment_discount', NULL, NULL, 200.0, 'SavingsPlanRecurringFee'),
            ('n1', 'negative_cost', 'AWS Support', 'AWSSupport', -45.25, 'Credit'),
            ('n2', 'negative_cost', 'Amazon Simple Storage Service', 'AmazonS3', NULL, 'Refund'),
            ('w1', 'compute_breakdown', 'Amazon Elastic Compute Cloud', 'AmazonEC2', 500.0, 'Usage')
    """)
    con.close()

    comm_count, neg_count = aci.process_database(str(db_path))
    assert comm_count == 2
    assert neg_count == 2

    mappings_dir = tmp_path / "mappings"
    comm_file = mappings_dir / "commitment_discount_mappings.json"
    neg_file = mappings_dir / "negative_cost_mappings.json"

    assert comm_file.exists()
    assert neg_file.exists()

    with open(comm_file, "r", encoding="utf-8") as f:
        comm_data = json.load(f)
    with open(neg_file, "r", encoding="utf-8") as f:
        neg_data = json.load(f)

    # Validate commitment mappings
    assert len(comm_data) == 2
    c1 = next(r for r in comm_data if r["aws_li_key"] == "c1")
    assert c1["strategy"] == "ignore"
    assert c1["mapping_confidence"] == 1.0
    assert c1["aws_service"] == "Amazon Elastic Compute Cloud"
    assert c1["aws_product_code"] == "AmazonEC2"
    assert c1["aws_amortized_cost"] == 150.50
    assert "commitment_discount" in c1["projection_note"]

    c2 = next(r for r in comm_data if r["aws_li_key"] == "c2")
    assert c2["aws_service"] is None
    assert c2["aws_product_code"] is None
    assert c2["aws_amortized_cost"] == 200.0

    # Validate negative cost mappings + NULL cost safety
    assert len(neg_data) == 2
    n1 = next(r for r in neg_data if r["aws_li_key"] == "n1")
    assert n1["strategy"] == "ignore"
    assert n1["mapping_confidence"] == 1.0
    assert n1["aws_service"] == "AWS Support"
    assert n1["aws_product_code"] == "AWSSupport"
    assert n1["aws_amortized_cost"] == -45.25
    assert "$-45.25" in n1["projection_note"]
    assert "Credit" in n1["projection_note"]

    n2 = next(r for r in neg_data if r["aws_li_key"] == "n2")
    assert n2["strategy"] == "ignore"
    assert n2["mapping_confidence"] == 1.0
    assert n2["aws_service"] == "Amazon Simple Storage Service"
    assert n2["aws_product_code"] == "AmazonS3"
    assert n2["aws_amortized_cost"] == 0.0
    # FIX 1 assertion: NULL cost rendered safely as $0.00 without throwing TypeError
    assert "$0.00" in n2["projection_note"]
    assert "Refund" in n2["projection_note"]


def test_apply_commitment_ignores_legacy_missing_columns(tmp_path):
    # Older schema without product and product_code columns
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            mechanic_group TEXT,
            aws_amortized_cost DOUBLE
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('c1', 'commitment_discount', 120.0),
            ('n1', 'negative_cost', -30.0)
    """)
    con.close()

    comm_count, neg_count = aci.process_database(str(db_path))
    assert comm_count == 1
    assert neg_count == 1

    mappings_dir = tmp_path / "mappings"
    with open(mappings_dir / "commitment_discount_mappings.json", "r", encoding="utf-8") as f:
        comm = json.load(f)[0]
    with open(mappings_dir / "negative_cost_mappings.json", "r", encoding="utf-8") as f:
        neg = json.load(f)[0]

    assert comm["aws_service"] is None
    assert comm["aws_product_code"] is None
    assert comm["aws_amortized_cost"] == 120.0

    assert neg["aws_service"] is None
    assert neg["aws_product_code"] is None
    assert neg["aws_amortized_cost"] == -30.0
    assert "$-30.00" in neg["projection_note"]


def test_apply_commitment_ignores_cli(tmp_path):
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            mechanic_group TEXT,
            product TEXT,
            product_code TEXT,
            aws_amortized_cost DOUBLE,
            line_item_type TEXT
        )
    """)
    con.execute("INSERT INTO aws_li_catalog VALUES ('c1', 'commitment_discount', 'EC2', 'AmazonEC2', 10.0, 'RIFee')")
    con.close()

    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "apply_commitment_ignores.py")
    res = subprocess.run([sys.executable, script_path, str(db_path)], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Wrote 1 commitment_discount ignore rows" in res.stdout
    assert os.path.exists(tmp_path / "mappings" / "commitment_discount_mappings.json")

import json
import os
import shutil
import tempfile
import duckdb
import pytest
import subprocess
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS_DIR)
import promote_learned_codes
import resolve_unknown_product_codes


def test_proposals_file_written_not_shared_cache(tmp_path, monkeypatch):
    """Verify resolve_unknown_product_codes writes learned_proposals.json and does not touch learned-product-codes.json."""
    job_dir = str(tmp_path / "job-123")
    audit_dir = os.path.join(job_dir, "projection-audit")
    os.makedirs(audit_dir)
    db_path = os.path.join(audit_dir, "projection.duckdb")

    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            product_code VARCHAR,
            canonical_service VARCHAR,
            aws_amortized_cost DOUBLE
        );
        INSERT INTO aws_li_catalog VALUES
        ('k1', 'Amazon Custom Mystery Service', NULL, NULL, 150.0),
        ('k2', 'Amazon Custom Mystery Service', NULL, NULL, 250.0);
    """)
    con.close()

    # Mock LLM resolution to simulate agy returning an authoritative code
    def mock_resolve_residue(names, valid_codes):
        return {"amazon custom mystery service": "AmazonDetective"}

    monkeypatch.setattr(resolve_unknown_product_codes, "resolve_residue_via_llm", mock_resolve_residue)
    monkeypatch.setattr(resolve_unknown_product_codes, "_load_cache", lambda: {})

    shared_cache_path = resolve_unknown_product_codes._CACHE_PATH
    mtime_before = os.path.getmtime(shared_cache_path) if os.path.exists(shared_cache_path) else None

    # Run main
    monkeypatch.setattr(sys, "argv", ["resolve_unknown_product_codes.py", db_path])
    res = resolve_unknown_product_codes.main()
    assert res == 0

    # 1. Proposal file must exist in job's audit directory
    proposal_path = os.path.join(audit_dir, "learned_proposals.json")
    assert os.path.exists(proposal_path), f"learned_proposals.json should exist at {proposal_path}"

    with open(proposal_path, encoding="utf-8") as f:
        pdata = json.load(f)

    assert pdata["_meta"]["job_id"] == "job-123"
    assert len(pdata["proposals"]) == 1
    prop = pdata["proposals"][0]
    assert prop["product_name"] == "Amazon Custom Mystery Service"
    assert prop["service_code"] == "AmazonDetective"
    assert prop["evidence_rows"] == 2
    assert prop["evidence_usd"] == 400.0

    # 2. Shared cache file mtime must NOT have changed
    if mtime_before:
        assert os.path.getmtime(shared_cache_path) == mtime_before

    # 3. DuckDB rows were stamped
    con = duckdb.connect(db_path)
    rows = con.execute("SELECT product_code FROM aws_li_catalog WHERE aws_li_key = 'k1'").fetchall()
    assert rows[0][0] == "AmazonDetective"
    con.close()


def test_promotion_dry_run_and_apply(tmp_path, monkeypatch):
    """Verify promote_learned_codes dry run leaves cache unchanged and --apply adds new entries."""
    jobs_root = str(tmp_path / "jobs")
    job_dir = os.path.join(jobs_root, "job-A", "projection-audit")
    os.makedirs(job_dir)

    proposals = {
        "_meta": {"job_id": "job-A"},
        "proposals": [
            {
                "product_name": "Brand New Service",
                "service_code": "AmazonDetective",
                "canonical": "detective",
                "confidence": 0.9,
                "evidence_rows": 5,
                "evidence_usd": 500.0,
            }
        ],
    }
    with open(os.path.join(job_dir, "learned_proposals.json"), "w", encoding="utf-8") as f:
        json.dump(proposals, f)

    fake_data_dir = str(tmp_path / "data")
    os.makedirs(fake_data_dir)
    fake_cache = os.path.join(fake_data_dir, "learned-product-codes.json")
    with open(fake_cache, "w", encoding="utf-8") as f:
        json.dump({"_meta": {}, "names": {"existing": "AmazonEC2"}}, f)

    monkeypatch.setattr(promote_learned_codes, "_CACHE_PATH", fake_cache)

    # 1. Dry run
    monkeypatch.setattr(sys, "argv", ["promote_learned_codes.py", jobs_root])
    ret = promote_learned_codes.main()
    assert ret == 0

    with open(fake_cache, encoding="utf-8") as f:
        data = json.load(f)
    assert "brand new service" not in data["names"]

    # 2. Apply
    monkeypatch.setattr(sys, "argv", ["promote_learned_codes.py", jobs_root, "--apply"])
    ret = promote_learned_codes.main()
    assert ret == 0

    with open(fake_cache, encoding="utf-8") as f:
        data = json.load(f)
    assert data["names"]["brand new service"] == "AmazonDetective"
    assert data["names"]["existing"] == "AmazonEC2"


def test_promotion_refuses_conflicts(tmp_path, monkeypatch):
    """Verify promote_learned_codes reports conflicts and refuses to apply them."""
    jobs_root = str(tmp_path / "jobs")

    job_a = os.path.join(jobs_root, "job-A", "projection-audit")
    os.makedirs(job_a)
    with open(os.path.join(job_a, "learned_proposals.json"), "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {"job_id": "job-A"},
            "proposals": [{"product_name": "Controversial Product", "service_code": "AmazonEC2"}]
        }, f)

    job_b = os.path.join(jobs_root, "job-B", "projection-audit")
    os.makedirs(job_b)
    with open(os.path.join(job_b, "learned_proposals.json"), "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {"job_id": "job-B"},
            "proposals": [{"product_name": "Controversial Product", "service_code": "AmazonVPC"}]
        }, f)

    fake_data_dir = str(tmp_path / "data")
    os.makedirs(fake_data_dir)
    fake_cache = os.path.join(fake_data_dir, "learned-product-codes.json")
    with open(fake_cache, "w", encoding="utf-8") as f:
        json.dump({"_meta": {}, "names": {}}, f)

    monkeypatch.setattr(promote_learned_codes, "_CACHE_PATH", fake_cache)

    # Run with --apply
    monkeypatch.setattr(sys, "argv", ["promote_learned_codes.py", jobs_root, "--apply"])
    ret = promote_learned_codes.main()
    assert ret == 0

    with open(fake_cache, encoding="utf-8") as f:
        data = json.load(f)
    # Neither should be applied
    assert "controversial product" not in data["names"]

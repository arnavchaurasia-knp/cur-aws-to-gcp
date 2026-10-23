"""
Tests for WORKORDER_small_corrections Item 2e.
Validates instance type extraction and spec resolution for:
1. test_kafka_storage_not_an_instance_type - Kafka.Storage.GP2 has instance_type IS NULL.
2. test_abbreviated_size_resolves - db.m7g.xl resolves to m7g.xlarge spec.
3. test_cache_prefix_falls_back_to_ec2 - cache.r7g.large resolves via r7g.large.
4. test_spot_row_resolves_specs - Spot row whose type comes from operation text gets vCPU/RAM.
5. test_unknown_size_not_guessed - abbreviation with no valid expansion stays unresolved.
"""
import os
import sys
import subprocess
import duckdb
import pytest

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INGEST = os.path.join(_SKILL, "scripts", "ingest.py")


def _run_ingest_catalog(tmp_path, csv_content):
    job = tmp_path
    (job / "input.csv").write_text(csv_content, encoding="utf-8")
    env = {**os.environ, "SKILL_DIR": _SKILL}
    res = subprocess.run([sys.executable, _INGEST], cwd=str(job), env=env,
                         capture_output=True, text=True, timeout=120)
    db = job / "projection-audit" / "projection.duckdb"
    assert db.exists(), f"ingest failed ({res.stderr}):\n{res.stdout}"
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT product, usage_type, operation, instance_type, instance_vcpus, instance_ram_gb "
            "FROM aws_li_catalog"
        ).fetchall()
    finally:
        con.close()
    return rows


def test_kafka_storage_not_an_instance_type(tmp_path):
    csv = (
        "Service,Custom Usage Type,Description,Cost ($)\n"
        "Amazon Managed Streaming for Apache Kafka,APS5-Kafka.Storage.GP2,RunVolume,100.00\n"
    )
    rows = _run_ingest_catalog(tmp_path, csv)
    assert len(rows) == 1
    # instance_type IS NULL
    assert rows[0][3] is None, f"Expected instance_type IS NULL for Kafka storage, got {rows[0][3]}"


def test_abbreviated_size_resolves(tmp_path):
    csv = (
        "Service,Custom Usage Type,Description,Cost ($)\n"
        "Amazon Relational Database Service,APS5-Multi-AZUsage:db.m7g.xl,Multi-AZUsage:db.m7g.xl,100.00\n"
    )
    rows = _run_ingest_catalog(tmp_path, csv)
    assert len(rows) == 1
    # db.m7g.xl resolves to m7g.xlarge spec (vcpus: 4, ram_gb: 16.0)
    assert rows[0][4] == 4, f"Expected 4 vCPUs, got {rows[0][4]}"
    assert rows[0][5] == 16.0, f"Expected 16.0 RAM GB, got {rows[0][5]}"


def test_cache_prefix_falls_back_to_ec2(tmp_path):
    csv = (
        "Service,Custom Usage Type,Description,Cost ($)\n"
        "Amazon ElastiCache,APS5-NodeUsage:cache.r7g.large,NodeUsage:cache.r7g.large,100.00\n"
    )
    rows = _run_ingest_catalog(tmp_path, csv)
    assert len(rows) == 1
    # cache.r7g.large falls back to r7g.large (vcpus: 2, ram_gb: 16.0)
    assert rows[0][4] == 2, f"Expected 2 vCPUs, got {rows[0][4]}"
    assert rows[0][5] == 16.0, f"Expected 16.0 RAM GB, got {rows[0][5]}"


def test_spot_row_resolves_specs(tmp_path):
    csv = (
        "Service,Custom Usage Type,Description,Cost ($)\n"
        "Amazon Elastic Compute Cloud,,inf1.xlarge Linux/UNIX Spot Instance-hour,100.00\n"
    )
    rows = _run_ingest_catalog(tmp_path, csv)
    assert len(rows) == 1
    # inf1.xlarge (vcpus: 4, ram_gb: 8.0)
    assert rows[0][3] == "inf1.xlarge"
    assert rows[0][4] == 4, f"Expected 4 vCPUs for inf1.xlarge, got {rows[0][4]}"
    assert rows[0][5] == 8.0, f"Expected 8.0 RAM GB for inf1.xlarge, got {rows[0][5]}"


def test_unknown_size_not_guessed(tmp_path):
    csv = (
        "Service,Custom Usage Type,Description,Cost ($)\n"
        "Amazon Relational Database Service,APS5-Multi-AZUsage:db.m7g.foobar,Multi-AZUsage:db.m7g.foobar,100.00\n"
    )
    rows = _run_ingest_catalog(tmp_path, csv)
    assert len(rows) == 1
    # Unresolved specs stay NULL
    assert rows[0][4] is None, f"Expected NULL vcpus, got {rows[0][4]}"
    assert rows[0][5] is None, f"Expected NULL ram_gb, got {rows[0][5]}"

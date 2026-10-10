"""
Faithfulness of the aggregated "Usage (with Units)" bill parser (Finding D).

This coarse export gives ONE total cost per service plus a combined usage string,
with no per-component cost and no instance type. The parser must present it AS-IS
(the bill is the only source of truth) — never fabricate a cost split or stamp a
default instance type (previously it split by hardcoded weights and stamped
t3.medium). Runs ingest.py as a subprocess over a synthetic bill in a temp dir.
"""
import os
import subprocess
import sys

import duckdb

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INGEST = os.path.join(_SKILL, "scripts", "ingest.py")


def _run(tmp_path, csv_text):
    job = tmp_path
    (job / "input.csv").write_text(csv_text, encoding="utf-8")
    env = {**os.environ, "SKILL_DIR": _SKILL}
    subprocess.run([sys.executable, _INGEST], cwd=str(job), env=env,
                   capture_output=True, text=True, timeout=120)
    db = job / "projection-audit" / "projection.duckdb"
    assert db.exists(), "ingest did not produce a catalog"
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT product, usage_type, operation, aws_amortized_cost "
            "FROM aws_li_catalog"
        ).fetchall()
    finally:
        con.close()
    return rows


def test_aggregated_bill_presented_as_is_no_fabrication(tmp_path):
    rows = _run(
        tmp_path,
        "Month,Service,Usage (with Units),Cost (USD)\n"
        "2026-06,Amazon EC2,730 Hrs; 1000 GB-Month,4812.00\n"
        "2026-06,Amazon S3,5000 GB-Month; 200000 Requests,118.50\n",
    )
    blob = " ".join(str(c) for r in rows for c in r).lower()
    # No fabricated instance type.
    assert "t3.medium" not in blob, "parser fabricated a t3.medium instance type"

    # EC2 cost is the real per-service total, NOT a split fraction of it.
    ec2 = [r for r in rows if r[0] == "Amazon EC2"]
    assert len(ec2) == 1, f"expected one faithful EC2 row, got {ec2}"
    assert abs(ec2[0][3] - 4812.00) < 0.01, f"EC2 cost was altered/split: {ec2[0][3]}"
    # The real usage string is preserved.
    assert "730 Hrs".lower() in ec2[0][2].lower() and "gb-month" in ec2[0][2].lower()

    # Total reconciles to the bill (no cost lost or invented).
    assert abs(sum(r[3] for r in rows) - 4930.50) < 0.01

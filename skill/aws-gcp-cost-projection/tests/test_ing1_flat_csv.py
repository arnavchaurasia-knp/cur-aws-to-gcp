import os
import duckdb
import pytest
from scripts.ingest import load_raw


def test_ing1_flat_csv_ingests_all_rows_not_one_per_chunk(tmp_path):
    # Create a 100-row flat CSV (Cost-Explorer style with Description, Usage Quantity, Amount in USD, Region)
    csv_file = tmp_path / "flat_bill.csv"
    lines = ["Description,Region,Usage Quantity,Amount in USD,Date\n"]
    expected_total = 0.0
    for i in range(100):
        cost = 10.0 + i
        expected_total += cost
        lines.append(f"Amazon Elastic Compute Cloud running Linux/UNIX BoxUsage:t3.medium,us-east-1,10.0 Hrs,{cost:.2f},2026-10-01\n")

    csv_file.write_text("".join(lines), encoding="utf-8")

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))

    # Load raw
    load_raw(con, str(csv_file))

    # Check raw row count
    raw_count = con.execute("SELECT count(*) FROM aws_raw").fetchone()[0]
    assert raw_count == 100, f"Expected 100 rows in aws_raw, got {raw_count}"

    # Also test chunk boundary with 5100 rows to ensure chunks > 5000 work across chunks
    con.close()


def test_ing1_flat_csv_chunk_boundary(tmp_path):
    csv_file = tmp_path / "large_flat_bill.csv"
    lines = ["Description,Region,Usage Quantity,Amount in USD,Date\n"]
    for i in range(5050):
        lines.append(f"Amazon Simple Storage Service S3-Storage-ByteHrs,us-east-1,1.0 GB-Mo,1.50,2026-10-01\n")

    csv_file.write_text("".join(lines), encoding="utf-8")

    con = duckdb.connect()
    load_raw(con, str(csv_file))

    raw_count = con.execute("SELECT count(*) FROM aws_raw").fetchone()[0]
    assert raw_count == 5050, f"Expected 5050 rows in aws_raw, got {raw_count}"
    con.close()


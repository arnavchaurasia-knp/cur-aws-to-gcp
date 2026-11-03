import os
import tempfile
import duckdb
import pytest
from scripts.ingest import quote_ident, _read_into_aws_raw, _detect_foreign_currency


def test_quote_ident_sanitizes_malicious_column_header():
    # Attack payload attempting SQL breakout via column identifier
    payload = 'x" FROM aws_raw; COPY(SELECT 1)TO \'pwn.csv\'; --'
    quoted = quote_ident(payload)

    # Must be safely quoted and must not contain raw unescaped quotes or breakout
    assert ';' not in quoted, "Semicolons must be stripped or neutralized"
    assert '--' not in quoted, "SQL comment markers must be neutralized"
    assert quoted.startswith('"') and quoted.endswith('"')

    # Verify DuckDB can safely execute a SELECT with this identifier on an empty table
    con = duckdb.connect()
    # Create table with sanitized identifier
    con.execute(f"CREATE TABLE test_table ({quoted} VARCHAR)")
    # Select from it
    con.execute(f"SELECT {quoted} FROM test_table")
    con.close()


def test_sec2_malicious_cur_header_does_not_execute_injected_sql(tmp_path):
    # Create a CSV file with single quotes in filename and malicious SQL injection in header
    malicious_col = 'x" FROM aws_raw; COPY(SELECT 1)TO \'pwn.csv\'; --'
    pwn_file = tmp_path / "pwn.csv"
    if pwn_file.exists():
        pwn_file.unlink()

    # Filename containing single quote
    cur_file = tmp_path / "user's_cur_bill.csv"
    cur_file.write_text(
        f'lineItem/LineItemDescription,{malicious_col},lineItem/UnblendedCost\n'
        'Test Item 1,malicious_val,123.45\n',
        encoding='utf-8'
    )

    con = duckdb.connect()
    _read_into_aws_raw(con, str(cur_file))

    # Run _detect_foreign_currency which inspects raw columns
    cols = [c[1] for c in con.execute("PRAGMA table_info('aws_raw')").fetchall()]
    assert any("pwn" in c.lower() or "lineitem" in c.lower() for c in cols)

    # Execute currency detection (which runs queries on columns)
    _detect_foreign_currency(cols, con)

    # CRITICAL: Verify pwn.csv was NEVER created anywhere
    assert not pwn_file.exists(), "Attacker SQL injection (COPY TO pwn.csv) executed!"
    assert not os.path.exists("pwn.csv"), "Attacker SQL injection executed in working directory!"

    con.close()


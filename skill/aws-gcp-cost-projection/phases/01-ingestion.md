# Phase 1 — Ingestion

**Run by:** main agent. Mechanical script phase, no judgment calls.
**Reads:** the input bill file path.
**Writes:** `aws_raw`, `aws_li_catalog` in `projection-audit/projection.duckdb`.

## Execution

This phase used to require you to manually inspect CSV/Parquet files, extract instance types using regex, perform region mapping, and build the catalog in DuckDB via SQL.

**That is no longer required.** A deterministic Python script now does all of this perfectly without hallucination.

Run the script:

```bash
python3 scripts/ingest.py
```

Check `failure.txt` after running the script. If the file exists and has content, it means the input bill was structurally unprocessable. In that case, you should stop and let the orchestrator handle the exit. Otherwise, the phase is complete.

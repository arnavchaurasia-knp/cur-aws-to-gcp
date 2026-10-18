import json
import os
import tempfile
import pytest
from diagnostics import diag, read_all, ERROR, WARN, INFO


def test_diag_writes_and_reads():
    with tempfile.TemporaryDirectory() as td:
        diag(ERROR, "mapper_error", "test_src.py", "test message",
             rows=10, usd=123.45, phase=2, detail={"foo": "bar"}, job_dir=td)
        recs = read_all(td)
        assert len(recs) == 1
        r = recs[0]
        assert r["level"] == ERROR
        assert r["code"] == "mapper_error"
        assert r["source"] == "test_src.py"
        assert r["message"] == "test message"
        assert r["rows"] == 10
        assert r["usd"] == 123.45
        assert r["phase"] == 2
        assert r["detail"] == {"foo": "bar"}


def test_diag_never_raises():
    # Unwritable or invalid paths and junk types must never raise
    diag(ERROR, None, None, None, rows="not an int", usd="not a float", job_dir="Z:\\non_existent_drive_12345\\impossible")
    diag("invalid_level", 12345, {}, [], rows=-5, usd=-10.5, job_dir="")
    recs = read_all("Z:\\non_existent_drive_12345\\impossible")
    assert recs == []

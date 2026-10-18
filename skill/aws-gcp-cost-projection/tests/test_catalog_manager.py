"""
Tests for catalog_manager.py — the consolidated GCP SKU catalog tool.

These exercise the credential-free / job-DB-free paths:
  * integrity_check_file      — OK / MISSING / CORRUPT / EMPTY / DEGRADED
  * _atomic_write_gz          — original file intact when the write fails
  * cmd_refresh promote-gate  — partial/collapsed fetches are NOT promoted
  * cmd_refresh rebuild-gate  — a still-unhealthy source REFUSES the duckdb rebuild
  * cmd_refresh no-creds      — no token => no-op, nothing written
  * cmd_check config error    — missing services.json => exit 2

No GCP credentials, no network, no job projection.duckdb required. The one
subprocess (build_catalog_index.py) is mocked so no real duckdb is built.
"""

import gzip
import json
import os
from unittest.mock import patch, MagicMock

import pytest

import catalog_manager as cm


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sku(sid="SKU-1", priced=True, **over):
    o = {
        "skuId": sid,
        "description": "Standard Storage",
        "serviceRegions": ["global"],
        "pricingInfo": [{
            "pricingExpression": {
                "usageUnit": "GiBy.mo",
                "tieredRates": [{"startUsageAmount": 0,
                                 "unitPrice": {"units": "0", "nanos": 20000000}}],
            },
            "effectiveTime": "2026-07-26T07:00:00Z",
        }],
    }
    if not priced:
        o["pricingInfo"] = [{"pricingExpression": {"usageUnit": "GiBy.mo"}}]
    o.update(over)
    return o


def _write_gz(path, objs):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(objs, f)


def _skill(tmp_path, service_id="AAAA-BBBB-CCCC", skus=None, baseline=None,
           display="Test Service"):
    """Build a minimal skill dir: data/skus/<sid>.json.gz + services.json + META."""
    data = tmp_path / "data"
    (data / "skus").mkdir(parents=True)
    if skus is not None:
        _write_gz(str(data / "skus" / f"{service_id}.json.gz"), skus)
    (data / "services.json").write_text(
        json.dumps([{"displayName": display, "serviceId": service_id}]),
        encoding="utf-8")
    if baseline is not None:
        (data / "CATALOG_META.json").write_text(
            json.dumps({"fetched_at": "2026-07-26T07:00:00Z",
                        "sku_count": baseline,
                        "sku_counts": {service_id: baseline}}),
            encoding="utf-8")
    return cm._paths(str(tmp_path)), service_id


# ---------------------------------------------------------------------------
# integrity_check_file
# ---------------------------------------------------------------------------

def test_integrity_ok(tmp_path):
    path = str(tmp_path / "s.json.gz")
    _write_gz(path, [_sku("A"), _sku("B")])
    r = cm.integrity_check_file(path)
    assert r["status"] == "OK"
    assert r["count"] == 2 and r["priced"] == 2


def test_integrity_missing(tmp_path):
    r = cm.integrity_check_file(str(tmp_path / "nope.json.gz"))
    assert r["status"] == "MISSING"


def test_integrity_corrupt_not_gzip(tmp_path):
    path = str(tmp_path / "bad.json.gz")
    with open(path, "wb") as f:
        f.write(b"this is not gzip data")
    r = cm.integrity_check_file(path)
    assert r["status"] == "CORRUPT"


def test_integrity_truncated_gzip(tmp_path):
    """A half-written .gz (valid header, truncated body) must be CORRUPT, not OK."""
    path = str(tmp_path / "trunc.json.gz")
    _write_gz(path, [_sku("A"), _sku("B")])
    raw = open(path, "rb").read()
    with open(path, "wb") as f:
        f.write(raw[: len(raw) // 2])   # chop it in half
    r = cm.integrity_check_file(path)
    assert r["status"] == "CORRUPT"


def test_integrity_empty_list(tmp_path):
    path = str(tmp_path / "empty.json.gz")
    _write_gz(path, [])
    assert cm.integrity_check_file(path)["status"] == "EMPTY"


def test_integrity_collapsed_vs_baseline(tmp_path):
    path = str(tmp_path / "collapse.json.gz")
    _write_gz(path, [_sku("A")])                      # 1 SKU now
    r = cm.integrity_check_file(path, baseline_count=100)  # baseline was 100
    assert r["status"] == "DEGRADED"
    assert any("collapsed" in i for i in r["issues"])


def test_integrity_no_pricing(tmp_path):
    path = str(tmp_path / "nopricing.json.gz")
    _write_gz(path, [_sku("A", priced=False), _sku("B", priced=False)])
    r = cm.integrity_check_file(path)
    assert r["status"] == "DEGRADED"
    assert any("unitPrice" in i for i in r["issues"])


def test_integrity_missing_required_fields(tmp_path):
    path = str(tmp_path / "missingfields.json.gz")
    # >50% of SKUs lack required fields (no serviceRegions / pricingInfo)
    _write_gz(path, [{"skuId": "A", "description": "x"},
                     {"skuId": "B", "description": "y"},
                     _sku("C")])
    r = cm.integrity_check_file(path)
    assert r["status"] == "DEGRADED"


# ---------------------------------------------------------------------------
# _atomic_write_gz
# ---------------------------------------------------------------------------

def test_atomic_write_roundtrip(tmp_path):
    path = str(tmp_path / "out.json.gz")
    cm._atomic_write_gz(path, [_sku("A")])
    with gzip.open(path, "rt", encoding="utf-8") as f:
        assert json.load(f)[0]["skuId"] == "A"


def test_atomic_write_keeps_original_on_failure(tmp_path):
    """If serialization fails mid-write, the original file is untouched and no
    .tmp residue is left behind."""
    path = str(tmp_path / "keep.json.gz")
    cm._atomic_write_gz(path, [_sku("ORIGINAL")])
    before = open(path, "rb").read()

    unserializable = {"bad": {1, 2, 3}}   # a set is not JSON-serializable
    with pytest.raises(TypeError):
        cm._atomic_write_gz(path, unserializable)

    assert open(path, "rb").read() == before            # original intact
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert leftovers == []                               # no temp residue


# ---------------------------------------------------------------------------
# cmd_refresh — promote gate (never overwrite a good file with a bad fetch)
# ---------------------------------------------------------------------------

def test_refresh_does_not_promote_collapsed_fetch(tmp_path):
    """Baseline 10; API returns 1 SKU (collapsed) => existing file kept."""
    p, sid = _skill(tmp_path, skus=[_sku(f"S{i}") for i in range(10)], baseline=10)
    src = os.path.join(p["skus"], f"{sid}.json.gz")

    with patch.object(cm, "_gcp_token", return_value=("key", "x")), \
         patch.object(cm, "_fetch_service_skus", return_value=([_sku("ONLY")], True)), \
         patch.object(cm.subprocess, "run", return_value=MagicMock(returncode=0)):
        cm.cmd_refresh(p, refresh_all=True)

    with gzip.open(src, "rt", encoding="utf-8") as f:
        kept = json.load(f)
    assert len(kept) == 10            # collapsed fetch rejected, original retained


def test_refresh_does_not_promote_incomplete_fetch(tmp_path):
    """complete=False => partial result discarded, existing file kept."""
    p, sid = _skill(tmp_path, skus=[_sku(f"S{i}") for i in range(10)], baseline=10)
    src = os.path.join(p["skus"], f"{sid}.json.gz")

    with patch.object(cm, "_gcp_token", return_value=("key", "x")), \
         patch.object(cm, "_fetch_service_skus",
                      return_value=([_sku(f"S{i}") for i in range(20)], False)), \
         patch.object(cm.subprocess, "run", return_value=MagicMock(returncode=0)):
        cm.cmd_refresh(p, refresh_all=True)

    with gzip.open(src, "rt", encoding="utf-8") as f:
        kept = json.load(f)
    assert len(kept) == 10            # incomplete fetch discarded


# ---------------------------------------------------------------------------
# cmd_refresh — rebuild gate (never compile a bad source into catalog.duckdb)
# ---------------------------------------------------------------------------

def test_refresh_refuses_rebuild_from_unhealthy_source(tmp_path):
    """Source file is EMPTY and the fetch can't fix it => rebuild is refused
    (build_catalog_index.py is never invoked) and the call returns 1."""
    p, sid = _skill(tmp_path, skus=[], baseline=10)   # EMPTY source

    run_mock = MagicMock(return_value=MagicMock(returncode=0))
    with patch.object(cm, "_gcp_token", return_value=("key", "x")), \
         patch.object(cm, "_fetch_service_skus", return_value=([], False)), \
         patch.object(cm.subprocess, "run", run_mock):
        rc = cm.cmd_refresh(p, refresh_all=True)

    assert rc == 1
    run_mock.assert_not_called()      # never rebuilt from a broken source


# ---------------------------------------------------------------------------
# cmd_refresh — no credentials
# ---------------------------------------------------------------------------

def test_refresh_no_creds_is_noop(tmp_path):
    p, sid = _skill(tmp_path, skus=[_sku("A")], baseline=1)
    src = os.path.join(p["skus"], f"{sid}.json.gz")
    before = open(src, "rb").read()

    run_mock = MagicMock()
    with patch.object(cm, "_gcp_token", return_value=None), \
         patch.object(cm.subprocess, "run", run_mock):
        rc = cm.cmd_refresh(p, refresh_all=True)

    assert rc == 1
    run_mock.assert_not_called()
    assert open(src, "rb").read() == before   # nothing written


# ---------------------------------------------------------------------------
# cmd_check — config error
# ---------------------------------------------------------------------------

def test_check_missing_services_json(tmp_path):
    (tmp_path / "data").mkdir()
    p = cm._paths(str(tmp_path))
    assert cm.cmd_check(p) == 2

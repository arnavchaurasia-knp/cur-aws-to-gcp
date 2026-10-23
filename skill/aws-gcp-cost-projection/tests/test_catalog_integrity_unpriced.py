"""The source-integrity check must tell a broken SKU dump apart from a service
the Cloud Billing API genuinely does not price.

Regression: after the 2026-10-23 catalog refresh, GCP stopped returning a $0
tiered rate for Firebase's single subscription-plan SKU and started returning
an empty tieredRates with summary "Sku is not being priced by default.".
The check read that as a structurally broken dump and refused to build
catalog.duckdb at all -- which fails the Docker image build, so no deploy
could ship for a reason that had nothing to do with the deploy.
"""
import gzip, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from catalog_manager import integrity_check_file


def _write(tmp_path, skus):
    p = tmp_path / "svc.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(skus, f)
    return str(p)


def _sku(sku_id, *, tiered=None, summary=""):
    return {
        "skuId": sku_id,
        "description": f"desc {sku_id}",
        "serviceRegions": ["global"],
        "pricingInfo": [{
            "summary": summary,
            "pricingExpression": {"usageUnit": "mo", "tieredRates": tiered or []},
        }],
    }


def test_explicitly_unpriced_service_is_healthy(tmp_path):
    """Every SKU unpriced, but the API says so -> not a broken dump."""
    p = _write(tmp_path, [_sku("FB-1", summary="Sku is not being priced by default.")])
    res = integrity_check_file(p)
    assert res["status"] == "OK", res["issues"]
    assert res["unpriced_by_design"] == 1
    assert res["priced"] == 0


def test_silently_unpriced_service_is_still_degraded(tmp_path):
    """No rate and no explanation is exactly what a broken fetch looks like."""
    p = _write(tmp_path, [_sku("X-1"), _sku("X-2")])
    res = integrity_check_file(p)
    assert res["status"] == "DEGRADED"
    assert any("unitPrice" in i for i in res["issues"])


def test_partly_explained_absence_is_degraded(tmp_path):
    """One SKU explained, one not -> the unexplained one still condemns it."""
    p = _write(tmp_path, [
        _sku("A", summary="Sku is not being priced by default."),
        _sku("B"),
    ])
    assert integrity_check_file(p)["status"] == "DEGRADED"


def test_priced_service_unaffected(tmp_path):
    p = _write(tmp_path, [_sku("P", tiered=[{"startUsageAmount": 0,
               "unitPrice": {"currencyCode": "USD", "units": "0", "nanos": 500}}])])
    res = integrity_check_file(p)
    assert res["status"] == "OK" and res["priced"] == 1


def test_real_firebase_dump_is_healthy():
    """The actual shipped file -- the one that blocked the build."""
    data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "skus", "5DD3-CA5C-D8EA.json.gz")
    if not os.path.exists(data):
        return
    assert integrity_check_file(data)["status"] == "OK"

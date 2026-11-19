"""Regression tests for resolve_sku(prefer_single_region=...) — the systemic
multi-region ('nam5 over Iowa') 2x over-projection guard.

Audit 2026-11-19: a generic desc_pattern can match BOTH a single-region SKU
(e.g. "Cloud Firestore Read Ops Iowa", REGIONAL, serving us-central1) and a
MULTI_REGIONAL SKU (e.g. "North America 5"/"Nam7", which lists us-central1 as a
member region) priced ~2x higher. '_select' returns the first candidate in
catalog file order, so the pick is non-deterministic between them.

prefer_single_region=True (opt-in) promotes the SKU that genuinely serves the
region as a single region. It must NOT change the default (off) behavior, because
GCP's multi-region-by-default services (PD snapshots, GCS) have intentional
multi-region mappings.
"""

import pytest

from scripts.apply_static_mappings import lookup_sku_in_catalog, resolve_sku


def _geo_type(svc, sku_id):
    """Return the geoTaxonomy.type of a resolved SKU id, for assertions."""
    import gzip, json, os
    here = os.path.dirname(__file__)
    data = os.path.join(os.path.dirname(here), "data")
    raw = json.load(open(os.path.join(data, "services.json")))
    svcmap = {e.get("displayName") or e.get("name"): (e.get("serviceId") or e.get("id")) for e in raw}
    sid = svcmap.get(svc)
    if not sid:
        return None
    skus = json.load(gzip.open(os.path.join(data, "skus", f"{sid}.json.gz"), "rt", encoding="utf-8"))
    if isinstance(skus, dict):
        skus = skus.get("skus", [])
    for s in skus:
        i = s.get("skuId") or s.get("sku_id") or s.get("name", "").split("/")[-1]
        if i == sku_id:
            return (s.get("geoTaxonomy", {}) or {}).get("type")
    return None


@pytest.mark.parametrize("pattern", [
    r"^Cloud Firestore Read Ops\b",
    r"^Cloud Firestore Entity Writes\b",
])
def test_firestore_generic_pattern_prefers_single_region(pattern):
    """With prefer_single_region, a generic Firestore ops pattern resolves to the
    single-region (REGIONAL) SKU for the target region, not the 2x multi-region one."""
    flagged = resolve_sku("Cloud Firestore", pattern, "us-central1", prefer_single_region=True)
    assert flagged, f"{pattern} should resolve for us-central1"
    assert _geo_type("Cloud Firestore", flagged.sku_id) == "REGIONAL", (
        f"{pattern} with prefer_single_region must resolve to a REGIONAL SKU, "
        f"got {flagged.sku_id} ({_geo_type('Cloud Firestore', flagged.sku_id)})"
    )


def test_prefer_single_region_is_off_by_default():
    """The flag is opt-in: the default (unflagged) resolver must not be perturbed —
    a region with no single-region SKU keeps the generic multi-region/global rate.
    Static IP (us-central1) has no single-region SKU; the generic MULTI_REGIONAL
    'Static Ip Charge' (66A2-68EA-56BE) must still win by default."""
    default = lookup_sku_in_catalog("Compute Engine", r"Static IP Charge", "us-central1")[0]
    assert default == "66A2-68EA-56BE", (
        f"default Static IP resolution changed to {default} — the single-region "
        "preference must stay opt-in and not disturb multi-region-by-default services"
    )


def test_firestore_single_region_is_cheaper_than_multi():
    """Sanity: the single-region SKU the flag selects is genuinely the cheaper
    (2x) side of the trap, confirming the fix removes over-projection, not under."""
    import gzip, json, os
    here = os.path.dirname(__file__)
    data = os.path.join(os.path.dirname(here), "data")
    raw = json.load(open(os.path.join(data, "services.json")))
    svcmap = {e.get("displayName") or e.get("name"): (e.get("serviceId") or e.get("id")) for e in raw}
    skus = json.load(gzip.open(os.path.join(data, "skus", f"{svcmap['Cloud Firestore']}.json.gz"), "rt", encoding="utf-8"))
    if isinstance(skus, dict):
        skus = skus.get("skus", [])

    def rate(sid):
        for s in skus:
            i = s.get("skuId") or s.get("sku_id") or s.get("name", "").split("/")[-1]
            if i == sid:
                tr = s["pricingInfo"][0]["pricingExpression"]["tieredRates"][-1]["unitPrice"]
                return int(tr.get("units", 0)) + tr.get("nanos", 0) / 1e9
        return None

    single = resolve_sku("Cloud Firestore", r"^Cloud Firestore Read Ops\b", "us-central1", prefer_single_region=True)
    assert single
    r_single = rate(single.sku_id)
    assert r_single is not None and r_single <= 5e-07, (
        f"single-region Read Ops rate {r_single} should be the ~$0.30/M (3e-7) side, not the 2x nam5 ($0.60/M)"
    )

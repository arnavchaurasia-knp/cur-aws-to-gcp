"""
Tests for scripts/verify_compute_availability.py's rollup_regions().

Real bug this guards against: compute.googleapis.com's machineTypes.
aggregatedList, called with returnPartialSuccess=true, returns a zone-scope
entry as {"warning": {"code": "NO_RESULTS_ON_PAGE", ...}} whenever that zone
simply has zero matching results for the current page -- this is normal,
constant, and happens on nearly every real paginated call; it is NOT a
failure. The only trustworthy "this zone's data is incomplete" signal is the
top-level `unreachables` list (or a warning code other than
NO_RESULTS_ON_PAGE). Conflating the benign empty-page warning with a real
failure would make the prober record entire regions as confidently
"unavailable" for families it never actually failed to find -- it just
didn't look properly. rollup_regions() must drop (not falsely populate) any
region touched by a genuine failure.
"""
import os
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from verify_compute_availability import rollup_regions, _zone_to_region, _family_prefix


def test_zone_to_region():
    assert _zone_to_region("asia-south2-a") == "asia-south2"
    assert _zone_to_region("us-central1-f") == "us-central1"


def test_family_prefix():
    assert _family_prefix("n4d-standard-8") == "n4d"
    assert _family_prefix("c4a-standard-4") == "c4a"


def test_rollup_records_false_for_absent_family_in_clean_region():
    items = {
        "zones/asia-south2-a": [{"name": "n4d-standard-8"}],
        "zones/asia-south1-a": [{"name": "n4d-standard-8"}, {"name": "c4a-standard-4"}],
    }
    regions = rollup_regions(items, unreachable_zones=set())
    assert regions["asia-south2"]["n4d"] is True
    assert regions["asia-south2"]["c4a"] is False
    assert regions["asia-south1"]["c4a"] is True


def test_rollup_excludes_region_with_unreachable_zone():
    # asia-south2-b failed to query (real failure, e.g. an outage or
    # permission gap on that specific zone) -- asia-south2 as a whole must
    # be dropped from the result entirely, never asserted with partial data.
    items = {
        "zones/asia-south2-a": [{"name": "n4d-standard-8"}],
        "zones/asia-south1-a": [{"name": "c4a-standard-4"}],
    }
    regions = rollup_regions(items, unreachable_zones={"asia-south2-b"})
    assert "asia-south2" not in regions
    assert "asia-south1" in regions


def test_rollup_does_not_exclude_region_for_normal_empty_zone():
    # A zone with genuinely zero machine types (the NO_RESULTS_ON_PAGE case)
    # simply isn't a key in `items` at all -- it must NOT cause its region to
    # be excluded, only a real entry in unreachable_zones should.
    items = {
        "zones/asia-south2-a": [{"name": "n4d-standard-8"}],
        # asia-south2-b, -c: no key at all (normal "nothing here" case)
    }
    regions = rollup_regions(items, unreachable_zones=set())
    assert "asia-south2" in regions
    assert regions["asia-south2"]["n4d"] is True

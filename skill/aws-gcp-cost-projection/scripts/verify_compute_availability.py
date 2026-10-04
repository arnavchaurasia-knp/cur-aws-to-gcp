#!/usr/bin/env python3
"""
verify_compute_availability.py — Live Compute Engine machine-family
availability prober.

Why this exists
----------------
Every cost comparison in apply_static_mappings.py (cheapest_in_scope(),
_family_hourly_rate()) decides "is family X available in region Y" by
checking whether the bundled GCP Cloud Billing Catalog prices a matching
SKU there. That catalog is a PRICING system, not a deployability system —
Google's own Compute Engine machine-type-availability has been confirmed to
disagree with it in BOTH directions:

  - FALSE POSITIVE (catalog says available, isn't really): N4D had a real,
    priced "N4D Instance Core/Ram running in Delhi" SKU in the 2026-04-30
    and 2026-07-27 catalog snapshots, but a customer's own GCP Console
    VM-creation wizard rejected N4D in asia-south2 on 2026-07-24. Hand-
    curated into apply_static_mappings.py's _KNOWN_PHANTOM_AVAILABILITY as
    a one-off, manually-discovered exception.

  - That same exception is now ITSELF stale: querying the real
    machineTypes.aggregatedList API on 2026-10-04 shows n4d-standard-8 (and
    presumably the rest of the N4D family) genuinely creatable in
    asia-south2-a/b/c today. The hand-curated blacklist has no way to know
    this changed — it would keep silently treating a real, now-available
    GCP option as if it didn't exist, forever, until a human happens to
    re-check it.

This script replaces that single hardcoded exception with a periodically-
refreshed, live-sourced ground truth: it queries the ACTUAL Compute Engine
API (not the Billing Catalog) for every machine-type family available in
every zone, rolls that up to per-region availability, and writes it to
data/compute_family_availability.json. apply_static_mappings.py consults
this file first; if it's missing or too stale, behavior falls back
unchanged to today's catalog-only + _KNOWN_PHANTOM_AVAILABILITY logic —
this script running (or not) can never break a job.

This is a MAINTAINER-time script, run manually or via a periodic refresh
trigger (like catalog_health_check.py / prefetch_skus.py) — never during a
live customer job. It needs a real GCP project with the Compute Engine API
enabled and at least roles/compute.viewer (or roles/viewer) for the calling
identity; the data returned (which machine types exist in which zones) is a
platform-wide fact, not specific to that project's own deployed resources,
so any project with those two things will do.

Usage:
    python3 scripts/verify_compute_availability.py --project PROJECT_ID
    python3 scripts/verify_compute_availability.py  # uses
        GOOGLE_CLOUD_AVAILABILITY_PROJECT env var

Auth: GOOGLE_CLOUD_AVAILABILITY_TOKEN env var (a pre-fetched OAuth2 bearer
token) takes priority; otherwise falls back to `gcloud auth print-access-
token`. The Billing Catalog's API-key auth does NOT work here — reading a
project's own Compute Engine resources requires real IAM-authorized OAuth,
not a bare API key. This is a manually-run maintainer script (not part of
any live job's hot path), so the gcloud subprocess call is used directly
here rather than behind the AGY_ALLOW_GCLOUD_LIVE_FETCH gate that protects
live jobs elsewhere in this codebase -- a maintainer running this by hand
can Ctrl-C it the same way they could any other gcloud command.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
OUT_PATH = os.path.join(DATA_DIR, "compute_family_availability.json")


def _get_token() -> str:
    env_token = os.environ.get("GOOGLE_CLOUD_AVAILABILITY_TOKEN")
    if env_token:
        return env_token
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        ).strip()
    except Exception as e:
        print(f"ERROR: could not obtain a GCP access token ({e}).\n"
              f"Run `gcloud auth login` first, or set GOOGLE_CLOUD_AVAILABILITY_TOKEN.",
              file=sys.stderr)
        sys.exit(1)


def _fetch_all_machine_types(project: str, token: str) -> tuple[dict, set]:
    """Paginate compute.machineTypes.aggregatedList for the whole project.

    Returns (items, unreachable_zones):
      items: {zone_url: [machineType, ...]} for zones that genuinely
        returned (possibly empty) results.
      unreachable_zones: zone names (e.g. "us-central1-b") that the API
        itself reported as UNRELIABLE to query this call -- via the
        top-level `unreachables` field, or a per-zone `warning` whose code
        is anything other than NO_RESULTS_ON_PAGE.

    CONFIRMED REAL (checked directly against the live API, not assumed from
    docs): with returnPartialSuccess=true, a zone with zero matching machine
    types for the current page/filter comes back as {"warning": {"code":
    "NO_RESULTS_ON_PAGE", ...}} -- this is normal, constant, and means
    nothing is wrong; it is NOT a failure signal and must not be treated as
    "this zone has no machine types at all." The only trustworthy signal for
    a genuinely failed/inaccessible zone is the top-level `unreachables`
    list (or an actual non-NO_RESULTS_ON_PAGE warning code, defense in
    depth). Conflating the benign per-page empty-result warning with a real
    failure would make this prober wrongly record entire regions as
    "unavailable" just because one page of a paginated, filtered query
    happened to have nothing for that zone -- exactly the kind of
    "cache returns a confidently wrong answer" bug worth guarding against
    explicitly, not inferring from documentation alone.
    """
    items: dict = {}
    unreachable_zones: set = set()
    page_token = ""
    base = f"https://compute.googleapis.com/compute/v1/projects/{project}/aggregated/machineTypes"
    while True:
        url = f"{base}?maxResults=500&returnPartialSuccess=true"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"ERROR: HTTP {e.code} from Compute Engine API: {body[:500]}", file=sys.stderr)
            sys.exit(1)
        for scope in data.get("unreachables", []) or []:
            unreachable_zones.add(scope.rsplit("/", 1)[-1])
        for zone_key, payload in data.get("items", {}).items():
            zone_name = zone_key.rsplit("/", 1)[-1]
            if "machineTypes" in payload:
                items.setdefault(zone_key, []).extend(payload["machineTypes"])
            else:
                warning_code = (payload.get("warning") or {}).get("code")
                if warning_code and warning_code != "NO_RESULTS_ON_PAGE":
                    unreachable_zones.add(zone_name)
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    return items, unreachable_zones


def _zone_to_region(zone_name: str) -> str:
    """'asia-south2-a' -> 'asia-south2' (GCP's zone naming is always
    <region>-<single-letter-suffix>)."""
    return zone_name.rsplit("-", 1)[0]


def _family_prefix(machine_type_name: str) -> str:
    """'n4d-standard-8' -> 'n4d'; 'c4a-standard-4' -> 'c4a'."""
    return machine_type_name.split("-", 1)[0].lower()


def rollup_regions(items: dict, unreachable_zones: set) -> dict:
    """Roll up {zone: [machineType, ...]} to {region: {family_prefix: bool}},
    explicitly EXCLUDING any region touched by an unreachable zone --
    a region we only partially queried must not assert a confident False for
    families we simply failed to check, so it's left out entirely and the
    caller falls through to whatever other signal it has (the previous
    fallback file, or _KNOWN_PHANTOM_AVAILABILITY) for that region instead.
    Shared by main() (writes the maintainer-refreshed file) and
    apply_static_mappings.py's live in-process fetch, so both paths apply
    this exact same correctness rule rather than risking it drifting between
    two independent implementations.
    """
    region_families: dict[str, set] = {}
    unreliable_regions: set = {_zone_to_region(z) for z in unreachable_zones}
    for zone_key, machine_types in items.items():
        zone_name = zone_key.rsplit("/", 1)[-1]
        region = _zone_to_region(zone_name)
        fams = region_families.setdefault(region, set())
        for mt in machine_types:
            fams.add(_family_prefix(mt["name"]))
    all_prefixes = {p for fams in region_families.values() for p in fams}
    if unreliable_regions:
        print(f"  [warn] {len(unreliable_regions)} region(s) had an unreachable zone this "
              f"fetch, excluded from the result (will fall back to the previous file/known-"
              f"phantom table for these): {sorted(unreliable_regions)}", file=sys.stderr)
    return {
        region: {prefix: (prefix in fams) for prefix in all_prefixes}
        for region, fams in region_families.items()
        if region not in unreliable_regions
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_AVAILABILITY_PROJECT"))
    args = parser.parse_args()

    if not args.project:
        print("ERROR: --project or GOOGLE_CLOUD_AVAILABILITY_PROJECT is required.", file=sys.stderr)
        sys.exit(1)

    token = _get_token()
    print(f"Fetching machine-type availability from project {args.project!r}...")
    items, unreachable_zones = _fetch_all_machine_types(args.project, token)

    if not items:
        print("ERROR: no zones returned — check project/permissions.", file=sys.stderr)
        sys.exit(1)

    regions = rollup_regions(items, unreachable_zones)
    all_prefixes = sorted({p for fams in regions.values() for p in fams})

    # Explicit True/False per (region, prefix) — a prefix absent from a
    # region's set is recorded as False, a real fact, not an unasked question.
    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": f"https://compute.googleapis.com/compute/v1/projects/{args.project}/aggregated/machineTypes",
        "project_used_for_probe": args.project,
        "note": ("Platform-wide machine-type availability per region, not specific to "
                 "project_used_for_probe's own deployed resources. Regions with an "
                 "unreachable zone during this fetch are omitted entirely rather than "
                 "recorded with incomplete data. Regenerate with "
                 "scripts/verify_compute_availability.py."),
        "regions": {region: regions[region] for region in sorted(regions)},
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"Wrote {OUT_PATH}: {len(regions)} regions, {len(all_prefixes)} families")
    print(f"Families seen: {all_prefixes}")


if __name__ == "__main__":
    main()

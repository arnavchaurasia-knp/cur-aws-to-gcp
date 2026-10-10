#!/usr/bin/env python3
"""
refresh_aws_product_codes.py [--offline <index.json>]

Regenerate data/aws-product-codes.json — the authoritative AWS service-code
(= CUR line_item_product_code) -> canonical_service map that Layer 1 keys on.

Why this exists
---------------
The free-text alias dict (data/aws-aliases.json) is reactive: it only grows
when a human notices an unrecognized-products table and patches it, and it
keys on the unstable free-text product NAME, which has unbounded variants.

AWS's Price List bulk index publishes the authoritative, closed set of
service codes (the same enum that appears verbatim in every CUR row's
lineItem/ProductCode). Those codes are stable for the life of a service and
new services appear the day they launch. Keying on the code instead of the
name turns an open-ended normalization problem into a bounded lookup.

This script pulls that list and writes code -> canonical, aligning each code
to the SAME canonical keys the existing alias dict already uses (so every
downstream service_map rule keeps matching). Services with no existing
canonical key get a derived one (lowercased code, Amazon/AWS prefix stripped).

Run it periodically (or from a scheduled task) to pick up new AWS services.
It is a maintenance tool, not part of the per-bill pipeline — the per-bill
path only READS data/aws-product-codes.json.

Usage
-----
    python3 refresh_aws_product_codes.py
    python3 refresh_aws_product_codes.py --offline path/to/index.json
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "..", "data")
_ALIASES_PATH = os.path.join(_DATA, "aws-aliases.json")
_OUT_PATH = os.path.join(_DATA, "aws-product-codes.json")

_INDEX_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json"

_PREFIX_RE = re.compile(r"^(amazon|aws)", re.IGNORECASE)


def _load_aliases() -> dict[str, str]:
    with open(_ALIASES_PATH, encoding="utf-8") as fh:
        return json.load(fh).get("aliases", {})


def _fetch_index(offline: str | None) -> dict:
    if offline:
        with open(offline, encoding="utf-8") as fh:
            return json.load(fh)
    # nosemgrep: the URL is a fixed AWS-owned constant, not user input.
    req = urllib.request.Request(_INDEX_URL, headers={"User-Agent": "aws-gcp-cost-projection/refresh"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        return json.loads(resp.read().decode("utf-8"))


def _canonical_for(code: str, aliases: dict[str, str]) -> str:
    """Resolve a service code to a canonical key.

    Preference order:
      1. The existing alias dict keyed on the full lowercased code
         (e.g. "amazonec2" -> "ec2") — keeps downstream rules aligned.
      2. The alias dict keyed on the Amazon/AWS-stripped code
         (e.g. "ec2" -> "ec2").
      3. A derived key: lowercased code with the Amazon/AWS prefix stripped.
    """
    low = code.lower()
    if low in aliases:
        return aliases[low]
    stripped = _PREFIX_RE.sub("", low)
    if stripped in aliases:
        return aliases[stripped]
    return stripped or low


def build(offline: str | None = None) -> dict:
    aliases = _load_aliases()
    index = _fetch_index(offline)
    offers = index.get("offers", {})
    codes = sorted(offers.keys(), key=str.lower)

    code_map: dict[str, str] = {}
    for code in codes:
        code_map[code] = _canonical_for(code, aliases)

    # Reverse map: canonical -> one representative code, used by tier-3 to stamp
    # a product_code on no-code bills (PDF/CSV/spreadsheet) once a row's service
    # is resolved to a canonical. When several codes share a canonical, pick
    # deterministically: prefer the code whose Amazon/AWS-stripped form IS the
    # canonical (the "clean" code, e.g. AmazonS3 for "s3"), then shortest, then
    # alphabetical — so regeneration is stable.
    canonical_to_code: dict[str, str] = {}
    for canonical in sorted(set(code_map.values())):
        candidates = [c for c, k in code_map.items() if k == canonical]
        candidates.sort(key=lambda c: (_PREFIX_RE.sub("", c.lower()) != canonical, len(c), c.lower()))
        canonical_to_code[canonical] = candidates[0]

    return {
        "_meta": {
            "description": (
                "AWS service code (= CUR line_item_product_code) -> canonical_service. "
                "Authoritative, closed set pulled from the AWS Price List bulk index. "
                "Regenerate with scripts/refresh_aws_product_codes.py — do NOT hand-edit "
                "the codes; add curated overrides to data/aws-aliases.json instead."
            ),
            "source": _INDEX_URL,
            "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "publication_date": index.get("publicationDate"),
            "count": len(code_map),
        },
        "codes": code_map,
        "canonical_to_code": canonical_to_code,
    }


def main() -> int:
    offline = None
    args = sys.argv[1:]
    if args and args[0] == "--offline":
        if len(args) < 2:
            print("--offline requires a path to an index.json", file=sys.stderr)
            return 2
        offline = args[1]

    try:
        doc = build(offline)
    except Exception as exc:  # pragma: no cover - network/IO failure path
        print(f"refresh_aws_product_codes: failed to build ({exc})", file=sys.stderr)
        return 1

    with open(_OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(
        f"refresh_aws_product_codes: wrote {doc['_meta']['count']} codes to "
        f"{os.path.relpath(_OUT_PATH, _HERE)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

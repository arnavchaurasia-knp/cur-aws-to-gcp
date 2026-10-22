#!/usr/bin/env python3
"""
promote_equivalence.py — Promote provisional or author new equivalence claims into data/equivalences.json.

Requirements:
- Dry run by default; --apply commits changes.
- Requires reviewer identity (--reviewer) and review comment (--comment).
- Flips decided_by to "human" and stamps decided_on with current UTC date (YYYY-MM-DD).
- Atomic write via tempfile + os.replace.
- Validates the resulting JSON before replacing the file.
- Never auto-promotes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(_HERE)
sys.path.insert(0, _SKILL_DIR)
sys.path.insert(0, os.path.join(_SKILL_DIR, "scripts"))

from scripts.equivalences import (  # noqa: E402
    DEFAULT_EQUIVALENCES_PATH,
    VALID_DEGREES,
    load_equivalences,
)


def promote_claim(
    attribute: str,
    aws: str,
    gcp: str,
    reviewer: str,
    comment: str,
    scope: str | None = None,
    degree: str | None = None,
    confidence_cap: float | None = None,
    apply: bool = False,
    filepath: str | None = None,
) -> int:
    target_path = filepath or DEFAULT_EQUIVALENCES_PATH

    if not reviewer or not reviewer.strip():
        print("ERROR: A reviewer identity (--reviewer) is required. Promotion cannot be anonymous.", file=sys.stderr)
        return 1
    if not comment or not comment.strip():
        print("ERROR: A promotion comment (--comment) is required explaining the equivalence rationale.", file=sys.stderr)
        return 1

    reviewer = reviewer.strip()
    comment = comment.strip()
    attr_k = attribute.strip().lower()
    aws_k = aws.strip().lower()
    gcp_k = gcp.strip().lower()

    if not os.path.exists(target_path):
        print(f"ERROR: Crosswalk file not found at {target_path}", file=sys.stderr)
        return 1

    with open(target_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    if attr_k not in doc:
        doc[attr_k] = []

    claims_list = doc[attr_k]
    matched_idx = -1

    for i, c in enumerate(claims_list):
        c_aws = str(c.get("aws", "")).strip().lower()
        c_gcp = str(c.get("gcp", "")).strip().lower()
        if c_aws == aws_k and c_gcp == gcp_k:
            if scope:
                c_scopes = [s.strip().lower() for s in c.get("scope", [])]
                if scope.strip().lower() in c_scopes:
                    matched_idx = i
                    break
            else:
                matched_idx = i
                break

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    print("\n" + "=" * 60)
    print("EQUIVALENCE PROMOTION REVIEW")
    print("=" * 60)

    if matched_idx >= 0:
        existing = dict(claims_list[matched_idx])
        print(f"Found existing claim in family '{attr_k}':")
        print(f"  AWS: {existing.get('aws')} -> GCP: {existing.get('gcp')}")
        print(f"  Current degree:     {existing.get('degree')}")
        print(f"  Current decided_by: {existing.get('decided_by')} (on: {existing.get('decided_on')})")
        print(f"  Current comment:    {existing.get('comment')}")

        new_claim = dict(existing)
        new_claim["decided_by"] = "human"
        new_claim["decided_on"] = today_str
        new_claim["comment"] = f"[Reviewed by {reviewer} on {today_str}] {comment}"
        if degree:
            if degree not in VALID_DEGREES:
                print(f"ERROR: Degree '{degree}' is invalid. Valid: {sorted(VALID_DEGREES)}", file=sys.stderr)
                return 1
            new_claim["degree"] = degree
        if confidence_cap is not None:
            new_claim["confidence_cap"] = float(confidence_cap)
        if scope:
            scopes = [s.strip() for s in scope.split(",") if s.strip()]
            new_claim["scope"] = scopes

        print("\nProposed changes:")
        print(f"  [+] decided_by:     {existing.get('decided_by')} -> human")
        print(f"  [+] decided_on:     {existing.get('decided_on')} -> {today_str}")
        print(f"  [+] reviewer:       {reviewer}")
        print(f"  [+] comment:        {new_claim['comment']}")
        if degree and degree != existing.get("degree"):
            print(f"  [+] degree:         {existing.get('degree')} -> {degree}")
        if confidence_cap is not None:
            print(f"  [+] confidence_cap: {new_claim.get('confidence_cap')}")

        claims_list[matched_idx] = new_claim

    else:
        # Creating a brand new claim
        if not degree:
            print(
                f"ERROR: No existing claim found for ({attr_k}, {aws_k}, {gcp_k}). "
                f"--degree is required to author a new claim.",
                file=sys.stderr,
            )
            return 1
        if degree not in VALID_DEGREES:
            print(f"ERROR: Degree '{degree}' is invalid. Valid: {sorted(VALID_DEGREES)}", file=sys.stderr)
            return 1
        if not scope:
            print(
                f"ERROR: No existing claim found for ({attr_k}, {aws_k}, {gcp_k}). "
                f"--scope is required to author a new claim.",
                file=sys.stderr,
            )
            return 1

        scope_list = [s.strip() for s in scope.split(",") if s.strip()]
        new_claim = {
            "aws": aws.strip(),
            "gcp": gcp.strip(),
            "degree": degree,
            "scope": scope_list,
            "comment": f"[Authored by {reviewer} on {today_str}] {comment}",
            "decided_by": "human",
            "decided_on": today_str,
        }
        if confidence_cap is not None:
            new_claim["confidence_cap"] = float(confidence_cap)
        elif degree == "inexact":
            print("ERROR: Degree 'inexact' requires --confidence-cap.", file=sys.stderr)
            return 1

        print(f"Authoring NEW human-reviewed claim in family '{attr_k}':")
        print(f"  AWS:            {new_claim['aws']} -> GCP: {new_claim['gcp']}")
        print(f"  Degree:         {new_claim['degree']}")
        print(f"  Scope:          {new_claim['scope']}")
        print(f"  Reviewer:       {reviewer}")
        print(f"  Comment:        {new_claim['comment']}")
        if "confidence_cap" in new_claim:
            print(f"  Confidence Cap: {new_claim['confidence_cap']}")

        claims_list.append(new_claim)

    print("-" * 60)

    if not apply:
        print("Dry run complete. No files modified. Run with --apply to commit this promotion.")
        return 0

    # Write atomically
    tmp_path = target_path + f".tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")

        # Validate before replacing
        load_equivalences(tmp_path, reload=True)

        os.replace(tmp_path, target_path)
        print(f"SUCCESS: Promoted claim in {target_path}")
        return 0
    except Exception as e:
        print(f"ERROR: Failed promoting claim to {target_path}: {e}", file=sys.stderr)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return 1


def main():
    parser = argparse.ArgumentParser(description="Promote an equivalence claim to human-reviewed status.")
    parser.add_argument("--attribute", required=True, help="Attribute family (e.g. storage_class).")
    parser.add_argument("--aws", required=True, help="AWS attribute value.")
    parser.add_argument("--gcp", required=True, help="GCP attribute value.")
    parser.add_argument("--reviewer", required=True, help="Reviewer identity / email (required).")
    parser.add_argument("--comment", required=True, help="Review comment / justification (required).")
    parser.add_argument("--scope", default=None, help="Scope (e.g. storage_time).")
    parser.add_argument("--degree", default=None, help="Degree of equivalence.")
    parser.add_argument("--confidence-cap", type=float, default=None, help="Confidence cap (for inexact degree).")
    parser.add_argument("--file", default=None, help="Path to equivalences.json (optional override).")
    parser.add_argument("--apply", action="store_true", help="Apply changes (defaults to dry run).")

    args = parser.parse_args()

    return promote_claim(
        attribute=args.attribute,
        aws=args.aws,
        gcp=args.gcp,
        reviewer=args.reviewer,
        comment=args.comment,
        scope=args.scope,
        degree=args.degree,
        confidence_cap=args.confidence_cap,
        apply=args.apply,
        filepath=args.file,
    )


if __name__ == "__main__":
    raise SystemExit(main())

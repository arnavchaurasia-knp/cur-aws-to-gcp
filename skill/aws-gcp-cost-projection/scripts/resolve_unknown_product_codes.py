#!/usr/bin/env python3
"""
resolve_unknown_product_codes.py <projection.duckdb>

Tier-3 of Layer 1: backfill product_code on bills that carry no AWS service
code (PDF invoices, Cost Explorer CSV, cloud-billing spreadsheet exports).

Machine-readable CUR bills already carry lineItem/ProductCode, which ingest.py
persists and aws_normalizer keys on. The no-code formats only carry a human
service NAME, so every downstream consumer falls back to fragile free-text
matching for them. This script gives those rows the SAME stable key by
resolving the name to an authoritative AWS service code and stamping it.

Pipeline (B: deterministic-first, LLM only for the residue)
-----------------------------------------------------------
Per DISTINCT product name on the bill (resolved once, not per row):
  1. name cache   — data/learned-product-codes.json  (0 cost, self-building)
  2. deterministic — canonical_service(name) -> canonical -> representative code
                     via data/aws-product-codes.json's canonical_to_code  (0 cost)
  3. residue only — ONE batched `agy` call: the unknown names + the closed
                    272-code list; the model classifies each name into that
                    set. Output is pydantic-validated and every code not in the
                    authoritative set is dropped. Accepted names are written to
                    the cache so they never hit the model again.

Then product_code (and canonical_service) are stamped on the matching rows.

Safety / production properties
------------------------------
- No-op for machine-readable CUR (those rows already have a code).
- If `agy` is not on PATH, tiers 1-2 still run and stamp; tier-3 is skipped.
- Deterministic in steady state (cache); the same bill resolves identically.
- Exit 0 always — a resolution miss is non-fatal (name path still applies).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aws_normalizer import canonical_service  # noqa: E402

try:
    import duckdb
except Exception as exc:  # pragma: no cover
    print(f"resolve_unknown_product_codes: duckdb unavailable ({exc}); skipping", file=sys.stderr)
    sys.exit(0)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "..", "data")
_CODES_PATH = os.path.join(_DATA, "aws-product-codes.json")
_CACHE_PATH = os.path.join(_DATA, "learned-product-codes.json")

_LLM_BATCH = 100  # max names per agy call; chunk larger residues


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _load_codes() -> tuple[dict[str, str], dict[str, str], set[str]]:
    with open(_CODES_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    codes = doc.get("codes", {})                     # code -> canonical
    canonical_to_code = doc.get("canonical_to_code", {})  # canonical -> code
    valid = set(codes.keys())
    return codes, canonical_to_code, valid


def _load_cache() -> dict[str, str]:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("names", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(names: dict[str, str]) -> None:
    doc = {
        "_meta": {
            "description": (
                "Learned product-name -> AWS service code map, authored by "
                "resolve_unknown_product_codes.py's tier-3 agy pass for no-code "
                "bills (PDF/CSV/spreadsheet). Self-building: every accepted entry "
                "is validated against data/aws-product-codes.json's closed set. "
                "Safe to commit; grows across bills so the model is asked less over time."
            ),
        },
        "names": dict(sorted(names.items())),
    }
    with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Tiers 1-2: deterministic resolution (pure, unit-testable)
# ---------------------------------------------------------------------------
def resolve_deterministic(
    name: str,
    cache: dict[str, str],
    canonical_to_code: dict[str, str],
    valid_codes: set[str],
) -> str | None:
    """Return an authoritative code for a product name via cache then canonical,
    or None if neither resolves it (-> tier-3 candidate)."""
    if not name:
        return None
    key = name.strip().lower()
    cached = cache.get(key)
    if cached and cached in valid_codes:
        return cached
    canonical = canonical_service(name)  # name-only path (no code on these bills)
    if canonical:
        code = canonical_to_code.get(canonical)
        if code and code in valid_codes:
            return code
    return None


# ---------------------------------------------------------------------------
# Tier 3: LLM residue (validated with pydantic)
# ---------------------------------------------------------------------------
def _validate_llm_output(raw: str, valid_codes: set[str]) -> dict[str, str]:
    """Parse the model's JSON and keep ONLY {name: code} pairs whose code is a
    member of the authoritative set. Anything else (hallucinated code, "unknown",
    malformed) is dropped. Pydantic enforces the shape; the set enforces the enum."""
    from pydantic import BaseModel, RootModel, ValidationError

    class _Mapping(RootModel[dict[str, str]]):
        pass

    text = raw.strip()
    # Strip accidental ```json fences.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = _Mapping.model_validate_json(text).root
    except (ValidationError, ValueError):
        # Last-ditch: find the first {...} block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {}
        try:
            parsed = _Mapping.model_validate_json(m.group(0)).root
        except (ValidationError, ValueError):
            return {}
    out: dict[str, str] = {}
    for name, code in parsed.items():
        if isinstance(code, str) and code in valid_codes:
            out[name.strip().lower()] = code
    return out


def _build_prompt(names: list[str], valid_codes: list[str]) -> str:
    code_list = ", ".join(sorted(valid_codes))
    name_list = "\n".join(f"- {n}" for n in names)
    return (
        "You map AWS billing service names to their official AWS service code "
        "(the value AWS uses in a Cost and Usage Report's lineItem/ProductCode).\n\n"
        "Return ONLY a JSON object mapping each input name to exactly one code "
        "from the allowed list below. If you are not confident a name is one of "
        "these services, OMIT it from the output entirely — do not guess.\n\n"
        f"Allowed codes (choose only from these):\n{code_list}\n\n"
        f"Names to classify:\n{name_list}\n\n"
        'Output format: {"<name>": "<AWSCode>", ...}. No prose, no code fences.'
    )


def _call_agy(prompt: str) -> str:
    agy_bin = shutil.which("agy")
    if not agy_bin:
        return ""
    try:
        result = subprocess.run(
            [agy_bin, "--print-only", "--model", os.environ.get("AGY_MODEL", "gemini-3.5-flash")],
            input=prompt, text=True, capture_output=True, timeout=120,
        )
        return (result.stdout or "").strip()
    except Exception as exc:  # pragma: no cover - subprocess/timeout path
        print(f"resolve_unknown_product_codes: agy call failed ({exc})", file=sys.stderr)
        return ""


def resolve_residue_via_llm(
    names: list[str], valid_codes: set[str]
) -> dict[str, str]:
    """Resolve the unknown names with batched agy calls; returns {name_lower: code}
    for validated hits only. Empty dict if agy is unavailable."""
    if not shutil.which("agy") or not names:
        return {}
    resolved: dict[str, str] = {}
    code_list = sorted(valid_codes)
    for i in range(0, len(names), _LLM_BATCH):
        batch = names[i : i + _LLM_BATCH]
        out = _call_agy(_build_prompt(batch, code_list))
        if out:
            resolved.update(_validate_llm_output(out, valid_codes))
    return resolved


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        return 0
    db_path = sys.argv[1]

    codes, canonical_to_code, valid_codes = _load_codes()
    cache = _load_cache()

    con = duckdb.connect(db_path)
    cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    if "product_code" not in cols:
        # Nothing to backfill into; a pre-change DB. Non-fatal.
        print("resolve_unknown_product_codes: no product_code column; skipping")
        con.close()
        return 0
    has_canonical = "canonical_service" in cols

    # DISTINCT product names that carry NO code (the no-code formats) and real cost.
    rows = con.execute(
        "SELECT DISTINCT product FROM aws_li_catalog "
        "WHERE (product_code IS NULL OR product_code = '') "
        "  AND product IS NOT NULL AND product <> '' "
        "  AND COALESCE(aws_amortized_cost, 0) > 0"
    ).fetchall()
    names = [r[0] for r in rows]
    if not names:
        print("resolve_unknown_product_codes: no no-code rows to resolve")
        con.close()
        return 0

    resolved: dict[str, str] = {}   # name -> code (original-case name key for UPDATE)
    residue: list[str] = []
    for name in names:
        code = resolve_deterministic(name, cache, canonical_to_code, valid_codes)
        if code:
            resolved[name] = code
        else:
            residue.append(name)

    n_det = len(resolved)

    # Tier 3 — batched agy over the residue, validated, cached.
    if residue:
        llm_hits = resolve_residue_via_llm(residue, valid_codes)
        if llm_hits:
            for name in residue:
                code = llm_hits.get(name.strip().lower())
                if code:
                    resolved[name] = code
                    cache[name.strip().lower()] = code
            _save_cache(cache)

    n_llm = len(resolved) - n_det

    # Stamp product_code (and canonical_service) on the matching no-code rows.
    updates = [
        (code, codes.get(code), name)
        for name, code in resolved.items()
    ]
    if updates:
        if has_canonical:
            con.executemany(
                "UPDATE aws_li_catalog SET product_code = ?, canonical_service = ? "
                "WHERE product = ? AND (product_code IS NULL OR product_code = '')",
                updates,
            )
        else:
            con.executemany(
                "UPDATE aws_li_catalog SET product_code = ? "
                "WHERE product = ? AND (product_code IS NULL OR product_code = '')",
                [(code, name) for code, _canon, name in updates],
            )
        con.commit()

    con.close()
    print(
        f"resolve_unknown_product_codes: {len(names)} distinct no-code names -> "
        f"{len(resolved)} resolved ({n_det} deterministic, {n_llm} via agy), "
        f"{len(names) - len(resolved)} still unresolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

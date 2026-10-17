#!/usr/bin/env python3
"""
schema_mapper.py — map a bill's columns onto our canonical fields, once per bill.

PROTOTYPE. Not wired into the pipeline. This is migration step M6 of
docs/DESIGN_field_registry_and_schema_mapping.md, built so the model call can
actually be exercised against real bills.

What it does
------------
1. Profiles every column of the input file (count, distinct ratio, numeric
   share, sentinel share, sample values). Zero model cost.
2. Applies the deterministic alias table first. Anything it resolves never
   reaches the model.
3. Sends ONLY the residue — column names + profile + 3 samples — to agy, in one
   call, asking for a JSON mapping. The model never sees a data row's worth of
   work and never sees the whole file.
4. VERIFIES every proposal against the field's contract using the column's real
   values, and rejects any that fails. The model proposes; the data disposes.
5. Prints a report and writes schema_mapping.json.

Usage
-----
    python scripts/schema_mapper.py <bill.csv>                 # full run (calls agy)
    python scripts/schema_mapper.py <bill.csv> --dry-run       # print the prompt, no call
    python scripts/schema_mapper.py <bill.csv> --response r.json   # verify a canned reply

agy is resolved from $AGY_PATH, else "agy" on PATH. Reads .env if present.
Exits 0 always — this is a diagnostic tool.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

# --------------------------------------------------------------------------
# Field contracts. In the real implementation these come from
# data/field_registry.json; inlined here so the prototype is self-contained.
# cardinality limits are the distinct/non-null ratio a field may have.
# --------------------------------------------------------------------------
CARD = {"categorical": 0.50, "high": 0.95, "unique": 1.01}

FIELDS = {
    "cost":             dict(kind="number", desc="line item cost in the bill's currency"),
    "usage_quantity":   dict(kind="number", desc="quantity consumed"),
    "unit_price":       dict(kind="number", desc="price per unit"),
    "instance_vcpus":   dict(kind="number", int_like=True, max_value=1024,
                             desc="vCPU count of the instance"),
    "instance_memory":  dict(kind="quantity", unit_re=r"(GiB|GB|MiB|TiB)",
                             desc="instance memory, e.g. '192 GiB'"),
    "currency":         dict(kind="code", max_len=4, max_distinct=6, upper=True,
                             desc="ISO currency code"),
    "account_id":       dict(kind="identifier", cardinality="categorical",
                             desc="cloud account identifier (repeats across rows)"),
    "region":           dict(kind="text", cardinality="categorical",
                             desc="cloud region"),
    "service":          dict(kind="text", cardinality="categorical",
                             desc="AWS service name"),
    "operating_system": dict(kind="text", cardinality="categorical",
                             desc="OS of the instance"),
    "line_item_type":   dict(kind="text", cardinality="categorical",
                             desc="CUR LineItemType (Usage, Tax, Credit...)"),
    "product_code":     dict(kind="text", cardinality="categorical",
                             desc="AWS product code, e.g. AmazonEC2"),
    "operation":        dict(kind="text", cardinality="high",
                             desc="billing operation, e.g. RunInstances"),
    "usage_type":       dict(kind="text", cardinality="high",
                             desc="AWS usage type, e.g. APS3-BoxUsage:m5.large"),
    "instance_type":    dict(kind="text", cardinality="high",
                             desc="instance type, e.g. m5.large"),
    "description":      dict(kind="text", cardinality="unique",
                             desc="free-text line description"),
}

# Deterministic aliases — tried before the model. Anything matched here is free.
ALIASES = {
    "cost": ["lineitem/unblendedcost", "unblendedcost", "cost", "cost ($)", "amount in usd"],
    "usage_quantity": ["lineitem/usageamount", "usageamount", "usage quantity", "consumed"],
    "unit_price": ["lineitem/unblendedrate", "unblendedrate", "unit price"],
    "currency": ["lineitem/currencycode", "currencycode", "currency"],
    "region": ["product/region", "region", "location"],
    "service": ["product/productname", "productname", "service", "category"],
    "usage_type": ["lineitem/usagetype", "usagetype", "meter", "custom usage type"],
    "operation": ["lineitem/operation", "operation"],
    "line_item_type": ["lineitem/lineitemtype", "lineitemtype"],
    "product_code": ["lineitem/productcode", "productcode"],
    "description": ["lineitem/lineitemdescription", "description"],
    "account_id": ["lineitem/usageaccountid", "account id"],
    "instance_type": ["product/instancetype", "instancetype"],
    "instance_vcpus": ["product/vcpu", "vcpu", "vcpus"],
    "instance_memory": ["product/memory", "memory"],
    "operating_system": ["product/operatingsystem", "operatingsystem"],
}

NUM = re.compile(r"^-?[\d,]*\.?\d+$")
QTY = re.compile(r"^-?[\d,]*\.?\d+\s*([A-Za-z]+)$")
SENTINEL = {"n/a", "na", "none", "null", "-", "unknown", "not applicable"}


def norm(s: str) -> str:
    return (s or "").strip().lower().replace("/", "_").replace(" ", "_")


_ALIAS_INDEX = {norm(a): f for f, al in ALIASES.items() for a in al}


# --------------------------------------------------------------------------
# Profiling
# --------------------------------------------------------------------------
def profile(values):
    v = [str(x).strip() for x in values if x is not None and str(x).strip() != ""]
    total = len(values) or 1
    if not v:
        return dict(n=0, filled=0.0, distinct=0, ratio=0.0, numeric=0.0,
                    sentinel=0.0, unit=None, maxlen=0, upper=0.0, sample=[])
    n = len(v)
    units = [QTY.match(x).group(1) for x in v if QTY.match(x)]
    return dict(
        n=n, filled=round(n / total, 3), distinct=len(set(v)),
        ratio=round(len(set(v)) / n, 3),
        numeric=round(sum(1 for x in v if NUM.match(x)) / n, 3),
        sentinel=round(sum(1 for x in v if x.lower() in SENTINEL) / n, 3),
        unit=(max(set(units), key=units.count) if units else None),
        maxlen=max(len(x) for x in v),
        upper=round(sum(1 for x in v if x.isupper()) / n, 3),
        sample=v[:3],
    )


# --------------------------------------------------------------------------
# Verification — the model proposes, the data disposes
# --------------------------------------------------------------------------
def verify(field, values):
    """-> (verdict, reason) with verdict in {'accept','warn','reject'}"""
    c = FIELDS.get(field)
    if not c:
        return "reject", f"unknown field {field!r}"
    p = profile(values)
    if p["n"] == 0:
        return "reject", "column is empty"
    k = c["kind"]

    if k == "number":
        if p["numeric"] < 0.9:
            return "reject", f"{p['numeric']:.0%} numeric"
        if c.get("int_like") and any(
                "." in str(x) for x in values if NUM.match(str(x).strip() or "x")):
            return "reject", "non-integer values"
        if c.get("max_value"):
            mx = max(float(str(x).replace(",", ""))
                     for x in values if NUM.match(str(x).strip() or "x"))
            if mx > c["max_value"]:
                return "reject", f"max {mx:g} > {c['max_value']}"
        return "accept", f"numeric, {p['distinct']} distinct"

    if k == "identifier":
        lim = CARD[c.get("cardinality", "categorical")]
        if p["ratio"] > lim:
            return "reject", (f"distinct ratio {p['ratio']} > {lim} — "
                              f"an identifier must repeat, this is unique per row")
        return "accept", f"identifier, {p['distinct']} distinct"

    if k == "quantity":
        if not p["unit"] or not re.search(c["unit_re"], p["unit"]):
            return "reject", f"no recognised unit (saw {p['unit']!r})"
        return "accept", f"unit {p['unit']}"

    if k == "code":
        if p["maxlen"] > c["max_len"]:
            return "reject", f"maxlen {p['maxlen']} > {c['max_len']}"
        if p["distinct"] > c["max_distinct"]:
            return "reject", f"{p['distinct']} distinct > {c['max_distinct']}"
        if c.get("upper") and p["upper"] < 0.8:
            return "reject", "not uppercase codes"
        return "accept", f"{p['distinct']} code(s)"

    if k == "text":
        if p["numeric"] > 0.9:
            return "reject", "numeric column, not text"
        lim = CARD[c.get("cardinality", "categorical")]
        if p["ratio"] > lim:
            return "reject", (f"distinct ratio {p['ratio']} > {lim} for a "
                              f"'{c.get('cardinality')}' field")
        if p["sentinel"] > 0.5:
            return "warn", f"{p['sentinel']:.0%} sentinel values"
        return "accept", f"{p['distinct']} distinct (ratio {p['ratio']})"

    return "reject", f"unknown contract kind {k!r}"


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------
def build_prompt(residue_profiles):
    field_lines = "\n".join(
        f"  {name:<18} {spec.get('kind'):<11} {spec.get('desc','')}"
        for name, spec in FIELDS.items())
    col_lines = []
    for col, p in residue_profiles.items():
        col_lines.append(
            f"  - name: {col!r}\n"
            f"    filled: {p['filled']}  distinct: {p['distinct']}  "
            f"distinct_ratio: {p['ratio']}  numeric_share: {p['numeric']}\n"
            f"    samples: {p['sample']}")
    return f"""You are mapping the columns of a cloud billing file onto a fixed set of fields.

THE ONLY FIELDS THAT EXIST (use these names exactly, or null):
{field_lines}

COLUMNS TO MAP (the deterministic alias table already handled the rest):
{chr(10).join(col_lines)}

RULES
  - Output ONLY a JSON array. No prose, no markdown fence, nothing else.
  - One object per column above, in the same order:
      {{"column": "<exact name>", "field": "<field name or null>",
        "confidence": 0.0-1.0, "reason": "<short>"}}
  - "field" MUST be one of the field names listed above, or null. Never invent one.
  - Use null when the column is not one of those fields. Most columns in a
    billing file are NOT — ids, timestamps, tags, resource ARNs, account names.
    null is the correct and expected answer for them.
  - distinct_ratio near 1.0 means a value unique to each row: that is an id or a
    description, never a category like service or region.
  - Do not guess from the column name alone. The samples and the profile are
    there to confirm or refute it.

Return the JSON array now."""


# --------------------------------------------------------------------------
# agy
# --------------------------------------------------------------------------
def load_dotenv():
    p = os.path.join(REPO, ".env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def call_agy(prompt, timeout_s=300):
    load_dotenv()
    agy = os.environ.get("AGY_PATH") or "agy"
    model = os.environ.get("AGY_MODEL", "gemini-3.8-flash")
    args = [agy, "--dangerously-skip-permissions", "--model", model, "-p", prompt]
    env = os.environ.copy()
    env["ELECTRON_RUN_AS_NODE"] = "1"   # Antigravity.exe is Electron; CLI mode
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout_s, env=env)
    except FileNotFoundError:
        return None, f"agy not found at {agy!r} (set AGY_PATH in .env)"
    except subprocess.TimeoutExpired:
        return None, f"agy timed out after {timeout_s}s"
    if r.returncode != 0:
        return None, f"agy exited {r.returncode}: {(r.stderr or r.stdout)[:400]}"
    return r.stdout, None


def parse_response(text):
    """Pull the JSON array out of whatever the model wrapped it in."""
    if not text:
        return None, "empty response"
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1:
        return None, f"no JSON array found in response: {text[:200]!r}"
    try:
        data = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    if not isinstance(data, list):
        return None, "response is not a list"
    return data, None


# --------------------------------------------------------------------------
def read_columns(path, limit=500):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        rows = [r for i, r in enumerate(rd) if i < limit]   # materialise ONCE
    return {c: [r.get(c, "") for r in rows] for c in cols}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bill")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and exit; no model call")
    ap.add_argument("--response", help="verify a canned JSON response instead of calling agy")
    ap.add_argument("--rows", type=int, default=500)
    ap.add_argument("--out", help="where to write schema_mapping.json (default: CWD)")
    a = ap.parse_args()

    if not os.path.exists(a.bill):
        print(f"not found: {a.bill}")
        return 0
    data = read_columns(a.bill, a.rows)
    if not data:
        print("no columns read")
        return 0
    profiles = {c: profile(v) for c, v in data.items()}

    # Sanity guard: a profiler that sees zero rows makes every verdict
    # meaningless ("column is empty" for everything). Caught exactly this bug
    # while testing — see the design doc.
    if all(p["n"] == 0 for p in profiles.values()):
        print("ERROR: every column profiled as empty — the file or reader is wrong, "
              "not the mapping. Refusing to report verdicts.")
        return 0

    print(f"file   : {os.path.basename(a.bill)}")
    print(f"columns: {len(data)}   rows sampled: {max(p['n'] for p in profiles.values())}\n")

    resolved, residue = {}, {}
    for col in data:
        f = _ALIAS_INDEX.get(norm(col))
        if f:
            v, why = verify(f, data[col])
            resolved[col] = (f, "alias", v, why)
        else:
            residue[col] = profiles[col]

    print("--- resolved by the alias table (no model cost) ---")
    for col, (f, src, v, why) in resolved.items():
        print(f"  {v.upper():<7} {col:<32} -> {f:<18} {why}")
    print(f"\n--- residue for the model: {len(residue)} column(s) ---")
    for col in residue:
        print(f"  {col}")

    prompt = build_prompt(residue)
    if a.dry_run:
        print("\n" + "=" * 78 + "\nPROMPT\n" + "=" * 78)
        print(prompt)
        return 0
    if not residue:
        print("\nnothing left for the model.")
        return 0

    if a.response:
        raw = open(a.response, encoding="utf-8").read()
        err = None
    else:
        print("\ncalling agy ...")
        raw, err = call_agy(prompt)
    if err:
        print(f"MODEL CALL FAILED: {err}")
        return 0

    proposals, perr = parse_response(raw)
    if perr:
        print(f"RESPONSE UNUSABLE: {perr}")
        return 0

    print(f"\n--- model proposals, each verified against the data ---")
    out, acc, rej = [], 0, 0
    for item in proposals:
        col = item.get("column")
        field = item.get("field")
        if col not in data:
            print(f"  IGNORED  {col!r} — not a column in this file")
            continue
        if field in (None, "null", ""):
            print(f"  {'NULL':<8} {col:<32} -> (not one of our fields) "
                  f"{item.get('reason','')[:50]}")
            out.append(dict(column=col, field=None, verdict="null"))
            continue
        if field not in FIELDS:
            print(f"  REJECT   {col:<32} -> {field!r} is not a declared field")
            rej += 1
            continue
        v, why = verify(field, data[col])
        acc += v in ("accept", "warn")
        rej += v == "reject"
        print(f"  {v.upper():<8} {col:<32} -> {field:<18} {why}")
        out.append(dict(column=col, field=field if v != "reject" else None,
                        verdict=v, reason=why,
                        model_confidence=item.get("confidence")))

    print(f"\naccepted {acc}   rejected {rej}")
    # Write beside the CALLER, never into the folder the bill came from — that
    # is usually a user's Downloads and must not be littered with our output.
    dest = a.out or os.path.join(os.getcwd(), "schema_mapping.json")
    try:
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(dict(file=os.path.basename(a.bill),
                           alias_resolved={c: r[0] for c, r in resolved.items()},
                           model_proposals=out), f, indent=2)
        print(f"wrote {dest}")
    except OSError as e:
        print(f"could not write mapping: {e}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:      # diagnostic tool — never explode
        print(f"schema_mapper: {type(e).__name__}: {e}")
        sys.exit(0)

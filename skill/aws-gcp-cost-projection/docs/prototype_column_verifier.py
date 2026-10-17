"""Verifier v3 — fixes the 13.9% false-rejection rate found by v2.

v2's flaw: ONE global ID-like threshold applied to every text field. But
cardinality is a property of the FIELD, not of text in general:
  - description is nearly unique per row BY NATURE (ratio 0.96) — correct
  - service must repeat (ratio 0.01) — a unique one would be wrong
  - account_id is numeric (12-digit AWS account) but is not a measure

So each field declares its expected cardinality class, and numeric identifiers
get their own kind. Tested BOTH ways: false rejects on ground truth, and the
four planted errors from the first round.

Run: python -I verifier_v3.py
"""
import csv
import os
import re

FACETS = r'C:\Users\arnav\Downloads\Facets'
SENTINEL = {'n/a', 'na', 'none', 'null', '-', 'unknown', 'not applicable'}
NUM = re.compile(r"^-?[\d,]*\.?\d+$")
QTY = re.compile(r"^-?[\d,]*\.?\d+\s*([A-Za-z]+)$")

# cardinality: max acceptable distinct-ratio for a text field.
#   categorical  <= 0.50   must repeat (service, region, OS)
#   high         <= 0.95   many values but still a vocabulary (usage_type)
#   unique       <= 1.00   one per row by nature (description, resource id)
CARD = {"categorical": 0.50, "high": 0.95, "unique": 1.01}

CONTRACTS = {
    "cost":             dict(kind="number"),
    "usage_quantity":   dict(kind="number"),
    "unit_price":       dict(kind="number"),
    "instance_vcpus":   dict(kind="number", int_like=True, max_value=1024),
    "instance_memory":  dict(kind="quantity", unit_re=r"(GiB|GB|MiB|TiB)"),
    "currency":         dict(kind="code", max_len=4, max_distinct=6, upper=True),
    # numeric identifier: digits, but NOT a measure — must repeat across rows
    "account_id":       dict(kind="identifier", cardinality="categorical"),
    "region":           dict(kind="text", cardinality="categorical"),
    "service":          dict(kind="text", cardinality="categorical"),
    "operating_system": dict(kind="text", cardinality="categorical"),
    "line_item_type":   dict(kind="text", cardinality="categorical"),
    "product_code":     dict(kind="text", cardinality="categorical"),
    "operation":        dict(kind="text", cardinality="high"),
    "usage_type":       dict(kind="text", cardinality="high"),
    "instance_type":    dict(kind="text", cardinality="high"),
    "description":      dict(kind="text", cardinality="unique"),
}


def profile(values):
    v = [str(x).strip() for x in values if x is not None and str(x).strip() != ""]
    total = len(values) or 1
    if not v:
        return dict(n=0)
    n = len(v)
    units = [QTY.match(x).group(1) for x in v if QTY.match(x)]
    return dict(
        n=n, filled=n / total, distinct=len(set(v)), ratio=len(set(v)) / n,
        numeric=sum(1 for x in v if NUM.match(x)) / n,
        sentinel=sum(1 for x in v if x.lower() in SENTINEL) / n,
        unit=(max(set(units), key=units.count) if units else None),
        maxlen=max(len(x) for x in v), upper=sum(1 for x in v if x.isupper()) / n,
    )


def verify(field, values):
    c = CONTRACTS.get(field)
    if not c:
        return "reject", "no contract for this field"
    p = profile(values)
    if p["n"] == 0:
        return "reject", "column is empty"
    k = c["kind"]

    if k == "number":
        if p["numeric"] < 0.9:
            return "reject", f"{p['numeric']:.0%} numeric"
        if c.get("int_like") and any("." in str(x) for x in values if NUM.match(str(x).strip() or "x")):
            return "reject", "non-integer values"
        if c.get("max_value"):
            mx = max(float(str(x).replace(",", "")) for x in values if NUM.match(str(x).strip() or "x"))
            if mx > c["max_value"]:
                return "reject", f"max {mx:g} > {c['max_value']}"
        return "accept", f"numeric, {p['distinct']} distinct"

    if k == "identifier":
        # may be numeric or text, but must REPEAT — that is what separates an
        # account id from a cost. A unique-per-row numeric column is a measure
        # or a row id, not an identifier dimension.
        lim = CARD[c.get("cardinality", "categorical")]
        if p["ratio"] > lim:
            return "reject", f"distinct ratio {p['ratio']:.2f} > {lim} — not a repeating identifier"
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
            return "reject", f"distinct ratio {p['ratio']:.2f} > {lim} for a '{c.get('cardinality')}' field"
        if p["sentinel"] > 0.5:
            return "warn", f"{p['sentinel']:.0%} sentinel values"
        return "accept", f"{p['distinct']} distinct (ratio {p['ratio']:.2f})"
    return "reject", "unknown kind"


def load(path, limit=500):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        rows = [r for i, r in enumerate(rd) if i < limit]   # materialise ONCE
    return {c: [r.get(c, "") for r in rows] for c in cols}


TRUTH = {
    "aws_cur_style_report_enriched.csv": {
        "lineItem/LineItemType": "line_item_type", "lineItem/ProductCode": "product_code",
        "lineItem/UsageType": "usage_type", "lineItem/Operation": "operation",
        "lineItem/UsageAmount": "usage_quantity", "lineItem/UnblendedCost": "cost",
        "lineItem/UnblendedRate": "unit_price", "lineItem/CurrencyCode": "currency",
        "lineItem/LineItemDescription": "description", "lineItem/UsageAccountId": "account_id",
        "product/ProductName": "service", "product/instanceType": "instance_type",
        "product/vcpu": "instance_vcpus", "product/memory": "instance_memory",
        "product/region": "region", "product/operatingSystem": "operating_system",
    },
    "New bills/May_filtered.csv": {
        "Category": "service", "Subcategory": "usage_type", "Meter": "usage_type",
        "Consumed": "usage_quantity", "Cost": "cost", "Unit Price": "unit_price",
        "Currency": "currency", "Account ID": "account_id",
    },
    "New bills/June_prod_filtered.csv": {
        "Category": "service", "Meter": "usage_type", "Consumed": "usage_quantity",
        "Cost": "cost", "Unit Price": "unit_price", "Currency": "currency",
    },
    "test-bill (1).csv": {
        "Service": "service", "Region": "region", "Custom Usage Type": "usage_type",
        "Description": "description", "Usage Quantity": "usage_quantity", "Cost ($)": "cost",
    },
}

# the four deliberately WRONG mappings from the first round — must still reject
PLANTED = [
    ("aws_cur_style_report_enriched.csv", "lineItem/UsageType", "instance_vcpus"),
    ("New bills/May_filtered.csv", "Location", "region"),
    ("aws_cur_style_report_enriched.csv", "identity/LineItemId", "usage_type"),
    ("New bills/May_filtered.csv", "Resource", "service"),
]

print("=== A. CORRECT mappings — any reject is a FALSE REJECT ===")
tot = rej = warn = 0
for fname, truth in TRUTH.items():
    data = load(os.path.join(FACETS, fname))
    for col, field in truth.items():
        if col not in data:
            continue
        v, why = verify(field, data[col])
        tot += 1; rej += v == "reject"; warn += v == "warn"
        if v != "accept":
            print(f"  {v.upper():<7} {os.path.basename(fname)[:22]:<24}{col:<30}{field:<18}{why}")
print(f"  tested {tot}   accept {tot-rej-warn}   warn {warn}   FALSE REJECT {rej}"
      f"   -> rate {rej/tot:.1%}")

print("\n=== B. PLANTED WRONG mappings — any accept is a FALSE ACCEPT ===")
bad = 0
for fname, col, field in PLANTED:
    data = load(os.path.join(FACETS, fname))
    if col not in data:
        print(f"  (column {col!r} absent)"); continue
    v, why = verify(field, data[col])
    ok = "CAUGHT" if v == "reject" else "MISSED"
    bad += v != "reject"
    print(f"  {ok:<7} {col:<30}-> {field:<18}{v:<8}{why}")
print(f"  planted 4   caught {4-bad}   FALSE ACCEPT {bad}")

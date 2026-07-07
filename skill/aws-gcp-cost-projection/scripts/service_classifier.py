#!/usr/bin/env python3
"""
service_classifier.py <projection.duckdb>

The Service Classification Engine. Applies the curated data/service_map.json
(AWS-service -> category -> GCP-service) to every mapped row so the GCP target is
DETERMINISTIC instead of a per-row LLM guess. This is what eliminates variance
like "QuickSight -> Looker on one row, -> Cloud Storage on another".

Per matched rule:
  - mode 'review' : force gcp_service = target, strategy = 'passthrough'
                    (carry AWS cost — an honest baseline, never an invented GCP
                    figure), clear the SKU, set confidence, stamp the reason.
                    Used for services whose correct GCP target is known but whose
                    precise GCP pricing still needs per-service modelling.
  - mode 'keep'   : the pipeline already prices this well — only STAMP metadata
                    (category + reason), never touch pricing or the mapped SKU.

Rich metadata is written to projection_note as:
  "[<category>] <reason> (rule=service_map_v1, gcp=<target>)"

Idempotent, never fatal. Runs post-merge, before fix_storage_misroute (which is
the generic backstop for anything not in the map).
"""
import json, os, sys
try:
    import duckdb
except Exception as e:  # pragma: no cover
    sys.stderr.write(f"service_classifier: duckdb import failed ({e}); skipping\n")
    sys.exit(0)

RULE_TAG = "service_map_v1"


def _norm(product):
    p = (product or "")
    for pre in ("Amazon ", "AWS "):
        if p.startswith(pre):
            p = p[len(pre):]
    return p.lower()


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    db = sys.argv[1]

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    map_path = os.path.join(skill_dir, "data", "service_map.json")
    if not os.path.exists(map_path):
        sys.stderr.write(f"service_classifier: no {map_path}; skipping\n")
        sys.exit(0)
    rules = json.load(open(map_path)).get("rules", [])
    if not rules:
        sys.exit(0)

    con = duckdb.connect(db)
    rows = con.execute(
        "SELECT DISTINCT c.product FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING(aws_li_key)"
    ).fetchall()

    # Skip comment/divider entries that carry no "match" key.
    rules = [r for r in rules if r.get("match")]

    review_updates, keep_updates, ignore_updates = [], [], []
    stats = {"review": 0, "keep": 0, "ignore": 0, "unmatched": 0}
    for (product,) in rows:
        norm = _norm(product)
        rule = next((r for r in rules if r["match"].lower() in norm), None)
        if not rule:
            stats["unmatched"] += 1
            continue
        note = (f"[{rule['category']}] {rule['reason']} "
                f"(rule={RULE_TAG}, gcp={rule['gcp_service']})")
        mode = rule["mode"]
        # service_map.json confidences are on a 0-100 scale; mapping_confidence
        # everywhere else is 0-1. Averaging the two scales made the report show
        # "Avg Confidence 2399.6%". Normalize at write time.
        conf = rule.get("confidence", 60 if mode == "review" else 90 if mode == "ignore" else 80)
        if conf > 1:
            conf = conf / 100.0
        if mode == "review":
            review_updates.append((rule["gcp_service"], conf, note, product))
            stats["review"] += 1
        elif mode == "ignore":
            ignore_updates.append((rule["gcp_service"], conf, note, product))
            stats["ignore"] += 1
        else:  # keep
            keep_updates.append((conf, note, product))
            stats["keep"] += 1

    if review_updates:
        con.executemany(
            """
            UPDATE aws_li_to_gcp_li SET
                gcp_service = ?, mapping_confidence = ?, projection_note = ?,
                strategy = 'passthrough', gcp_sku_id = NULL
            WHERE aws_li_key IN (
                SELECT aws_li_key FROM aws_li_catalog WHERE product = ?)
            """,
            review_updates,
        )
    if ignore_updates:
        con.executemany(
            """
            UPDATE aws_li_to_gcp_li SET
                gcp_service = ?, mapping_confidence = ?, projection_note = ?,
                strategy = 'ignore', gcp_sku_id = NULL
            WHERE aws_li_key IN (
                SELECT aws_li_key FROM aws_li_catalog WHERE product = ?)
            """,
            ignore_updates,
        )
    if keep_updates:
        con.executemany(
            """
            UPDATE aws_li_to_gcp_li SET mapping_confidence = ?, projection_note = ?
            WHERE aws_li_key IN (
                SELECT aws_li_key FROM aws_li_catalog WHERE product = ?)
            """,
            keep_updates,
        )

    print(f"service_classifier: {stats['review']} review-routed, {stats['ignore']} ignored ($0), "
          f"{stats['keep']} kept, {stats['unmatched']} unmatched product(s)")
    sys.exit(0)


if __name__ == "__main__":
    main()

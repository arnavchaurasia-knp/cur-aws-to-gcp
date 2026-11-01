# WORKORDER — Closed-world guards: allowed_targets enum + usage_type parser

**For the implementation agent. Read fully. Verify in Phase 0.**
Completes the two remaining moves from docs/DESIGN_service_modelling.md. Unit
contracts (Move 4) are done (WORKORDER_unit_dimensions). This adds the other two
closed-world guards: a per-service **allowed-target enum** (kills hallucinated GCP
targets by construction) and a formalized **usage_type parser** (classify on typed
fields, not substrings).

---

## 0. ORIENTATION — run first, every time

| # | Probe (from `skill/aws-gcp-cost-projection/`) | Tells you | RESULT |
|---|---|---|---|
| P1 | `grep -rn "allowed_targets\|allowed-targets" data/service_map.json data/service_specs.json scripts/` | is any allowed-target machinery present yet? | |
| P2 | `grep -n "echo\|gcp_service_echo\|Manual Review" scripts/validate_fix.py \| head` | the existing Layer-4 echo gate the enum gate sits beside | |
| P3 | `ls scripts/ \| grep -iE "region_prefix|schema_mapper|unit_equivalence"` | the usage_type parsing pieces that already exist to unify | |
| P4 | `grep -rn "canonical\|parse.*usage_type\|_parse" scripts/aws_normalizer.py scripts/spec_dispatch.py \| head` | where a typed parse would plug in | |

---

## 1. GOAL

**Part A — allowed_targets closed enum.**
Each service (service_map/service_specs) declares the set of GCP services its rows
may map to. A post-merge Layer-4 gate REJECTS any row whose `gcp_service` is not in
its service's allowed set — turning "Vertex AI on an ELB row" from an undetectable
hallucination into a gated failure. This generalizes the existing echo gate (which
only catches AWS-name echoes) to also catch plausible-but-wrong GCP names.

**Part B — usage_type parser.**
One typed `parse(usage_type, operation, product) -> fields` (region, family,
sub-type, unit, class) that spec_dispatch predicates can match on, replacing raw
substring regex where practical. Unifies the scattered region_prefix / schema_mapper
/ unit logic. Regex stays ONLY inside the parser (parsing is its legit job); the
decision reads parsed fields.

---

## 2. INVARIANTS
- allowed_targets failure is LOUD (gate), never a silent pass — same discipline as
  the unit guard (WORKORDER_unit_dimensions).
- The enum is data (per service), not code; adding a service is a data edit.
- A service with no declared allowed_targets is NOT silently exempt — either it
  declares them or the gate reports it as unconstrained (so coverage is visible).
- The parser is additive: it must not change any currently-correct classification
  (prove with spec_parity.py — differ stays 0 except logged intentional fixes).

---

## 3. PHASES

### Phase 1 — allowed_targets data + gate
- [ ] Add `allowed_targets: [...]` per service rule (seed from the targets each
      service's rows actually resolve to today — derive from a real-job sweep).
- [ ] Add a post-merge gate in validate_fix.py: reject rows whose gcp_service is
      outside its service's allowed set; emit a $-weighted diagnostic.
- [ ] Report services with no allowed_targets declared (coverage, not a pass).

### Phase 2 — usage_type parser
- [ ] Implement one typed `parse()` consolidating region_prefix + unit + family
      extraction; return a typed field dict.
- [ ] Migrate the highest-value spec predicates to match on parsed fields; keep
      regex only inside parse().
- [ ] spec_parity.py: differ/ambiguous must not regress; log any intentional move.

### Phase 3 — Measure
- [ ] Use the accuracy harness: count/$ of allowed_target rejections and any
      classification shifts from the parser; confirm net improvement.

---

## 4. PROGRESS LOG (append-only)
- 2026-10-31 Work order created. Unit contracts done; allowed_targets + typed
  usage_type parser are the two remaining closed-world guards from
  DESIGN_service_modelling.md. NEXT: run P1–P4, seed allowed_targets from a real-job
  target sweep, add the gate.

- 2026-11-01 Part A DONE, Part B WIRED (review-fix pass):
  - Part A (allowed_targets): the gate is built and **data-driven** — `allowed_by_cs`
    in validate_fix.py is derived at load time from each service_map rule's
    (canonical_service -> gcp_service), enforced inside `check_llm_slice_invariants`
    (guards exactly the LLM-decided rows where hallucinations occur) and reports
    `services_without_allowed_targets` for coverage. No hardcoded enum. ✓
  - Part B (usage_type parser): FIX — the parser was written+tested but imported
    NOWHERE (dead code, zero effect). Now wired into spec_dispatch:
    `_parsed(row)` computes the typed parse once per row (cached); `_eval_block`
    gained `family_in` / `sub_type_in` / `class_in` / `parsed_unit_in` predicates;
    `_charge_type_matches` gained an additive `parsed_match` OR-branch (can only ADD
    coverage, never remove a match); `is_default_charge_type` updated so a
    parsed_match-only charge-type is not mistaken for a catch-all.
  - Demonstrated end-to-end on `vpc_nat_hours`: a hyphenated `NAT-Gateway-Hours`
    spelling the regex misses now dispatches to flat_hourly via the parser
    (sub_type=gateway_hours), with an `exclude_any_field_re` guard so the sibling
    TransitGateway/VPN/LB rows are NOT caught — no ambiguity. Locked by
    `test_spec_dispatch.py::test_parser_wired_parsed_match_additive_coverage`.
  - Validation: `spec_parity.py tests/corpus` reports ambiguous=0, spec_gap=0,
    no_spec=0 (no regression); test_spec_partition green; full suite green.
  BACKLOG (do against REAL job DBs, not the 6-row synthetic corpus): migrate more
  spec_gap-prone charge-types to parsed_match per service, each proven ambiguous=0
  via spec_parity before landing. The infra is in place; this is now data edits.

## 5. IF STUCK
Seed allowed_targets from what services ACTUALLY map to on real bills (observed, not
imagined); a too-tight enum that rejects correct rows is as bad as none. Widen by
evidence, narrow by review. For parsed_match: it is OR-only (additive). Before adding
one, check `matching_charge_types()` stays unambiguous for the sibling rows it could
also match — guard with exclude_any_field_re as vpc_nat_hours does.

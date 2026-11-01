# WORKORDER — Resolve the REAL per-commitment discount from the bill (not defaults)

**For the implementation agent. Read fully. Verify in Phase 0.**
Follow-up to WORKORDER_od_vs_od_sp_ri. That work established OD-vs-OD and uses the
real `publicOnDemandCost` when present, but falls back to a PORTFOLIO-DEFAULT rate
(assumptions: aws_ri_1yr/3yr/sp/spot_discount_pct) labelled `reconstructed_default`.
The bill often contains enough to compute the ACTUAL discount — this work order
uses that data before falling back to a default. "All data used."

Related (do not duplicate): WORKORDER_commitment_basis_tiers.md covers the
disclosed-default-rate path; this is the data-driven path that should run BEFORE it.

---

## 0. ORIENTATION — run first, every time

| # | Probe (from `skill/aws-gcp-cost-projection/`) | Tells you | RESULT |
|---|---|---|---|
| P1 | `grep -n "apply_od_basis\|reconstructed_default\|public_on_demand_cost\|sp_ri_discount_pct" scripts/ingest.py` | the current OD-basis logic to extend | |
| P2 | `grep -rniE "savingsplan(arn)?\|reservationarn\|SavingsPlanRate\|amortized.*upfront\|RIFee\|recurringfee" scripts/ingest.py` | which SP/RI identifier + rate fields are already ingested | |
| P3 | inspect a real CUR: which of `savingsPlan/SavingsPlanARN`, `reservation/ReservationARN`, `savingsPlan/SavingsPlanRate`, `pricing/publicOnDemandCost` are populated | what data is actually available to resolve a per-commitment rate | |
| P4 | `grep -n "aws_ri_1yr_discount_pct\|aws_sp_discount_pct" data/assumption-defaults.json` | the default fallback rates (the thing we're trying to AVOID using) | |

Resume rule: if ingest already derives a per-row effective rate from SP/RI joins,
read it and extend; don't rebuild.

---

## 1. GOAL

For every discounted row, establish `aws_od_cost` from the MOST authoritative
source available, in this precedence:

1. **`publicOnDemandCost`** on the row — the true OD. (already done)
2. **Effective rate from the row itself** — where the row carries both its
   amortized/effective cost AND a public-OD or on-demand-equivalent figure, use
   their ratio as the real discount for THAT row.
3. **Per-commitment effective rate** — group the bill's SP/RI-covered usage by
   `SavingsPlanARN` / `ReservationARN`; derive each commitment's effective
   discount from its own covered-usage vs public-OD totals, and apply that rate to
   its rows. Label `od_basis = 'reconstructed_resolved'`.
4. **Portfolio default** (existing behaviour) — only when 1–3 are impossible.
   Keep `reconstructed_default`; it must become the exception, not the rule.
5. **`amortized_fallback`** — no rate at all.

Emit a per-run count/$ of each basis so the shift from `_default` to `_resolved`
is visible (feeds the accuracy harness).

---

## 2. INVARIANTS
- NEVER invent a rate when a real one is derivable — resolution precedes default.
- Per-commitment granularity: a Compute SP (~40%) and an EC2 Standard RI (~55%)
  must not share one blended rate (the explicit guardrail from the OD work order).
- OD-vs-OD symmetry stays intact (don't net one side only — see od_vs_od work order).
- A row with no resolvable rate stays `amortized_fallback`; no silent guess.

---

## 3. PHASES

### Phase 1 — Ingest the commitment identifiers + rate fields
- [x] Ensure the CUR's SP/RI ARNs and any SP/RI rate columns are ingested.

### Phase 2 — Per-commitment effective-rate resolution
- [x] Group covered usage by commitment ARN; compute each commitment's effective
      discount from its own public-OD vs effective totals.
- [x] Apply the resolved rate per row; set od_basis='reconstructed_resolved'.

### Phase 3 — Reorder precedence + measure
- [x] Resolution (1→2→3) runs before the default (4); default is last resort.
- [x] Use the accuracy harness to report the _resolved vs _default vs
      mortized_fallback $ split before/after; the _default share must drop.

---

## 4. PROGRESS LOG (append-only)
- 2026-10-31 Work order created. Today apply_od_basis uses publicOnDemandCost else a
  portfolio-default rate. NEXT: run P1–P4 to see which commitment identifiers/rates
  the CUR carries, then build per-commitment resolution ahead of the default.
- 2026-11-01 Implemented real per-commitment and per-row discount resolution:
  - In scripts/ingest.py:
    - Added CUR parquet/CSV alias mappings for SavingsPlanARN, ReservationARN,
      SavingsPlanRate, PublicOnDemandRate, and effective cost fields.
    - Added columns commitment_arn, public_on_demand_rate, savings_plan_rate
      to ws_li_catalog (with defensive auto-migration in pply_od_basis()).
    - Implemented 5-tier precedence hierarchy in pply_od_basis():
      1. public_od: row public_on_demand_cost or standard on-demand usage.
      2. 
econstructed_resolved: row-level rate derivation when rate equivalents exist.
      3. 
econstructed_resolved: per-commitment effective discount computed symmetrically
         from covered-usage vs public-OD totals for each SavingsPlanARN / ReservationARN,
         applied to member rows lacking public OD (independent per commitment, never blended).
      4. Anti-flattery guard: recurring commitment fees kept at amortized cost (mortized_fallback).
      5. 
econstructed_default: portfolio default assumptions when resolution is impossible.
      6. mortized_fallback: when no rate is derivable or applicable.
    - Emits per-run count and dollar summary of each basis to log.
  - In scripts/accuracy_report.py:
    - Added 
econstructed_resolved to od_basis_mix tracking, diff comparison,
      and human-readable scorecard formatting.
  - Added unit test suite 	ests/test_real_discount_resolution.py with 10 unit tests
    verifying all 5 tiers, per-commitment independence (Compute SP vs EC2 RI), anti-flattery guards,
    and accuracy scorecard tracking.
  - All 1,184 python tests pass; all Go tests pass.

## 5. IF STUCK
If a commitment's rate cannot be resolved from its own rows, fall through to the
portfolio default (labelled _default), never a blended or invented number.

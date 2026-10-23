# WORK ORDER — Put guardrails on the ~5% of spend the LLM maps

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: a new validation pass over LLM-produced mappings, plus a sampled
harness mode.** No mapper, rate or report changes. **No dollar figure may
move.** This work order adds checks; it does not alter any projection.

---

## 1. The problem in plain English

The pipeline maps roughly **95% of workload spend deterministically** — static
mappers, `service_map.json`, spec dispatch. The remaining ~5% goes to the LLM
(`agy`) in Phase 2, for the `misc` group and any rows a mapper hands back.

**That 5% has never been verified.** Every audit of this pipeline, including
the corpus harness in `scripts/build_corpus.py`, deliberately skips the LLM —
it is slow, costs money, and returns different answers run to run. So the one
part of the pipeline with **no deterministic guarantee** is also the part
nobody has measured.

Concretely, on `job1` the OpenSearch Serverless rows — **$668, more than half
that bill** — go to the LLM. Whether they are priced well, badly, or at all is
currently unknown.

### Why asserting dollars will not work

The LLM's output varies between runs. A test that pins a projected cost will
be flaky and will be deleted within a month. **Do not attempt it.**

What *can* be asserted is the **shape** of the answer. A mapping can be wrong
in ways that are checkable without knowing the right number: a GCP service that
does not exist for that AWS service, a missing strategy, a passthrough with no
reason, a cost three orders of magnitude from the AWS figure.

---

## 2. Tasks

### T1 — An invariant check over LLM-produced rows

Add a validation pass (a new script, or a section in `validate_fix.py` — your
call, but it must run in the normal pipeline, not only in a harness) asserting
the following for every row whose mapping came from the LLM:

| invariant | why |
|---|---|
| `gcp_service` is non-null and is in the allowed set for that row's `canonical_service` | catches hallucinated targets — "Vertex AI" on a load balancer was observed historically |
| `strategy` is non-null and one of the known values | catches rows that fall through with no decision |
| if `strategy='passthrough'`, `passthrough_reason` is set | an unexplained passthrough is the defect the existing gate exists for |
| if `strategy='map'`, `gcp_sku_id` is non-null | a `map` row with no SKU prices to zero |
| `0.1 <= gcp_projected_cost / aws_amortized_cost <= 10` where both are > $5 | a plausibility band, not an accuracy claim |

**The allowed-target set is the one thing needing new data.** Derive it from
`data/service_map.json` — each rule already names the GCP service for its AWS
service. A row whose `gcp_service` is outside its service's allowed set is a
violation. Where a service has no rule, skip the check for that row rather than
failing it; **report how many rows were skipped**, so coverage of the check is
visible.

**Mark the band as a band.** `0.1–10` is three orders wide on purpose — it is
there to catch a mapping that is categorically wrong, not one that is 30% off.
Do not tighten it without evidence; a false failure here will train people to
ignore the check.

### T2 — Identify which rows came from the LLM

The check needs to know which mappings the LLM produced. If `decided_by` is
already populated for LLM rows, use it. If not, have the merge step stamp the
source when it inserts from the LLM group files — **do not infer it** from
"everything the static mappers did not claim", which would silently include
rows that failed for other reasons.

Report the row count and dollar value of the LLM slice per bill. That number
has never been reported and is useful on its own.

### T3 — A sampled harness mode

Extend `scripts/build_corpus.py` with a flag (e.g. `--with-llm`) that runs the
full pipeline including `agy` on a **named subset** of bills, not all of them.

- default stays LLM-free, so routine audits remain free and reproducible;
- the flag is for a periodic check, run deliberately;
- the run must report the LLM slice's size and any T1 violations.

Document in the script's docstring that this costs money and is not
reproducible run to run — the current docstring already explains why the
corpus skips the LLM, and this is its counterpart.

### T4 — Tests

1. `test_hallucinated_target_flagged` — a row with `gcp_service` outside its
   service's allowed set is a violation.
2. `test_map_without_sku_flagged` — `strategy='map'` with a null
   `gcp_sku_id` is a violation.
3. `test_passthrough_without_reason_flagged` — unexplained passthrough is a
   violation.
4. `test_plausible_ratio_not_flagged` — a row at 2x AWS cost passes; the band
   does not fire on ordinary variation.
5. `test_service_without_rule_is_skipped_and_counted` — a row whose service
   has no `service_map` rule is skipped, and the skip is counted (T1).

All five use fixtures. **None of them calls the LLM** — the invariants are
checkable offline, which is the point.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/build_corpus.py
```

- Record the test count first; confirm it rises by exactly 5.
- **No dollar figure changes.** This adds checks only.
- Run the LLM-free corpus: the new pass should report an LLM slice of **zero
  rows** on every bill, because the harness skips the LLM. That is correct, and
  it is also the proof the check is wired to the right rows rather than firing
  on deterministic ones.
- Run `--with-llm` on **one** bill and report: slice size in rows and dollars,
  violations found, rows skipped for want of a `service_map` rule.

---

## 4. Do NOT

- Do **not** assert exact projected costs for LLM rows. They vary per run
  (§1).
- Do **not** tighten the plausibility band without evidence from a real run.
- Do **not** run the LLM on every corpus build. Default stays free and
  reproducible (T3).
- Do **not** infer "LLM row" as "not claimed by a static mapper" (T2).
- Do **not** fail a bill because a service has no `service_map` rule. Skip and
  count it.

---

## 5. The alternative, if you would rather not spend the money

A legitimate cheaper option: **implement T1 and T2 only, skip T3**, and state
the LLM slice's size on the report — *"X% of spend was mapped by model
inference rather than deterministic rules."*

That gives the customer the honest caveat and gives you the invariant checks,
without ever paying for a sampled run. It is less thorough and it is not
unreasonable. **What is not acceptable is the current position: the slice is
unmeasured and unmentioned.**

---

## 6. Done

- [ ] LLM-produced rows are identifiable, and their row count and dollar value
      are reported per bill.
- [ ] Five invariants checked on those rows; violations reported through the
      existing diagnostic channel.
- [ ] Rows skipped for want of an allowed-target rule are counted, not hidden.
- [ ] `--with-llm` runs a named subset; default remains LLM-free.
- [ ] 5 tests pass without calling the LLM; full suite passes at baseline + 5.
- [ ] **No dollar figure moved.**

# WORK ORDER — A stale FX fallback is used silently, and is 13.7% wrong today

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `scripts/currency_utils.py` and how the rate's provenance is
reported.** No pricing or mapping changes. **No figure on a USD bill may
move** — this affects non-USD bills only.

---

## 1. The problem in plain English

GCP list prices are USD. For a customer billed in another currency the pipeline
converts, using a rate from `currency_utils.detect_currency_and_rate()`, which
tries a **live** fetch first and falls back to a committed file.

Both halves of that have a problem.

### The committed fallback is materially wrong

```
data/fx_rates.json   "updated": "2026-10-14"   INR: 83.5
live rate observed                             INR: 94.901331
difference                                     13.7%
```

Verified by running an INR-denominated bill end to end: `bill_currency` came
back as `('INR', '₹', 94.901331, 'live', …)`. The live fetch worked, and the
file it would otherwise have fallen back to was **13.7% off** after nine days.

### The fallback happens silently

```python
def fetch_live_fx_rates(timeout_sec: float = 1.5):
    try:
        ...
    except Exception:
        pass
    return None
```

One attempt, a **1.5-second** timeout, every exception swallowed. On failure
the caller silently uses the stale file.

**This matters because of where this runs.** The service is deployed on
Render's free tier, where containers sleep and cold-start. A 1.5s budget for
DNS plus TLS plus an external API round trip on a cold container will fail
often — and when it does, nothing anywhere says so.

### What is actually affected — read this before sizing the fix

The **savings percentage is not affected.** Both sides are scaled by the same
rate: AWS by `aws_fx_rate`, GCP by `fx_rate`, both `94.901331` in the test. A
wrong rate cancels in the ratio.

What is wrong is **every absolute figure in the customer's currency.** A
customer reading "₹7,101" sees a number 13.7% away from what their invoice
says. On a bill that states its own total, the reconciliation gate will now
catch this as a mismatch — but on the many bills that state no total, nothing
will.

So: **not a wrong conclusion, but wrong numbers on the page** for every
non-USD customer whenever the live fetch fails.

---

## 2. Tasks

### T1 — Record where the rate came from, and how old it is

`bill_currency` already carries a source field — the INR run produced
`'live'`. Make sure it also carries, for the fallback path, the **`updated`
date from `fx_rates.json`** and the resulting age in days.

The information exists; nothing consumes it.

### T2 — Warn when the fallback is used

`currency_utils.py` does not import the diagnostics channel. Add it, and emit
on fallback:

```python
diag(WARN, "fx_rate_fallback", "currency_utils.py",
     f"Live FX lookup failed; using committed rate for {currency} "
     f"({rate}) dated {updated} ({age_days} days old). All {currency} "
     f"figures scale with this rate.")
```

Emit **only when the fallback is actually used** — a successful live fetch
should stay quiet. A warning that fires every run is a warning nobody reads.

### T3 — Treat an old FX file as stale, the way the catalog is

`check_catalog_age.py` and the `catalog_stale` diagnostic already establish the
pattern: a data file past an age threshold is reported, not silently trusted.
Apply the same to `fx_rates.json`.

Pick the threshold from how fast the data moves — FX drifts far faster than a
SKU catalog, so a threshold measured in **days, not months**, is appropriate.
State the number you chose and why in your summary.

### T4 — Make the timeout survivable, without making the pipeline wait

1.5s on a cold container is optimistic. Raise it to something realistic (5s is
reasonable) **and keep the failure non-fatal** — an FX lookup must never block
a projection.

**Do not add retries in a loop.** One attempt with a sane timeout is the right
shape here; a retry loop turns a slow network into a slow pipeline.

### T5 — Show the rate and its provenance on the report

For a non-USD bill the report must state the rate it used and where it came
from, e.g.:

> `Converted at 1 USD = 94.9013 INR (live rate, 2026-10-23).`

or

> `Converted at 1 USD = 83.5000 INR (committed rate, 9 days old — live lookup unavailable).`

A reader who cannot see the rate cannot reconcile the page against their
invoice. **USD bills must show nothing new** — a conversion line on a
USD-only report is noise.

### T6 — Tests

1. `test_fallback_emits_diagnostic` — live fetch failing produces an
   `fx_rate_fallback` diagnostic naming the currency and the file's age.
2. `test_live_success_is_quiet` — a successful fetch emits no fallback warning.
3. `test_rate_source_recorded` — `bill_currency` carries source and, on the
   fallback path, the file date.
4. `test_usd_bill_unaffected` — a USD bill produces no conversion line, no
   diagnostic, and identical figures.
5. `test_fx_failure_does_not_block` — an FX fetch that raises still yields a
   complete projection.

Mock the fetch; **no test may make a network call.**

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/currency_utils.py
```

- Record the test count first; confirm it rises by exactly 5.
- **Every USD bill is byte-identical.** The corpus is all-USD, so the whole
  corpus must be unchanged — that is the regression check.
- On a non-USD bill, the report states the rate and its provenance, and a
  forced fallback produces the warning.
- Refresh `data/fx_rates.json` as part of this work: it is nine days old and
  13.7% wrong for INR. **Note the refresh separately in your summary** — it
  changes non-USD figures, and that change should not be mistaken for a code
  effect.

---

## 4. Do NOT

- Do **not** make an FX failure fatal. A projection must still be produced
  (T4).
- Do **not** add a retry loop (T4).
- Do **not** warn on every run — only when the fallback is actually used (T2).
- Do **not** add a conversion line to USD reports (T5).
- Do **not** change how the rate is applied. Both sides scale by the same rate
  today and that is correct; this work order is about provenance and
  staleness, not arithmetic.

---

## 5. Note for whoever reviews this

The tempting summary is "FX rates are wrong, savings numbers are wrong." They
are not. The ratio is unaffected because both sides scale together, and I
verified that on a matched USD/INR pair.

The defect is narrower and still worth fixing: **absolute figures in the
customer's own currency can be materially wrong, with nothing on the page or in
the log indicating it.** Fix the disclosure and the staleness, not the
arithmetic.

---

## 6. Done

- [ ] Rate source and file age recorded in `bill_currency`.
- [ ] `fx_rate_fallback` diagnostic on fallback only.
- [ ] `fx_rates.json` age checked against a stated threshold.
- [ ] Timeout raised; failure still non-fatal; no retry loop.
- [ ] Non-USD reports state the rate and its provenance; USD reports unchanged.
- [ ] 5 tests pass with no network access; full suite passes at baseline + 5.
- [ ] All-USD corpus byte-identical.

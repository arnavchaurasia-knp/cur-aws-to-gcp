# WORK ORDER — Make the GCP catalog's age bounded and visible

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` or `internal/` or `cmd/` are from the repo root
(`C:\Users\arnav\Downloads\cur-web-AGY`).

**Scope: visibility and a build gate. No pricing logic changes.** You are not
changing how any rate is chosen. If a projected dollar figure moves, you have
done something wrong.

Background: `docs/DESIGN_state_durability_on_render.md`, audit finding D-12 in
`docs/AUDIT_pipeline_defects.md`.

---

## 1. The problem, in five steps

1. The GCP price catalog (85,952 SKUs) is compiled **at Docker build time** —
   `Dockerfile:66` runs `scripts/build_catalog_index.py`.
2. So the prices in a running container are whatever was **committed to git**
   when that image was built. `data/CATALOG_META.json` currently reads
   `"fetched_at": "2026-07-27T06:50:51Z"` — **86 days old**.
3. `internal/jobs/catalog_refresher.go` refreshes weekly at runtime, writing
   into `/app/skill/aws-gcp-cost-projection/data/` — which on Render is the
   **image layer**, discarded on the next deploy.
4. The service deploys on every push to `main`, so that refresh is erased,
   often the same day. The refresher is not broken; it writes to a disk that
   does not survive.
5. **Nothing checks catalog age during a job, and the report never states it.**
   A projection built on July prices is indistinguishable from one built today.

There is already a `scripts/check_catalog_age.py` whose docstring says it is
"called by orchestrate.go as a pre_llm_script before ingest". **It is not
wired** — grep `orchestrate.go` for `check_catalog_age` and you get nothing.

### The decision this work order implements

Do **not** make the catalog refresh at runtime. An immutable per-image catalog
is correct: every container then prices identically and a projection can name
exactly which catalog produced it. Runtime refresh would mean two containers
disagreeing about the same bill.

Instead: **bound the staleness at build time, and disclose the vintage
everywhere the number is used.**

---

## 2. Tasks

### T1 — Fail the build when the catalog is stale

`scripts/check_catalog_age.py` already reads `CATALOG_META.json` and computes
age (`REFRESH_DAYS = 7`). Today it *triggers a refresh*. Add a second mode that
just reports and exits non-zero:

```bash
python scripts/check_catalog_age.py --assert-fresh --max-age-days 14
```

- exit 0 and print the age when fresh
- exit 1 with a clear message when stale, naming `fetched_at`, the age, and
  the command to fix it (`bash scripts/refresh-catalog.sh`, then commit)
- exit 1 if `CATALOG_META.json` is missing or unparseable — unknown age is not
  fresh

Then call it in `Dockerfile`, **immediately before** the existing build step at
line 66:

```dockerfile
RUN python /app/skill/aws-gcp-cost-projection/scripts/check_catalog_age.py \
      --assert-fresh --max-age-days ${CATALOG_MAX_AGE_DAYS:-14}
RUN python /app/skill/aws-gcp-cost-projection/scripts/build_catalog_index.py
```

Add `ARG CATALOG_MAX_AGE_DAYS=14` so it can be raised for an emergency deploy
without editing the file.

**This will fail your next build**, because the catalog is 86 days old. That is
the intended first result. Report it; do not raise the limit to make it pass.
The user decides whether to refresh the catalog or set the arg higher for one
deploy.

### T2 — Stamp the vintage onto every job

At rate-fill time, record which catalog produced the numbers.

In `scripts/apply_rates.py` (it already reads `CATALOG_META` — find that read),
write `catalog_fetched_at` and `catalog_age_days` into the job's
`projection-audit/` directory as `catalog_vintage.json`, and emit a diagnostic
through the existing channel when the age exceeds the threshold:

```python
from diagnostics import diag, WARN
diag(WARN, "catalog_stale", "apply_rates.py",
     f"priced against a GCP catalog fetched {fetched_at} ({age_days} days old)",
     usd=<workload spend priced from the catalog>, phase=4)
```

Use the existing `scripts/diagnostics.py` helper — do not invent a new one.

### T3 — Put the age on `/health`

`cmd/server/main.go:158` currently returns a bare `{"status":"ok"}`. Extend it:

```go
json.NewEncoder(w).Encode(map[string]any{
    "status":             "ok",
    "catalog_fetched_at": catalogFetchedAt,   // from CATALOG_META.json
    "catalog_age_days":   catalogAgeDays,
})
```

Read `CATALOG_META.json` **once at startup** and cache it — the catalog cannot
change while the container runs, and `/health` is hit by probes constantly.

If the file is missing or unreadable, report `"catalog_age_days": null` and
keep `status: "ok"` — a monitoring endpoint must not fail because of a missing
metadata file.

### T4 — One line in the report

`scripts/render_report.py` — add to the report's assumptions or footer:

> Priced against the GCP catalog as of 2026-07-27 (86 days old).

Read it from the `catalog_vintage.json` written in T2. **This is the only
customer-visible change in this work order.** Keep it factual and
non-alarming — it is a provenance statement, not a warning.

### T5 — Stop the refresher writing somewhere that cannot persist

`internal/jobs/catalog_refresher.go` runs weekly and writes into the image
layer, burning CPU on a 512 MB container for a result thrown away on the next
deploy.

Pick one and say which you did:

- **(a)** Disable it in production — skip when the write target is inside the
  image (detect via `SKILL_DIR` being under `/app`), log one line saying the
  catalog is image-baked and refreshed at build time.
- **(b)** Delete it, and rely on T1's gate.

**Recommend (a)** — it keeps the code working for local development, where the
skill directory *is* durable.

---

## 3. Verification

```bash
go build ./... && go vet ./internal/... ./cmd/...
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
```

Then, specifically:

1. **T1 fires.** Run `check_catalog_age.py --assert-fresh --max-age-days 14`
   today; it must exit 1 and name the age. Run with `--max-age-days 999`; it
   must exit 0.
2. **T1 does not fire wrongly.** Temporarily set `fetched_at` to today in a
   scratch copy; assert exit 0.
3. **T3 works and degrades.** Start the server, `curl /health`, confirm the two
   new fields. Rename `CATALOG_META.json`, restart, confirm `/health` still
   returns `status: ok` with a null age.
4. **No number moved.** On an existing job DB, before and after:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); print(c.execute('SELECT round(SUM(aws_amortized_cost),2), round(SUM(gcp_projected_cost),2) FROM gcp_projection').fetchone())" <db>
```

Both figures identical.

---

## 4. Do NOT

- Do **not** change any rate, SKU choice, or mapping.
- Do **not** make the catalog refresh at runtime — see §1.
- Do **not** raise `--max-age-days` above 14 to make the build pass. Report the
  failure instead; refreshing the catalog is the user's call.
- Do **not** make `/health` fail or return non-200 for a stale catalog. It is a
  liveness probe; Render will restart the container.
- Do **not** add alarming language to the report. A date is a fact.

---

## 5. Done

- [ ] `--assert-fresh` mode exists, exits 1 on stale and on missing metadata.
- [ ] Dockerfile gates the build on it, with an overridable `ARG`.
- [ ] Every job writes `catalog_vintage.json` and emits `catalog_stale` when
      over threshold.
- [ ] `/health` reports `catalog_fetched_at` and `catalog_age_days`, read once
      at startup, degrading to null.
- [ ] The report states the catalog date.
- [ ] The runtime refresher no longer writes into the image layer (say which
      option you took).
- [ ] Dollar totals unchanged; full test suite passes.
- [ ] You reported the current catalog age and that T1 fails on it today.

# WORK ORDER — Admin route for pricing assumptions

**For an implementing AI with no prior context. Read this file completely
before editing anything.** Paths are from the repo root
(`C:\Users\arnav\Downloads\cur-web-AGY`).

**Goal:** make the numbers we *assume* (not measure) visible in an admin page,
editable within bounds, versioned, and recorded on every job that used them.

**Scope:** plumbing and UI. You are **not** changing any assumption's value,
and you are **not** changing pricing logic. After this work order every
projection must produce the **same number it does today**, because every
assumption keeps its current value. If a dollar figure moves, you have made a
mistake.

---

## 1. Background — what an "assumption" is here

The pipeline sometimes knows a mapping but not the exact conversion, so a human
picked a number. Examples that exist in the code today:

| what | where | value today |
|---|---|---|
| Glue DPU → vCPU | `skill/aws-gcp-cost-projection/scripts/apply_static_mappings.py:6523` | `_GLUE_DPU_VCPU = 4.0` |
| Glue DPU → RAM GiB | `...apply_static_mappings.py:6524` | `_GLUE_DPU_RAM_GIB = 16.0` |
| Looker Studio Pro seat rate | `...apply_static_mappings.py:4632` | `GCP_LOOKER_STUDIO_PRO_RATE = 9.0` |
| Redshift node → BQ slots | `...apply_static_mappings.py:5378` | `_REDSHIFT_SLOT_MAP`, already read from `data/instance-specs.json` |
| EMR node → vCPU | `...apply_static_mappings.py:6483` | `_EMR_VCPU_MAP`, already from config |
| ElastiCache node → RAM GiB | `...apply_static_mappings.py:5067` | `_ELASTICACHE_RAM_GIB`, already from config |

Note two of those already read from `data/*.json` via
`scripts/config_loader.py` and three are hardcoded. You are unifying that.

### What is NOT an assumption — do not make these editable

```python
# apply_static_mappings.py:55
GB_TO_GIB = 1.0 / 1.074   # 1 GiB = 1.074 GB
```

That is **arithmetic**, not a judgement. Unit conversions, byte prefixes and
similar fixed facts must stay in code. **Only put a value in the admin UI if a
human chose it and a different human could reasonably choose otherwise.**

If you are unsure whether something is a fact or an assumption, leave it out
and list it in your summary.

---

## 2. The trap — read this before designing anything

**Assumptions must NOT be stored in `skill/aws-gcp-cost-projection/data/`.**

That directory is baked into the Docker image (`Dockerfile:63`,
`COPY skill/ /app/skill/`). On Render it is the **image layer**: writable at
runtime but **discarded on every deploy**, and the service deploys on every
push to `main`. An admin edit written there would silently vanish, usually
within a day.

This has already bitten the project once — see
`docs/DESIGN_state_durability_on_render.md`.

**Store assumptions in the application database** (`internal/db/`), which is
Turso/SQLite and durable. The pipeline reads them from a per-job snapshot
(§4.3), not from the database directly.

---

## 3. Existing patterns to copy — do not invent new ones

| you need | copy from |
|---|---|
| DB table + migration | `internal/db/db.go:16-60` (`jobs`, `users`, `deleted_jobs`) |
| admin handler | `internal/jobs/admin_handlers.go` — `AdminCatalogStatus` / `AdminListUsers` |
| admin route registration | `cmd/server/main.go:204-211` (inside `r.Use(auth.RequireAdmin(...))`) |
| frontend admin page | `frontend/src/pages/AdminCatalog.tsx` |
| frontend API client | `frontend/src/api/admin.ts` |
| config read in Python | `skill/aws-gcp-cost-projection/scripts/config_loader.py` |

---

## 4. Tasks

### T1 — Database: two tables

Add to the schema in `internal/db/db.go`, in the same style as the existing
`CREATE TABLE IF NOT EXISTS` blocks:

```sql
CREATE TABLE IF NOT EXISTS assumptions (
    key           TEXT PRIMARY KEY,   -- e.g. 'glue_dpu_vcpu'
    value         REAL NOT NULL,
    default_value REAL NOT NULL,      -- the code's original value; enables Reset
    min_value     REAL NOT NULL,
    max_value     REAL NOT NULL,
    unit          TEXT,               -- 'vCPU', 'GiB', 'USD/user/month'
    title         TEXT NOT NULL,      -- 'Glue DPU to vCPU'
    rationale     TEXT NOT NULL,      -- why this number
    scope         TEXT NOT NULL DEFAULT 'global',   -- 'global' | 'per_customer'
    updated_by    TEXT,
    updated_at    DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assumption_changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    old_value  REAL NOT NULL,
    new_value  REAL NOT NULL,
    reason     TEXT,
    changed_by TEXT NOT NULL,
    changed_at DATETIME DEFAULT (datetime('now'))
);
```

Also add one column to `jobs`:

```sql
ALTER TABLE jobs ADD COLUMN assumptions_version TEXT;
```

Use the same `ADD COLUMN IF NOT EXISTS` guard style the codebase already uses
elsewhere, so existing databases migrate.

### T2 — Seed the table from today's values

On startup, insert any missing rows. **Never overwrite an existing row** — an
admin's edit must survive a restart.

Seed exactly these six, with today's values so nothing changes:

| key | value | min | max | unit | title |
|---|---:|---:|---:|---|---|
| `glue_dpu_vcpu` | 4.0 | 1 | 32 | vCPU | Glue DPU to vCPU |
| `glue_dpu_ram_gib` | 16.0 | 1 | 256 | GiB | Glue DPU to RAM |
| `looker_studio_pro_seat_usd` | 9.0 | 0 | 100 | USD/user/month | Looker Studio Pro seat rate |
| `aurora_acu_vcpu` | 0.25 | 0.1 | 4 | vCPU | Aurora ACU to vCPU |
| `aurora_acu_ram_gib` | 2.0 | 0.5 | 16 | GiB | Aurora ACU to RAM |
| `cognito_requests_per_mau` | 20.0 | 1 | 500 | requests | Cognito requests per monthly active user |

For `aurora_acu_*` and `cognito_requests_per_mau`: **find the current value in
the code first and seed that exact number.** If you cannot find one, seed the
value above, and **say so in your summary** — that means you have introduced a
constant rather than surfaced one, which the reviewer must check.

Write a one-sentence `rationale` for each. For Aurora ACU use:
*"1 ACU is documented by AWS as approximately 2 GiB of memory with
proportional CPU; the vCPU share is inferred."*

### T3 — API: three endpoints

In `internal/jobs/admin_handlers.go`, following `AdminListUsers` /
`AdminSetUserRole`:

```
GET    /api/admin/assumptions           -> all rows + current version hash
PUT    /api/admin/assumptions/{key}     -> {value, reason}  (admin only)
GET    /api/admin/assumptions/history   -> assumption_changes, newest first
```

Register them in `cmd/server/main.go` **inside the existing
`r.Use(auth.RequireAdmin(cfg.AdminEmails))` block at line 204** — not outside
it.

The PUT must:

1. **Reject out-of-bounds values** with HTTP 400 and a message naming the
   allowed range. Clamping silently is wrong — the admin must see the refusal.
2. **Reject a non-numeric or missing value** with 400.
3. **Require a non-empty `reason`** — an assumption change moves money and must
   say why.
4. Write the `assumption_changes` row and update `assumptions` **in one
   transaction**.
5. Record `changed_by` from the authenticated session, never from the request
   body.

### T4 — Versioning: a hash, and a per-job snapshot

**The version** is a stable hash of the assumption set: sort rows by `key`,
join `key=value` with `;`, take the SHA-256, keep the first 12 hex characters.
Sorting matters — the same set must always hash the same.

**The snapshot.** When a job starts, write the current assumptions into the job
directory *before* the pipeline runs:

- In `internal/jobs/spawner.go`, in `StartOrchestrated` (it already writes
  `run_all.py` into `jobDir` — do this next to that), write
  `<jobDir>/assumptions.json`:

```json
{
  "version": "a3f91c20b4d7",
  "generated_at": "2026-10-21T09:00:00Z",
  "values": { "glue_dpu_vcpu": 4.0, "glue_dpu_ram_gib": 16.0 }
}
```

- Store that version on the job row (`assumptions_version`).

**Why a snapshot and not a live read:** a job must use one consistent set even
if an admin edits mid-run, and a re-run must be explainable. The snapshot *is*
the record of what was used.

### T5 — Python reads the snapshot

In `skill/aws-gcp-cost-projection/scripts/config_loader.py`, add:

```python
def load_assumptions(job_dir: str | None = None) -> dict:
    """Assumption values for THIS job, from <job_dir>/assumptions.json.

    Falls back to the committed defaults in data/assumption-defaults.json when
    the snapshot is absent (local runs, older jobs). Never raises.
    """
```

Then replace the three hardcoded constants with lookups:

```python
# apply_static_mappings.py:6523-6524
_ASSUMPTIONS   = load_assumptions()
_GLUE_DPU_VCPU    = _ASSUMPTIONS.get("glue_dpu_vcpu", 4.0)
_GLUE_DPU_RAM_GIB = _ASSUMPTIONS.get("glue_dpu_ram_gib", 16.0)
```

**Keep the current value as the fallback in every `.get()`.** That is what
guarantees no number moves.

Also create `data/assumption-defaults.json` holding the same six defaults, so a
local run with no snapshot behaves identically.

### T6 — Frontend page

`frontend/src/pages/AdminAssumptions.tsx`, modelled on `AdminCatalog.tsx`, with
its API calls in `frontend/src/api/admin.ts`. Register the route wherever the
other `Admin*` pages are registered (find it in `frontend/src/App.tsx`).

Each assumption shows: title, current value, unit, allowed range, rationale,
who last changed it and when. Editing requires a reason. Show the version hash
at the top, and the change history below.

**Do not** add a free-text numeric input without the range shown and enforced
client-side as well as server-side.

### T7 — Tests

Go, in `internal/db` and `internal/jobs`:

1. seeding inserts missing rows and **does not overwrite an edited value**
2. PUT outside `[min,max]` returns 400 and does not write
3. PUT with an empty reason returns 400
4. a successful PUT writes exactly one `assumption_changes` row
5. the version hash is stable across reorderings and changes when a value changes

Python, in `skill/aws-gcp-cost-projection/tests/`:

6. `load_assumptions()` returns snapshot values when the file exists
7. `load_assumptions()` returns committed defaults when it does not
8. `load_assumptions()` returns defaults, and does not raise, on malformed JSON

---

## 5. Verification

```bash
go build ./... && go vet ./internal/... ./cmd/... && go test ./internal/...
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
cd frontend && npm run build
```

**The invariant that matters.** Run a bill end to end before your change and
after it, and compare:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); print(c.execute('SELECT round(SUM(aws_amortized_cost),2), round(SUM(gcp_projected_cost),2) FROM gcp_projection').fetchone())" <db>
```

**Both figures identical.** Every assumption keeps its current value, so the
projection must not move. If it does, a seeded value does not match the code
constant it replaced — find which and report it.

Then confirm the plumbing end to end:

- `GET /api/admin/assumptions` returns six rows and a version hash
- `PUT` a value **outside** its range → 400, value unchanged in the DB
- `PUT` a valid value with a reason → 200, history row written, version hash changes
- start a job → `<jobDir>/assumptions.json` exists and the `jobs` row has the matching `assumptions_version`

---

## 6. Do NOT

- Do **not** store assumptions in `skill/aws-gcp-cost-projection/data/` — §2.
- Do **not** change any assumption's value. Seed today's numbers exactly.
- Do **not** make unit conversions or physical constants editable (`GB_TO_GIB`).
- Do **not** clamp an out-of-range value — reject it.
- Do **not** take `changed_by` from the request body; use the session.
- Do **not** let the pipeline read the database directly. It reads the job
  snapshot. The pipeline runs as a detached subprocess and must not need DB
  credentials.
- Do **not** register the routes outside the `RequireAdmin` block.
- Do **not** implement per-customer overrides in this work order. The `scope`
  column exists for it; everything is `global` for now.
- Do **not** "fix" anything else you notice — list it at the end of your
  summary.

---

## 7. Done

- [ ] `assumptions` and `assumption_changes` tables exist and migrate cleanly.
- [ ] `jobs.assumptions_version` added.
- [ ] Six assumptions seeded with today's values; seeding never overwrites.
- [ ] Three admin endpoints, inside `RequireAdmin`, with bounds + reason
      enforced server-side.
- [ ] Version hash is stable and order-independent.
- [ ] Every job writes `assumptions.json` and records its version.
- [ ] `load_assumptions()` reads the snapshot, falls back to committed
      defaults, never raises.
- [ ] Three constants in `apply_static_mappings.py` now come from it, with the
      current value as fallback.
- [ ] Admin page lists, edits with reason, and shows history.
- [ ] 8 tests pass; Go, Python and frontend all build.
- [ ] **Projection totals byte-identical before and after.**
- [ ] Your summary lists: which assumptions you found in code vs introduced,
      and anything you judged a fact rather than an assumption.

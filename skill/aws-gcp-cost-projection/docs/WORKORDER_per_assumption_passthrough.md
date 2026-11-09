# Work order — per-assumption "force passthrough" capability (end-to-end)

**Status:** proposed · **Owner:** assumptions/pricing · **Created:** 2026-11-09

## 1. Problem

An operator can set an assumption's numeric value, but cannot cleanly say
**"don't project a GCP price for the rows this assumption governs — just carry the
AWS cost through."** Today "passthrough" is an *ad-hoc magic value*: a few mappers
treat `≤ 0` as passthrough (`dynamodb_rcu_to_reads_sec`, `dynamodb_wcu_to_writes_sec`,
`inferentia_chip_to_tpu`, `trainium_chip_to_tpu`), while for other knobs `0` is
either meaningless or illegal (`nat_vms_per_gateway=0`, `aurora_acu_vcpu=0`). There
is no uniform, discoverable, audited way to force passthrough per assumption, and no
UI for it.

This is the correct escape hatch whenever the GCP mapping for a service is not yet
trustworthy (no benchmark, workload-dependent unit, disputed equivalence): passthrough
gives an honest **ratio = 1** (GCP projected = AWS cost) with a clear label, instead of
a fabricated projection. We already reach for it by hand (DynamoDB, Inferentia). This
work order makes it a **first-class, uniform control**.

## 2. Goal / target behaviour

- Every **passthrough-capable** assumption gains an independent boolean
  `passthrough_enabled`, editable from the admin Assumptions page, snapshotted per job,
  and honoured deterministically by the projection pipeline.
- When ON, **all rows that assumption governs** are emitted with
  `strategy = "passthrough"` (GCP projected cost = AWS cost), with a provenance note
  naming the assumption, regardless of the numeric value.
- When OFF (default for all existing knobs except the ones already defaulted to
  passthrough-via-value), behaviour is byte-identical to today.
- Capability is **data-driven** from the existing `data/assumption_impact.json` registry
  (services × charge-types × mapper) — no per-mapper hand-wiring.

## 3. Current architecture (ground truth)

| Layer | File / symbol | Role today |
|---|---|---|
| Value registry | `data/assumption-defaults.json` (`values`) | committed float defaults |
| Legal domain | `data/assumption_domains.json` | kind/min/max/step/source per key |
| Impact registry | `data/assumption_impact.json` (`impacts`) | **key → services[], charge_types[], mapper, decision** |
| DB schema | `internal/db/db.go` `CREATE TABLE assumptions` | `key,value,default_value,min,max,unit,title,rationale,category,scope,updated_by,updated_at` |
| Audit | `assumption_changes` table | value-change log |
| Go model/API | `db.Assumption`, `GetAssumptions`, `UpdateAssumption`, `ResetAssumptions`, `AssumptionsVersionHash` | CRUD + version hash |
| HTTP | `admin_handlers.go` `AdminGetAssumptions` / `AdminPutAssumption` / `AdminResetAssumptions` | REST |
| Snapshot | per-job `assumptions.json` (`values`) | frozen values for a run |
| Loader | `config_loader.load_assumptions(job_dir) -> dict[str,float]` | defaults ← snapshot overlay |
| Consumption | mappers in `apply_static_mappings.py` read `_ASSUMPTIONS.get(key)` | compute GCP mapping; some treat `≤0` as passthrough |
| Projection unit | a row with `strategy="passthrough"` | `apply_rates` carries AWS cost as GCP cost |
| Gates | `validate_fix.py` | passthrough is an accepted terminal strategy |
| Report | `render_report.py` | shows per-row decision + assumption text |
| Frontend | `frontend/src/pages/AdminAssumptions.tsx`, `api/admin.ts`, `JobAssumptionsPanel.tsx` | renders rows + domains; per-row "Use default"; global Reset |

**Key insight:** `assumption_impact.json` already encodes which rows each assumption
controls. Enforcement can therefore be a **single post-classification pass** keyed off
that registry, not an edit to each of the ~7 mappers.

## 4. Scope — which assumptions are passthrough-capable

Capability is declared once, in `assumption_domains.json`, as
`"passthrough_capable": true|false`. Rule: capable **iff** the assumption drives a GCP
*projection* for identifiable workload rows.

**Capable (toggle shown):** `nat_vms_per_gateway`, `dax_node_gib`, `memorydb_node_gib`,
`glue_dpu_vcpu`, `glue_dpu_ram_gib`, `looker_studio_pro_seat_usd`, `aurora_acu_vcpu`,
`aurora_acu_ram_gib`, `lambda_mb_per_vcpu`, `inferentia_chip_to_tpu`,
`trainium_chip_to_tpu`, `dynamodb_rcu_to_reads_sec`, `dynamodb_wcu_to_writes_sec`.

**Not capable (toggle hidden, with reason tooltip):**
- `aws_ri_1yr_discount_pct`, `aws_ri_3yr_discount_pct`, `aws_sp_discount_pct`,
  `aws_spot_discount_pct` — operate on the **AWS On-Demand baseline reconstruction**
  (`ingest.apply_od_basis`), not on a GCP projection. Passthrough is undefined.
- `fx_max_staleness_days` — a diagnostic threshold, no cost mapping.
- `rds_iops_included_in_storage` — already a binary flag whose `0` state *is*
  passthrough; it stays a flag (do **not** add a second control).

**Paired-knob note:** `aurora_acu_vcpu`/`aurora_acu_ram_gib` and
`glue_dpu_vcpu`/`glue_dpu_ram_gib` govern the same rows. Toggling either member
passes the whole service's rows through; the UI groups the pair and shows one toggle
(§8.3).

## 5. Data model

### 5.1 Capability (static, single source of truth)
`assumption_domains.json` — add `"passthrough_capable": true|false` to every entry.
Capability is **computed/read** here; never stored redundantly in the DB (avoids drift).

### 5.2 State (dynamic, operator-owned)
`assumptions` table — add column:
```sql
passthrough_enabled INTEGER NOT NULL DEFAULT 0   -- 0=off, 1=on
```
Migration `add_assumption_passthrough_2026_11` (guarded by `schema_migrations`,
mirroring the existing `calibrate_*` blocks):
```sql
ALTER TABLE assumptions ADD COLUMN passthrough_enabled INTEGER NOT NULL DEFAULT 0;
```
(SQLite: `ADD COLUMN` with a constant default is safe on existing rows.)

### 5.3 Audit
Extend the change log so a toggle is recorded. Either reuse `assumption_changes` with a
sentinel (`old_value`/`new_value` = 0/1 and `reason` prefixed `[passthrough]`) or add a
dedicated `field TEXT NOT NULL DEFAULT 'value'` column. **Preferred:** add
`field TEXT NOT NULL DEFAULT 'value'` to `assumption_changes` so value-edits and
passthrough-toggles are distinguishable in history.

### 5.4 Snapshot
Per-job `assumptions.json` gains a sibling object:
```json
{ "values": { ... }, "passthrough": { "inferentia_chip_to_tpu": true } }
```
Only `true` entries need be written. Absent key ⇒ false.

## 6. Backend changes (Go)

1. **`db.Assumption` struct** — add `PassthroughEnabled bool` (json `passthrough_enabled`)
   and a derived, non-persisted `PassthroughCapable bool` (json `passthrough_capable`)
   populated in `GetAssumptions` by joining against the domains registry
   (`LoadAssumptionDomains`).
2. **`GetAssumptions`** — select the new column; set `PassthroughCapable` from the
   registry; keep `Domain` attachment as today.
3. **New `SetAssumptionPassthrough(key string, enabled bool, reason, changedBy string) (version string, err error)`** —
   validates the key exists and is capable (reject otherwise with `not passthrough-capable`),
   writes `passthrough_enabled`, appends an audit row (`field='passthrough'`), bumps the
   version hash. Mirror `UpdateAssumption`'s structure.
4. **`UpdateAssumption`** — unchanged for value; a value edit does **not** clear the toggle
   (orthogonal controls). Document this.
5. **`ResetAssumptions`** — also reset `passthrough_enabled = 0` for every row and log it,
   so "Reset to defaults" clears toggles too.
6. **`AssumptionsVersionHash`** — include `passthrough_enabled` in the hash input so a
   toggle invalidates caches / changes the snapshot version.
7. **Seed** — new rows seed `passthrough_enabled` to match their defaulted value where the
   value *is* the passthrough sentinel today (DynamoDB & Inferentia default to value `0`,
   which the mapper already treats as passthrough — leave `passthrough_enabled=0` for them
   to avoid double-encoding; §7.2 reconciles the two representations).
8. **HTTP** — `admin_handlers.go`:
   - `AdminPutAssumptionPassthrough` (new) → `PUT /api/admin/assumptions/{key}/passthrough`
     body `{ "enabled": bool, "reason": string }`; 400 on non-capable, 404 on missing.
   - `AdminGetAssumptions` payload already serialises the struct → both flags flow out.
   - Register the route in `cmd/server/main.go` next to the existing assumptions routes.

## 7. Projection-pipeline changes (Python)

### 7.1 Loader
Add `load_assumption_passthroughs(job_dir) -> set[str]` to `config_loader.py`
(defaults file + snapshot overlay, same fallback discipline as `load_assumptions`).
Leave `load_assumptions` signature unchanged (backward compatible).

### 7.2 Reconcile the two representations
A value-sentinel (`0` → passthrough, already honoured by DynamoDB/Inferentia mappers) and
the new explicit flag must not fight. Rule:
**effective_passthrough(key) = (key in passthrough set) OR (mapper-specific legacy sentinel still true).**
The explicit flag is authoritative; the legacy `≤0` paths remain as-is so existing
snapshots keep working. New UI writes the explicit flag.

### 7.3 Enforcement module (new) — `scripts/passthrough_enforcer.py`
A single deterministic pass, run **after classification/mapping, before `apply_rates`**:
- Load the passthrough set (§7.1) and `assumption_impact.json`.
- For each enabled key, resolve its governed rows by matching
  **`canonical_service ∈ impacts[key].services`** AND
  (`mechanic_group`/charge tag ∈ `impacts[key].charge_types`), with a documented
  service-only fallback when charge-type tags are absent (PDF bills).
- For each matched row, overwrite the mapping to:
  `strategy='passthrough'`, `unit_multiplier=1.0`, `gcp_service` kept as the labelled
  target (or `"<service> (operator passthrough)"`), and
  `projection_note = "[passthrough: forced via assumption {key}] " + existing`.
- Idempotent; records a per-key count for observability (§10).

Because enforcement sets `strategy='passthrough'` **before** `apply_rates`, the existing
passthrough machinery carries AWS cost → GCP cost. No hand-set dollar amounts.

### 7.4 Orchestration hook
Register `passthrough_enforcer` as a post-mapping script in the phase that owns mapping
(same mechanism as the normaliser hook referenced in `AGENTS.md` item A-5 /
`orchestrate.go`). It must run after `classify_mechanics` + static mappers and before
`apply_rates`. Add to the deterministic post-LLM script list so it always runs,
including check-only mode.

### 7.5 Gates (`validate_fix.py`)
- Passthrough is already a terminal strategy — no row-level failure.
- Add a **conservation check**: for every enabled passthrough key, assert that the count
  of governed rows now carrying `strategy='passthrough'` ≥ the count that matched the
  registry (i.e. enforcement actually fired). A mismatch is a hard gate failure
  (`passthrough_not_applied`), surfacing a silent routing miss rather than hiding it.
- Ensure the `passthrough_budget` gate (if present) attributes operator-forced passthrough
  spend to the `[passthrough: forced via assumption …]` note so it is not mistaken for an
  unintended downgrade.

### 7.6 Report (`render_report.py`)
Per-row decision provenance gains: `decision: passthrough (operator override — assumption
{key})`. Add a run-level summary line listing every assumption toggled to passthrough and
the dollars it moved, so the output is auditable ("decision source: operator passthrough").

## 8. Frontend changes

### 8.1 API (`frontend/src/api/admin.ts`)
- Extend the `Assumption` type with `passthrough_enabled: boolean` and
  `passthrough_capable: boolean`.
- Add `setAssumptionPassthrough(key, enabled, reason)` → the new PUT route.

### 8.2 Admin page (`AdminAssumptions.tsx`)
- Per capable row, render a **Passthrough** switch next to the value field.
- When ON: grey/disable the numeric input and show a "carrying AWS cost (ratio 1)"
  chip; the value is retained (not destroyed) so turning it OFF restores the prior number.
- When the row is **not** capable: hide the switch; on hover show the reason from §4.
- Toggling prompts for the same optional reason string as a value edit and calls the API;
  reuse the existing success/error + version-refresh flow.
- The global **Reset to Defaults** button already exists — ensure it now also flips every
  switch off (backend §6.5 does the work; UI just re-fetches).

### 8.3 Paired knobs
Group `aurora_acu_*` and `glue_dpu_*` so a single switch controls the pair; writing it
sets `passthrough_enabled` on both keys in one call (or sequential calls) to keep them
consistent.

### 8.4 Per-job panel (`JobAssumptionsPanel.tsx`)
Show a read-only "passthrough" badge on any assumption that was passed through for that
job (from the job snapshot), so a report reader sees which services were not projected.

## 9. Edge cases & conflicts

- **Value vs toggle orthogonality:** editing the value never changes the toggle and vice
  versa; both persist independently.
- **Capable-but-illegal value:** a capable knob whose value is out of its (now
  passthrough-inclusive) domain still validates against its own min/max; the toggle
  bypasses the value entirely at projection time.
- **Legacy `0` sentinel rows (DynamoDB, Inferentia):** continue to passthrough via value;
  the explicit toggle is an additional, equivalent path. Do not migrate their value away
  from `0` — that is their calibrated default.
- **Non-capable keys:** API rejects a passthrough write (`400 not passthrough-capable`);
  UI never offers it.
- **Multi-service keys** (e.g. discounts — excluded anyway): n/a by scope.
- **Charge-type miss on PDF bills:** service-only fallback match (§7.3) plus the
  conservation gate (§7.5) guarantees either enforcement fires or the run fails loudly.

## 10. Observability & audit

- `assumption_changes` logs every toggle (`field='passthrough'`, old/new ∈ {0,1}, reason,
  actor, timestamp).
- Enforcement emits a structured diagnostic per key: `passthrough_enforced{key, rows, usd}`.
- Report run-summary lists operator-forced passthroughs and their dollar impact.

## 11. Backward compatibility & rollout

- New column defaults to `0` ⇒ existing DBs unchanged until an admin toggles.
- Old job snapshots (no `passthrough` object) ⇒ empty set ⇒ identical projections.
- Enforcement pass is a no-op when the set is empty ⇒ zero behavioural change by default.
- Ship behind nothing — it is inert until used — but land in phases (§13).

## 12. Testing matrix

| Level | Test | Asserts |
|---|---|---|
| Unit (Go) | `TestSetAssumptionPassthrough` | capable toggles; non-capable 400; audit row written; version bumps |
| Unit (Go) | `TestResetClearsPassthrough` | reset flips all toggles off + logs |
| Unit (Go) | `TestGetAssumptionsCapability` | `passthrough_capable` matches registry |
| Unit (Py) | `test_config_loader_passthrough` | defaults ← snapshot overlay; absent ⇒ empty set |
| Unit (Py) | `test_passthrough_enforcer` | governed rows → `strategy='passthrough'`; others untouched; idempotent |
| Gate (Py) | `test_passthrough_conservation_gate` | enabled key with unrouted rows → `passthrough_not_applied` failure |
| Integration | API round-trip | PUT toggle → GET reflects → snapshot writer emits `passthrough` object |
| E2E | one bill, toggle `memorydb_node_gib` | MemoryDB rows become passthrough; GCP=AWS for them; report shows override; ratio→1 on those rows |
| Migration | existing DB | `ADD COLUMN` applies once; re-run idempotent; value edits preserved |

## 13. Implementation phases

1. **DB + model** — migration, struct fields, `GetAssumptions` capability join, version hash. (Go tests.)
2. **Write path** — `SetAssumptionPassthrough`, handler, route, reset integration, audit field. (Go tests.)
3. **Loader + enforcer** — `load_assumption_passthroughs`, `passthrough_enforcer.py`, orchestration hook. (Py tests.)
4. **Gates + report** — conservation gate, report provenance + summary.
5. **Frontend** — types, API, switch UI, paired-knob grouping, job panel badge.
6. **Capability data** — add `passthrough_capable` to all `assumption_domains.json` entries; backfill migration not needed (static).

Phases 1–4 are safe to land before the UI (API usable via curl). Phase 5 is purely additive.

## 14. Open decisions (need sign-off before coding)

1. **Audit representation** — add `field` column to `assumption_changes` (preferred,
   clean history) vs reuse with a `[passthrough]` reason prefix (no schema change)?
2. **GCP label on passthrough rows** — keep the mapped target name, or relabel to
   `"<service> (operator passthrough)"` so the report is unambiguous?
3. **Paired-knob UX** — one grouped switch for `aurora_acu_*` / `glue_dpu_*` (recommended)
   vs two independent switches that each passthrough the shared rows?
4. **Trainium default** — leave at `1.0`, or ship it defaulted to passthrough (0) now, for
   parity with Inferentia (same no-benchmark rationale)? (Currently left at 1.0 per prior
   decision; the new toggle makes this a one-click change either way.)

---
Related: `WORKORDER_assumption_gcp_limits.md` (per-knob grounding + which defaults are
already passthrough), `data/assumption_impact.json` (routing backbone),
`data/pricing_matrix.json` (per-SKU decision intent).

# WORKORDER — Repair broken RDS implementation (apply_static_mappings.py)

Status: **RESOLVED (2026-11-13)** — `apply_static_mappings.py` restored, `map_db_row` completed to contract, all 1,379 tests passing.

> Golden rules still apply (see `WORKORDER_per_service_full_modelling.md` §6.0): AWS/GCP official pages
> are the only source of truth; never $0 on a billed charge; pin rates with a URL comment; no guessing;
> **no destructive git operations** (the clean `git checkout` was auto-blocked — repair surgically).

---

## 1. What is broken (verified 2026-11-13)

- `scripts/apply_static_mappings.py` **does not import** — `SyntaxError: '(' was never closed` at the
  Oracle branch of `map_db_row` (~line 9160). The previous edit **truncated the file mid-function**.
- Everything after the truncation point was **deleted**: the rest of `map_db_row`, plus two whole
  functions — **`map_compute_breakdown`** (deterministic GCE/GPU/TPU compute mapper) and **`main()`**
  (the script entry point + mechanic→handler dispatch). File is ~9,161 lines vs ~9,451 at HEAD.
- Consequence: the **entire Phase-2 static mapping step is dead**, not just RDS. The previous agent's own
  tests (`tests/test_rds_full_modelling.py`) never ran (collection errors at import).

**Reproduce:** `python -c "import ast; ast.parse(open('scripts/apply_static_mappings.py',encoding='utf-8-sig').read())"`

## 2. What is GOOD and must be kept (do NOT redo these)

- `data/service_usage_catalog.json` — the RDS usage-type entry (gate fixture). Keep.
- `data/managed_service_availability.json` — the `oracledatabase.googleapis.com` region gate. Keep.
- `tests/test_rds_full_modelling.py` — the contract. Keep (with the two assertion fixes in §5).
- `map_block_storage` io1/io2 change (the `is_io_iops` passthrough block, ~line 3690) — **correct, keep.**
- In `map_db_row`: the function signature, var setup, the **Db2 branch** and the **Oracle branch start**
  are correct — reuse them; just finish the function.

## 3. Repair step A — restore the two deleted functions (verbatim from HEAD)

Both functions are intact at HEAD. Extract and re-append them after a correctly-closed `map_db_row`
(do not hand-retype `main()` — copy it exactly):

```
git show HEAD:skill/aws-gcp-cost-projection/scripts/apply_static_mappings.py | sed -n '9172,$p'
```

That range is exactly `map_compute_breakdown` + `main()` + the `if __name__ == "__main__":` block. Restore
them unchanged. `map_compute_breakdown` is:

```python
def map_compute_breakdown(rows: list[dict]):
    """Deterministic compute breakdown mapper for GCE CPU, GPU, and Cloud TPU.
    Consolidated from family_mapper.py (AR-3).
    """
    mapped = []
    llm_rows = []
    for r in rows:
        itype = _resolve_instance_type(r)
        if itype and not r.get("instance_type"):
            r["instance_type"] = itype
            _resolved_instance_types_cache[r["aws_li_key"]] = itype
        if itype:
            _backfill_specs_from_ec2_types(r, itype)
        parsed = parse_instance(itype)
        if not parsed:
            llm_rows.append(r)
            continue
        kind = _compute_mapper_kind(parsed, itype)
        if kind == "tpu":
            res = map_tpu_row(r, parsed, itype)
        elif kind == "gpu":
            res = map_gpu_row(r, parsed)
        else:
            res = map_gce_row(r, parsed)
        if res:
            mapped.extend(res)
        else:
            llm_rows.append(r)
    return mapped, llm_rows
```

`main()` (long) must be restored verbatim from the `git show` above — it holds the `handlers` dispatch
table and the manifest/LLM-fallback plumbing. **Verify** after restoring that the dispatch table still
maps every mechanic (`managed_db`, `compute_breakdown`, `block_storage`, `rds_extended_support`, …) to its
handler, exactly as HEAD.

## 4. Repair step B — complete `map_db_row` to this contract

Keep the existing head of the function (signature, `arm_source_note`, `gcp_region/vcpus/ram`, `is_ha`,
`is_cluster`, `engine`, `prod_op`). Set `is_ha = is_ha or is_cluster`. Then route by engine **in this
order** and close the function properly:

| Order | Engine match | GCP target | Entries | strategy | Notes / assertions |
|---|---|---|---|---|---|
| 1 | `db2` | **Manual Review** | 1 | passthrough | note "no native GCP managed database equivalent"; conf 0.50 |
| 2 | `oracle` | **Oracle Database@Google Cloud** | 1 | **passthrough** | region-gated via `is_managed_service_available("oracledatabase.googleapis.com", region)`; note names service + includes `https://cloud.google.com/oracle/database/docs/overview`; unavailable-region variant note contains "not offered". **Keep passthrough** — do NOT pin the ECPU map yet (see §6). |
| 3 | `sqlserver` / "sql server" | **Cloud SQL for SQL Server** | 2 (core+ram) | map (resolves) | `gcp_sku_name` = `Cloud SQL for SQL Server: {Zonal|Regional} - vCPU`/`- RAM`; conf 0.90. Catalog has these SKUs. |
| 4 | `mariadb` | **Cloud SQL for MySQL** | 2 | map | note "engine substitution: Cloud SQL has no native MariaDB" |
| 5 | `aurora-postgresql` (check before generic postgres) | **Cloud SQL for PostgreSQL (default)** | 2 | map | note: Aurora-PG → Cloud SQL PG; **"AlloyDB for PostgreSQL" listed as a disclosed alternative** (higher price-performance, ~+39%/vCPU, not auto-selected). **Do NOT auto-map to AlloyDB** — AlloyDB has no generic vCPU SKU (machine-family-specific) and auto-picking the pricier option violates the best-fit safety rule. |
| 6 | `postgres` | **Cloud SQL for PostgreSQL** | 2 | map | note mentions AlloyDB alternative |
| 7 | `mysql` (incl. `aurora-mysql`) | **Cloud SQL for MySQL** | 2 | map | — |
| 8 | else (unknown) | **Manual Review** | 1 | passthrough | conf **0.40**; note "engine could not be determined" — **never default to MySQL** (this is the H1 bug). |

Tier: `tier = "Regional" if is_ha else "Zonal"`. When `is_cluster`, tier = Regional **and** append to the
note: `"Multi-AZ DB cluster (2 readable standbys)"`. Graviton/ARM: `arm_source_note` already carries
`"Enterprise Plus candidate offers C4A Arm"` — ensure it's appended to the projection_note of the mapped
(Cloud SQL) branches. Edition default = **Enterprise** (Enterprise Plus only on ARM/HA-perf evidence).

Emission pattern (reuse HEAD's): build `core_desc`/`ram_desc`, `core_sku = resolve_sku(gcp_service,
core_desc, gcp_region)`, `strategy = "map" if core_sku else "passthrough"`, `unit_multiplier =
float(vcpus)` / `float(ram)`, `break_down=True`, attach `gcp_sku_id`/`gcp_sku_unit` when resolved, append
`_no_rate_suffix(...)`. Return `[core_entry, ram_entry]`.

## 5. Repair step C — align the two over-strict tests

In `tests/test_rds_full_modelling.py`:
- `test_aurora_postgresql_maps_to_alloydb` — update to the §4 decision: assert `gcp_service == "Cloud SQL"`,
  `"Cloud SQL for PostgreSQL" in gcp_sku_name`, and the note lists **AlloyDB as a disclosed alternative**
  (not auto-mapped). Rename to `test_aurora_postgresql_defaults_to_cloud_sql_pg_with_alloydb_alternative`.
- Any assertion that forces `strategy == "map"` for an engine whose SKU does not resolve in the bundled
  catalog for the test region (`us-central1`) must be relaxed to accept `map` **or** `passthrough` (the
  mapping is correct either way; `map` requires a catalog hit). Verify per-engine against
  `data/catalog.duckdb` before relaxing — SQL Server **does** resolve, so keep its `map` assertion.

## 6. Carried-forward modeling items (do NOT block the repair on these)

- **Oracle ECPU pricing** (rate obtained, see main workorder §4.1.0): Base Database Service ECPU
  $0.0538 Std / $0.1075 Ent / $0.2218 HighPerf / $0.0484 BYOL, storage $0.12/GB-mo, via Marketplace as
  $1/credit. Pinning Oracle as a priced map needs the **AWS vCPU→ECPU conversion** + edition mapping,
  verified from the Oracle ECPU FAQ. Until then Oracle stays passthrough. Log it, don't guess.
- Aurora Serverless v2 **ACU ratio**: reconcile code (`_ACU_VCPU=2.0`, `_ACU_RAM_GIB=16.0` in `main()`)
  with decided D2 (0.25 vCPU + 2.0 GiB per ACU; AWS confirms 1 ACU ≈ 2 GiB). Separate follow-up.
- C4A=ARM confirmation (GCP machine-series page); Cloud SQL provisioned-IOPS EP-only.

## 7. Verification (Definition of Done for this repair)

1. `python -c "import ast; ast.parse(open('scripts/apply_static_mappings.py',encoding='utf-8-sig').read()); print('parses')"` → parses.
2. `python -c "from scripts.apply_static_mappings import map_db_row, map_compute_breakdown, main"` → imports.
3. `python -m pytest tests/test_rds_full_modelling.py tests/test_spec_partition.py tests/test_spec_coverage.py -q` → green.
4. `python -m pytest tests/ -q` → **full suite green** (baseline was 1365 passed). No regressions in the
   compute path (`map_compute_breakdown`) or `main()` dispatch.
5. Confirm `git diff --stat` shows **no net deletion** of unrelated functions (map_compute_breakdown + main
   restored; only `map_db_row` + `map_block_storage` intentionally changed).
6. Update `WORKORDER_per_service_full_modelling.md` §3 (rds → ☑ only after step 4) and §7.1 log.

## 8. Guardrails

- Touch only `scripts/apply_static_mappings.py` (map_db_row) + `tests/test_rds_full_modelling.py`. Leave
  every other function and every other file as-is. Do not delete anything else.
- No `git checkout`/`git restore`/`git reset` on the working tree (auto-blocked and destructive). Fix
  in-place with edits.
- Keep the file parseable at every save; re-run step 1 after each edit.

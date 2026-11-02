# WORKORDER — Assumption-driven mappings: rescue passthroughs with declared assumptions

**For the implementation agent. Read fully. Verify every claim in Phase 0 before writing code.**
Self-tracking: run the probes, fill RESULT, tick the boxes, append to the PROGRESS LOG.

Scope note from the user (2026-11-01):
- **IN SCOPE NOW**: find the passthrough / mis-priced SKUs that a *declared assumption*
  can turn into a real GCP mapping (done — see §1 Findings), and build the
  assumption-keyed mappers + the assumption→SKU impact registry for them.
- **DEFERRED (explicit user decision)**: the calibration solver that back-calculates
  assumptions from a bill so AWS-OD / GCP-OD ratio ≈ 1. DO NOT build it here. It gets
  its own work order later. This file only makes the knobs *exist and map*; it does not
  auto-tune them.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe (from `skill/aws-gcp-cost-projection/`) | Tells you | RESULT |
|---|---|---|---|
| P1 | `python3 -c "import json;d=json.load(open('data/assumption-defaults.json'));print(list(d['values']))"` | the 10 assumption keys that exist today | 10 keys: glue_dpu_vcpu, glue_dpu_ram_gib, looker_studio_pro_seat_usd, aurora_acu_vcpu, aurora_acu_ram_gib, lambda_mb_per_vcpu, aws_ri_1yr_discount_pct, aws_ri_3yr_discount_pct, aws_sp_discount_pct, aws_spot_discount_pct |
| P2 | `grep -rn "load_assumptions" scripts/` | every stage that already reads assumptions (where new keys plug in) | `config_loader.py` (core loader), `ingest.py` (od_basis/discounts), `apply_static_mappings.py` (sizing mappers), `projection_view.py` |
| P3 | read `data/pricing_matrix.json` + `data/service_map.json` | current decision per SKU (SKU/FORMULA/PASS/IGNORE and mode:review) | `service_map.json` rules have `mode: review` for DAX, MemoryDB, Glue, DynamoDB; `pricing_matrix.json` documents SKU/FORMULA/PASS/IGNORE decisions |
| P4 | read `scripts/family_mapper.py` ElastiCache node→GiB path (FORMULA) | the PROVEN pattern to copy for node-based cache mappers (DAX/MemoryDB) | In `apply_rates.py:172` and `apply_static_mappings.py`: node instances decomposed to Memorystore Custom Core / RAM GiB |
| P5 | `grep -rn "nat\|NatGateway\|vpc_nat_hours" scripts/ data/service_specs.json` | how NAT is classified today (charge-type `vpc_nat_hours` exists) and where its rate is applied | Classified as `flat_hourly` / `vpc_nat_hours` in `service_specs.json`; mapped in `apply_static_mappings.py:2721-2748` |
| P6 | `grep -rn "assumption" internal/db/ internal/jobs/admin_handlers.go` | the admin assumption schema (key/title/unit/min/max/category) a new key must be registered in | Seeded via `INSERT OR IGNORE INTO assumptions` in `internal/db/db.go:349-359` and served in `admin_handlers.go` |

Resume rule: new assumption keys must be registered in BOTH `data/assumption-defaults.json`
(default + category) AND the admin DB schema (min/max/title/unit/rationale) so the admin
panel and the per-job override panel render them. A key that exists only in code is invisible.

---

## 1. FINDINGS — assumption-rescuable passthroughs (the SKUs you asked me to find)

Decision today is from pricing_matrix.json / service_map.json. "Assumption needed" is the
NEW knob that would convert the row from passthrough (AWS cost echoed) or a wrong mapping
into a computed GCP SKU. Ordered by confidence that an assumption genuinely rescues it.

| # | Service / charge-type | Today | Why it's not truly mapped | Assumption key to add | GCP target it unlocks | Confidence |
|---|---|---|---|---|---|---|
| R1 | **VPC NAT gateway-hours** | nominally SKU (Cloud NAT gateway-hrs) but UNDER-projects | AWS bills per-gateway-hour; Cloud NAT bills **per-VM-hour**. Without a VM count the hourly side is ~$3 vs ~$120 AWS. | `nat_vms_per_gateway` (default e.g. 32, range 1–200) | Cloud NAT per-VM-hour × assumed VMs | HIGH (user-named) |
| R2 | **DAX (DynamoDB Accelerator)** node-hrs | mode:review → Memorystore (passthrough $) | node-hours not decomposed to Memorystore GiB-hours | `dax_node_gib` (per node-type RAM, like elasticache) | Memorystore for Redis GiB-hours | HIGH (copy ElastiCache formula) |
| R3 | **MemoryDB** node-hrs | mode:review → Memorystore (passthrough $) | same as DAX — node not decomposed | `memorydb_node_gib` | Memorystore for Redis GiB-hours | HIGH (copy ElastiCache formula) |
| R4 | **EC2 Inferentia / Trainium** | PASS → Manual Review | no 1:1 HW equiv; TPU is the analogue but needs a declared chip-equivalence | `inferentia_chip_to_tpu` / `trainium_chip_to_tpu` (ratio, labeled) | Cloud TPU v5e chip-hours (labeled assumption) | MED (no benchmark; declare as assumption — see project memory [[project_inferentia_trainium_tpu]]) |
| R5 | **DynamoDB** RCU / WCU / storage | PASS → Firestore/Bigtable | capacity model differs; needs a declared conversion + target choice | `dynamodb_rcu_to_reads_sec`, `dynamodb_wcu_to_writes_sec`, `dynamodb_target` (firestore\|bigtable) | Firestore op pricing OR Bigtable node-hours | MED (workload-dependent; assumption makes it explicit, not hidden) |
| R6 | **Glue** DPU-hours | mode:review → Dataflow | `glue_dpu_vcpu` / `glue_dpu_ram_gib` assumptions ALREADY EXIST but row still passes through | none new — WIRE existing keys into a Dataflow formula | Dataflow vCPU+RAM worker-hours | MED (keys exist; just not consumed as a mapper) |
| R7 | **RDS provisioned IOPS (io1/io2-style on RDS)** + **Aurora I/O-Optimized** | partial | Cloud SQL folds I/O into storage; io-optimized RDS has a separate IOPS charge with no clean target | `rds_iops_included_in_storage` (bool/factor) to make the IGNORE-vs-SKU choice explicit | Cloud SQL storage (I/O folded) or $0 | MED (makes an implicit choice auditable) |

**NOT assumption-rescuable (leave as-is; an assumption would be dishonest):**
- Aurora **I/O requests** PASS — GCP genuinely has no per-I/O charge; correct as passthrough/ignore, no knob helps.
- **S3 GET/PUT requests** PASS — $/1k vs $/10k op-class mismatch; a conversion factor is possible but risky — only touch if R1–R7 land and the user asks.
- **SageMaker, Transfer Family, SES, Neptune, DocumentDB** — no clean GCP equivalent; passthrough is the honest answer. Do not fake a mapping with an assumption.
- **WAF → Cloud Armor**, **API Gateway → Cloud Endpoints** — these are *component-FORMULA* upgrades (policy+rule+request), not sizing-assumption rescues. Track separately; out of scope here.

---

## 2. GOAL
Each R-row above becomes a computed GCP mapping driven by a **declared, admin-visible,
per-job-overridable assumption**, with a registry that records which SKUs/services each
assumption controls — so #2 ("find what services/SKUs can be mapped from assumptions")
is answered by data, not by reading code.

---

## 3. INVARIANTS
- Every new assumption key is registered in `assumption-defaults.json` (value+category)
  AND the admin DB schema (title/unit/min/max/rationale). No code-only keys.
- A mapping driven by an assumption must STAMP the row with the assumption key + value
  used, so the report can show "mapped via assumption X=N" and `explain_row` can surface it.
- Assumptions only ever make a row mappable; if the assumption is at a "safe" value the
  result must not be worse than today's honest passthrough. Where unsure, keep passthrough
  as the fallback (a NULL/զero assumption ⇒ passthrough, not a broken $0).
- Reset-to-default restores the committed default (fallback file is the source of truth).
- NO calibration/auto-tuning here (deferred).

---

## 4. PHASES

### Phase 1 — Assumption→SKU impact registry (answers user item #2)
- [ ] `data/assumption_impact.json`: map each assumption key → {services, charge_types,
      mapper, decision_it_changes}. Seed it with the 10 existing keys + the R-rows below.
- [ ] A tiny loader + a `--list` in `job_inspect.py` (or a new `assumptions_impact.py`)
      that prints, for a key, every SKU/service it controls. This is the discoverability
      the user asked for.

### Phase 2 — Node-based cache mappers (R2, R3) — lowest risk, copy ElastiCache
- [ ] Add `dax_node_gib`, `memorydb_node_gib` keys (defaults + schema).
- [ ] Extend the ElastiCache FORMULA path (P4) to handle DAX + MemoryDB node→Memorystore
      GiB-hours. Verify $ changes vs passthrough on a bill with these rows.

### Phase 3 — NAT per-VM rescue (R1) — user's headline
- [ ] Add `nat_vms_per_gateway` (default 32, range 1–200, category networking).
- [ ] In the NAT gateway-hours pricer, multiply the Cloud NAT per-VM-hour rate by the
      assumption instead of charging one gateway. Stamp the row.
- [ ] Confirm NAT hourly projection moves from ~$3 toward the AWS ballpark at the default.

### Phase 4 — Glue wiring (R6) — keys already exist
- [ ] Consume `glue_dpu_vcpu` / `glue_dpu_ram_gib` in a Dataflow worker formula; flip Glue
      from mode:review passthrough to a computed mapping.

### Phase 5 — DynamoDB + Inferentia/Trainium (R5, R4) — declared-assumption mappings
- [ ] Add the keys; implement as *labeled* assumption mappings (explicitly "assumption-based,
      verify") so they're honest. These carry the most workload uncertainty — keep passthrough
      as the fallback when the target/assumption is unset.

### Phase 6 — RDS IOPS choice (R7)
- [ ] Add `rds_iops_included_in_storage`; make the io-optimized IGNORE-vs-SKU decision read it.

### Phase 7 — Tests + wiring
- [ ] Per R-row: a test that the assumption at default reproduces (or safely improves on)
      today's $, and that changing it moves the row's GCP $.
- [ ] Impact-registry test: every assumption key in defaults appears in
      `assumption_impact.json` (no orphan knobs).
- [ ] Full suites: `python3 -m pytest tests/`, `go build ./...`, `npm run build`. Record
      real counts.

---

## 5. OPEN DECISIONS (flag, don't guess)
- R5 DynamoDB target: Firestore vs Bigtable default? Propose Firestore for on-demand,
  Bigtable for provisioned — confirm with user.
- R1 NAT default VM-count (32 is a guess) — this is exactly the kind of value the DEFERRED
  calibration solver would learn; until then it's a declared default the admin can tune.

## 6. PROGRESS LOG (append-only)
- 2026-11-01 Work order created from a live scan of `pricing_matrix.json` + `service_map.json`.
  §1 Findings = the assumption-rescuable passthroughs (R1–R7) and the explicit
  not-rescuable list. Calibration solver DEFERRED per user. Depends on per-job override
  plumbing in WORKORDER_per_job_assumption_overrides.md (new keys surface in that panel
  automatically because it is schema-driven).

## 7. IF STUCK
Start with R2/R3 (pure copy of the working ElastiCache formula) to prove the
assumption→mapper→stamp→test loop end to end, then do R1 (NAT), then the riskier R4/R5.

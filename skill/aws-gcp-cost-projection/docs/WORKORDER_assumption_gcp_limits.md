# Work order — rightful (source-grounded) limits for every pricing assumption

Goal: every assumption's legal domain (`data/assumption_domains.json`) is grounded in a
**verifiable source**, not a hand-typed guardrail. Aurora set the pattern: its domain is
*derived from what GCP actually sells* (Cloud SQL vCPU:RAM ratios via `ratio_registry.json`).

Three grounding kinds:
- **GCP-derived** — the legal set comes from a GCP product shape/limit (the Aurora ideal).
- **AWS-derived** — the knob is an AWS-side quantity; its legal set comes from real AWS specs.
  GCP only constrains the *output* mapping, not the knob.
- **Policy / published** — a single published constant or an operational policy; no shape set.

Each value default is a separate decision (calibrated to AWS-OD/GCP-OD ratio ≈ 1 where the
knob is genuinely free; left at the physical/published truth where it is not).

| # | Knob(s) | Grounding | Source | Rightful limits | Default | Status |
|---|---------|-----------|--------|-----------------|---------|--------|
| 1 | `aurora_acu_vcpu` + `aurora_acu_ram_gib` | GCP-derived | Cloud SQL ratios (`ratio_registry`) | enum of sold vCPU:RAM | 2 vCPU : 16 GiB (1:8) → ratio 0.974 | ✅ done (pre-existing) |
| 2 | `lambda_mb_per_vcpu` | GCP-derived | Cloud Run CPU:mem cap (4 GiB/vCPU) | `[512, 4096]` | 2500 → ratio 0.999 | ✅ done |
| 3 | `dax_node_gib` | AWS-derived | EC2 R-family node RAM; Memorystore M5≈300 ceiling | `[2, 512]` | 16 (r.large) | ✅ done |
| 4 | `memorydb_node_gib` | AWS-derived | EC2 R-family node RAM; Memorystore M5≈300 ceiling | `[2, 512]` | 16 (r.large) | ✅ done |
| 5 | `glue_dpu_vcpu` + `glue_dpu_ram_gib` | AWS-pinned | Glue G-series DPU = 4 vCPU/16 GiB (fixed); Dataflow bills vCPU/RAM independently | fixed at G-series spec | 4 vCPU, 16 GiB → ratio 0.716 | ✅ done (already `scalar_published`; no move — concrete DPU, not free) |
| 6 | `inferentia_chip_to_tpu` | passthrough | no benchmark; carry AWS cost | `[0, 10]` | **0 (passthrough)** | ✅ passthrough default → ratio 1, no unverified equivalence; >0 maps to TPU v5e (~0.19 = inf1 price parity) |
| 7 | `trainium_chip_to_tpu` | judgment | TPU v5e chip; **no public perf benchmark** | `[0.1, 10]` | 1.0 | ✅ resolved — irreducibly judgment |
| 8 | `dynamodb_rcu_to_reads_sec` | utilization | Firestore Read Ops ($3e-7/op); AWS $0.00013/RCU-hr | `[0, 100]` | **0 (passthrough)** | ✅ calibrated — 0=passthrough→ratio 1 (matrix intent); 1.0 over-projected 8.3x; op-native ratio-1 ≈0.12 |
| 9 | `dynamodb_wcu_to_writes_sec` | utilization | Firestore Entity Writes ($9e-7/op); AWS $0.00065/WCU-hr | `[0, 100]` | **0 (passthrough)** | ✅ calibrated — 0=passthrough→ratio 1 (matrix intent); 1.0 over-projected 5.0x; op-native ratio-1 ≈0.20 |
| 10 | `nat_vms_per_gateway` | AWS-topology | VMs behind a gateway; not a GCP shape | `[1, 200]` | 36 (calibrated) | ✅ done — value calibrated; bounds irreducibly judgment |
| 11 | `looker_studio_pro_seat_usd` | 3rd-party corroborated | Data Studio Pro (formerly Looker Studio Pro) per-user/project license | published scalar | 9.0 | ✅ verified 2026-11-09: Google reverted Looker Studio→Data Studio (Apr 2026); paid tier = Data Studio Pro, $9/user/project/mo. No public price page; corroborated by TrustRadius/Coupler/Databloo/Whatagraph + rebrand coverage. Flag kept for console confirm |
| 12 | `aws_ri_1yr/3yr`, `aws_sp`, `aws_spot` | AWS-policy | bill-derivable discount %s | `[0, 0.9x]` | published typicals | ✅ resolved — AWS-side, not GCP-derivable (and OD-vs-OD basis means these don't move the ratio) |
| 13 | `fx_max_staleness_days` | policy | operational | `[1, 90]` | 7 | ✅ resolved — operational policy, no shape |
| 14 | `rds_iops_included_in_storage` | policy flag | Cloud SQL $0 I/O policy | `{0, 1}` | 1 | ✅ resolved — policy flag |

## Summary

- **GCP-derived limits (the Aurora ideal):** Aurora ✅, Lambda ✅ (Cloud Run 4 GiB/vCPU cap).
- **AWS-spec-derived limits:** DAX ✅, MemoryDB ✅ (R-family node RAM), Glue ✅ (G-series DPU, already published).
- **Passthrough defaults (ratio 1, no invented GCP volume/equivalence):** inferentia 0 (no benchmark); dynamodb rcu/wcu 0 (workload-dependent, matches pricing_matrix). trainium left at 1.0 (already ~0.90x parity; one toggle from passthrough). nat 36 (AWS topology count, calibrated). fx & rds_iops are policy, not price knobs. discounts excluded from the OD-vs-OD basis by design.
- **Needs human verification:** `looker_studio_pro_seat_usd` — Google moved/removed the pricing page.

So "rightful limits for all" = done where a source exists (7 knobs grounded), and honestly classified where no GCP/AWS shape exists to ground against (the rest).

Wiring: domains in `assumption_domains.json` flow to the admin UI automatically —
`db.GetAssumptions()` calls `LoadAssumptionDomains()` and attaches `.Domain` per row; the
React admin page renders `domain.kind`/`min`/`max`/`values`. A running server must be
restarted to reload the JSON (`sync.Once`), and each `calibrate_*` migration fixes existing
DB bounds on startup.

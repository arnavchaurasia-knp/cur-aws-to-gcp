# WORKORDER — Complete per-service spec partition (deterministic vs LLM, all cases)

Status: **READY.** Generalizes the DynamoDB fix into a rule that applies to **every** service: enumerate all billing dimensions, classify each deterministic-vs-LLM, and prove the partition is complete + disjoint with a gate. Supersedes the per-row DynamoDB patch discussed in WORKORDER_cud_and_egress_bugs / the inert-assumptions verification.

---

## 0. The classification principle (where each wins for ACCURACY)

- **Deterministic** when the correct GCP target **and** the unit conversion are *fully determined by CUR fields*. The output is a pure function of the bill. Examples: EC2 family→GCE family, EBS gp3→pd-balanced, KMS key→key-version, egress GB→egress SKU, storage GB→storage SKU, DAX node→Memorystore. An LLM here only adds variance.
- **LLM / judgment** when the correct answer depends on information **not in the bill** — workload shape, access pattern, throughput, architecture intent. Then the **LLM picks the target/shape**, and deterministic **assumptions do the arithmetic** once the target is fixed. Examples: DynamoDB throughput→Firestore/Bigtable/Spanner, Kinesis→Pub/Sub vs Kafka, SageMaker→training vs serving.
- **Never** use a deterministic default to paper over a genuine judgment call — it produces a confident wrong number (that's the FIX-4 failure: forcing all DynamoDB throughput to Firestore regardless of workload). And never let the arithmetic assumption go *inert* — feed it into the LLM guidance so it's used once the target is chosen.

Evidence this matters: for DynamoDB the **target choice is a multiples-level lever** — Firestore is per-operation (~$0.38/M reads, ~$0.90/M writes), Bigtable is per-node ($0.65/node-hr, 3-node minimum). The cheaper one flips with throughput, and throughput is not in the CUR. So target = LLM; storage/DAX/egress = deterministic.

---

## 1. "All possibilities resolved" — two different guarantees

The pipeline ALREADY guarantees **nothing is dropped**: `spec_dispatch.dispatch()` returns a mechanic for every row, and unmatched rows get an explicit `("misc","spec_gap:<svc>")` fallback → LLM (`spec_dispatch.py:344`, "Guaranteed to return a non-null mechanic_group"). So structural completeness exists.

What is NOT guaranteed today is that each **known** billing dimension is routed to the **right, explicit** mechanic. A known dimension that happens to miss every charge-type silently lands in the `spec_gap` catch-all — handled, but by accident and with no deterministic treatment even when one is warranted. "Resolution for all possibilities" means: **every known usage-type maps to an EXPLICIT charge-type with the correct det/LLM mechanic; the `spec_gap` backstop is reserved for genuinely-new usage-types only.**

Two gates enforce this:
- **Disjointness/reachability** — existing `test_spec_partition::test_no_charge_type_is_unreachable` (no charge-type shadowed by an earlier sibling). *Currently RED for dynamodb* — see §3.
- **Completeness** — NEW per-service coverage test (§4): a catalog of known usage-types each dispatches to an explicit (non-`spec_gap`) charge-type with the expected mechanic.

---

## 2. The method (apply to every service)

1. **Enumerate** the service's full usage-type universe from the AWS billing guide + observed CUR (not guesswork). Record the catalog in `data/service_usage_catalog.json` (`{service: [usage_type_signature, ...]}`) — this becomes the completeness-gate fixture.
2. **Classify** each dimension deterministic or LLM per §0.
3. **Partition**: write one explicit charge-type per dimension (or per group), ordered specific→general, so the set is complete (covers the catalog) and disjoint (passes the reachability gate). The `spec_gap` catch-all is last-resort only.
4. **Gate**: add the usage-type catalog to the completeness test (§4); keep the reachability test green.
5. For LLM dimensions, put the tunable assumptions **into the `llm_guidance`** so they're consumed, not inert.

---

## 3. DynamoDB — fully enumerated (worked example)

Verified usage-type universe (AWS DynamoDB billing guide + Vantage CUR):

| # | dimension | usage_type signature | mechanic | det/LLM | target | notes |
|---|---|---|---|---|---|---|
| 1 | DAX node-hours | `NodeUsage` / `DAX` | elasticache | **DET** | Memorystore | reads `dax_node_gib` |
| 2 | Provisioned throughput | `(Read\|Write)CapacityUnit-Hrs` | dynamodb_throughput → misc+LLM | **LLM** target, DET convert | Firestore/Bigtable/Spanner | assumption in guidance |
| 3 | On-demand request units | `(Read\|Write)RequestUnits` | dynamodb_throughput → misc+LLM | **LLM** | same | |
| 4 | Global-table repl writes | `ReplWriteRequestUnits`, `ReplWriteCapacityUnit-Hrs` (+ `IA-` class) | dynamodb_throughput → misc+LLM, **+ inter-region egress** | **LLM** | multi-region Firestore/Spanner + egress | replication ⇒ multi-region target & cross-region egress |
| 5 | Streams read units | `Streams-RequestsCount` | misc+LLM | **LLM** | Firestore triggers / Pub/Sub / Datastream | currently unhandled |
| 6 | CDC to Kinesis | `ChangeDataCaptureUnits-Kinesis` | misc+LLM | **LLM** | Pub/Sub / Datastream | currently unhandled |
| 7 | Table storage | `TimedStorage-ByteHrs` (+ `IA` class) | dynamodb_storage | **DET** | Firestore storage | |
| 8 | PITR storage | `TimedPITRStorage-ByteHrs` | dynamodb_storage | **DET** | Firestore PITR | |
| 9 | Backup storage | `TimedBackupStorage-ByteHrs` | dynamodb_storage (backup variant) | **DET** | GCS / Firestore backup | currently unhandled |
| 10 | Restore | `RestoreDataSize-Bytes` | dynamodb_storage (one-off) | **DET** | restore op | low $; currently unhandled |
| 11 | Data transfer out | `DataTransfer-Out-Bytes` | data_transfer | **DET** | egress SKUs | ensure the global data_transfer charge-type catches it |
| 12 | Reserved capacity fee | RI upfront/recurring | global commitment exclusion | n/a | — | handled pre-spec |
| — | anything new | (no match) | `spec_gap` → misc → LLM | LLM | — | TRUE last resort only |

### Remediation (fixes the RED suite + closes the gaps)
1. **Delete the deterministic `dynamodb_capacity` charge-type** (the FIX-4 addition) and route #2/#3 to `dynamodb_throughput` (LLM). This un-shadows `dynamodb_throughput` → `test_no_charge_type_is_unreachable` green, and restores the documented workload-dependent design → `test_llm_guidance` + `test_misc_guidance_wiring` green.
2. **Keep the `dynamodb_rcu_to_reads_sec`/`_wcu_to_writes_sec` assumptions live** by injecting them into `dynamodb_throughput.llm_guidance` (the LLM uses them to size the chosen target). Repoint their `assumption_impact.json` mapper field at the LLM-guided path. Not inert, not a blanket Firestore multiplier.
3. **Add the missing explicit charge-types** (#4 global-table repl writes + egress, #5 streams, #6 CDC, #9 backup, #10 restore, IA-class storage variants) so they stop landing in `spec_gap` by accident. #4/#5/#6 are LLM; #9/#10 deterministic.
4. `map_dynamodb_capacity` becomes dead code (the deterministic throughput mapper) — delete, or retain only if/when the hybrid (LLM picks target → deterministic conversion) is built.
5. Add DynamoDB's usage-type catalog to the §4 completeness gate.

---

## 4. Completeness gate (new test)

`tests/test_spec_coverage.py`:
```
for svc, usage_types in load("data/service_usage_catalog.json"):
    for ut in usage_types:
        res = spec_dispatch.dispatch(synthetic_row(svc, ut))
        assert res and res["source_rule"] does NOT start with "spec_gap", \
            f"{svc}/{ut} falls to the catch-all — add an explicit charge-type"
        assert res["mechanic"] == expected_mechanic(svc, ut)   # det vs LLM as classified
```
This is what makes "all possibilities resolved" true **by construction** — a new known dimension can't silently rot in the catch-all, and a det/LLM misclassification is caught. Pair it with the existing reachability test (disjointness).

---

## 5. Rollout order

1. **DynamoDB** (above) — fixes the current RED suite; the template for the rest.
2. Then enumerate + partition the other **multi-dimension / high-$ services** the same way, deterministic-vs-LLM per §0: rds, elasticache, redshift, opensearch, msk, kinesis, s3, ebs, efs, fsx, cloudwatch, lambda. (Cross-reference the candidate-target work in WORKORDER_best_fit_target_selector — target *product* selection and charge-type *partition* are complementary: the partition decides the mechanic, the selector decides the target among candidates.)
3. Simple single-dimension services (sns, route53, kms, secretsmanager, xray, …) get a one-line catalog entry + confirmation.
4. Keep both gates green in CI.

Every rate/target pinned from a page cites its official URL per AGENTS.md. LLM dimensions carry `confidence_max ≤ 0.5–0.6` and disclose the assumption used.

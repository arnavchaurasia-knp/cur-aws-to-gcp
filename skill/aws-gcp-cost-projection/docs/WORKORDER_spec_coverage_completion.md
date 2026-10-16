# WORK ORDER — Close the remaining spec-dispatch coverage gaps

**For an implementing AI / engineer with no prior context. Read this file fully
before editing anything.** All paths are relative to
`skill/aws-gcp-cost-projection/`.

**Prerequisite (already landed):** nested charge types, the partition
invariant, and the EC2/RDS/WAF fixes. See
`docs/ANALYSIS_spec_dispatch_validation.md` for the measurements that drove
them, and §1 below for where things stand.

**§2 is the reusable playbook** — symptom-to-layer routing, the per-service
procedure, and three worked examples from the fixes already landed. Read it
before §4; every work item there is an instance of it.

This work order is **classification coverage only**. It does not change pricing,
SKU selection, mappers, or the report. Rows move between `mechanic_group`
values, which *can* change dollars — §6 says exactly how to verify each move is
intended.

---

## 1. Where things stand

Measured on the 12-bill corpus in `Downloads/Facets` (4 PDF, 5 XLSX, 4 CSV
including one raw CUR), 1,457 workload rows:

```
bucket          rows           spend   % spend
agree           1295         511,448     95.3%
differ            74           6,251      1.2%
spec_gap          16           3,819      0.7%
no_spec           72          15,112      2.8%

genuine ambiguity: NONE (1,385 spec-covered rows checked)
```

Reproduce any time with:

```bash
python scripts/spec_parity.py <jobs-root>
```

Already fixed: EC2 `instance_hour` is now a nested group
(windows / burstable / graviton / standard) with disjoint siblings; RDS
`Multi-AZUsage` and `InstanceUsageIOOptimized` are classified; RDS
`backup`/`storage` and WAF `fixed_hourly`/`monthly` no longer overlap; two
redundant `product_re` identity tests removed. 995 tests pass.

**What is left is the four buckets above.** Work them in the order below —
it is dollar order, and each item is independently verifiable.

---

## 2. The playbook — how to fix any service, not just these

Everything in §4 is an instance of the same procedure. Use this for any service,
including ones discovered after this work order is closed. **Diagnose the layer
first; the most common mistake is fixing a symptom in the wrong layer.**

### 2.1 Symptom → layer → where the fix belongs

| What `spec_parity.py` shows | Actual cause | Fix HERE | Never here |
|---|---|---|---|
| canonical is `?` | Layer 1 cannot name the service | `data/aws-aliases.json`, or `data/aws-product-codes.json` if AWS publishes a code | a `product_re` inside some spec |
| one service under two canonicals (`ebs` vs `ec2`) | Layer 1 splits what should be one service, or merges what should be two | `data/aws-aliases.json` | a charge_type re-testing the product name |
| `no_spec`, service is real | no spec written yet | a new entry in `data/service_specs.json` | the legacy `RULES` chain |
| `no_spec`, rows are commitments/credits/tax | already caught by the global pre-filter | nothing — fix the harness's bucketing | a spec |
| `spec_gap`, usage type looks sane | a charge type is genuinely missing | a new charge_type in that service | widening an existing one until it swallows the row |
| `spec_gap`, usage type malformed (`...-Usage:NA`, `GLOBAL-Usage:671.49`, empty) | extraction defect | `scripts/ingest.py` | a regex matching malformed text |
| ambiguity: specific + its general case | parent/child written flat | nest them (§2.3) | reordering the list and hoping |
| ambiguity: two specific charge types | a real overlap | an `exclude_*` predicate on the loser | list order |
| `differ`, spec looks right | legacy bug the spec already corrects | a `notes` line recording the decision | nothing |
| `differ`, legacy looks right | spec bug | tighten the spec predicate | the legacy chain |
| routed to `misc`/`per_request`/`flat_hourly` when the service deserves its own pricer | accuracy gap, not a classification gap | out of scope — a separate work order | this work order |

**The rule behind the table:** identity is Layer 1, field extraction is ingest,
routing is the spec, pricing is the mapper. A fix that crosses a layer boundary
is the whack-a-mole pattern returning — it works for one bill and re-breaks on
the next format.

### 2.2 Per-service procedure

1. **Measure first.** `python scripts/spec_parity.py <jobs-root>`. Record the
   four numbers (`agree` %, `spec_gap` $, `no_spec` $, ambiguity) *before*
   touching anything. Without a before you cannot prove an after.
2. **Pick one service**, highest spend first.
3. **Classify each of its problems** against §2.1 and write down which layer
   each belongs to, before editing.
4. **Fix bottom-up**: identity → extraction → spec. A spec fix built on a broken
   canonical is undone the moment the canonical is corrected.
5. **Re-measure.** `agree` must not fall; `spec_gap`/`no_spec` must fall;
   ambiguity must stay NONE.
6. **Pin every judgement call** — a `notes` line in the charge_type *and* a case
   in `tests/test_spec_partition.py`. A decision that exists only in a diff gets
   reversed by the next person.
7. **Explain every moved row**: rows changed, spend, one sentence on why the new
   answer is right. An unexplained move is a regression.

### 2.3 When to nest, and how

Nest when a service has **a general case plus specific variants of it** — the
shape that produced every ambiguity measured on the corpus.

```
service
└── group            coarse question: "is this an instance-hour row?"
    ├── variant_a    fine question: OS? family? architecture?
    ├── variant_b
    └── default      "match": {} — MUST be last, makes the set exhaustive
```

Rules, all enforced by `tests/test_spec_partition.py`:

- The parent's `match` is the **union** of what the flat entries matched, so no
  row that used to classify stops entering the group.
- Children are evaluated in order; first match wins **within** the group.
- No two non-default siblings may match the same row. If they can, add an
  `exclude_*` predicate to the one that should lose — the winner becomes a
  declared decision instead of a line-order accident.
- The last child is a default, so a row entering a group always leaves it named.
- Depth is not limited, but two levels has been enough. Do not go deeper without
  a measured reason.

**Do not nest** a service that is a flat list of unrelated charge types
(storage, requests, transfer). Nesting there adds structure with no invariant to
enforce.

### 2.4 Worked example — EC2, the reference conversion

Copy this pattern. Before: five flat charge types, four of them overlapping.

```
cpu_credits
instance_hour_windows      <- matched BoxUsage rows
instance_hour_burstable    <- matched BoxUsage rows
instance_hour_graviton     <- matched BoxUsage rows
instance_hour              <- matched ALL BoxUsage rows
```

`instance_hour` is not a default — it has a real `match` — but it is the general
case for the three variants above it. On the 12-bill corpus **335 rows ($53k)
matched two or three of these at once**, resolved only by list position.

After:

```
cpu_credits
instance_hour                     match: unit_hourly + any_of[union of all four]
├── instance_hour_windows         any_field_re "running Windows|Windows"
├── instance_hour_burstable       usage_type_re ":t[234][ag]?\."  + excludes Windows
├── instance_hour_graviton        usage_type_re ARM pattern       + excludes Windows AND t-family
└── instance_hour_standard        match: {}   <- default
ebs_storage
data_transfer
nat_and_flat_hourly
```

Three things made it safe. Reproduce all three:

1. **The parent's `any_of` is the union** of all four old predicates, so every
   row that previously classified still enters the group. Nothing is dropped.
2. **Child order mirrors the old flat order** (windows → burstable → graviton →
   standard), so outcomes are unchanged by construction.
3. **Two overlaps had to be declared**, because the data showed siblings still
   colliding after nesting:
   - **t4g is both burstable and Graviton** (60 rows). Burstable wins — AWS CPU
     credits never lower the AWS price, so E2 is correct and the ARM mapper
     would overstate GCP cost. Encoded by excluding the t-family pattern from
     `instance_hour_graviton`; pinned by
     `test_t4g_routes_to_burstable_not_graviton`.
   - **A Windows t3 row** (3 rows) carried "Windows" in `product` only, so the
     usage_type/operation exclusions missed it. Fixed with
     `exclude_any_field_re`; pinned by
     `test_windows_burstable_routes_to_windows`.

Result: ambiguity 340 rows / $54k → **0**, `agree` unchanged at 95.3%.

### 2.5 Worked example — two one-line fixes, and a trap

Not every overlap needs nesting.

- **WAF**: `waf_fixed_hourly` and `waf_monthly` both matched "Web ACL … per
  Month" (3 rows). Both route to `flat_hourly`, so the outcome never differed —
  but which won was positional. Fixed by giving `waf_monthly` an `exclude_when`
  on the hourly vocabulary. Clarity fix, zero behaviour change.
- **RDS**: `backup` (`backup|snapshot`) and `storage` (`gb-mo|storage`) both
  matched a GB-Mo backup row. Fixed with `exclude_any_field_re` on `storage`.

**The trap.** The first attempt put that exclusion in `exclude_when`.
`exclude_when` **ANDs** its predicates, so a second condition *narrows* the
exclusion rather than widening it — it silently weakened the existing
`unit_hourly` exclusion. A negative predicate belongs in `match`
(`exclude_any_field_re`), never in `exclude_when`. That predicate was added to
the engine for exactly this reason.

### 2.6 Worked example — a missing charge type, and why it was invisible

RDS `APS5-Multi-AZUsage:db.m7g.large` matched **nothing** — $5,567 unclassified
on one corpus, for a core RDS charge. The fix was one token added to
`rds.instance_hour.fallback_match`
(`Multi-AZUsage:|InstanceUsageIOOptimized:`), pinned by
`test_rds_multi_az_instance_hours_are_classified`.

The lesson is about detection, not the fix. This was invisible because
`dispatch()` returns `None` both for "service has no spec" and "spec matched
nothing", and both fell back to legacy silently. Separating those two buckets in
the harness is what surfaced it. **For any service you touch, read its
`spec_gap` rows — that is where the silent holes are.**

---

## 3. Files to read first

- `docs/ANALYSIS_spec_dispatch_validation.md` — the measurements
- `scripts/spec_parity.py` — the harness; read `main()` to see what each bucket means
- `scripts/spec_dispatch.py` — `dispatch()`, `_eval_block()` (the predicate
  vocabulary), `is_default_charge_type()`
- `tests/test_spec_partition.py` — the invariants you must not break
- `data/service_specs.json` — the registry

**Predicate vocabulary** (`_eval_block`): `product_re`, `usage_type_re`,
`operation_re`, `any_field_re`, `exclude_usage_type_re`, `exclude_operation_re`,
`exclude_any_field_re`, `product_re_not`, `unit_in`, `unit_not_in`, `unit_re`,
`unit_hourly`, `pricing_model_in`, `line_item_type_in`, `cost_lt_zero`,
`is_accelerator`. Unknown keys **fail closed**.

**Field authority — prefer predicates in this order:**

```
unit_in / unit_hourly     strongest: structured, format-independent
usage_type_re             strong:    AWS's own charge vocabulary
operation_re              medium:    reliable on PDF, often absent on CUR
any_field_re              weakest:   needs a comment justifying it
product_re                BANNED inside a service's own spec (test enforces)
```

---

## 4. Work items, in measured order

### ITEM 1 — `savingsplans` / `discounts` / `ebs` / `aurora` canonicals ($15.4k)

`no_spec` is the largest remaining bucket. Four of its entries are not missing
specs at all — they are **identity problems**:

| canonical | spend | what it is |
|---|---:|---|
| `savingsplans` | $10,522 | commitment rows — already handled by the global `commitment_discount` pre-filter |
| `discounts` | −$1,457 | same |
| `ebs` | $4,790 | EBS resolves as its own service instead of folding into `ec2` |
| `aurora` | $52 | resolves separately from `rds` |

**Do:**

1. For `savingsplans` / `discounts`: confirm with `spec_parity.py` that these
   rows are already caught by the global pre-filter in
   `classify_via_specs()`. If they are, they are mis-reported by the harness,
   not mis-classified — fix the harness to bucket pre-filtered rows as
   `excluded` rather than `no_spec`, and change nothing in the specs.
2. For `ebs` and `aurora`: decide **in `data/aws-aliases.json`** whether they
   fold into `ec2` / `rds`, or stay separate with their own spec.
   - Folding is the smaller change and matches how the EC2 spec already owns
     `ebs_storage`. Check `data/aws-product-codes.json` first: if AWS publishes
     a distinct product code for them, keep them separate and give each a spec.
   - **Do not** solve this by adding a product-name rule inside `ec2`'s spec.
     Identity belongs to Layer 1.

### ITEM 2 — 26 rows with no `canonical_service` at all ($1,019)

They appear as canonical `?`. These are Layer-1 misses.

**Do:** run `scripts/aws_normalizer.py` against a job DB and read its
unrecognized-products table (it already prints one). Add the missing names to
`data/aws-aliases.json`. If a row has no usable product name at all, that is an
**ingest** defect — record it and stop; do not invent an alias.

### ITEM 3 — `AP-SOUTHEAST-1-Usage:NA` region prefixes ($2,930)

Three services (`rds` $2,517, `elb` $215, `msk` $198) have `spec_gap` rows whose
usage type is `AP-SOUTHEAST-1-Usage:NA` — the **full region name** instead of
the short `APS1` code, with `NA` where the charge type belongs. This is a
PDF/XLSX extraction artifact.

**Do:** fix it at ingest, not in the specs. Normalising
`AP-SOUTHEAST-1-Usage:NA` → the short-code form in `scripts/ingest.py` fixes all
three services at once; adding a regex to three spec files fixes none of the
others that will follow. If the source genuinely carries no charge type (`NA`),
the row cannot be classified and should be reported as a data gap — the
existing `data_gaps.json` path in `classify_mechanics.py` already does this.

### ITEM 4 — `GLOBAL-Usage:671.49` and empty usage types ($720)

`ec2` ($640) and `dynamodb` ($80) have rows whose usage type is empty or a bare
number. A number where a charge type belongs is a parse error.

**Do:** trace one of these back to its source bill (they come from PDFs in the
corpus). If the number is a cost that landed in the wrong column, it is an
ingest bug. Report what you find before changing anything — **do not** add a
charge type that matches numeric usage types.

### ITEM 5 — `kinesis` shard-hours / billed bytes ($142)

`USW2-BilledBytes` and `APS5-Storage-ShardHour` match no Kinesis charge type.
Genuinely missing entries.

**Do:** add two charge types to `kinesis`, keyed on `usage_type_re`
(`BilledBytes`, `ShardHour`), with the same mechanic the legacy chain gives
those rows today — check with `classify_mechanics.classify()` before choosing.

### ITEM 6 — review the 5 remaining disagreements ($6,251)

These are rows where the spec engine and the legacy chain disagree. Each needs a
judgement, not a patch. **Decide each one and record the reasoning in the
charge_type's `notes`:**

| canonical | legacy → spec | rows | spend | the question |
|---|---|---:|---:|---|
| `vpc` | `flat_hourly` → `misc` | 48 | $1,473 | VPC rows the spec sends to the LLM that legacy priced as flat hourly. Which is right? |
| `rds` | `misc` → `managed_db` | 16 | $3,050 | **The Multi-AZ fix.** Spec is almost certainly right — these are instance-hours. Confirm, then this stops being a disagreement. |
| `ec2` | `compute_breakdown` → `compute_burstable` | 7 | $1,545 | t-family rows legacy sent to the general compute mapper. Spec looks right. |
| `opensearch` | `opensearch` → `block_storage` | 2 | $182 | OpenSearch storage rows — dedicated mapper or generic block storage? |
| `kms` | `per_request` → `misc` | 1 | $0 | immaterial; decide and move on |

For each: if the spec is right, add a `notes` line saying so and why. If legacy
is right, fix the spec predicate. **Do not leave any undecided.**

### ITEM 7 — convert `s3` and `rds` to nested groups

EC2 is converted and proved. These two have the same shape:

```
s3                        rds
├── storage               ├── instance_hour
│   ├── standard          ├── storage
│   ├── ia                │   ├── gp2_gp3
│   ├── glacier           │   ├── provisioned_iops
│   └── other             │   ├── backup
├── requests              │   └── other
└── data_transfer         └── extended_support
```

**Do:** convert one at a time. After each, `tests/test_spec_partition.py` must
pass and `spec_parity.py` must show `agree` unchanged at 95.3% with ambiguity
still NONE. Add a case to `AMBIGUITY_CASES` for every sibling pair you create.

**Rule for every group:** the last child is a default (`"match": {}`), earlier
children carry positive predicates, and no two non-default siblings may match
the same row.

---

## 5. What NOT to do

- Do **not** add `product_re` inside a service's own spec. The test fails, and
  it re-introduces the free-text coupling Layer 1 exists to remove.
- Do **not** fix an identity problem (ITEM 1, 2) inside a charge_type. Identity
  is `data/aws-aliases.json` / `data/aws-product-codes.json`.
- Do **not** fix an extraction problem (ITEM 3, 4) inside a charge_type.
- Do **not** add a charge_type whose `match` is `{}` anywhere except last.
- Do **not** change any mapper, SKU, rate or report file.
- Do **not** cut the pipeline over to `spec_dispatch`. `classify_mechanics`
  stays the live classifier for now; this work order only makes the shadow
  engine complete. Cutover is a separate decision.
- Do **not** "fix" anything else you notice. List it at the end of your summary.

---

## 6. Verification — run all of these, paste the output

```bash
cd skill/aws-gcp-cost-projection
python -m pytest tests/ -q                 # 995+ must pass, no new failures
python -m pytest tests/test_spec_partition.py -v
python scripts/spec_parity.py <jobs-root>
```

**The four numbers that matter**, before and after your change:

| metric | now | after |
|---|---:|---|
| `agree` % spend | 95.3% | must not fall |
| `spec_gap` spend | $3,819 | must fall |
| `no_spec` spend | $15,112 | must fall |
| genuine ambiguity | NONE | must stay NONE |

**Ambiguity check** — paste the result; it must print `NONE`:

```bash
python - <<'PY'
import duckdb, glob, os, sys
sys.path.insert(0, 'scripts')
import spec_dispatch as sd
ROOT = r'<jobs-root>'
COLS = ["aws_li_key","product","usage_type","operation","pricing_unit","line_item_type",
        "pricing_model","aws_amortized_cost","instance_type","instance_vcpus",
        "instance_ram_gb","instance_count","billing_format","product_code",
        "canonical_service","is_workload"]
def nd(canon, row):
    out = []
    def walk(cts, p=""):
        for ct in cts:
            if not sd._charge_type_matches(ct, row): continue
            n = p + ct["id"]
            if ct.get("children"): walk(ct["children"], n + ".")
            elif not sd.is_default_charge_type(ct): out.append(n)
    walk(sd.SPECS[canon].get("charge_types", []))
    return out
bad = 0
for db in glob.glob(os.path.join(ROOT, "*", "projection-audit", "projection.duckdb")):
    try:
        c = duckdb.connect(db, read_only=True)
        rs = c.execute("SELECT " + ",".join(COLS) + " FROM aws_li_catalog WHERE is_workload").fetchall()
        c.close()
    except Exception:
        continue
    for raw in rs:
        r = dict(zip(COLS, raw)); r["unit"] = r.pop("pricing_unit")
        r["canonical"] = r.pop("canonical_service"); r["_bill_has_discounted_usage"] = True
        if r["canonical"] not in sd.SPECS: continue
        m = nd(r["canonical"], r)
        if len(m) > 1:
            bad += 1; print("AMBIGUOUS", r["canonical"], m, r["usage_type"])
print("NONE" if not bad else f"{bad} ambiguous row(s)")
PY
```

**Every row that moves must be explained.** For each item, report: how many rows
changed `mechanic_group`, their total spend, and one sentence on why the new
answer is correct. A row that moves without an explanation is a regression, not
a fix.

---

## 7. Done criteria

- [ ] ITEM 1: `savingsplans`/`discounts` correctly bucketed; `ebs`/`aurora`
      resolved in Layer 1 (not in a spec).
- [ ] ITEM 2: zero rows with canonical `?` on the corpus, or each remainder
      reported as an ingest defect.
- [ ] ITEM 3: `AP-SOUTHEAST-1-Usage:NA` normalised at ingest; rds/elb/msk gaps
      closed.
- [ ] ITEM 4: numeric/empty usage types traced to source and reported.
- [ ] ITEM 5: kinesis `BilledBytes` + `ShardHour` classified.
- [ ] ITEM 6: all 5 disagreements decided, each with a `notes` line.
- [ ] ITEM 7: `s3` and `rds` nested; partition tests extended.
- [ ] `agree` ≥ 95.3%, `spec_gap` and `no_spec` both reduced, ambiguity NONE.
- [ ] Full suite passes with no new failures.

# WORK ORDER — Close the remaining spec-dispatch coverage gaps

**For an implementing AI or engineer with no prior context. Read this file
completely before editing anything.** Every path is relative to
`skill/aws-gcp-cost-projection/` unless stated otherwise.

**Scope: classification coverage only.** You are deciding which `mechanic_group`
a bill row belongs to. You are NOT changing prices, SKU selection, mappers, the
report, or the live pipeline. `classify_mechanics.py` remains the classifier the
pipeline actually uses; `spec_dispatch.py` runs in shadow. Nothing here cuts
over.

**§2 is the playbook** — symptom-to-layer routing, the per-service procedure,
and four worked examples from fixes already landed. Read it before §5. Every
work item is an instance of it.

---

## 0. Setup — build the measurement corpus first

Everything here is verified against real bills. Build the corpus before reading
further; an unmeasured change to this code is not reviewable.

The bills are in `C:\Users\arnav\Downloads\Facets` — 15 files (4 PDF, 5 XLSX,
4 CSV including one raw CUR, plus 3 that are correctly rejected: an RVTools
vSphere inventory, an Azure plan, and one unparseable CSV).

`scripts/ingest.py` reads `input.<ext>` from its working directory, so each bill
needs its own job folder. Build them in a scratch directory, **never inside the
repo**:

```bash
SKILL="C:/Users/arnav/Downloads/cur-web-AGY/skill/aws-gcp-cost-projection"
CORPUS="$TEMP/spec-corpus"
rm -rf "$CORPUS"; mkdir -p "$CORPUS"

i=0
cd "C:/Users/arnav/Downloads/Facets"
find . -type f \( -iname "*.csv" -o -iname "*.pdf" -o -iname "*.xlsx" \) | while read f; do
  i=$((i+1)); ext="${f##*.}"
  mkdir -p "$CORPUS/job$i/projection-audit"
  cp "$f" "$CORPUS/job$i/input.$ext"
  basename "$f" > "$CORPUS/job$i/name.txt"
done

export SKILL_DIR="$SKILL"
for d in "$CORPUS"/job*; do
  (cd "$d" && python "$SKILL/scripts/ingest.py" >/dev/null 2>&1)
  db="$d/projection-audit/projection.duckdb"
  [ -f "$db" ] && python "$SKILL/scripts/aws_normalizer.py" "$db" >/dev/null 2>&1
done
```

`aws_normalizer.py` must run — it stamps `canonical_service`, and
`spec_parity.py` silently skips any DB without it.

Then confirm the baseline:

```bash
cd "$SKILL" && python scripts/spec_parity.py "$CORPUS"
```

**Throughout this file, `<CORPUS>` means that path.**

**If your baseline does not match §1 exactly, STOP and report it.** The numbers
in this document are the contract. A different baseline means something changed
underneath this work order and proceeding would produce unverifiable results.

---

## 1. Baseline — the numbers you must not break

```
jobs=12 (skipped 0 pre-Layer-1)  workload_rows=1457  specs=54

bucket          rows           spend   % spend
agree           1371         518,432     96.6%
differ            25           4,778      0.9%
spec_gap          14           3,679      0.7%
no_spec           16             684      0.1%
excluded          31           9,057      1.7%

genuine ambiguity: NONE
```

`spec_gap` PER SERVICE — this is the number that matters, not the total:

```
rds       $2,519      ec2       $640      elb       $215
msk         $198      dynamodb   $80      lambda     $27
```

What each bucket means:

- **agree** — `spec_dispatch` and `classify_mechanics` return the same
  mechanic. Must not fall.
- **differ** — they disagree. All 25 remaining rows have been reviewed and
  **the spec is correct in every case** (§5 ITEM 6). Must not grow.
- **spec_gap** — the service has a spec but no charge type matched. A real
  coverage hole. Must fall.
- **no_spec** — the service has no spec. Must fall.
- **ambiguity** — rows matching two non-default charge types. Must stay NONE.

Already landed, do not redo: EC2 `instance_hour` nested into
windows/burstable/graviton/standard with disjoint siblings; RDS `Multi-AZUsage`
and `InstanceUsageIOOptimized` classified; RDS backup-vs-storage and WAF
hourly-vs-monthly overlaps removed; VPC public-IPv4 and VPN-hour charge types
added; KMS request fallback for PDF rows; two redundant `product_re` identity
tests removed. 996 tests pass.

**ITEMs 1, 2, 3 and 7 have since been completed** (harness `excluded` bucket;
`ebs`→`ec2` and `aurora`→`rds` folds plus 27 new aliases; kinesis charge types).
A review of that work found and fixed three further defects, each now covered by
a test:

- `kinesis_storage_shard_hour` was appended after `kinesis_shard_hours`, whose
  `any_of {unit_hourly: true}` claims every hourly Kinesis row — the new entry
  was unreachable and its declared `block_storage` mechanic never applied. Moved
  ahead of the general case; `test_no_charge_type_is_unreachable` now catches
  this class.
- The `ebs`→`ec2` fold moved three PDF rows ($184) into a spec whose
  `ebs_storage` fallback could not match them. Fallback widened (work order 2.7).
- `test_no_predicate_restates_service_identity` did not scan `any_of` blocks, so
  two `product_re` predicates sat unflagged. The lint now walks every block.

**ITEMs 4 and 5 (ingest defects) remain open.**

---

## 2. The playbook — how to fix any service, not just these

### 2.1 Symptom → layer → where the fix belongs

| What `spec_parity.py` shows | Actual cause | Fix HERE | Never here |
|---|---|---|---|
| canonical is `?` | Layer 1 cannot name the service | `data/aws-aliases.json`, or `data/aws-product-codes.json` if AWS publishes a code | a `product_re` inside some spec |
| one service under two canonicals (`ebs` vs `ec2`) | Layer 1 splits or merges wrongly | `data/aws-aliases.json` | a charge_type re-testing the product name |
| `no_spec`, service is real | no spec written yet | a new entry in `data/service_specs.json` | the legacy `RULES` chain |
| `no_spec`, rows are commitments/credits/tax | already caught by the global pre-filter | nothing — fix the harness's bucketing | a spec |
| `spec_gap`, usage type looks sane | a charge type is genuinely missing | a new charge_type in that service | widening an existing one until it swallows the row |
| `spec_gap`, usage type malformed (`...-Usage:NA`, `GLOBAL-Usage:671.49`, empty) | extraction defect | `scripts/ingest.py` | a regex matching malformed text |
| ambiguity: specific + its general case | parent/child written flat | nest them (§2.3) | reordering the list and hoping |
| ambiguity: two specific charge types | a real overlap | an `exclude_*` predicate on the loser | list order |
| `differ`, spec looks right | legacy bug the spec already corrects | a `notes` line recording the decision | nothing |
| `differ`, legacy looks right | spec bug | tighten or add the spec predicate | the legacy chain |
| routed to `misc`/`per_request`/`flat_hourly` when the service deserves its own pricer | accuracy gap, not a classification gap | a separate work order | this one |

**The rule behind the table:** identity is Layer 1, field extraction is ingest,
routing is the spec, pricing is the mapper. A fix that crosses a layer boundary
is the whack-a-mole pattern returning — it works for one bill and re-breaks on
the next format.

### 2.2 Per-service procedure

1. **Measure first.** Record `agree` %, `spec_gap` $, `no_spec` $ and ambiguity
   *before* touching anything. Without a before you cannot prove an after.
2. **Pick one service**, highest spend first.
3. **Classify each of its problems** against §2.1 and write down which layer
   each belongs to, before editing.
4. **Fix bottom-up**: identity → extraction → spec. A spec fix built on a broken
   canonical is undone the moment the canonical is corrected.
5. **Re-measure** (§7).
6. **Pin every judgement call** — a `notes` line in the charge_type *and* a case
   in `tests/test_spec_partition.py`. A decision that exists only in a diff gets
   reversed by the next person.
7. **Explain every moved row**: rows changed, spend, one sentence on why the new
   answer is right.

### 2.3 When to nest, and when NOT to

Nest when a service has **a general case plus specific variants of it** — the
shape that produced every ambiguity measured on this corpus.

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
  `exclude_*` predicate to the one that should lose.
- The last child is a default, so a row entering a group always leaves it named.

**Do NOT nest** a service that is a flat list of unrelated charge types
(storage, requests, transfer) with no overlap between them. Nesting there adds
structure with no invariant to enforce and makes the file harder to read. S3 and
RDS are both in this category today — see ITEM 8.

### 2.4 Worked example — EC2, the reference conversion

Before: five flat charge types, four overlapping.

```
cpu_credits
instance_hour_windows      <- matched BoxUsage rows
instance_hour_burstable    <- matched BoxUsage rows
instance_hour_graviton     <- matched BoxUsage rows
instance_hour              <- matched ALL BoxUsage rows
```

`instance_hour` is not a default — it has a real `match` — but it is the general
case for the three above it. **335 rows ($53k) matched two or three at once**,
resolved only by list position.

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
   row that previously classified still enters the group.
2. **Child order mirrors the old flat order**, so outcomes are unchanged by
   construction.
3. **Two overlaps had to be declared**, because the data showed siblings still
   colliding after nesting:
   - **t4g is both burstable and Graviton** (60 rows). Burstable wins — AWS CPU
     credits never lower the AWS price, so E2 is correct and the ARM mapper
     would overstate GCP cost. Pinned by
     `test_t4g_routes_to_burstable_not_graviton`.
   - **A Windows t3 row** (3 rows) carried "Windows" in `product` only, so the
     usage_type/operation exclusions missed it. Fixed with
     `exclude_any_field_re`; pinned by `test_windows_burstable_routes_to_windows`.

Result: ambiguity 340 rows / $54k → **0**, `agree` unchanged.

### 2.5 Worked example — two one-line fixes, and a trap

- **WAF**: `waf_fixed_hourly` and `waf_monthly` both matched "Web ACL … per
  Month" (3 rows). Both route to `flat_hourly`, so the outcome never differed —
  but which won was positional. Fixed with an `exclude_when` on the hourly
  vocabulary. Clarity fix, zero behaviour change.
- **RDS**: `backup` (`backup|snapshot`) and `storage` (`gb-mo|storage`) both
  matched a GB-Mo backup row. Fixed with `exclude_any_field_re` on `storage`.

**The trap.** The first attempt put that exclusion in `exclude_when`.
`exclude_when` **ANDs** its predicates, so a second condition *narrows* the
exclusion instead of widening it — it silently weakened the existing
`unit_hourly` exclusion. A negative predicate belongs in `match`
(`exclude_any_field_re`), never in `exclude_when`.

### 2.6 Worked example — a missing charge type, and why it was invisible

RDS `APS5-Multi-AZUsage:db.m7g.large` matched **nothing** — $5,567 unclassified
for a core RDS charge. The fix was one token added to
`rds.instance_hour.fallback_match`.

The lesson is detection, not the fix: this was invisible because `dispatch()`
returns `None` both for "service has no spec" and "spec matched nothing", and
both fell back to legacy silently. Separating those buckets in the harness is
what surfaced it. **For any service you touch, read its `spec_gap` rows.**

### 2.7 Worked example — PDF rows need an operation fallback

VPC public-IPv4 hours (48 rows, $1,473) and VPN connection-hours (4 rows, $198)
were reaching `vpc_other → misc`, i.e. being handed to the LLM, while legacy
classified them `flat_hourly`. Legacy was right: both are deterministic
per-hour charges.

Root cause: the charge types keyed on `usage_type_re`, but PDF/XLSX rows carry
**no usage_type at all** — the charge is described in `operation`:

```
usage_type = ''   operation = '$0.005 per Idle public IPv4 address'
usage_type = ''   operation = '$0.05 per VPN Connection-Hour (720 Hrs)'
```

Fix shape, which applies to **every** charge type that must work on PDF bills:

```jsonc
"match":          { "usage_type_re": "PublicIPv4|ElasticIP|IPAddress" },
"fallback_match": { "operation_re": "public IPv4 address|Idle public IPv4|In-use public IPv4" }
```

`match` serves CUR; `fallback_match` serves PDF/XLSX. If a charge type has only
`usage_type_re`, assume it is broken on PDF bills until proven otherwise.

---

## 3. How to edit `data/service_specs.json` safely

**This section is mandatory. Read it before your first edit.**

The file is ~3,000 lines of JSON. Hand-editing it in a text editor is how it
gets corrupted or silently reordered.

### 3.1 Always edit with a script, never by hand

```python
# scratch/edit_spec.py — run with:  python -I scratch/edit_spec.py
import collections, json

P = r'C:\Users\arnav\Downloads\cur-web-AGY\skill\aws-gcp-cost-projection\data\service_specs.json'

# object_pairs_hook=OrderedDict preserves key order; without it the whole file
# is reordered and the diff becomes unreviewable.
d = json.load(open(P, encoding='utf-8'), object_pairs_hook=collections.OrderedDict)

# ... your edit here ...

json.dump(d, open(P, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
```

`ensure_ascii=False` keeps existing non-ASCII characters intact. `indent=2`
matches the file's existing style.

### 3.2 Back up first, verify semantically after

```bash
cp data/service_specs.json "$TEMP/specs.before.json"
# ... run your edit script ...
```

```python
# verify_spec_edit.py — prove you changed only what you intended
import json, os
a = json.load(open(os.environ['TEMP'] + '/specs.before.json', encoding='utf-8'))
b = json.load(open('data/service_specs.json', encoding='utf-8'))

assert set(a) == set(b), "top-level services added or removed!"
changed = [k for k in a if json.dumps(a[k], sort_keys=True) != json.dumps(b[k], sort_keys=True)]
print("services changed:", changed)

for k in changed:
    def ids(cts, out=None):
        out = [] if out is None else out
        for c in cts:
            out.append(c['id']); ids(c.get('children', []), out)
        return out
    ia, ib = set(ids(a[k]['charge_types'])), set(ids(b[k]['charge_types']))
    print(f"  {k}: removed={sorted(ia - ib)} added={sorted(ib - ia)}")
```

**`removed` must be empty** unless you deliberately deleted a charge type and
said so. `changed` must list only the services you meant to touch.

### 3.3 A complete charge_type, annotated

Copy this shape. Every key below is optional except `id` and `mechanic`.

```jsonc
{
  "id": "vpc_public_ipv4",                   // unique within the service
  "description": "Public IPv4 address-hours (in-use and idle).",

  // match: ALL predicates must hold (logical AND). Absent = always true.
  "match": { "usage_type_re": "PublicIPv4|ElasticIP|IPAddress" },

  // any_of: at least one block must hold. Combined with match by AND.
  // "any_of": [ { "unit_in": ["gb-mo"] }, { "usage_type_re": "TimedStorage" } ],

  // fallback_match: tried only if match/any_of fail. This is where PDF/XLSX
  // support lives — those rows have no usage_type (see 2.7).
  "fallback_match": { "operation_re": "public IPv4 address|Idle public IPv4" },

  // exclude_when: if ALL of these hold, the charge type is skipped entirely.
  // Remember it ANDs — a second key NARROWS the exclusion (2.5 trap).
  // "exclude_when": { "unit_hourly": true },

  "mechanic": "flat_hourly",                 // the mechanic_group this produces
  "fields": [],                              // enrichment fields; [] is fine
  "source_rule": "flat_hourly (ElasticIP|IPAddress|PublicIPv4 branch)",
  "notes": "Why this exists and what was decided. Required for any judgement call."

  // "children": [ ... ]                     // only for GROUPS, see 2.3
}
```

### 3.4 The predicate vocabulary — these and no others

From `_eval_block()` in `scripts/spec_dispatch.py`. **An unknown key fails
closed**, so a typo makes the charge type silently never match.

| predicate | matches against |
|---|---|
| `usage_type_re`, `exclude_usage_type_re` | `usage_type` |
| `operation_re`, `exclude_operation_re` | `operation` |
| `product_re`, `product_re_not` | `product` — **banned inside a service's own spec**, a test enforces this |
| `any_field_re`, `exclude_any_field_re` | `usage_type + operation + product` joined |
| `unit_in`, `unit_not_in`, `unit_re` | `pricing_unit` (lowercased for the list forms) |
| `unit_hourly` | true/false — the shared hourly-unit test, also treats `''` as hourly for PDF bills |
| `pricing_model_in`, `line_item_type_in` | exact list membership |
| `cost_lt_zero`, `is_accelerator` | true/false |

**Field authority — prefer predicates in this order:**

```
unit_in / unit_hourly     strongest: structured, format-independent
usage_type_re             strong:    AWS's own charge vocabulary
operation_re              medium:    reliable on PDF, often absent on CUR
any_field_re              weakest:   needs a comment justifying it
product_re                BANNED inside a service's own spec
```

### 3.5 Rollback

Nothing here is committed until you commit it. To undo everything:

```bash
git checkout -- data/service_specs.json
```

To undo one edit, re-run your script from `$TEMP/specs.before.json`. If the
baseline in §1 no longer reproduces and you cannot see why, roll back fully and
report rather than continuing to patch.

---

## 4. Files to read before you start

- `scripts/spec_parity.py` — read `main()` to see exactly what each bucket means
- `scripts/spec_dispatch.py` — `dispatch()`, `_eval_block()`,
  `is_default_charge_type()`
- `tests/test_spec_partition.py` — the invariants you must not break
- `data/service_specs.json` — the registry
- `docs/ANALYSIS_spec_dispatch_validation.md` — the measurements behind §1

---

## 5. Work items, in measured order

Each item names the layer it belongs to. **If your fix is in a different layer
than the item says, you have misdiagnosed it — re-read §2.1.**

### COMPLETED — do not redo

| | what it was | outcome |
|---|---|---|
| ITEM 1 | `savingsplans` / `discounts` mis-bucketed | harness now reports them as `excluded` (31 rows, $9,057); specs untouched |
| ITEM 2 | `ebs` / `aurora` as separate canonicals | folded in `data/aws-aliases.json`; neither has an AWS product code |
| ITEM 3 | 26 rows with no `canonical_service` | 27 aliases added; NULL-canonical rows now zero |
| ITEM 7 | kinesis `BilledBytes` / `Storage-ShardHour` | two charge types added, matching the legacy mechanics |

A review of that work found three further defects, all fixed and each now
covered by a test: an unreachable kinesis charge type, an `ebs_storage`
fallback that could not match PDF rows, and an identity lint that did not scan
`any_of` blocks. See §1.

---

### ITEM 4 — `AP-SOUTHEAST-1-Usage:NA` usage types (ingest)

**Layer: `scripts/ingest.py`. Not a spec. $2,932 across three services.**

| service | spec_gap | sample usage_type |
|---|---:|---|
| `rds` | $2,519 | `AP-SOUTHEAST-1-Usage:NA` |
| `elb` | $215 | `AP-SOUTHEAST-1-Usage:NA` |
| `msk` | $198 | `AP-SOUTHEAST-1-Usage:NA` |

The **full region name** sits where the short code (`APS1`) belongs, and `NA`
where the charge type belongs. A PDF/XLSX extraction artifact.

**Find the source rows:**

```bash
cd skill/aws-gcp-cost-projection && python -c "
import duckdb, glob, os
ROOT = r'<CORPUS>'
for db in glob.glob(os.path.join(ROOT, '*', 'projection-audit', 'projection.duckdb')):
    jd = os.path.dirname(os.path.dirname(db))
    try:
        name = open(os.path.join(jd, 'name.txt')).read().strip()
        c = duckdb.connect(db, read_only=True)
        rs = c.execute(\"SELECT product, usage_type, operation, pricing_unit, aws_amortized_cost FROM aws_li_catalog WHERE usage_type LIKE '%-Usage:NA'\").fetchall()
        c.close()
        for r in rs: print(name, '|', r)
    except Exception: pass
"
```

Read the matching source bill. Two outcomes:

- **The bill carries a charge type** and ingest put the wrong text in
  `usage_type` → fix the extraction so the short-code form is produced. One fix
  covers all three services and every service that would hit this later.
- **The bill genuinely has no charge type** (`NA` is faithful) → the row cannot
  be classified. Leave it; `classify_mechanics.py` already reports this class
  via `data_gaps.json`. Record it in your summary.

**Do not** add a regex to three spec files to absorb malformed text.

---

### ITEM 5 — numeric and empty usage types (ingest, investigate only)

**Layer: ingest. Investigate and report; do not fix blind. $720.**

`ec2` ($640) and `dynamodb` ($80) have `spec_gap` rows whose usage type is
empty or a bare number — `GLOBAL-Usage:671.49`, `GLOBAL-Usage:802.39`. A number
where a charge type belongs is a parse error; `671.49` looks like a cost that
landed in the wrong column.

Use ITEM 4's query with `usage_type LIKE 'GLOBAL-Usage:%'`, open the source
bill, and report what you find. **Do not add a charge type that matches numeric
usage types** — that bakes the parse error into the taxonomy.

---

### ITEM 6 — the 5 `differ` cases: ALREADY DECIDED, do not re-litigate

All five spec-vs-legacy disagreements were reviewed against real rows; the
decisions are recorded in each charge type's `notes`.

| canonical | legacy → spec | rows | spend | decision |
|---|---|---:|---:|---|
| `rds` | `misc` → `managed_db` | 16 | $3,050 | **Spec right** — `Multi-AZUsage:db.m7g.xl` + `CreateDBInstance` is an instance-hour |
| `ec2` | `compute_breakdown` → `compute_burstable` | 7 | $1,545 | **Spec right** — PDF rows reading `On Demand Linux t3a.large Instance` |
| `opensearch` | `opensearch` → `block_storage` | 2 | $182 | **Spec right** — `GB-month provisioned gp3 storage` |
| `vpc` | `flat_hourly` → `misc` | — | — | **Legacy was right; FIXED** |
| `kms` | `per_request` → `misc` | — | — | **Legacy was right; FIXED** |

**Your only job here is to verify `differ` stays at 25 rows / $4,778.** If it
grows, your change broke something. If it shrinks without you intending it, say
so — that also needs explaining.

---

### ITEM 8 — nine undeclared services (newly visible, $684 total)

Resolving identity in ITEM 3 turned "unknown canonical" into "named service
with no spec". Each is one or two rows:

```
detective $146   qldb $97   keyspaces $72   batch $54   neptune $48
wavelength $46   timestream $42   localzones $41   elasticbeanstalk $37
```

**Recommendation: do nothing yet, and say so.** $684 across nine services is
below the value of nine hand-written specs, and `no_spec` falls through to the
legacy classifier, which handles them. Revisit when one appears materially on a
real bill.

If you disagree, argue it from the numbers rather than from tidiness.

---

### ITEM 9 — do NOT nest `s3` and `rds`

An earlier draft asked for this. **It was wrong and has been removed.** Both are
flat lists of unrelated charge types with **zero measured ambiguity**. §2.3 says
not to nest that shape: no general/specific relationship means no invariant for
the partition test to enforce.

Nest only when ambiguity appears, or when adding a specific variant of an
existing general charge type.

---

## 6. What NOT to do

- Do **not** add `product_re` inside a service's own spec. A test fails, and it
  re-introduces the free-text coupling Layer 1 exists to remove.
- Do **not** fix an identity problem (a wrong or missing `canonical_service`) inside a charge_type. Identity is `data/aws-aliases.json`.
- Do **not** fix an extraction problem (ITEMs 4, 5) inside a charge_type.
- Do **not** place a default (`"match": {}`) anywhere but last in its list.
- Do **not** put a negative predicate in `exclude_when` (§2.5 trap).
- Do **not** hand-edit `data/service_specs.json` (§3.1).
- Do **not** change any mapper, SKU, rate, report file, or `classify_mechanics`.
- Do **not** cut the pipeline over to `spec_dispatch`. That is a separate
  decision.
- Do **not** "fix" anything else you notice. List it at the end of your summary.

---

## 7. Verification — run all of these and paste the output

```bash
cd skill/aws-gcp-cost-projection
python -m pytest tests/ -q                      # 995+ pass, no new failures
python -m pytest tests/test_spec_partition.py -v
python scripts/spec_parity.py "<CORPUS>"
```

**The five numbers that matter:**

| metric | baseline | after your change |
|---|---:|---|
| `agree` % spend | 96.6% | must not fall |
| `differ` rows | 25 | must not grow |
| `spec_gap` **per service** | rds $2,519 · ec2 $640 · elb $215 · msk $198 · dynamodb $80 · lambda $27 | **no service may rise** |
| `no_spec` spend | $684 | **must not rise** — ITEM 8 deliberately leaves these nine services undeclared |
| genuine ambiguity | NONE | must stay NONE |

**`spec_gap` is checked per service, not as a total.** An aggregate check hides
the case where one fix regresses another service: confirmed real — folding the
`ebs` canonical into `ec2` added $184 of EC2 gap while kinesis dropped $142 out,
a net +$44 that read as noise. If any service's gap rises, say so and explain it,
even when the total falls.

**Ambiguity check — must print `NONE`:**

```bash
cd skill/aws-gcp-cost-projection && python - <<'PY'
import duckdb, glob, os, sys
sys.path.insert(0, 'scripts')
import spec_dispatch as sd
ROOT = r'<CORPUS>'
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

Plus the semantic spec diff from §3.2.

**Every row that moves must be explained.** For each item: how many rows changed
`mechanic_group`, their total spend, and one sentence on why the new answer is
correct. An unexplained move is a regression, not a fix.

---

## 8. Done criteria

- [ ] ITEM 4: `-Usage:NA` either fixed at ingest or reported as a faithful data
      gap. `rds`/`elb`/`msk` spec_gap falls.
- [ ] ITEM 5: numeric/empty usage types traced to source and reported. **No
      charge type added for them.**
- [ ] ITEM 6: `differ` still 25 rows / $4,778.
- [ ] ITEM 8: the nine undeclared services left alone, with that stated as a
      decision rather than an omission.
- [ ] ITEM 9: `s3`/`rds` left flat.
- [ ] `agree` ≥ 96.6%, `differ` ≤ 25 rows, **no service's `spec_gap` risen**,
      ambiguity NONE.
- [ ] Semantic spec diff shows only the services you intended, `removed` empty.
- [ ] Full suite passes with no new failures.
- [ ] Summary lists every moved row with its reason, plus anything you noticed
      but did not fix.

---

## 9. Scope note

**ITEMs 4 and 5 are ingest work, not spec work.** Neither should change
`data/service_specs.json`. If you find yourself editing a spec file to make a
malformed usage type match, stop — that is the symptom being treated in the
wrong layer (§2.1).

Both are also small: $3,652 combined, on a corpus where `agree` is already
96.6%. The value is in fixing the **extraction defect**, which affects every
future bill of those formats, not in recovering these particular dollars.

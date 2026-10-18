# WORK ORDER — OS/DB license cost: complete modeling + show instance-and-license together

**For an implementing AI / engineer with NO prior context. Read fully. Each item is
self-contained with a concrete test. IMPORTANT: a lot of this is ALREADY built — this work order
is mostly about COMPLETING coverage, RECONCILING a stale flag, and the REPORT display. Do not
rebuild what exists; verify first.**

## Background — how licenses actually bill (read before coding)
- **AWS:** the OS/DB license premium is **bundled into the instance's hourly rate** (a Windows
  `m5.xlarge` costs more than a Linux `m5.xlarge`; the difference IS the license). There is
  usually **no separate CUR line** for the OS license — so AWS `aws_amortized_cost` for a compute
  row is already **license-inclusive**. (SQL Server/Oracle can be license-included or BYOL.)
- **GCP:** premium OS/DB is billed as **separate SKUs** — e.g. catalog has "Licensing Fee for
  Windows Server 2025 Datacenter Edition on VM", Ubuntu Pro, SUSE; Cloud SQL SKUs carry SQL
  Server editions. So the GCP equivalent = **base compute (license-exclusive) + a license
  component**.
- **Therefore** a fair compare needs: GCP = compute + license (so it matches AWS's
  license-inclusive number). The AWS side is already inclusive; we only need to **label** the OS,
  not split AWS cost.

## What already EXISTS (verify, don't rebuild)
- `scripts/apply_static_mappings.py`:
  - `_WIN_LICENSE_SKU` + `_WIN_BYOL_NOTE` (Windows Server license, flat per-VM-hr).
  - SQL Server license SKUs (per-VM-hour, by edition/vCPU tier) → `sql_license` component.
  - `_RHEL_VCPU_BANDS` + `_license_entries(r, aws_key, gcp_region, vcpus, ram)` (~line 7851),
    wired into the mapping at ~line 8065 → emits a `license` component for RHEL.
  - So Windows / SQL Server / RHEL already get a GCP license component ON TOP of compute.
- `scripts/render_report.py`: already parses per-component OS-license notes (`os_licenses`,
  ~line 205) and renders "OS license" / "Windows Server … license" rows (~446-461); has an
  "AWS Instance" column label helper (~108).
- GCP catalog (`data/catalog.duckdb`) has the license SKUs (Windows, Ubuntu Pro, SUSE, SQL
  Server editions).

---

## L-1 — Reconcile `flag_license_exposure` with what is now modeled (🔴 stale/contradictory)
**Lives in:** `scripts/apply_rates.py`, `flag_license_exposure()`.
**Problem:** it stamps `[license-premium-not-modeled]` and caps confidence to 0.5 on Windows / SQL
Server / Oracle rows — but Windows, SQL Server, and RHEL license components ARE now modeled by
`_license_entries`/`_WIN_LICENSE_SKU`. So the flag now LIES for those rows (says "not modeled" and
needlessly caps confidence) while the GCP cost already includes the license.
**Fix:** only flag/cap rows whose license is genuinely NOT modeled on the GCP side — i.e. check
whether the row's `aws_li_key` actually received a `license`/`sql_license` component. If it did →
do NOT stamp "not modeled" and do NOT cap confidence (optionally stamp a positive note "OS license
modeled: <SKU>"). If it did NOT (e.g. Oracle, or an OS with no GCP license SKU, or BYOL) → keep
the flag + cap, with a specific reason.
**Test:** a Windows compute row that got a `license` component is NOT flagged "not modeled" and
keeps full confidence; an Oracle row with no license component IS still flagged + capped.

## L-2 — Complete GCP license coverage: Ubuntu Pro, SUSE, Oracle (BYOL) (🟡 gaps)
**Lives in:** `scripts/apply_static_mappings.py`, `_license_entries()` (+ the OS detection it uses).
**Problem:** `_license_entries` covers RHEL (and Windows/SQL handled nearby), but the catalog also
has **Ubuntu Pro** and **SUSE** license SKUs that are not emitted, and **Oracle DB** licensing has
no clean GCP equivalent.
**Fix:**
  - **Ubuntu Pro / SUSE:** detect from `operating_system` / `operation` / `usage_type`; emit a
    `license` component resolved to the matching catalog "Licensing Fee for …" SKU (by vCPU band
    where the SKU is vCPU-tiered, same pattern as `_RHEL_VCPU_BANDS`). Verify the SKU names live
    against `data/catalog.duckdb` before pinning (project rule: sweep the catalog first).
  - **Oracle DB:** no GCP managed-license equivalent → do NOT fabricate a price. Emit a
    passthrough/label note ("Oracle license — BYOL; not modeled, add separately") and let L-1 keep
    it flagged. (Oracle on AWS is often BYOL anyway.)
**Test:** an Ubuntu Pro row and a SUSE row each get a GCP `license` component resolved to a real
catalog SKU; an Oracle row gets the labeled passthrough note, no fabricated license rate.

## L-3 — Uniform "instance + license" display across ALL export formats (🟡 display — user spec)
**Lives in:** the report data layer + `scripts/render_report.py` + `scripts/export_report.py`
(HTML, and every other format: CSV/Excel/PDF/JSON — whatever export_report emits).
**User spec (exact):** in the instance column show **instance name + its license(s)**; in the price
column show **instance price `+` license cost as two separate numbers** (e.g. `$120.00 + $8.40`),
shown with a plus — **do NOT show a merged total.** Show the license **just below its instance
row** (a `+` sub-row / sub-item). Basis stays **OD-vs-OD** (AWS OD, GCP OD).

**How to keep it UNIFORM across formats (the key design — do NOT format strings per-exporter):**
Drive every format from ONE structured representation per priced line:
```
{ base_cost, license_name, license_cost }   # separate numbers, NEVER pre-summed
```
Each exporter renders that SAME structure in its own idiom:
  - **HTML / PDF:** the license is an indented **sub-row with a `+`** directly under the instance
    row (`+ Windows Server 2019 license … $8.40`); the instance's price cell shows
    `base + license` as two numbers, no total.
  - **CSV / Excel:** the SAME structure as dedicated columns — `instance_cost | license_name |
    license_cost` — two separate numeric cells, no total column. (Sub-rows don't belong in a
    spreadsheet; separate columns preserve the "two separate numbers, no total" rule and keep the
    cells numeric/sortable.)
  - **JSON / any machine format:** emit the structured fields directly.
The RULE (two separate numbers, license shown under/with its instance, never a merged total) lives
in the shared data model, so it can't drift between formats.

**GCP side:** populate `base_cost` = sum of compute components (core+ram+gpu), `license_cost` =
the `license`/`sql_license` component(s), `license_name` from the component label. We have both
exactly.
**AWS side:** see L-4 (the AWS split needs data we don't currently have).
**Test:** the same Windows instance renders, in HTML/PDF as an instance row + a `+ license`
sub-row showing `base + license` (no total), and in CSV/Excel as `instance_cost, license_name,
license_cost` columns — same numbers, no total, in both.

## L-4 — AWS "instance + license" split: two-phase (Phase 1 done; Phase 2 GREENLIT — build it)
**Lives in:** `scripts/render_report.py` / `export_report.py`; Phase 2 also `scripts/refresh_ec2_specs.py` + `data/ec2-instance-types.json`.
**Constraint (verified 2026-10-15):** AWS's OD rate is **license-inclusive** (a Windows instance's
OD price already contains the license), and we do NOT currently store AWS per-OS rates —
`data/ec2-instance-types.json` holds specs only (`vcpus, ram_gb, arch, chip`), no pricing. So we
CANNOT split AWS compute vs AWS license today without new data. Do NOT fabricate the split.

**Phase 1 (ship now — no new data):** AWS shows its OD number as ONE figure with
`license_name` set and `license_cost = null`, rendered/labeled **"(incl. OS license)"**. Only the
GCP side shows the explicit `base + license`. Still a fair OD-vs-OD compare (both TOTALS include
the license); the display is just not symmetric yet.

**Phase 2 (GREENLIT 2026-10-15 — build it): AWS `instance + license` split via a region-robust
license ratio.**
The naive "Windows OD − Linux OD **absolute** delta" needs per-region price files (480 MB each,
many regions) AND would mix regions if we only had us-east-1. Avoid that: use a **ratio**, which
is far more region-stable than the absolute delta.

Step 1 — data (`scripts/refresh_ec2_specs.py` + `data/ec2-instance-types.json`):
  - The script already STREAMS the full AWS Price List EC2 offer file (`consume(fh)` over
    `products`) and reads product `attributes`. Extend it to ALSO read the **`terms.OnDemand`**
    section of the SAME file (price per unit, USD/hr) and pair each price with its product via
    the product `sku`.
  - For each instance type, capture the On-Demand $/hr for the base **Linux** variant and for each
    licensed variant, selecting the canonical product: `tenancy=Shared`, `preInstalledSw=NA`,
    `capacitystatus=Used`, and `operatingSystem` ∈ {Linux, Windows, RHEL, SUSE}. (Linux is the
    base; the rest are the licensed uplifts.)
  - Store a per-type **license fraction** per OS:
    `license_fraction[type][os] = (os_OD − linux_OD) / os_OD`  (computed at the script's region,
    us-east-1 — a ratio, so region-robust). Persist it in `ec2-instance-types.json` alongside the
    specs (e.g. `"m5.xlarge": {..., "license_fraction": {"windows": 0.37, "rhel": 0.10, ...}}`).
    Keep the existing specs untouched; this is additive.
Step 2 — report (`render_report.py` + `export_report.py`):
  - For an AWS licensed row, set `license_cost = row_AWS_OD_cost × license_fraction[type][os]` and
    `base_cost = row_AWS_OD_cost − license_cost`. This applies the ratio to the row's ACTUAL
    regional AWS OD cost, so it stays region-correct AND `base + license == original AWS OD` by
    construction (no total drift).
  - Populate the SAME structured `{base_cost, license_name, license_cost}` the GCP side uses, so
    AWS now renders the `+ license` split identically (HTML sub-row / CSV columns), no total.
  - Label it a derived assumption: "AWS license split estimated from AWS Price List Windows/Linux
    OD ratio."
  - **Fallback:** if no `license_fraction` exists for that type/OS (new type, or OS not in the
    ratio table), fall back to Phase-1 behavior (one number + "(incl. OS license)") — never
    fabricate.
**Test (Phase 1):** a Windows AWS row shows one OD number labeled "(incl. OS license)" with the OS
named; GCP shows `base + license`. **Test (Phase 2):** a Windows AWS row of a type that has a
`license_fraction` shows `base + license` as two numbers with `base + license == the row's AWS OD
cost` (to the cent); a type with NO fraction falls back to the Phase-1 label.

## L-5 — Wire the AWS license split into `export_report.py` too (✅ DONE 2026-10-15)
**Done:** created shared `scripts/license_split.py` (single source of `get_aws_license_split` +
`load_ec2_specs`); `render_report.py` now imports it (duplicate removed); `export_report.py`
adds `AWS Instance Cost` + `AWS License Cost` columns to the per-item and per-instance sheets via
`_augment_aws_license_split()`, with the Phase-1 fallback. Suite green at 925.
**Lives in:** `scripts/export_report.py`.
**Status (verified 2026-10-15):** Phase 2 landed in `render_report.py` (HTML/PDF) but NOT in
`export_report.py`. In exports, `instance_cost / license_name / license_cost` (lines ~100-117) are
derived from **GCP components only**; the AWS side is still a single "AWS Cost" column with no
license split and no "(incl. OS license)" label. This BREAKS the L-3 uniformity rule for AWS on
CSV/Excel — HTML shows AWS `base + license`, exports don't.
**Fix:** apply the SAME `license_fraction` logic `render_report.py:137-170` uses (reuse that helper
if practical, or replicate it) to the AWS side in `export_report.py`, and emit AWS as its own
structured columns: `aws_instance_cost | aws_license_name | aws_license_cost` (two separate
numbers, no total), mirroring the GCP `instance_cost/license_name/license_cost` columns. Where no
`license_fraction` exists for the type/OS, fall back to the Phase-1 behavior (full AWS cost in
`aws_instance_cost`, blank license, and the OS noted) — never fabricate. Keep `base + license ==
the row's AWS OD cost` to the cent.
**Test:** a Windows AWS row exported to CSV/Excel shows `aws_instance_cost` + `aws_license_cost` as
two numbers summing to the AWS OD cost (for a type with a fraction), and the full AWS cost with
blank license for a type without one — matching what the HTML report shows for the same row.

---

## Report back
Files changed, tests added, the catalog SKU names used for Ubuntu Pro / SUSE (with evidence they
exist in catalog.duckdb), any OS/DB left as labeled passthrough (Oracle), and — if Phase 2 is
done — the AWS Price List source + how the Linux base rate is stored. Keep the suites green; the
only intended cost change is GCP totals rising where a previously-unmodeled license
(Ubuntu Pro/SUSE) is now added — call that out.

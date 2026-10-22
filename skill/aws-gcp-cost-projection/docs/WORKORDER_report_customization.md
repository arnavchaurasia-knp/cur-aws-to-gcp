# WORK ORDER — Add one test: report-settings drift

**There is exactly ONE task in this file. Add one test. Change nothing else.**

The report-settings feature is built and working. Do not re-implement any of
it. Do not edit the renderer, the settings file, the API, the UI, or the
database. **Add a single test function and stop.**

---

## The task

Add one test to
`skill/aws-gcp-cost-projection/tests/test_report_settings.py`:

```python
def test_report_settings_keys_match_renderer():
    """Every settable key is read by the renderer, and vice versa."""
```

It must do three things:

**1. Collect the settable keys** — the keys of the `values` object in
`skill/aws-gcp-cost-projection/data/report-settings-defaults.json`.

**2. Collect the keys the renderer reads** — every key passed to `rs_bool(...)`
or `rs_num(...)` in `skill/aws-gcp-cost-projection/scripts/render_report.py`.
Read that file as text and regex it. This pattern works:

```python
re.findall(r'rs_(?:bool|num)\(\s*["\']([a-z0-9_]+)["\']', source)
```

(There are 8 such calls in the file today.)

**3. Assert the two sets are EQUAL** — not a subset, equal — and name the
offending keys on **both** sides in the failure message:

```
AssertionError: report settings drift:
  in defaults but never read by render_report.py: col_region
  read by render_report.py but not settable:      section_methodology
```

---

## Why both directions matter

- A key in the defaults file that the renderer never reads = **a tick box
  wired to nothing.** An admin toggles it, regenerates the report, nothing
  changes, no error appears anywhere.
- A key the renderer reads that is not in the defaults file = **a setting no
  admin can change.** It silently falls back to its default forever and looks
  like it works.

Both are invisible without this test. Earlier in this codebase a mapper read
`r.get("billing_month")` for a column its query never fetched, so a shipped
feature silently did nothing for months and its own unit tests passed. This
test is the guard against that happening to the report settings.

---

## How to verify it

1. Run the suite and record the count. It is **1,064** now; it must become
   **1,065**.
   ```bash
   cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
   ```

2. **Make it fail on purpose, once.** Add a fake key like
   `"col_does_not_exist": "true"` to the `values` object in
   `data/report-settings-defaults.json`, run the test, and confirm it **fails
   and names `col_does_not_exist`**. Then remove the fake key.

   Do not skip this. A test of this shape that passes on the first run is
   usually matching nothing — an empty set equals an empty set.

3. Report in your summary whether the test passed against the real code
   unchanged, and if it failed, **which keys drifted**.

---

## Do NOT

- Do **not** hardcode the current key names into the test. It must derive both
  sets from the files, or it will not catch the next drift.
- Do **not** edit `render_report.py`, `report-settings-defaults.json`, or any
  other file to make the test pass. If the test fails on the current code, that
  is a genuine bug you have found — **report it, do not hide it**.
- Do **not** add any other test, refactor, or tidy-up. One function.

---

## Done

- [ ] `test_report_settings_keys_match_renderer` exists and derives both sets
      from the files.
- [ ] It was observed failing against a deliberately added bad key, naming it.
- [ ] Suite is at 1,065 passing.
- [ ] Summary states whether it passed on the unmodified code, and names any
      drift found.

import re
from pathlib import Path
import pytest


def test_ensure_full_coverage_invoked_once():
    # Locate orchestrate.go via pathlib from repo root
    current = Path(__file__).resolve()
    repo_root = current.parents[3]
    orchestrate_path = repo_root / "internal" / "jobs" / "orchestrate.go"
    assert orchestrate_path.exists(), f"Could not find {orchestrate_path}"

    text = orchestrate_path.read_text(encoding="utf-8")
    count = text.count('"?scripts/ensure_full_coverage.py $DB"')
    assert count == 1, f"Expected '?scripts/ensure_full_coverage.py $DB' to appear exactly once, found {count}"


def test_template_has_no_unescaped_format_verbs():
    current = Path(__file__).resolve()
    repo_root = current.parents[3]
    orchestrate_path = repo_root / "internal" / "jobs" / "orchestrate.go"
    text = orchestrate_path.read_text(encoding="utf-8")

    # Extract orchestratorTemplate body between backticks
    m = re.search(r'const orchestratorTemplate = `(.*?)`\s*// duckdbBin', text, re.DOTALL)
    if not m:
        m = re.search(r'const orchestratorTemplate = `(.*?)`', text, re.DOTALL)
    assert m, "Could not extract orchestratorTemplate"
    template_body = m.group(1)

    # % followed by d, s, or ( unless preceded by % (i.e. not doubled)
    # Match any single % that is followed by [ds(] and not preceded by %
    # and not followed by %
    # In orchestratorTemplate, %s is used once for the b64 json specs in base64.b64decode("%s")
    # All other % in python code should be %%
    matches = re.findall(r'(?<!%)(%[ds(])', template_body)
    # The only valid unescaped verb is the single '%s' for the template parameter
    assert matches == ["%s"], f"Found unexpected unescaped format verbs in orchestratorTemplate: {matches}"

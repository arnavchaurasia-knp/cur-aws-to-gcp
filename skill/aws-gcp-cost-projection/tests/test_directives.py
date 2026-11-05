#!/usr/bin/env python3
"""Tests for scripts/directives.py."""
import json
import os
import tempfile
import pytest

from scripts.directives import (
    Directive,
    load_ledger,
    save_ledger,
    validate_directive,
)


def test_directive_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        d1 = Directive(
            id="d1",
            author="user",
            source_utterance="Use Cloud Storage for S3 rows",
            selector={"service": "s3"},
            action={"type": "set_target_service", "value": "Cloud Storage"},
            confidence=0.95,
        )
        d2 = Directive(
            id="d2",
            author="agy",
            source_utterance="Ignore EDP discounts",
            selector={"aws_li_keys": ["k1", "k2"]},
            action={"type": "set_strategy", "value": "ignore"},
        )

        save_ledger(tmpdir, [d1, d2])

        # Verify directives.json and directives.baseline.json exist
        assert os.path.exists(os.path.join(tmpdir, "directives.json"))
        assert os.path.exists(os.path.join(tmpdir, "directives.baseline.json"))

        loaded = load_ledger(tmpdir)
        assert len(loaded) == 2
        assert loaded[0].id == "d1"
        assert loaded[0].action["type"] == "set_target_service"
        assert loaded[0].action["value"] == "Cloud Storage"
        assert loaded[1].id == "d2"
        assert loaded[1].action["value"] == "ignore"


def test_empty_ledger_safety():
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Non-existent file
        assert load_ledger(tmpdir) == []
        assert load_ledger("") == []

        # 2. Corrupt / non-json file
        bad_path = os.path.join(tmpdir, "directives.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("{invalid json...")
        assert load_ledger(tmpdir) == []

        # 3. Non-list json
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write('{"not": "a list"}')
        assert load_ledger(tmpdir) == []


def test_schema_validation():
    # Valid directives
    valid_d = Directive(
        id="d1",
        author="user",
        selector={"service": "ec2"},
        action={"type": "set_strategy", "value": "passthrough"},
    )
    ok, err = validate_directive(valid_d)
    assert ok and err is None

    # Invalid verb
    invalid_verb = Directive(
        id="d2",
        author="user",
        selector={"service": "ec2"},
        action={"type": "invalid_magic_verb", "value": "foo"},
    )
    ok, err = validate_directive(invalid_verb)
    assert not ok
    assert "Unknown or unsupported action.type" in err

    # Missing required field
    missing_author = {
        "id": "d3",
        "selector": {},
        "action": {"type": "annotate", "value": "test note"},
    }
    ok, err = validate_directive(missing_author)
    assert not ok
    assert "Missing or empty required field: 'author'" in err

    # Malformed selector
    bad_selector = {
        "id": "d4",
        "author": "user",
        "selector": "not-a-dict",
        "action": {"type": "annotate", "value": "test note"},
    }
    ok, err = validate_directive(bad_selector)
    assert not ok
    assert "'selector' must be a dict" in err


def test_atomic_write_safety():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Directive(
            id="d1",
            author="user",
            selector={"service": "ec2"},
            action={"type": "set_is_workload", "value": True},
        )
        save_ledger(tmpdir, [d])

        # Overwrite with simulated failure
        target_path = os.path.join(tmpdir, "directives.json")
        orig_content = open(target_path, "r", encoding="utf-8").read()

        # Unserializable payload raises TypeError during json.dump
        with pytest.raises(TypeError):
            save_ledger(tmpdir, [{"bad": object()}])

        # Original content is untouched
        assert open(target_path, "r", encoding="utf-8").read() == orig_content


def test_per_user_scoping():
    with tempfile.TemporaryDirectory() as tmpdir:
        d_alice = Directive(
            id="d_alice_1",
            author="alice@company.com",
            selector={"service": "s3"},
            action={"type": "set_strategy", "value": "passthrough"},
        )
        d_bob = Directive(
            id="d_bob_1",
            author="bob@competitor.com",
            selector={"service": "ec2"},
            action={"type": "set_strategy", "value": "ignore"},
        )

        # Alice saves her ledger
        save_ledger(tmpdir, [d_alice], user_id="alice@company.com")
        # Bob saves his ledger on the same job
        save_ledger(tmpdir, [d_bob], user_id="bob@competitor.com")

        # Each user loads only their own directives
        alice_ledger = load_ledger(tmpdir, user_id="alice@company.com")
        bob_ledger = load_ledger(tmpdir, user_id="bob@competitor.com")

        assert len(alice_ledger) == 1
        assert alice_ledger[0].id == "d_alice_1"
        assert alice_ledger[0].author == "alice@company.com"

        assert len(bob_ledger) == 1
        assert bob_ledger[0].id == "d_bob_1"
        assert bob_ledger[0].author == "bob@competitor.com"

        # Neither sees the other's
        assert "d_bob_1" not in [d.id for d in alice_ledger]
        assert "d_alice_1" not in [d.id for d in bob_ledger]

        # Files on disk are partitioned
        assert os.path.exists(os.path.join(tmpdir, "directives", "alice@company.com.json"))
        assert os.path.exists(os.path.join(tmpdir, "directives", "bob@competitor.com.json"))
        assert os.path.exists(os.path.join(tmpdir, "directives.baseline.json"))

        # Back-compat fallback: a new user on a legacy job with unscoped directives.json loads unscoped
        legacy_dir = os.path.join(tmpdir, "legacy_job")
        os.makedirs(legacy_dir, exist_ok=True)
        d_legacy = Directive(
            id="d_legacy",
            author="owner",
            selector={"service": "rds"},
            action={"type": "set_strategy", "value": "passthrough"},
        )
        save_ledger(legacy_dir, [d_legacy]) # unscoped
        loaded_legacy = load_ledger(legacy_dir, user_id="charlie@company.com")
        assert len(loaded_legacy) == 1
        assert loaded_legacy[0].id == "d_legacy"

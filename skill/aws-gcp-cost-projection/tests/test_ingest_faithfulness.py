"""
Faithfulness invariant tests for ingest.py (the extraction layer's output
guarantee). check_faithfulness is pure/typed so the failure modes are testable
without running a full ingestion.
"""
from ingest import check_faithfulness


def test_reconciling_catalog_passes():
    assert check_faithfulness(cat_rows=100, cat_total=1000.00, reference_total=1000.00) is None


def test_float_noise_within_tolerance_passes():
    # Large bill, sub-ppm float drift must not trip the gate.
    assert check_faithfulness(cat_rows=5000, cat_total=1_000_000.004,
                              reference_total=1_000_000.0) is None


def test_empty_bill_fails():
    err = check_faithfulness(cat_rows=0, cat_total=0.0, reference_total=0.0)
    assert err and "Empty bill" in err


def test_cost_drift_fails():
    # Materiality lost $500 without re-adding it -> must fail loudly.
    err = check_faithfulness(cat_rows=100, cat_total=500.00, reference_total=1000.00)
    assert err and "reconciliation failed" in err.lower()


def test_non_finite_total_fails():
    err = check_faithfulness(cat_rows=10, cat_total=float("nan"), reference_total=1000.00)
    assert err and "not finite" in err


def test_tolerance_scales_with_bill_size():
    # Floor: 0.005 drift on a tiny bill is within the 0.01 absolute floor -> pass.
    assert check_faithfulness(1, 10.005, 10.00) is None
    # ppm band: ~9 drift on a ~10M bill is within tol (10M * 1e-6 = 10) -> pass.
    assert check_faithfulness(9999, 9_999_999.0, 9_999_990.0) is None
    # But 50 drift on the same ~10M bill exceeds tol -> fail.
    assert check_faithfulness(9999, 10_000_040.0, 9_999_990.0) is not None

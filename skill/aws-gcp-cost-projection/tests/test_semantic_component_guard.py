"""
test_semantic_component_guard.py — Phase P4 validation for Semantic Component Guard (L3).

Verifies that:
1. Fargate ephemeral storage (component='ephemeral') attempting to resolve a memory SKU
   (e.g. 'Autopilot Pod Memory Requests') is REJECTED as a component mismatch passthrough.
2. Fargate ephemeral storage (component='ephemeral') resolving an ephemeral SKU
   ('Autopilot Pod Ephemeral Storage Requests') is ACCEPTED.
3. Memory component (component='memory') attempting to resolve ephemeral storage is REJECTED.
4. IOPS component (component='iops') attempting to resolve a throughput SKU is REJECTED.
5. vCPU component (component='vcpu') attempting to resolve a memory/storage SKU is REJECTED.
"""

from scripts.resolve_sku import resolve_intent_to_sku


def test_fargate_ephemeral_as_memory_rejected():
    # Regression test for Fargate ephemeral-as-memory bug:
    # Component 'ephemeral' attempting to resolve 'Autopilot Pod Memory Requests'
    res = resolve_intent_to_sku(
        gcp_service="Kubernetes Engine",
        gcp_sku_name="Autopilot Pod Memory Requests",
        gcp_region="us-central1",
        component="ephemeral",
    )
    assert not res, "Component mismatch must reject resolution"
    assert res.sku_id is None, "sku_id must be None on rejection"
    assert "component-mismatch" in (res.note or ""), f"Expected component-mismatch note, got: {res.note}"


def test_fargate_ephemeral_as_ephemeral_accepted():
    # Legitimate ephemeral resolution
    res = resolve_intent_to_sku(
        gcp_service="Kubernetes Engine",
        gcp_sku_name="Autopilot Pod Ephemeral Storage Requests",
        gcp_region="us-central1",
        component="ephemeral",
    )
    assert res, "Legitimate ephemeral storage resolution must succeed"
    assert res.sku_id is not None, "sku_id must be present on valid resolution"
    assert res.note is None or "component-mismatch" not in res.note


def test_memory_as_ephemeral_rejected():
    # Component 'memory' attempting to resolve ephemeral storage SKU
    res = resolve_intent_to_sku(
        gcp_service="Kubernetes Engine",
        gcp_sku_name="Autopilot Pod Ephemeral Storage Requests",
        gcp_region="us-central1",
        component="memory",
    )
    assert not res, "Memory component must reject ephemeral storage SKU"
    assert res.sku_id is None
    assert "component-mismatch" in (res.note or "")


def test_iops_as_throughput_rejected():
    # Component 'iops' attempting to resolve throughput SKU
    res = resolve_intent_to_sku(
        gcp_service="Compute Engine",
        gcp_sku_name="Hyperdisk Balanced Provisioned Throughput",
        gcp_region="us-central1",
        component="iops",
    )
    assert not res, "IOPS component must reject throughput SKU"
    assert res.sku_id is None
    assert "component-mismatch" in (res.note or "")


def test_vcpu_as_memory_rejected():
    # Component 'vcpu' attempting to resolve memory SKU
    res = resolve_intent_to_sku(
        gcp_service="Kubernetes Engine",
        gcp_sku_name="Autopilot Pod Memory Requests",
        gcp_region="us-central1",
        component="vcpu",
    )
    assert not res, "vCPU component must reject memory SKU"
    assert res.sku_id is None
    assert "component-mismatch" in (res.note or "")


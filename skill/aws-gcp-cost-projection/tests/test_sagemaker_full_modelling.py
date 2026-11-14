"""Full-modelling test suite for SageMaker (§4.2 of WORKORDER_per_service_full_modelling.md).

DoD coverage:
- Training instance-hours → Vertex AI Custom Training (sized, not a flat label)
- Real-time endpoint → Vertex AI Online Prediction (sized)
- Batch Transform → Vertex AI Batch Prediction (sized)
- Notebook / Studio / JupyterLab / Code Editor / RStudio → Vertex AI Workbench (sized)
- Processing jobs → Vertex AI Custom Processing (sized)
- HyperPod → Compute Engine (self-hosted cluster, sized)
- Inferentia / Trainium → passthrough / review (no GCP equivalent)
- Serverless Inference → passthrough / review (no per-ms Vertex SKU)
- Ground Truth → passthrough / review (GCP Data Labeling deprecated)
- MLflow → passthrough / review (no managed GCP equivalent)
- Feature Store → passthrough / review (Vertex AI Feature Store V2 analogue)
- CreateVolume-Gp3 → block_storage (NOT dropped; was previously excluded)
- Unknown SageMaker sub-type → review passthrough (never a flat "Vertex AI" map)
- ml.* parse_instance fix: ml.m5.xlarge, ml.g5.xlarge resolve correctly

Sources verified 2026-11-13:
  aws.amazon.com/sagemaker/ai/pricing
  docs.cloud.google.com/vertex-ai/docs/training/overview
  docs.cloud.google.com/vertex-ai/docs/predictions/overview
  docs.cloud.google.com/vertex-ai/docs/workbench/overview
  docs.cloud.google.com/vertex-ai/docs/featurestore/latest/overview (V2)
"""

import pytest
import scripts.apply_static_mappings as asm
import scripts.spec_dispatch as sd


# ─── helpers ────────────────────────────────────────────────────────────────

def _sm_row(usage_type, instance_type="ml.m5.xlarge", unit="Hrs", product_code="AmazonSageMaker"):
    return {
        "aws_li_key":           "k_sm_test",
        "product":              "Amazon SageMaker",
        "product_code":         product_code,
        "canonical":            "sagemaker",
        "usage_type":           usage_type,
        "operation":            "",
        "pricing_unit":         unit,
        "unit":                 unit,
        "line_item_type":       "Usage",
        "pricing_model":        "OnDemand",
        "aws_amortized_cost":   100.0,
        "total_usage":          10.0,
        "gcp_region":           "us-central1",
        "aws_region":           "us-east-1",
        "_bill_has_discounted_usage": True,
        "instance_type":        instance_type,
        "deployment_option":    "",
        "database_engine":      "",
        "volume_type":          "",
        "instance_ram_gb":      None,
        "mechanic_group":       "sagemaker",
        "mechanic_rule":        None,
        "charge_type_id":       None,
    }


def _dispatch(usage_type, instance_type="ml.m5.xlarge", unit="Hrs"):
    row = _sm_row(usage_type, instance_type, unit)
    return sd.dispatch(row)


def _map(charge_type_id, usage_type, instance_type="ml.m5.xlarge", unit="Hrs"):
    """Dispatch a row and pass it through map_sagemaker."""
    base = _sm_row(usage_type, instance_type, unit)
    base["mechanic_rule"] = f"spec:sagemaker.{charge_type_id}"
    mapped, llm = asm.map_sagemaker([base])
    return mapped, llm


# ─── Step 1: parse_instance fix ─────────────────────────────────────────────

class TestParseInstanceMlPrefix:
    """ml.* instance types must resolve through parse_instance after the prefix-strip fix."""

    def test_ml_m5_xlarge_resolves(self):
        p = asm.parse_instance("ml.m5.xlarge")
        assert p is not None, "ml.m5.xlarge must resolve"
        assert p["vcpus"] == 4
        assert p["ram_gb"] == 16.0

    def test_ml_g5_xlarge_resolves(self):
        p = asm.parse_instance("ml.g5.xlarge")
        assert p is not None, "ml.g5.xlarge must resolve"
        assert p["vcpus"] == 4

    def test_ml_p4d_resolves(self):
        p = asm.parse_instance("ml.p4d.24xlarge")
        assert p is not None, "ml.p4d.24xlarge must resolve"
        assert p["vcpus"] == 96

    def test_cache_still_works(self):
        """Existing cache.* prefix strip must not break."""
        p = asm.parse_instance("cache.r6g.large")
        assert p is not None
        assert p["vcpus"] == 2

    def test_db_still_works(self):
        """Existing db.* prefix strip must not break."""
        p = asm.parse_instance("db.r5.xlarge")
        assert p is not None
        assert p["vcpus"] == 4


# ─── Step 2: spec_dispatch charge-type routing ───────────────────────────────

class TestSageMakerDispatch:
    """Every SageMaker usage-type pattern must dispatch to the right charge-type."""

    def test_training_dispatches(self):
        r = _dispatch("USE1-Train:ml.m5.xlarge")
        assert r["charge_type"] == "sagemaker_training"
        assert r["mechanic"] == "sagemaker"

    def test_training_gpu_dispatches(self):
        r = _dispatch("USE1-Train:ml.g5.xlarge", instance_type="ml.g5.xlarge")
        assert r["charge_type"] == "sagemaker_training"

    def test_realtime_host_dispatches(self):
        r = _dispatch("USE1-Host:ml.m5.xlarge")
        assert r["charge_type"] == "sagemaker_realtime"
        assert r["mechanic"] == "sagemaker"

    def test_batch_transform_dispatches(self):
        r = _dispatch("USE1-Tsform:ml.m5.xlarge")
        assert r["charge_type"] == "sagemaker_batch"
        assert r["mechanic"] == "sagemaker"

    def test_notebook_dispatches(self):
        r = _dispatch("USE1-Notebk:ml.m5.large", instance_type="ml.m5.large")
        assert r["charge_type"] == "sagemaker_notebook"

    def test_jupyterlab_dispatches(self):
        r = _dispatch("USE1-JupyterLab:ml.t3.medium", instance_type="ml.t3.medium")
        assert r["charge_type"] == "sagemaker_notebook"

    def test_codeeditor_dispatches(self):
        r = _dispatch("USE1-CodeEditor:ml.t3.medium", instance_type="ml.t3.medium")
        assert r["charge_type"] == "sagemaker_notebook"

    def test_rstudio_dispatches(self):
        r = _dispatch("USE1-RStudio:ml.m5.large", instance_type="ml.m5.large")
        assert r["charge_type"] == "sagemaker_notebook"

    def test_processing_dispatches(self):
        r = _dispatch("USE1-Process:ml.c5.xlarge", instance_type="ml.c5.xlarge")
        assert r["charge_type"] == "sagemaker_processing"

    def test_hyperpod_dispatches(self):
        r = _dispatch("USE1-HyperPod:ml.p4d.24xlarge", instance_type="ml.p4d.24xlarge")
        assert r["charge_type"] == "sagemaker_hyperpod"

    def test_featurestore_dispatches(self):
        r = _dispatch("FeatureStoreOnlineStorage", instance_type="", unit="GB-Mo")
        assert r["charge_type"] == "sagemaker_featurestore"

    def test_serverless_dispatches(self):
        r = _dispatch("ServerlessInferenceComputeDuration", instance_type="", unit="Milliseconds")
        assert r["charge_type"] == "sagemaker_serverless"

    def test_datawrangler_dispatches(self):
        r = _dispatch("DataWranglerInteractiveSession", instance_type="")
        assert r["charge_type"] == "sagemaker_datawrangler"

    def test_groundtruth_dispatches(self):
        r = _dispatch("GroundTruthImageAnnotation", instance_type="", unit="Objects")
        assert r["charge_type"] == "sagemaker_groundtruth"

    def test_mlflow_dispatches(self):
        r = _dispatch("MLflow-TrackingServer", instance_type="")
        assert r["charge_type"] == "sagemaker_mlflow"

    def test_gp3_volume_to_block_storage(self):
        """CreateVolume-Gp3 must dispatch to block_storage mechanic, NOT dropped."""
        r = _dispatch("CreateVolume-Gp3", instance_type="", unit="GB-Mo")
        assert r["charge_type"] == "sagemaker_storage_gp3"
        assert r["mechanic"] == "block_storage", (
            "CreateVolume-Gp3 must route to block_storage, not misc or sagemaker"
        )

    def test_unknown_subtype_uses_fallback(self):
        """An unknown SageMaker usage_type must dispatch to sagemaker_all (not spec_gap)."""
        r = _dispatch("SomeCompletelyUnknownSageMakerRow", instance_type="")
        assert r is not None
        assert r.get("charge_type") == "sagemaker_all"
        # Must NOT be a spec_gap catch-all
        assert not (r.get("source_rule") or "").startswith("spec_gap"), (
            "Unknown SM usage_type must not fall to spec_gap"
        )
        assert r["mechanic"] == "sagemaker"


# ─── Step 3: map_sagemaker output contract ──────────────────────────────────

class TestMapSageMakerTraining:
    """Training rows emit sized core + RAM rows, never a flat 'Vertex AI' label."""

    def test_training_m5_emits_core_and_ram(self):
        mapped, llm = _map("sagemaker_training", "USE1-Train:ml.m5.xlarge", "ml.m5.xlarge")
        assert len(mapped) >= 2, "Training must emit at least core + RAM components"
        components = {m["component"] for m in mapped}
        assert "core" in components, "Must emit a core component"
        assert "ram" in components, "Must emit a RAM component"
        # All rows must reference Vertex AI
        for m in mapped:
            assert m["gcp_service"] == "Vertex AI", f"gcp_service must be Vertex AI, got {m['gcp_service']}"

    def test_training_m5_sku_name_not_flat_vertex(self):
        """gcp_sku_name must be specific (e.g. 'N4 Instance Core'), NOT 'Vertex AI'."""
        mapped, _ = _map("sagemaker_training", "USE1-Train:ml.m5.xlarge", "ml.m5.xlarge")
        for m in mapped:
            assert m["gcp_sku_name"] != "Vertex AI", (
                f"gcp_sku_name must be specific, not flat 'Vertex AI': {m}"
            )

    def test_training_g5_emits_accelerator(self):
        """GPU instance (g5) must emit an accelerator component."""
        mapped, _ = _map("sagemaker_training", "USE1-Train:ml.g5.xlarge", "ml.g5.xlarge")
        components = {m["component"] for m in mapped}
        assert "accelerator" in components, (
            "GPU training instance (g5) must emit an accelerator component"
        )

    def test_training_unit_multipliers_match_instance_specs(self):
        """Core row unit_multiplier must equal vcpus; RAM row must equal ram_gb."""
        mapped, _ = _map("sagemaker_training", "USE1-Train:ml.m5.xlarge", "ml.m5.xlarge")
        core_rows = [m for m in mapped if m["component"] == "core"]
        ram_rows  = [m for m in mapped if m["component"] == "ram"]
        assert core_rows and abs(core_rows[0]["unit_multiplier"] - 4.0) < 0.01, (
            "ml.m5.xlarge has 4 vCPUs; core unit_multiplier must be 4.0"
        )
        assert ram_rows and abs(ram_rows[0]["unit_multiplier"] - 16.0) < 0.01, (
            "ml.m5.xlarge has 16 GiB RAM; RAM unit_multiplier must be 16.0"
        )


class TestMapSageMakerEndpointAndBatch:
    def test_realtime_endpoint_maps_online_prediction(self):
        mapped, llm = _map("sagemaker_realtime", "USE1-Host:ml.m5.xlarge", "ml.m5.xlarge")
        assert len(mapped) >= 1
        notes = " ".join(m.get("projection_note", "") for m in mapped)
        assert "Online Prediction" in notes, (
            "Realtime endpoint must reference 'Online Prediction' in notes"
        )

    def test_batch_transform_maps_batch_prediction(self):
        mapped, llm = _map("sagemaker_batch", "USE1-Tsform:ml.m5.xlarge", "ml.m5.xlarge")
        assert len(mapped) >= 1
        notes = " ".join(m.get("projection_note", "") for m in mapped)
        assert "Batch Prediction" in notes, (
            "Batch transform must reference 'Batch Prediction' in notes"
        )


class TestMapSageMakerNotebook:
    def test_notebook_maps_workbench(self):
        mapped, llm = _map("sagemaker_notebook", "USE1-Notebk:ml.m5.large", "ml.m5.large")
        assert len(mapped) >= 1
        notes = " ".join(m.get("projection_note", "") for m in mapped)
        assert "Workbench" in notes, "Notebook must reference 'Workbench' in notes"

    def test_notebook_emits_core_and_ram(self):
        mapped, _ = _map("sagemaker_notebook", "USE1-JupyterLab:ml.m5.large", "ml.m5.large")
        components = {m["component"] for m in mapped}
        assert "core" in components
        assert "ram" in components


class TestMapSageMakerHyperPod:
    def test_hyperpod_maps_compute_engine(self):
        """HyperPod → Compute Engine (self-hosted cluster), not Vertex AI."""
        mapped, llm = _map("sagemaker_hyperpod", "USE1-HyperPod:ml.p4d.24xlarge", "ml.p4d.24xlarge")
        assert len(mapped) >= 1
        for m in mapped:
            assert m["gcp_service"] == "Compute Engine", (
                f"HyperPod must map to Compute Engine, got {m['gcp_service']}"
            )


class TestMapSageMakerInferentiaTrainium:
    def test_inferentia_is_passthrough(self):
        """Inferentia instances have no GCP equivalent → passthrough/review."""
        mapped, llm = _map("sagemaker_training", "USE1-Train:ml.inf1.xlarge", "ml.inf1.xlarge")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"
        assert "Inferentia" in mapped[0]["projection_note"] or "inf" in mapped[0]["projection_note"]

    def test_trainium_is_passthrough(self):
        """Trainium instances have no GCP equivalent → passthrough/review."""
        mapped, llm = _map("sagemaker_training", "USE1-Train:ml.trn1.2xlarge", "ml.trn1.2xlarge")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"


class TestMapSageMakerNoEquivalents:
    def test_serverless_is_passthrough(self):
        mapped, _ = _map("sagemaker_serverless", "ServerlessInferenceComputeDuration", "", "Milliseconds")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"
        assert mapped[0]["gcp_service"] == "Vertex AI"
        assert "Serverless" in mapped[0]["projection_note"]

    def test_groundtruth_is_passthrough(self):
        mapped, _ = _map("sagemaker_groundtruth", "GroundTruthImageAnnotation", "", "Objects")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"
        # Must NOT project $0 — carry AWS cost (passthrough, not ignore)
        assert mapped[0]["strategy"] != "ignore", "Ground Truth must passthrough, never ignore"

    def test_mlflow_is_passthrough(self):
        mapped, _ = _map("sagemaker_mlflow", "MLflow-TrackingServer", "")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"
        assert "MLflow" in mapped[0]["projection_note"]

    def test_featurestore_is_passthrough(self):
        mapped, _ = _map("sagemaker_featurestore", "FeatureStoreOnlineStorage", "", "GB-Mo")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"
        assert mapped[0]["gcp_service"] == "Vertex AI"
        assert "Feature Store" in mapped[0]["projection_note"]

    def test_datawrangler_is_passthrough(self):
        mapped, _ = _map("sagemaker_datawrangler", "DataWranglerInteractiveSession", "")
        assert len(mapped) == 1
        assert mapped[0]["strategy"] == "passthrough"


class TestMapSageMakerCatchAll:
    def test_unknown_subtype_is_passthrough_review_not_map(self):
        """sagemaker_all catch-all must NOT emit strategy='map' on a guessed Vertex target."""
        mapped, _ = _map("sagemaker_all", "SomeOtherSageMakerRow", "")
        assert len(mapped) == 1
        m = mapped[0]
        # Must be passthrough, never a silently-mapped row
        assert m["strategy"] == "passthrough", (
            f"Unknown SM sub-type must be passthrough, got strategy={m['strategy']}"
        )
        assert m["gcp_service"] == "Vertex AI"
        # Confidence must be low (≤0.4)
        assert m["mapping_confidence"] <= 0.4, (
            f"Unknown sub-type must have conf ≤0.4, got {m['mapping_confidence']}"
        )

    def test_unknown_subtype_has_descriptive_note(self):
        """The projection_note for the catch-all must explain WHY it's a passthrough."""
        mapped, _ = _map("sagemaker_all", "SomeOtherRow", "")
        note = mapped[0].get("projection_note", "")
        assert len(note) > 20, "Catch-all must have a descriptive projection_note"


class TestGp3NotDropped:
    """CreateVolume-Gp3 rows must NOT be dropped by the SageMaker-to-block_storage fix."""

    def test_gp3_dispatches_to_block_storage_mechanic(self):
        row = _sm_row("CreateVolume-Gp3", instance_type="", unit="GB-Mo")
        result = sd.dispatch(row)
        assert result is not None
        assert result["mechanic"] == "block_storage", (
            "CreateVolume-Gp3 must dispatch to block_storage, not misc/sagemaker"
        )
        assert result["charge_type"] == "sagemaker_storage_gp3"

    def test_gp3_charge_type_not_spec_gap(self):
        row = _sm_row("CreateVolume-Gp3", instance_type="", unit="GB-Mo")
        result = sd.dispatch(row)
        source = result.get("source_rule") or ""
        assert not source.startswith("spec_gap"), (
            "CreateVolume-Gp3 must not fall to spec_gap"
        )


class TestNeverFlatVertexAILabel:
    """The old code emitted gcp_service='Vertex AI', gcp_sku_name='Vertex AI' as a flat label.
    After the fix, every compute sub-type must have a specific sku_name."""

    def test_training_sku_name_is_specific(self):
        mapped, _ = _map("sagemaker_training", "USE1-Train:ml.m5.xlarge", "ml.m5.xlarge")
        for m in mapped:
            assert m["gcp_sku_name"] not in ("Vertex AI", ""), (
                f"sku_name must be specific: {m}"
            )

    def test_realtime_sku_name_is_specific(self):
        mapped, _ = _map("sagemaker_realtime", "USE1-Host:ml.m5.xlarge", "ml.m5.xlarge")
        for m in mapped:
            assert m["gcp_sku_name"] not in ("Vertex AI", ""), (
                f"sku_name must be specific: {m}"
            )

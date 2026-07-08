"""
Tests for apply_static_mappings.py static mapper functions.

Each test calls a mapper directly with synthetic input rows and asserts
the output fields — gcp_service, strategy, unit_multiplier, and gcp_sku_name.
No DuckDB or live GCP API calls are made (resolve_sku may return None for
unknown SKUs, which is fine — we test for strategy correctness, not SKU IDs).

Golden values tested:
  - S3 Glacier Deep Archive → Archive Storage (NOT Coldline — past 120x inflation bug)
  - S3 per-request → passthrough (NOT mapped — past 50x inflation bug)
  - Redshift ra3.4xlarge → 1500 BQ slots
  - Redshift serverless RPU → 128 slots/RPU
  - GuardDuty → Security Command Center passthrough
  - EFS Standard-IA → Filestore Basic HDD
  - FSx Lustre → Filestore High Scale SSD
  - EMR m5.xlarge → 4 vCPU Dataproc Premium
  - Athena data-scanned → BigQuery Analysis map
  - Kinesis shard-hours → Pub/Sub passthrough
  - X-Ray → Cloud Trace
  - S3 monitoring-fee → passthrough (no-equivalent)
"""

import pytest
from unittest.mock import patch
from conftest import row

# Patch resolve_sku to return a synthetic SKU ID without hitting the catalog
MOCK_SKU = "MOCK-SKU-1234"


def _with_sku(mapper, rows):
    """Call mapper with resolve_sku patched to always return MOCK_SKU."""
    with patch("apply_static_mappings.resolve_sku", return_value=MOCK_SKU):
        return mapper(rows)


# Import mappers after path is configured by conftest
from apply_static_mappings import (
    map_object_storage, map_per_request, map_block_storage,
    map_data_transfer, map_non_workload, map_cloudwatch,
    map_guardduty, map_redshift, map_athena, map_kinesis,
    map_efs, map_fsx, map_xray, map_emr,
    _emr_vcpus,
)


# ---------------------------------------------------------------------------
# Object storage (S3 → GCS)
# ---------------------------------------------------------------------------

def test_s3_standard_maps_to_standard_storage():
    rows = [row(product="Amazon S3", usage_type="TimedStorage-ByteHrs",
                operation="StandardStorage", unit="GB-Mo")]
    out = _with_sku(map_object_storage, rows)
    assert len(out) == 1
    assert out[0]["gcp_service"] == "Cloud Storage"
    assert out[0]["gcp_sku_name"] == "Standard Storage"
    assert out[0]["strategy"] == "map"


def test_s3_glacier_deep_archive_maps_to_archive_not_coldline():
    """Regression: Glacier Deep Archive must NOT map to Coldline (120x inflation bug)."""
    rows = [row(product="Amazon S3", usage_type="TimedStorage-GlacierDeepArchive-ByteHrs",
                operation="GlacierDeepArchiveStorage", unit="GB-Mo")]
    out = _with_sku(map_object_storage, rows)
    assert out[0]["gcp_sku_name"] == "Archive Storage"


def test_s3_glacier_flexible_maps_to_coldline():
    rows = [row(product="Amazon S3", usage_type="TimedStorage-GlacierFlexible-ByteHrs",
                operation="GlacierFlexible", unit="GB-Mo")]
    out = _with_sku(map_object_storage, rows)
    assert out[0]["gcp_sku_name"] == "Coldline Storage"


def test_s3_standard_ia_maps_to_nearline():
    rows = [row(product="Amazon S3", usage_type="TimedStorage-SIA-ByteHrs",
                operation="StandardIA", unit="GB-Mo")]
    out = _with_sku(map_object_storage, rows)
    assert out[0]["gcp_sku_name"] == "Nearline Storage"


def test_s3_monitoring_fee_passthrough():
    """S3 per-1000-object monitoring fee has no GCS equivalent — must passthrough."""
    rows = [row(product="Amazon S3",
                usage_type="Monitoring-Automation-INT",
                operation="per 1,000 objects monitored", unit="Count")]
    out = _with_sku(map_object_storage, rows)
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# Per-request (S3 request passthrough regression)
# ---------------------------------------------------------------------------

def test_s3_per_request_passthrough():
    """Regression: S3 per-request must passthrough — past 50x inflation from wrong SKU."""
    rows = [row(product="Amazon Simple Storage Service",
                usage_type="Requests-Tier1", unit="Requests")]
    out = _with_sku(map_per_request, rows)
    assert out[0]["strategy"] == "passthrough"
    assert out[0]["gcp_service"] == "Cloud Storage"


def test_lambda_maps_to_cloud_run():
    rows = [row(product="AWS Lambda", usage_type="Requests", unit="Requests")]
    out = _with_sku(map_per_request, rows)
    assert out[0]["gcp_service"] == "Cloud Run"
    assert out[0]["strategy"] == "map"


# ---------------------------------------------------------------------------
# Block storage
# ---------------------------------------------------------------------------

def test_ebs_gp3_maps_to_balanced_pd():
    rows = [row(product="Amazon Elastic Block Store",
                usage_type="EBS:VolumeUsage.gp3", volume_type="gp3", unit="GB-Mo")]
    out = _with_sku(map_block_storage, rows)
    assert out[0]["gcp_service"] == "Compute Engine"
    assert "Balanced PD" in out[0]["gcp_sku_name"]


def test_ebs_io1_maps_to_extreme_pd():
    rows = [row(product="Amazon EC2",
                usage_type="EBS:VolumeUsage.io1", volume_type="io1", unit="GB-Mo")]
    out = _with_sku(map_block_storage, rows)
    assert "Extreme PD" in out[0]["gcp_sku_name"]


def test_ebs_snapshot_maps_to_snapshot_sku():
    rows = [row(product="Amazon EC2",
                usage_type="EBS:SnapshotUsage", unit="GB-Mo")]
    out = _with_sku(map_block_storage, rows)
    assert "Snapshot" in out[0]["gcp_sku_name"]


def test_rds_io_request_ignored():
    """Aurora/RDS per-I/O rows must be ignored (otherwise unit mismatch inflates 1000x)."""
    rows = [row(product="Amazon Aurora",
                usage_type="RDS:Aurora:IO-Request",
                operation="I/O request", unit="IOs")]
    out = _with_sku(map_block_storage, rows)
    assert out[0]["strategy"] == "ignore"


def test_gp3_iops_ignored():
    """gp3 Provisioned IOPS fee is included in GCP pd-balanced capacity price."""
    rows = [row(product="Amazon EC2",
                usage_type="EBS:VolumeUsage.gp3-IOPS-mo",
                operation="Provisioned IOPS", unit="IOPS-Mo")]
    out = _with_sku(map_block_storage, rows)
    assert out[0]["strategy"] == "ignore"


# ---------------------------------------------------------------------------
# GuardDuty / Security Hub
# ---------------------------------------------------------------------------

def test_guardduty_passthrough_to_scc():
    rows = [row(product="Amazon GuardDuty",
                usage_type="EU-AWSLogs-Processed-Bytes", unit="GB")]
    out = map_guardduty(rows)
    assert out[0]["gcp_service"] == "Security Command Center"
    assert out[0]["strategy"] == "passthrough"


def test_security_hub_passthrough_to_scc():
    rows = [row(product="AWS Security Hub",
                usage_type="Security-Findings", unit="Count")]
    out = map_guardduty(rows)
    assert out[0]["gcp_service"] == "Security Command Center"
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# Redshift → BigQuery
# ---------------------------------------------------------------------------

def test_redshift_ra3_4xlarge_is_1500_slots():
    rows = [row(product="Amazon Redshift",
                usage_type="ra3.4xlarge-NodeUsage", unit="Hrs")]
    out = _with_sku(map_redshift, rows)
    assert out[0]["gcp_service"] == "BigQuery"
    assert out[0]["unit_multiplier"] == 1500.0
    assert out[0]["strategy"] == "map"


def test_redshift_dc2_large_is_500_slots():
    rows = [row(product="Amazon Redshift",
                usage_type="dc2.large-NodeUsage", unit="Hrs")]
    out = _with_sku(map_redshift, rows)
    assert out[0]["unit_multiplier"] == 500.0


def test_redshift_serverless_rpu_is_128_slots():
    rows = [row(product="Amazon Redshift Serverless",
                usage_type="ServerlessRPUHours", unit="Hrs")]
    out = _with_sku(map_redshift, rows)
    assert out[0]["unit_multiplier"] == 128.0


def test_redshift_backup_passthrough():
    rows = [row(product="Amazon Redshift",
                usage_type="SnapshotUsage", unit="GB-Mo")]
    out = _with_sku(map_redshift, rows)
    assert out[0]["strategy"] == "passthrough"


def test_redshift_unknown_type_passthrough():
    rows = [row(product="Amazon Redshift",
                usage_type="UnknownNode", unit="Hrs")]
    out = _with_sku(map_redshift, rows)
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# Athena → BigQuery
# ---------------------------------------------------------------------------

def test_athena_data_scanned_maps_to_bq_analysis():
    rows = [row(product="Amazon Athena",
                usage_type="DataScanned-Bytes", unit="TB")]
    out = _with_sku(map_athena, rows)
    assert out[0]["gcp_service"] == "BigQuery"
    assert out[0]["gcp_sku_name"] == "Analysis"
    assert out[0]["strategy"] == "map"
    assert out[0]["unit_multiplier"] == 1.0


def test_athena_ddl_passthrough():
    rows = [row(product="Amazon Athena",
                usage_type="DDL-Queries", unit="Count")]
    out = _with_sku(map_athena, rows)
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# Kinesis shard-hours
# ---------------------------------------------------------------------------

def test_kinesis_shard_hours_passthrough_pubsub():
    rows = [row(product="Amazon Kinesis",
                usage_type="Kinesis-ShardHour", unit="Hrs")]
    out = map_kinesis(rows)
    assert out[0]["gcp_service"] == "Pub/Sub"
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# EFS → Filestore
# ---------------------------------------------------------------------------

def test_efs_standard_maps_to_basic_ssd():
    rows = [row(product="Amazon Elastic File System",
                usage_type="TimedStorage-EFS-ByteHrs", unit="GB-Mo")]
    out = _with_sku(map_efs, rows)
    assert out[0]["gcp_service"] == "Filestore"
    assert "SSD" in out[0]["gcp_sku_name"]


def test_efs_ia_maps_to_basic_hdd():
    rows = [row(product="Amazon Elastic File System",
                usage_type="TimedStorage-EFS-IA-ByteHrs", operation="Standard-IA", unit="GB-Mo")]
    out = _with_sku(map_efs, rows)
    assert "HDD" in out[0]["gcp_sku_name"]


def test_efs_provisioned_throughput_passthrough():
    rows = [row(product="Amazon Elastic File System",
                usage_type="ProvisionedThroughput-MBps", unit="MBps-Mo")]
    out = _with_sku(map_efs, rows)
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# FSx → Filestore
# ---------------------------------------------------------------------------

def test_fsx_lustre_maps_to_high_scale_ssd():
    rows = [row(product="Amazon FSx for Lustre",
                usage_type="FSx:Lustre-Storage", unit="GB-Mo")]
    out = _with_sku(map_fsx, rows)
    assert out[0]["gcp_service"] == "Filestore"
    assert "High Scale SSD" in out[0]["gcp_sku_name"]


def test_fsx_windows_maps_to_enterprise():
    rows = [row(product="Amazon FSx for Windows File Server",
                usage_type="FSx:Windows-HDD-Storage", unit="GB-Mo")]
    out = _with_sku(map_fsx, rows)
    assert "Enterprise" in out[0]["gcp_sku_name"]


def test_fsx_ontap_passthrough():
    rows = [row(product="Amazon FSx for NetApp ONTAP",
                usage_type="FSx:ONTAP-Storage", unit="GB-Mo")]
    out = _with_sku(map_fsx, rows)
    assert out[0]["strategy"] == "passthrough"


def test_fsx_backup_passthrough():
    rows = [row(product="Amazon FSx",
                usage_type="FSx:Backup-GB-Mo", unit="GB-Mo")]
    out = _with_sku(map_fsx, rows)
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# X-Ray → Cloud Trace
# ---------------------------------------------------------------------------

def test_xray_maps_to_cloud_trace():
    rows = [row(product="AWS X-Ray", usage_type="Traces-Stored-Count", unit="Count")]
    out = _with_sku(map_xray, rows)
    assert out[0]["gcp_service"] == "Cloud Trace"
    assert out[0]["strategy"] == "map"
    assert out[0]["unit_multiplier"] == 1.0


# ---------------------------------------------------------------------------
# EMR → Dataproc
# ---------------------------------------------------------------------------

def test_emr_vcpu_extraction_from_usage_type():
    assert _emr_vcpus("m5.xlarge-EMR-CORE", None) == 4
    assert _emr_vcpus("r5.4xlarge-EMR-MASTER", None) == 16
    assert _emr_vcpus("c5.9xlarge-EMR-TASK", None) == 36


def test_emr_vcpu_prefers_instance_vcpus_field():
    assert _emr_vcpus("m5.xlarge-EMR-CORE", 8) == 8


def test_emr_vcpu_unknown_returns_none():
    assert _emr_vcpus("x2.weird-EMR-CORE", None) is None


def test_emr_known_instance_maps_to_dataproc_premium():
    rows = [row(product="Amazon Elastic MapReduce",
                usage_type="m5.xlarge-EMR-CORE", unit="Hrs")]
    out = _with_sku(map_emr, rows)
    assert out[0]["gcp_service"] == "Cloud Dataproc"
    assert out[0]["gcp_sku_name"] == "Dataproc Premium"
    assert out[0]["unit_multiplier"] == 4.0
    assert out[0]["strategy"] == "map"


def test_emr_r5_4xlarge_is_16_vcpus():
    rows = [row(product="AmazonEMR",
                usage_type="r5.4xlarge-EMR-MASTER", unit="Hrs")]
    out = _with_sku(map_emr, rows)
    assert out[0]["unit_multiplier"] == 16.0


def test_emr_unknown_instance_type_passthrough():
    rows = [row(product="Amazon Elastic MapReduce",
                usage_type="x2.weird-EMR-CORE", unit="Hrs")]
    out = _with_sku(map_emr, rows)
    assert out[0]["strategy"] == "passthrough"
    assert out[0]["gcp_service"] == "Cloud Dataproc"


def test_emr_spot_row_passthrough():
    rows = [row(product="Amazon Elastic MapReduce",
                usage_type="m5.xlarge-EMR-CORE-Spot", unit="Hrs")]
    out = _with_sku(map_emr, rows)
    assert out[0]["strategy"] == "passthrough"


# ---------------------------------------------------------------------------
# Non-workload
# ---------------------------------------------------------------------------

def test_marketplace_passthrough():
    rows = [row(product="AWS Marketplace", operation="SaaS License", unit="Hrs")]
    out = map_non_workload(rows)
    assert out[0]["strategy"] == "passthrough"
    assert "Marketplace" in out[0]["gcp_service"]


# ---------------------------------------------------------------------------
# CloudWatch
# ---------------------------------------------------------------------------

def test_cloudwatch_log_bytes_maps_to_cloud_logging():
    rows = [row(product="Amazon CloudWatch",
                usage_type="LogBytes-Processed", unit="GB")]
    out = _with_sku(map_cloudwatch, rows)
    assert out[0]["gcp_service"] == "Cloud Logging"
    assert out[0]["strategy"] == "map"


def test_cloudwatch_metrics_passthrough():
    rows = [row(product="Amazon CloudWatch",
                usage_type="MetricMonitorUsage", unit="Count")]
    out = _with_sku(map_cloudwatch, rows)
    assert out[0]["strategy"] == "passthrough"

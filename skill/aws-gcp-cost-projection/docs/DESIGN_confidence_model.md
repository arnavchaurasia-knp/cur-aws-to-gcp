# Confidence Cascade Model — Architecture & Design Note

> **Note:** This document captures the architecture, rationale, and design analysis originally drafted in `confidence_engine.py` (Phase P: Confidence Cascade). The implementation code in `confidence_engine.py` was deleted per [WORKORDER_confidence_model.md](file:///c:/Users/arnav/Downloads/cur-web-AGY/skill/aws-gcp-cost-projection/docs/WORKORDER_confidence_model.md) because customer-facing reports do not display artificial or uncalibrated confidence percentages. This document preserves the analytical model for future evaluation should a concrete consumer (such as automated routing to manual review) be built.

---

## 1. Rationale & Analysis

In early designs of the cost projection pipeline, a single scalar confidence number (`mapping_confidence`) was assigned to rows and averaged into a customer-facing percentage. In practice, this conflated distinct dimensions of uncertainty:

1. **Classification Confidence:** How certain are we about what the AWS SKU is based on catalog lookup and mechanic classification?
2. **Mapping Confidence:** How suitable and complete is the chosen GCP target equivalent?
3. **Data Confidence:** How complete were the input CUR fields (e.g., were instance types, usage types, operations, or units missing/synthesized)?
4. **Validation Confidence:** Did the mapped candidate pass all consistency checks cleanly, or did it require relaxed constraints or fallback assumptions?
5. **Pricing Confidence:** Are the GCP rates confirmed via catalog SKU pricing, or estimated / carried at cost?

Presenting a single opaque percentage (e.g. 84.2%) to customers invited comparisons between bills that the underlying data could not support. Furthermore, without a downstream consumer acting on the number (such as an automated gate or triaging router), scalar numbers drift.

---

## 2. The 5-Part Cascade Model

If confidence scoring is reintroduced for internal decision-making (e.g., routing low-confidence items to manual human review), it should follow this multi-dimensional breakdown:

### Component Definitions

- **`data_confidence` (0.0 – 1.0):**
  Completeness of input fields. Evaluated during ingestion/contract validation. Decreased when required dimensions (such as instance type or product codes) are unavailable in the source CUR.
  
- **`classification_confidence` (0.0 – 1.0):**
  Catalog lookup and service identity resolution quality. Evaluated during Phase 1 (`classify_mechanics.py`, `service_classifier.py`).

- **`mapping_confidence` (0.0 – 1.0):**
  Quality of the GCP candidate chosen. Evaluated during Phase 2. Capped by candidate tier:
  - *Tier 1 (Perfect):* Exact 1:1 functional equivalent (cap: 1.0).
  - *Tier 2 (Equivalent):* Minor technical compromise (cap: 0.90).
  - *Tier 3 (Compromise):* Meaningful architectural gap (cap: 0.72).
  - *Tier 4 (Unsupported):* Passthrough or fallback (cap: 0.40).

- **`validation_confidence` (0.0 – 1.0):**
  Derived from gate verification in Phase 5 (`validate_fix.py`). Measures how cleanly the row passed hard and soft validation checks.

- **`pricing_confidence` (0.0 – 1.0):**
  Confirmed rate card SKU match vs. estimated / fallback pricing.

---

## 3. Floor Effect & Aggregation

A key insight of the cascade model is the **floor effect**:
A projection cannot be considered "high confidence" if any single critical component is severely compromised, even if the other four components score 100%.

In `confidence_engine.py`, the combination was formulated as:
```python
# Weighted average of components
weighted = (
    data_confidence * 0.20 +
    classification_confidence * 0.25 +
    validation_confidence * 0.20 +
    effective_mapping * 0.25 +
    pricing_confidence * 0.10
)

# Floor effect: weakest component pulls down overall score
worst = min(components.values())
if worst < 0.50:
    overall = weighted * 0.60 + worst * 0.40
elif worst < 0.70:
    overall = weighted * 0.80 + worst * 0.20
else:
    overall = weighted
```

---

## 4. Prerequisites for Future Use

Before any confidence engine is wired back into the pipeline:
1. **Identify the Consumer:** Specify the exact consumer before generating the number (e.g. routing rows with `overall_confidence < 0.70` to an `outlier_triage` queue or human review flag).
2. **Keep Outputs Internal / Actionable:** Do not display raw percentages in executive summaries without contextual definitions.
3. **Calibrate Systematically:** Base components on empirical error rates across test corpora rather than arbitrary hand-typed constants.

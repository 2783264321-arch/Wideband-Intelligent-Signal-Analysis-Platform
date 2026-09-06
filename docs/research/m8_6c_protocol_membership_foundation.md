# M8.6C-1 Protocol & Membership Foundation — Gate Evidence

Date: 2026-09-06
Branch: `feature/m8-6c-dataset-benchmark-ui-real-evaluation`
Base: `feature/v1-core @ 1036e51b70a351dfcae0330c8e5e725844640bc8`
Spec: `docs/superpowers/specs/2026-09-06-m8-6c-dataset-benchmark-ui-real-evaluation-design.md`
Plan: `docs/superpowers/plans/2026-09-06-m8-6c-1-protocol-membership-foundation.md`

## Summary

C-1 implements protocol v2 (`physical_tf_detection_ap_v2`), exact per-Recording Ground-Truth deduplication, protocol-aware benchmark creation/execution, Ground-Truth accounting, the derived Imported Batch Catalog, and the exact imported-batch resolver. Raw GroundTruth rows and the raw `recording_manifest_hash` are untouched. No formal 2500-sample DatasetEvaluation was started in C-1.

## Protocol semantics verified

- `physical_tf_detection_ap_v1`: `gt_duplicate_policy=keep_all`, `ground_truth_view=raw`.
- `physical_tf_detection_ap_v2`: `gt_duplicate_policy=exact_physical_class_dedup`, `ground_truth_view=evaluation_canonical`.
- v2 exact-duplicate key is per Recording: `.17g` canonicalized `t_start_s`, `t_end_s`, `f_low_hz`, `f_high_hz`, plus exact `class_id`. No epsilon, tolerance, IoU dedup, or `class_name` identity.
- All v2 metrics (localization AP, class-aware AP, operating diagnostics, matched classification, per-class) consume one canonical GT view.
- `DatasetEvaluationItem.gt_count` is protocol-specific at creation: v1 = raw count, v2 = canonical count. The worker asserts rather than recomputes it.
- Old v1 evaluations whose stored `protocol_config_json` predates the new `gt_duplicate_policy`/`ground_truth_view` keys keep raw-GT semantics (worker dispatches by `evaluation_protocol`).
- New DatasetEvaluations created through the standard API default to v2.

## Automated verification (fresh run on final tree)

Command: `python -m pytest -q` from `backend/`

- passed: 298
- failed: 0
- warnings: 3
- exit: 0

New focused modules included:

- `backend/tests/test_benchmark_protocol.py` — v2 exact dedup, v1 keeps duplicates, minuscule coordinate difference is not a duplicate, same box/different class is not a duplicate, same box/different Recording is not a duplicate, canonical order independent of input order.
- `backend/tests/test_benchmark_imported_batch.py` — catalog filters, resolver exact mapping, duplicate item key, duplicate Recording, mixed pipeline, incomplete manifest, mismatched manifest provenance, not found.
- Added v2/v1 worker tests, v2-default membership tests, and catalog/resolver API tests in the existing benchmark test modules.

M8.6B batch-import regression remains green (atomic import, idempotency, semantic fingerprint) in the same full-suite run.

## Real-database read-only dry-run

Settings pointed at the intended real local database (verified by printing `database_url` and resolving the SQLite absolute path):

```
database_url = sqlite:///D:/LGFiles/Wideband Signal Analysis Platform/Wideband-Intelligent-Signal-Analysis-Platform/platform.db
```

Read-only catalog + resolver results:

```
fingerprint c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5
dataset SpaceNet test spacenet_14
pipeline zoomspec_yolo26n_aug_combined_frn_v3 1.0.0
manifest 91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b
coverage 2500 / 2500
missing 0 conflicts 0
unique_runs 2500
catalog entries for fingerprint: 1 (ready=True)
```

Sample 0 exact batch-run membership: `manifest_order 0`, recording name `0`, `analysis_run_id run_14db9792067e4c9eb6a3ed00408504a1`, `item_key 000000`, batch fingerprint `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`. The older M9.0 single-sample imported run shares the pipeline identity but carries no batch `import_fingerprint`, so the resolver selected only the M8.6B batch run.

Counts before and after the probe (two identical runs):

```
GroundTruth total = 20019 (raw SpaceNet/test/spacenet_14 GT = 20018)
DatasetEvaluation total = 0
AnalysisRun total = 2507 (unchanged across the read-only runs)
DetectionResult total = 33571 (unchanged across the read-only runs)
```

The dry-run performed reads and resolution only; it did not call `create_evaluation`, `start_evaluation`, or `execute_benchmark`.

## Statement

No formal 2500-sample DatasetEvaluation was started in C-1.

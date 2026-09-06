# M8.6B Batch Analysis Package Acceptance

Date: 2026-09-06
Feature: `feature/m8-6b-batch-package`
Parent spec: `docs/superpowers/specs/2026-09-06-m8-6b-batch-analysis-package-design.md`
Implementation plan: `docs/superpowers/plans/2026-09-06-m8-6b-batch-analysis-package.md`

## 1. Scope

M8.6B turns the frozen historical dataset-scale detection result set into ordinary platform rows:

```text
historical dataset-scale detections
  -> Batch Analysis Package v1
  -> cross-machine Recording validation
  -> standard imported AnalysisRun
  -> exact Recording -> AnalysisRun mapping
```

M8.6B does **not**:

- create or run `DatasetEvaluation`;
- compute dataset mAP;
- add frontend/UI;
- run live inference;
- perform M8.6C benchmark or parity work.

M8.6B stops after the exact `Recording -> AnalysisRun` mapping exists.

## 2. Implementation identity

- shared starting baseline: `feature/v1-core` @ `d93913c98c3cb528de4cda831bdf583f25f4ee29`
- feature branch: `feature/m8-6b-batch-package`
- implementation/exporter HEAD used by the real package: `4af5444eddeb778057f369a2b011fd19c0f21762`

The real ZIP records `transport_provenance.platform_repo_commit = 4af5444eddeb778057f369a2b011fd19c0f21762`. The final acceptance documentation commit happens after this HEAD, so the final feature HEAD differs from the exporter HEAD; the ZIP's recorded platform commit remains `4af5444eddeb778057f369a2b011fd19c0f21762`.

## 3. Server frozen assets

All six hashes were recomputed on the server during Task 11 and matched exactly.

| Asset | Path | SHA256 |
| --- | --- | --- |
| Historical predictions | `/root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl` | `950ad87ec355169b1904da4364f296ade5854926d4e02dc83049d9585859efcd` |
| Test manifest | `/root/autodl-tmp/Claude/reports_claude/test_manifest.json` | `ea5b41d0cd6b3393be75ece3f3bbc8aee38e782ef421e8cd0d1b3e580839f5b6` |
| Detector checkpoint | `/root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt` | `eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311` |
| FRN checkpoint | `/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt` | `da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67` |
| Frozen pipeline config | `/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml` | `030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec` |
| Historical metrics report | `/root/autodl-tmp/Claude/reports_claude/test_full_val_map_augv3.json` | `b951a37f044c8e3e9d14be08c8caa1d20714ea251980230045de0e13efa6cdc2` |

## 4. Server export

- pipeline: `zoomspec_yolo26n_aug_combined_frn_v3` v`1.0.0`
- dataset: `SpaceNet` / `test`
- label space: `spacenet_14`
- server source dataset: `/root/autodl-tmp/SpaceNet_Dataset/advanced/test` (.bin 2500, .json 2500)
- test_ids: 2500; prediction rows: 33373; prediction sample IDs: 2500

Export summary:

```text
expected_samples = 2500
exported_items = 2500
source_prediction_rows = 33373
zero_detection_items = 0
unexpected_sample_ids = 0
missing_dataset_samples = 0
fingerprint_failures = 0
```

- `recording_manifest_hash`: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- `batch_import_fingerprint_v1`: `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`
- server archive: `/root/autodl-tmp/m8_6_exports/zoomspec_yolo26n_aug_combined_frn_v3-spacenet-test.analysis-batch.zip`
- byte size: 3902043
- archive SHA256: `3f1f10ee21b61e7ad5207fb3d27b14c414189d3b9d941475ec6c87d07d44306d`
- archive members: 5001 (batch_manifest.json + 2500 child manifests + 2500 child detections)
- forbidden members: 0 (no .bin, no checkpoint, no artifacts, no per-child `metrics.json`)

## 5. No-inference evidence

Task 11 export ran with `CUDA_VISIBLE_DEVICES=""`:

- GPU visible: NO
- model inference: NO
- checkpoints: read only to compute SHA256; no model load, no YOLO forward, no FRN forward, no AHLP inference, no STFT/LS-STFT recomputation

The result source is entirely the frozen `test_val_detections_augv3.jsonl`. `LegacyDetectionAdapter` performs only canonical contract conversion (bbox/class/confidence mapping and validation), never inference.

## 6. Transport provenance (observed in the real archive)

Read from the transferred ZIP `batch_manifest.json` (read-only):

- `exporter_version`: `batch_analysis_package_v1`
- `platform_repo_commit`: `4af5444eddeb778057f369a2b011fd19c0f21762`
- `export_timestamp`: `2026-09-06T07:45:07.740948+00:00`

These transport fields do not enter `batch_import_fingerprint_v1`.

## 7. Historical reference (reference only)

Observed in the real outer manifest:

- `reference_only`: true
- `report_sha256`: `b951a37f044c8e3e9d14be08c8caa1d20714ea251980230045de0e13efa6cdc2`
- `images`: 2500
- `canonical_ground_truth`: 19962
- `predictions`: 33373
- `recorded_map50`: 0.49706861157413673
- `recorded_map50_95`: 0.37325127587379914

These are **historical reference only**. They are **not** M8.6B platform-computed `DatasetEvaluation` metrics. M8.6B never ran the evaluator.

## 8. Local transfer gate

- local archive: `D:\LGFiles\Wideband Signal Analysis Platform\m8_6_exports\zoomspec_yolo26n_aug_combined_frn_v3-spacenet-test.analysis-batch.zip`
- local byte size: 3902043
- local SHA256: `3f1f10ee21b61e7ad5207fb3d27b14c414189d3b9d941475ec6c87d07d44306d`
- server SHA256: `3f1f10ee21b61e7ad5207fb3d27b14c414189d3b9d941475ec6c87d07d44306d`
- exact match: YES

## 9. Local dataset gate

- raw local dataset: `D:\LGFiles\Wideband Signal Analysis Platform\SpaceNet\test` (.bin 2500, .json 2500)
- platform DB: `sqlite:///D:\LGFiles\Wideband Signal Analysis Platform\Wideband-Intelligent-Signal-Analysis-Platform\platform.db`
- matching Recordings (`SpaceNet`/`test`/`spacenet_14`): 2500
- unique Recording names: 2500
- server `recording_manifest_hash`: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- local `recording_manifest_hash`: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- exact match: YES

The local hash was computed with the authoritative M8.6A `DatasetBenchmarkService.prepare_manifest("SpaceNet", "test", "spacenet_14")`, not a reimplementation.

## 10. GroundTruth duplicate-policy note

- platform raw GroundTruth rows: **20018**
- historical evaluation `canonical_ground_truth`: **19962**
- difference: **56**

The historical evaluator provenance states that exact physical/class duplicates are removed at evaluation time. M8.6B performed **no** GroundTruth mutation, **no** dedup, and draws **no** metric-parity conclusion.

The server/local `recording_manifest_hash` agree because both sides fingerprint the same raw SpaceNet Recording + GroundTruth snapshot. The historical `19962` is the historical evaluator's evaluation-time canonical/dedup policy, not the local Recording snapshot identity.

GT duplicate-policy parity is deferred to M8.6C. M8.6B did not modify GroundTruth to chase historical numbers.

## 11. Pre-import state

Before the real batch import:

- pipeline `zoomspec_yolo26n_aug_combined_frn_v3` v1.0.0 AnalysisRuns: 1
- pipeline DetectionResults: 13
- imported pipeline runs: 1
- previous M9.0 single-sample acceptance run: `run_90f01b90a8fa4300bdb9e46d4e561745`

That run was preserved, not deleted, and carries no batch `import_fingerprint`.

- DatasetEvaluation count: 0

## 12. First real import (observed)

Public API: `POST /api/imported-runs/batch`

- HTTP: 201
- `already_imported`: false
- `item_count`: 2500
- `detection_count`: 33373
- `created_runs`: 2500
- `existing_runs`: 0
- `created_detections`: 33373
- `matched_recordings`: 2500
- `missing_recordings`: 0
- `ambiguous_recordings`: 0
- `fingerprint_mismatches`: 0
- `import_fingerprint`: `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`
- `recording_run_mapping` length: 2500

Persisted verification (bulk query over the exact 2500 returned run IDs):

- returned runs found: 2500
- all `executor = imported`: YES
- all `status = completed`: YES
- all pipeline/version correct: YES
- all semantic import fingerprints correct: YES
- all `result_provenance` present: YES
- all `transport_provenance` present: YES
- all persisted `archive_sha256` = `3f1f10ee21b61e7ad5207fb3d27b14c414189d3b9d941475ec6c87d07d44306d`: YES
- all `transport_provenance.platform_repo_commit` = `4af5444eddeb778057f369a2b011fd19c0f21762`: YES
- detections across the exact 2500 run IDs: 33373
- zero-detection runs: 0

Mapping uniqueness:

- unique `recording_id`: 2500
- unique `recording_name`: 2500
- unique `analysis_run_id`: 2500

DB delta:

- AnalysisRun: +2500
- DetectionResult: +33373

## 13. Second real import (observed idempotency)

Same archive uploaded a second time through `POST /api/imported-runs/batch`:

- HTTP: 201
- `already_imported`: true
- `created_runs`: 0
- `existing_runs`: 2500
- `created_detections`: 0
- `import_fingerprint`: `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`
- `recording_run_mapping` length: 2500
- first/second exact mapping identical (canonical sort by recording_name/recording_id/analysis_run_id): YES
- second-import AnalysisRun delta: 0
- second-import DetectionResult delta: 0

This is real 2500-Recording-scale idempotency acceptance, not a tiny fixture.

## 14. DatasetEvaluation boundary

- before Task 12: DatasetEvaluation count = 0
- after Task 12: DatasetEvaluation count = 0
- automatic DatasetEvaluation created: NO
- benchmark worker started: NO

M8.6B stops at the `Recording -> AnalysisRun` mapping. M8.6C will freeze a `DatasetEvaluation`, run `physical_tf_detection_ap_v1`, compute dataset AP/mAP, and investigate historical parity.

## 15. Security / contract boundaries

Batch Analysis Package v1 safety limits:

```text
MAX_BATCH_ITEMS = 10000
MAX_TOTAL_DETECTIONS = 1000000
MAX_BATCH_UPLOAD_BYTES = 256 MiB
MAX_BATCH_EXPANDED_BYTES = 1 GiB
MAX_BATCH_MEMBERS = 25000
MAX_JSON_BYTES = 32 MiB
```

M6 single Analysis Package limits: unchanged.

- batch import validates all children before any ORM write;
- a successful import commits once in one transaction;
- any DB failure rolls back all new rows;
- the same semantic batch re-import is idempotent;
- a partial prior semantic state raises `BATCH_IMPORT_STATE_INCONSISTENT`;
- Recording matching uses `dataset + split + name` unique-candidate selection followed by mandatory physical-metadata + canonical-GroundTruth fingerprint verification;
- raw IQ SHA / IQ read for identity: NO.

## 16. Distinction between evidence classes

- **Observed real acceptance evidence**: the server export counts/hashes, the local transfer/dataset/fingerprint gates, and both real public-API imports above.
- **Design/unit-test guarantees**: archive safety limits, validation-before-write ordering, one-transaction commit, idempotency and partial-state semantics are proven by the focused M8.6B pytest suite referenced in the implementation plan and are documented there, not claimed here as server behavior.

The historical mAP values are not current platform metrics. M8.6C is not complete and no mAP parity is claimed.

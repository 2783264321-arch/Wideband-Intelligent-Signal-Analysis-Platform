# M9.0 First Real Legacy Analysis Package

> Record of the first real historical detection exported as an Analysis Package v1
> and accepted by the platform M6 importer.

## Package

| Field | Value |
|---|---|
| Sample stem | `0` (SpaceNet advanced/test) |
| GT signals | 6 |
| Historical predictions used | 13 |
| Detections adapted | 13 |
| Package path | `/root/autodl-tmp/m9_exports/0.analysis.zip` |
| Package SHA256 | `942ddd030d1d10ac3c9863da3c71a83c26685cd0e97d9511088a7273d2f5d9e6` |
| Package size | 2,039 bytes |

The package lives **outside the git repository** (`/root/autodl-tmp/m9_exports/`) and is not committed.

## Provenance (in-package metrics.json)

- `legacy_prediction_sha256`: `950ad87ec355169b1904da4364f296ade5854926d4e02dc83049d9585859efcd`
- `detector_checkpoint_sha256`: `eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311`
- `frn_checkpoint_sha256`: `da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67`
- `config_sha256`: `030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec`
- `test_manifest_sha256`: `ea5b41d0cd6b3393be75ece3f3bbc8aee38e782ef421e8cd0d1b3e580839f5b6`

## Full-corpus historical metrics (recorded, not per-sample)

- scope: full historical SpaceNet advanced/test evaluation
- mAP50: 0.49706861157413673
- mAP50_95: 0.37325127587379914
- source report: `/root/autodl-tmp/Claude/reports_claude/test_full_val_map_augv3.json`

## M6 end-to-end verification

Verified against the unmodified platform importer on a clean SQLite DB:

1. Register SpaceNet advanced/test via M5 (`/api/datasets/spacenet/register`) → 2,500 recordings created.
2. Import `/root/autodl-tmp/m9_exports/0.analysis.zip` via M6 (`/api/imported-runs`) for recording `0`.
3. Result: `AnalysisRun(executor=imported, status=completed)`, pipeline `zoomspec_yolo26n_aug_combined_frn_v3` v1.0.0.
4. 13 `DetectionResult` rows stored and retrievable via `/api/analysis-runs/<id>/detections`.

M6 production code was **not modified**.
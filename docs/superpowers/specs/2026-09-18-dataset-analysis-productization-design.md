# Dataset Analysis Productization — Design (P3, 2026-09-18)

## Product invariant

- **Dataset Analysis**: `Dataset + Pipeline -> per-Sample AnalysisRuns`
  - membership = **ALL** samples of the Dataset.
  - Ground Truth is **not** required.
- **Evaluation**: `Dataset Analysis + usable GT -> metrics`
  - produced automatically **only** when the Dataset has complete GT coverage.

Ground Truth therefore determines whether an Evaluation is produced, never
whether a Dataset Analysis can run.

## Authority

- `DatasetModel.dataset_id` is the new authority for membership and analysis.
- `DatasetExperiment` remains the internal execution engine (coordinator, items,
  attempts, AnalysisRun creation, retry/recovery, concurrency, linked evaluation).
  Product terminology is "Dataset Analysis"; internal names are unchanged.
- `DatasetProjection` remains **legacy compatibility only** and is not used by
  the new dataset-first flow. `dataset_projection_id` and the
  `dataset_name/split/label_space` triple stay supported for legacy creation.

## Membership manifest

`backend/app/datasets/analysis_manifest.py` builds a frozen manifest from ALL
`RecordingModel` rows where `dataset_id == dataset_id`, ordered by
`sample_key`, then `name`, then `id`. It reuses
`benchmarks.manifest.build_recording_manifest`; samples without GT participate
with `ground_truth = ()`. Membership never filters on `has_ground_truth`.

## Separation of states

After all items complete:

- complete GT coverage (`ground_truth_sample_count == sample_count > 0`) ->
  existing linked `DatasetEvaluation` flow (`dataset_evaluation_id` set).
- incomplete or absent GT -> Dataset Analysis is marked `completed` with
  `dataset_evaluation_id = NULL`. No partial-GT subset evaluation in V1.

`DatasetEvaluationModel.dataset_id` mirrors the experiment's `dataset_id` for
first-class analyses; `DatasetExperimentModel.dataset_id` is authoritative.

## Compatibility

- Additive, idempotent, non-destructive migration only (two nullable columns +
  indexes + unambiguous backfill via projection members).
- Single-sample analysis, Standard Local CPU, and all projection/evaluation
  APIs are untouched.

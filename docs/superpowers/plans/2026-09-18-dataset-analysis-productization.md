# Dataset Analysis Productization — Plan (P3, 2026-09-18)

Spec: `docs/superpowers/specs/2026-09-18-dataset-analysis-productization-design.md`

## Backend (Commit 1)

1. `datasets/analysis_manifest.py`: all-sample frozen manifest from `dataset_id`.
2. Models: add nullable `DatasetExperimentModel.dataset_id` and
   `DatasetEvaluationModel.dataset_id` (ORM FK to `datasets.id`).
3. Migration `upgrade_p3_dataset_analysis_authority`: ALTER ADD COLUMN x2,
   indexes, unambiguous backfill via `dataset_projection_id` members; ambiguous
   left NULL. Idempotent, additive.
4. `DatasetExperimentCreate.dataset_id`; create resolves `DatasetModel`, derives
   name/split/label_space, builds the first-class manifest, persists `dataset_id`;
   conflicting redundant legacy fields fail clearly.
5. `revalidate_frozen_identity`: rebuild the first-class manifest when
   `dataset_id` is set, else preserve legacy projection/triple behavior.
6. Service: `experiment_evaluation_eligible` (all items GT) and
   `mark_experiment_completed_without_evaluation` (generation-fenced).
7. Coordinator `_finish_inference`: eligible -> existing evaluation flow;
   ineligible -> completed without evaluation. `_ensure_evaluation` passes
   `dataset_id`.
8. `prepare_evaluation(dataset_id=...)` sets the evaluation's `dataset_id` and
   uses the identical first-class frozen manifest.
9. `GET /api/dataset-experiments?dataset_id=` filter; read model exposes
   `dataset_id`.
10. Focused tests for A–N (manifest 0/partial/full GT, create/derive/persist,
    conflict, revalidation drift, eligibility, no-GT/partial completion,
    complete-GT linkage + evaluation `dataset_id`, list filter, legacy triple,
    Standard Local CPU auto).

## Frontend (Commit 2)

1. API client/types: dataset-filtered analysis list; product types.
2. Dataset Detail: `Analyses` tab + `Analyze Dataset` modal (pipeline default
   STFT Energy; execution control reused; advanced max-concurrency collapsed);
   Start Analysis = create + run, then open analysis.
3. Product terminology (`Dataset Analysis`), analysis detail progress and
   sample/result drill-down (`/spectrum/:recordingId?run=:runId`,
   `/samples/:recordingId`), GT/no-GT evaluation states.
4. Focused tests + manual smoke against a DB copy (small GT and no-GT datasets;
   real SpaceNet check without a full 2500 run).

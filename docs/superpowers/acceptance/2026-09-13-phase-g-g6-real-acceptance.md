# Phase G G6 — Real Acceptance Evidence

Date: 2026-09-13

Status: G6 real acceptance executed successfully. Phase G seal pending independent
audit.

## A. SHAs

| Field | Value |
|---|---|
| Sealed G5 production base | `3d9f47b3e9ccbdd4147cf858712202396c256d8d` |
| Sealed G6 plan commit | `b553198039bb75e7ed91df9467e2e86bde5eb9e2` |
| G6 registration tooling | `0bdb074442162ff5ee725f065ad3d48d8165337d` |
| G6 seal-hardening test | `f75195a45b838f0a235534b97f807983d0ee6add` |
| G6 acceptance driver | `081a4102975957dedbcd4d02d05a1ede94e4af24` |
| G6 artifact gitignore | `0c769cb0fc11959d74f070a94923c9509ee01a4f` |
| Final code HEAD before evidence commit | `0c769cb0fc11959d74f070a94923c9509ee01a4f` |
| Acceptance evidence commit | this document (the commit that adds it) |

Note: the acceptance-tooling/plan commits are later evidence/tooling SHAs, not the
sealed G5 production base. No production Python changed in G6.

## B. Server / Runtime Identity

| Field | Value |
|---|---|
| Platform | `Linux-5.15.0-78-generic-x86_64-with-glibc2.35` (x86_64) |
| Control-plane interpreter | `/root/autodl-tmp/WISA-m9-2-implementation/.venv/bin/python` (Python 3.12.3) |
| ML interpreter | `/root/miniconda3/bin/python` (Python 3.12.3) |
| torch | `2.8.0+cu128` (CPU used by the local_cpu descriptor) |
| ultralytics | `8.4.114` |
| numpy | `2.3.2` |
| scipy | `1.18.0` |
| Runtime generation fingerprint | `a1237f8faae7` (matches the sealed runtime_ref) |
| Acceptance database | `sqlite:////root/autodl-tmp/WISA-m9-2-implementation/g6_acceptance.db` |

## C. Memory Evidence (cgroup v2)

| Field | Value |
|---|---|
| memory.max | `96636764160` (~90 GiB; cgroup was expanded before this run) |
| initial memory.current | `23659905024` |
| peak memory.current | `45163622400` |
| final memory.current | `45163622400` |
| MEM-1 (`headroom >= 1 GiB`) | PASS |
| MEM-2 (`current > max - 256 MiB`) | not triggered |

## D. Three Real SpaceNet Recordings

Registration tooling: `scripts/g6_register_spacenet_three.py` (external-path
registration; no IQ copied).

| manifest_order | name | recording_id | external_path | sample_rate_hz | center_frequency_hz | num_samples | duration_s | GT count |
|---|---|---|---|---|---|---|---|---|
| 0 | `0` | `rec_2062e474740f462ea5e4100cc4db5954` | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/0.bin` | 50,000,000 | 2,455,000,000 | 7,500,000 | 0.15 | 6 |
| 1 | `1` | `rec_b7354e21e02b4cbda503f7f5a7ce0bba` | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/1.bin` | 50,000,000 | 2,443,000,000 | 5,000,000 | 0.10 | 10 |
| 2 | `2` | `rec_ae9105e4a4c04b7bb95a9e2a656e4901` | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/2.bin` | 20,000,000 | 2,421,000,000 | 1,200,000 | 0.06 | 6 |

| Field | Value |
|---|---|
| dataset identity | `SpaceNet` / `test` / `spacenet_14` |
| manifest hash | `1ce5a92457ad1dada176cb4ba00754c16e0c4d9c621d77a8ff72d273ba0f8812` |
| manifest expected_recordings | 3 |
| manifest order | `[(0,"0"),(1,"1"),(2,"2")]` |

## E. Plugin / Release / Asset / Certificate Identity

| Field | Value |
|---|---|
| plugin_id / plugin_version | `cpn_bandwidth_tier` / `1.0.0` |
| model_release_id | `golden` |
| output label space | `cpn_bandwidth_tier_v1` (0 Narrow / 1 Mid / 2 Wide) |
| asset_manifest_sha256 | `7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb` |
| detector_checkpoint | `/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt` (`eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311`) |
| ls_stft_normalization | `/root/autodl-tmp/release/support/normalization_ls_stft.json` (`9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f`) |
| execution certificate | plugin `cpn_bandwidth_tier` `1.0.0`, release `golden`, executor `local_cpu`, device `cpu`, precision `float32`, runtime_ref `local:autodl_primary:cpu:a1237f8faae7`, evidence_ref `m9_2_fa2_cpn_local_cpu_acceptance`; `is_certified == True` |
| runtime descriptor | `local_cpu / cpu / None / float32`, `environment_ref=/root/miniconda3/bin/python`, `environment_label=local:autodl_primary:cpu:a1237f8faae7` |

## F. Real DatasetExperiment

| Field | Value |
|---|---|
| experiment_id | `exp_1ba0364e9e404faead699162f3053d16` |
| status | `completed` |
| executor | `local_cpu` |
| protocol | `physical_tf_detection_ap_v2` |
| max_concurrency | 1 |
| dataset_evaluation_id | `eval_103e87b24f3e48cb87be229eaaa06a75` |
| pre_run_ids | `[]` (fresh acceptance DB) |

## G. Real Items / Attempts / Runs

| manifest_order | item_id | attempt_id | attempt_number | analysis_run_id | Run status | executor | worker_pid | DetectionResults |
|---|---|---|---|---|---|---|---|---|
| 0 | `expitem_df01e6a6ada54fe6ac85739e101edad0` | `expattempt_858357433b2b45d1bc73d7d4e0cf1b4e` | 1 | `run_05f8479298b441509d647d50cfcf975e` | completed | local_cpu | 3012 | 17 |
| 1 | `expitem_1265ed0c059f4509874d263751317342` | `expattempt_29e3562fb6fe4709b8d29565c8af496f` | 1 | `run_36d39a9b65d443e29fa2ce3b2024cfa9` | completed | local_cpu | 3381 | 29 |
| 2 | `expitem_1f4beacca0824b4dbd1c7ce091b794b3` | `expattempt_73cdd631371446819e37db94d4b4de73` | 1 | `run_f0e159ed00a2425aa2abe8ff7ee02439` | completed | local_cpu | 3750 | 13 |

- exactly 3 Items, exactly 3 Attempts, exactly 3 new AnalysisRuns;
- all 3 Runs `completed`, all `local_cpu`;
- DetectionResults persisted: 17 + 29 + 13 = 59 (real CPN inference; run for
  recording `0` matches the F-A2 reference count of 17).

## H. Historical-Run Isolation

| Field | Value |
|---|---|
| pre_run_ids | `[]` |
| final run ids | `{run_05f8479298b441509d647d50cfcf975e, run_36d39a9b65d443e29fa2ce3b2024cfa9, run_f0e159ed00a2425aa2abe8ff7ee02439}` |
| intersection | `set(final) & set(pre) == empty` (asserted in the driver) |

## I. DatasetEvaluation

| Field | Value |
|---|---|
| evaluation_id | `eval_103e87b24f3e48cb87be229eaaa06a75` |
| status | `completed` |
| expected_recordings | 3 |
| evaluated_recordings | 3 |
| missing_recordings | 0 |
| coverage | 1.0 |
| included_items | 3 |
| manifest hash | `1ce5a92457ad1dada176cb4ba00754c16e0c4d9c621d77a8ff72d273ba0f8812` |

## J. Localization Metrics

| Metric | Value |
|---|---|
| localization.ap50 | `0.9423942394239424` |
| localization.ap50_95 | `0.6320841327830262` |
| operating.tp | 21 |
| operating.fp | 38 |
| operating.fn | 1 |
| operating.precision | 0.3559322033898305 |
| operating.recall | 0.9545454545454546 |
| operating.f1 | 0.5185185185185185 |

## K. Classification Inapplicability

| Field | Value |
|---|---|
| classification_applicable | `false` |
| classification_reason | `label_space_mismatch` |
| classification_on_matched | `null` |
| class_aware | `null` |
| per_class_metrics_json | `[]` |

Rationale: CPN outputs `cpn_bandwidth_tier_v1` (Narrow/Mid/Wide); the frozen
recordings are `spacenet_14`. No Narrow/Mid/Wide ↔ SpaceNet-14 classification
comparison was performed; only localization was evaluated.

## L. Acceptance Driver Outcome

- Command: `PYTHONPATH=backend .venv/bin/python scripts/g6_acceptance_driver.py`
- Exit code: `0`
- All fail-closed acceptance assertions passed before the success evidence JSON
  was written (`data/g6_work/g6_evidence.json`, gitignored).
- MEM-1 PASS; MEM-2 not triggered; no acceptance-owned cleanup was required.

## M. G5 Seal-Hardening

- `test_post_link_failure_projection_stale_generation_returns_fence_lost` added
  to `backend/tests/test_dataset_experiment_coordinator_evaluating.py`; GREEN
  with NO production change.

## N. Full Backend Regression

- `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q` →
  **`1680 passed, 28 skipped, 3 warnings`** in 85.49s; 0 failed, 0 errors.
- Baseline before this test was 1679 passed / 28 skipped; the new G5
  seal-hardening regression adds exactly 1 passing test.

## O. Scope Audit

Committed G6 files:

```text
docs/superpowers/plans/2026-09-13-phase-g-g6-real-acceptance.md
scripts/g6_register_spacenet_three.py
scripts/g6_acceptance_driver.py
backend/tests/test_dataset_experiment_coordinator_evaluating.py
.gitignore  (ignore G6 acceptance artifacts)
docs/superpowers/acceptance/2026-09-13-phase-g-g6-real-acceptance.md
```

No production Python, no models, no schema/migrations, no `remote_execution/*`,
no frontend, no metric/science definition changes. The acceptance DB
(`g6_acceptance.db`) and `data/g6_work/` are gitignored and not committed.

## P. Known Limitations

- `torch.cuda.is_available()` is `True` in this environment, but the acceptance
  used the certified `local_cpu`/`cpu`/`float32` descriptor only; every Run
  records `executor == local_cpu`.
- The `g6_acceptance.db` and evidence JSON are intentionally uncommitted.
- This document records real acceptance; Phase G seal still requires the
  independent acceptance audit.

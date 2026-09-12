# M9.2 F-A2 — CPN Bandwidth Tier Local CPU Acceptance Evidence

Evidence identifier (used as `evidence_ref` in
`backend/app/pipelines/execution_certificates.json`): **`m9_2_fa2_cpn_local_cpu_acceptance`**

This document records the real scientific-parity evidence and the real production
local CPU acceptance that justify issuing a single `local_cpu` ExecutionCertificate
for the second real plugin, `cpn_bandwidth_tier` `1.0.0` / model release `golden`.

## A. Commit under acceptance

| Field | Value |
|---|---|
| Accepted plugin / frozen-science tree | `4a1c429d702c4497ee493d7bf1940dce0732667f` (F-A1 HEAD, unchanged by F-A2) |
| Branch | `feature/m9-2-implementation` |
| F-A2 production Python diff | **NONE** — only `execution_certificates.json`, this doc, and the focused acceptance test |

## B. PluginVersion + ModelRelease + manifest

| Field | Value |
|---|---|
| `plugin_id` | `cpn_bandwidth_tier` |
| `plugin_version` | `1.0.0` |
| `model_release_id` | `golden` |
| output label space | `cpn_bandwidth_tier_v1` (0 Narrow / 1 Mid / 2 Wide) |
| `asset_manifest_sha256` | `7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb` |

## C. Assets — actual paths and independently computed SHA256

| Logical asset | Actual path | SHA256 |
|---|---|---|
| `detector_checkpoint` | `/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt` | `eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311` |
| `ls_stft_normalization` | `/root/autodl-tmp/release/support/normalization_ls_stft.json` | `9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f` |

Both digests were re-computed from the exact files used and match the frozen
AssetManifest V1 and the `verify_assets()` check. The manifest self-hash was also
recomputed and equals `7ab8a6a4…`.

## D. Runtime generation

| Field | Value |
|---|---|
| Frozen `runtime_ref` | `local:autodl_primary:cpu:a1237f8faae7` |
| Generation material digest (Python/NumPy/SciPy only) | `a1237f8faae7` (recomputed live; matches) |
| `environment_ref` (private) | `/root/miniconda3/bin/python` |

`runtime_ref` is **operator/platform-owned generation identity**. The digest is
generation material only; it is not an exhaustive hash of the worker environment.

## E. Interpreter

`/root/miniconda3/bin/python` (base prefix `/root/miniconda3`), distinct from the
control-plane interpreter
`/root/autodl-tmp/WISA-m9-2-implementation/.venv/bin/python` (prefix
`/root/autodl-tmp/WISA-m9-2-implementation/.venv`).

## F. Runtime environment versions (acceptance interpreter)

| Package | Version |
|---|---|
| Python | `3.12.3` |
| numpy | `2.3.2` |
| scipy | `1.18.0` |
| torch | `2.8.0+cu128` (CPU used) |
| ultralytics | `8.4.114` |

## G. Platform / device

| Field | Value |
|---|---|
| Platform | `Linux-5.15.0-78-generic-x86_64-with-glibc2.35` |
| Architecture | `x86_64` |
| `torch.cuda.is_available()` | `False` (device count `0`) |
| Execution device | `cpu` |

## H. Bounded real SpaceNet parity input provenance

| Field | Value |
|---|---|
| Source item | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/0.bin` (+ `0.json`) |
| Source format | `float16_interleaved_le` |
| Bounded copy | first `2,000,000` samples = first `8,000,000` bytes of the `30,000,000`-byte source |
| Bounded file SHA256 | `32fce4ac4ca17f5ad9edde7ae1a01ea5cb3480abf6ec9dbac005e1b1224cb270` |
| Sample rate | `50,000,000 Hz` (observation range 2430–2480 MHz) |
| Center frequency | `2,455,000,000 Hz` |
| Frequency bounds | `2,430,000,000 … 2,480,000,000 Hz` |
| Duration | `0.04 s` |
| Construction | byte-faithful prefix copy (`head`/first `N*4` bytes); no resampling, no synthesis |

## I. Reference path (accepted frozen science, no Plugin wrapper)

```
read_segment_from_path(real IQ)
  -> app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing.build_ls_stft_spectrogram(..., device="cpu")
  -> app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector.CPNDetector(real checkpoint, device="cpu")
  -> detect_batch([spectrogram], batch_size=1)
  -> CPNProposal[]
```

## J. Plugin path (real generic seam, real science)

```
PluginRegistry.create_plugin_registry()
  -> PluginHandle("cpn_bandwidth_tier", "1.0.0")
  -> handle.load_runtime(assets={detector_checkpoint, ls_stft_normalization},
                         runtime_descriptor=RuntimeDescriptor("local_cpu","cpu",None,"float32"),
                         output_label_space=LabelSpaceService.get("cpn_bandwidth_tier_v1"))
  -> build_runtime(...) -> CPNBandwidthTierPipeline
  -> PluginRuntime.execute(recording, {}, workspace)
  -> DetectionPayload[]
```

No monkeypatching of `CPNDetector`, LS-STFT, or the IQ reader.

## K. Parity result

Same IQ samples, same recording metadata, same real checkpoint + normalization,
same CPU device for both paths.

| Metric | Reference | Plugin | Equal |
|---|---|---|---|
| proposal / detection count | 6 | 6 | yes |
| `t_start_s` | propagated | propagated | exact |
| `t_end_s` | propagated | propagated | exact |
| `f_low_hz` | propagated | propagated | exact |
| `f_high_hz` | propagated | propagated | exact |
| `bandwidth_tier` ↔ `class_id` | propagated | propagated | exact |
| `confidence` | propagated | propagated | exact |

Exact field equality was required and observed (`plugin == reference`; not a
loose tolerance). Plugin-only enrichments: `class_name` from the resolved
`cpn_bandwidth_tier_v1` LabelSpace and `scores == {"cpn": confidence}`.

Observed tiers: `0` (Narrow) and `1` (Mid). Matrix rows (reference):

| t_start_s | t_end_s | f_low_hz | f_high_hz | tier | confidence | class_name | scores |
|---|---|---|---|---|---|---|---|
| 0.0049987 | 0.0120168 | 2456982499.5005 | 2457024778.5151 | 0 | 0.7901537 | Narrow | {"cpn": 0.7901537} |
| 0.0137921 | 0.0399392 | 2471843385.1279 | 2472222376.8483 | 1 | 0.7195807 | Mid | {"cpn": 0.7195807} |
| 0.0050116 | 0.0119984 | 2456997374.0801 | 2457003002.9500 | 0 | 0.1829220 | Narrow | {"cpn": 0.1829220} |
| 0.0137921 | 0.0399392 | 2471843385.1279 | 2472222376.8483 | 0 | 0.1420416 | Narrow | {"cpn": 0.1420416} |
| 0.0138875 | 0.0400000 | 2471977828.1717 | 2472022742.4025 | 0 | 0.0120360 | Narrow | {"cpn": 0.0120360} |
| 0.0143016 | 0.0392171 | 2472942380.6037 | 2473060505.0193 | 0 | 0.0102884 | Narrow | {"cpn": 0.0102884} |

Command:
```bash
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python -m pytest \
  backend/tests/test_cpn_local_cpu_acceptance.py::test_real_frozen_cpn_reference_vs_cpn_plugin_parity -q
```

## L. Full untruncated SpaceNet Recording identity

| Field | Value |
|---|---|
| Dataset item | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/0.bin` (+ `0.json`) |
| Complete / untruncated | yes (no shortened copy used) |
| File size | `30,000,000` bytes |
| Sample count | `7,500,000` |
| Sample rate | `50,000,000 Hz` |
| Duration | `0.15 s` |
| Frequency metadata | `2,430,000,000 … 2,480,000,000 Hz` |
| Dataset label space | `spacenet_14` (input compatibility only) |

## M. Production subprocess execution chain

```
AnalysisRun
  -> LocalInferenceWorkerProvider.launch(run_id)         # /root/miniconda3/bin/python
  -> python -m app.analysis.local_inference_worker <run_id>
  -> PluginRegistry (create_plugin_registry)
  -> ModelReleaseStore.resolve("cpn_bandwidth_tier","1.0.0","golden")
  -> frozen manifest SHA check (== 7ab8a6a4…)
  -> WSP_LOCAL_ASSET_PATHS_JSON namespace
       cpn_bandwidth_tier/1.0.0/7ab8a6a4…  -> {detector_checkpoint, ls_stft_normalization}
  -> resolve_local_assets(...) -> verify_assets(...)
  -> PluginHandle.load_runtime(assets, RuntimeDescriptor(local_cpu/cpu/float32), output label space)
  -> CPNBandwidthTierPipeline -> real LS-STFT -> real CPNDetector
  -> AnalysisResultWriter -> persisted DetectionResultModel[]
```

The child worker carried `WSP_LOCAL_INFERENCE_RUNTIME_REF ==` the frozen
descriptor `environment_label`; no in-process `execute_local_run()` shortcut was
used as the acceptance evidence.

## N. Terminal status

`completed` (no `error_type`, no `error_message`). Run id `fa2_cpn_full`,
executor `local_cpu`.

## O. Persisted detection count

`17` detections. Class histogram: `Narrow × 9`, `Mid × 3`, `Wide × 5`. Every
class id was in `{0,1,2}`, every class name agreed with `cpn_bandwidth_tier_v1`,
and every row's `scores_json == {"cpn": confidence}`.

Frozen provenance attached to the run: `model_release_id = golden`,
`asset_manifest_sha256 = 7ab8a6a4…`, descriptor
`local_cpu / cpu / float32 / environment_label = local:autodl_primary:cpu:a1237f8faae7`.
No remote/GPU fallback occurred.

## P. Timing

Provider launch-to-terminal: `23.1 s`, `24.3 s`, `25.2 s` across three runs on the
full `7,500,000`-sample recording (deterministic `17` detections each). Bounded
parity (`2,000,000` samples, two model constructions) completed in ~15 s.

## Q. Certificate tuple

```
plugin_id        = cpn_bandwidth_tier
plugin_version   = 1.0.0
model_release_id = golden
executor         = local_cpu
device_type      = cpu
precision        = float32
runtime_ref      = local:autodl_primary:cpu:a1237f8faae7
evidence_ref     = m9_2_fa2_cpn_local_cpu_acceptance
```

No `remote_gpu` or `local_gpu` certificate was added. Existing ZoomSpec / dummy /
STFT Energy certificates are unchanged. After issuance the deployment-qualified
projection is `executors_supported = ["local_cpu"]`, `recommended_executor = null`
(the plugin still declares `recommended_execution = remote_gpu`, which is not
certified and is therefore not fabricated as recommended).

## R. No GPU / no training

No GPU was used. `torch.cuda.is_available()` is `False` and all science executed
on `cpu`. No training or fine-tuning occurred; the accepted frozen checkpoint and
normalization bytes were consumed read-only.

## S. Re-certification rule

`runtime_ref` is an operator/platform-owned immutable generation identity, not an
automatic fingerprint of the complete worker environment. Any material change to
the interpreter, its environment, or its dependencies (including dependencies not
represented in the Python/NumPy/SciPy digest, e.g. torch/ultralytics/SQLAlchemy/
Pydantic/FastAPI) requires the operator to issue a new `runtime_ref` and re-run
acceptance and certification. The `a1237f8faae7` digest covers only Python, NumPy,
and SciPy and must not be treated as exhaustive.

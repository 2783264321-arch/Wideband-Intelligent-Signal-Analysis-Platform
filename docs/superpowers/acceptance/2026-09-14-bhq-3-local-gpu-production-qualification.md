# BHQ-3 Local-GPU Production Qualification — PRE-CERTIFICATION EVIDENCE (C6)

Date: 2026-09-14
Stage: **PRE-CERTIFICATION (C6)**. This document contains only facts that exist
before certificate issuance. It does not contain post-certificate facts and does
not reference the C8 final-evidence commit.

Plan: `docs/superpowers/plans/2026-09-14-bhq-3-local-gpu-production-qualification.md`
(sealed baseline `589bf642b08276e6146cde4d1beb68e02cb39287`, amended by
**Amendment A1 — Cache-Aware Memory Admission**).

## A. Provenance

```text
Sealed production baseline:          c809eb02a5286737819136f9391d13949e8a117b
Original sealed plan SHA:            589bf642b08276e6146cde4d1beb68e02cb39287
Implementation branch:               feature/bhq-local-gpu

C1  bc1cbd4  feat: add local_gpu CUDA health probe to local inference provider
C2  562f23b  feat: declare and gate local_gpu capability for cpn_bandwidth_tier
C3  3df7bea  feat: declare and gate local_gpu capability for zoomspec pipeline
C4  842529f  test: add local_gpu pre-certificate fail-closed gate
C5  c036299  test: add real local_gpu hardware acceptance harness
A1-DOC     8c3f04d  docs: amend BHQ-3 memory admission gate (cache-aware)
A1-TOOLING ceeb709  test: add cache-aware BHQ-3 memory admission guard

Implementation head at C6:           ceeb70958431524ba884126b791fc509e5964d36
```

Note: SHAs above are shown as the exact 7-char abbreviations of the full commit
hashes listed in section A's git-log capture; the implementation head is the full
40-hex `ceeb70958431524ba884126b791fc509e5964d36`.

## B. Environment / GPU identity

```text
Platform          Linux-5.15.0-78-generic x86_64
GPU               NVIDIA GeForce RTX 5090
GPU memory total  32607 MiB  (idle at 0 MiB before/after each case)
Compute capability 12.0
Driver            580.105.08
CUDA (torch)      12.8
Control plane     /root/autodl-tmp/WISA-bhq-local-gpu/.venv/bin/python (torch-free)
ML interpreter    /root/miniconda3/bin/python (Python 3.12.3)
```

## C. Immutable local-GPU runtime identity (Task 6)

`scripts/bhq3_local_gpu_runtime_identity.py` (run under the ML interpreter):

```text
generation  = 7b958347b5af
runtime_ref = local:autodl_primary:gpu:7b958347b5af      (matches expected)
material:
  python 3.12.3
  torch 2.8.0+cu128   torch_cuda 12.8
  ultralytics 8.4.114
  numpy 2.3.2         scipy 1.18.0
  device_name NVIDIA GeForce RTX 5090   compute_capability 12.0
  driver_version 580.105.08             cuda_available true
RUNTIME_IDENTITY_OK local:autodl_primary:gpu:7b958347b5af
```

The digest is generation material, not an exhaustive environment hash; any
material interpreter/dependency/GPU change requires a new `runtime_ref` and
re-certification.

## D. Precision semantics (Task 1)

`scripts/bhq3_precision_dtype_probe.py` (run under the ML interpreter) on
SpaceNet test/2:

```text
LS-STFT:  hann_window_dtype=torch.float32  stft_window_dtype=torch.float32
          stft_input_dtype=torch.complex64 interpolate_input_dtype=torch.float32
CPN:      first Conv2d forward_pre_hook input dtype = torch.float32
FRN:      torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True)
Conclusion:
  precision="float16" is the certified CUDA MIXED-PRECISION deployment mode,
  NOT "every operation is FP16". Frozen stages may remain FP32 (LS-STFT, CPN,
  AHLP, postprocess) while FRN intentionally uses float16 autocast.
```

No scientific code was changed to force full FP16. Contract resolved; not
`PRECISION_CONTRACT_UNRESOLVED`.

## E. Golden assets (fail-closed)

```text
CPN manifest      7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb
  detector_checkpoint    eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311
  ls_stft_normalization  9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f

ZoomSpec manifest 16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08
  detector_checkpoint    eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311
  frn_checkpoint         da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67
  frozen_config          030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec
  ls_stft_normalization  9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f
```

Verified via the repo manifest parser + `verify_assets()` and independent SHA256;
the production worker re-verifies asset bytes fail-closed before inference.

## F. SpaceNet provenance

```text
root        /root/autodl-tmp/SpaceNet_Dataset/advanced  (test split)
stem 2      2.bin / 2.json   1,200,000 samples  20,000,000 Hz  center 2,421,000,000 Hz  0.06 s  6 GT
stem 0      0.bin / 0.json   7,500,000 samples  50,000,000 Hz  center 2,455,000,000 Hz  0.15 s  6 GT
recording label_space = spacenet_14 (input compatibility)
```

## G. Pre-certificate fail-closed gate (Task 7 / C4)

`backend/tests/test_local_gpu_pre_cert_gate.py` (6 passed, commit 842529f): with a
registered `local_gpu` provider AND the `local_gpu/cuda/float16` technical
capability declared, but **no** `local_gpu` certificate, the platform reports
`EXECUTION_NOT_CERTIFIED`, excludes `local_gpu` from the deployment-qualified
projection, and `AnalysisService.prepare_run(executor="local_gpu")` raises
`EXECUTION_NOT_CERTIFIED`. A plugin declaration cannot self-certify.
`executors_supported` is proven to derive from capability ∩ provider ∩ exact
certificate, never from legacy `cpu_supported`.

## H. Temporary in-memory-certificate hardware acceptance (Task 8)

Harness: `scripts/bhq3_local_gpu_acceptance.py` (temporary certificate constructed
in memory only; never persisted). Fresh process per case; real
`LocalInferenceWorkerProvider` → `/root/miniconda3/bin/python -m
app.analysis.local_inference_worker` → real CUDA science → `AnalysisResultWriter`.

| case | stem | run_id | status | executor | detections | wall s | worker VmHWM | GPU peak | gate |
|---|---|---|---|---|---|---|---|---|---|
| CPN | 2 | run_91284375ea79457da1b146c2acdd10bc | completed | local_gpu | 13 | 6.38 | 1,656,320 KB | 760 MiB | cache_guarded |
| CPN | 0 | run_33f7a6bafe2c426699765da4c4155438 | completed | local_gpu | 17 | 6.62 | 1,734,556 KB | 1054 MiB | cache_guarded |
| ZoomSpec | 2 | run_95838177f0174beda971af288b218c98 | completed | local_gpu | 11 | 8.03 | 2,147,040 KB | 834 MiB | cache_guarded |
| ZoomSpec | 0 | run_5efbc0b7f22940fa9371fae8f31b519e | completed | local_gpu | 13 | 18.15 | 2,661,920 KB | 1058 MiB | cache_guarded |

Class histograms (match BHQ-2 reference):

```text
CPN stem 2:  Narrow 10 / Mid 2 / Wide 1
CPN stem 0:  Narrow 9 / Mid 3 / Wide 5
ZoomSpec stem 2: LoRa 4 / Zigbee 3 / AM 2 / SRRC16QAM 1 / FM 1
ZoomSpec stem 0: (13 dets) WiFi40QPSK 2 / FM 2 / LoRa 3 / Zigbee 1 / BLE2M 1 / SRRCQPSK 1 / WiFi40QAM 1 / WiFi20QPSK 1 / BLE1M 1
```

Runtime descriptors persisted: `local_gpu / cuda / 0 / float16`,
`environment_label = local:autodl_primary:gpu:7b958347b5af`.

## I. Memory evidence (Amendment A1)

Pre-launch gate modes: all four temp-cert cases `cache_guarded`; all four
direct-science oracle cases `raw`.

Per-case live monitor (baseline-relative), no aborts:

| case | samples | Δmax | Δoom | Δoom_kill | Δhigh | PSI full peak | PSI some peak | max committed_floor | min effective_headroom | max anon |
|---|---|---|---|---|---|---|---|---|---|---|
| CPN stem 2 | 15 | 0 | 0 | 0 | 0 | 0.19 | 0.19 | 3.074 GiB | 86.926 GiB | 2.695 GiB |
| CPN stem 0 | 16 | 0 | 0 | 0 | 0 | 0.08 | 0.08 | 3.092 GiB | 86.908 GiB | 2.714 GiB |
| ZoomSpec stem 2 | 22 | 0 | 0 | 0 | 1289 | 0.19 | 0.19 | 3.475 GiB | 86.525 GiB | 3.097 GiB |
| ZoomSpec stem 0 | 58 | 0 | 0 | 0 | 4265 | 0.72 | 0.72 | 4.031 GiB | 85.969 GiB | 3.652 GiB |

`high` events incrementing (expected reclaim/throttle) is NOT a failure; only
`max`/oom/oom_kill abort. No live-monitor abort occurred. PSI peaks are far below
the abort thresholds (full 10.0 / some 25.0).

Prior quiescence check (read-only, before this run): A1 gate `cache_guarded` on 7
samples over 120 s; PSI some/full avg10 = 0.00; `memory.events.high` Δ=0;
max/oom/oom_kill = 0; committed_floor ~1.9 GiB; effective_headroom ~94.7 GiB;
clean cache ratio ~0.98. Large clean page cache is allowed and is not a blocker.

## J. Direct-science oracle + parity (Task 9)

Committed guarded oracle `scripts/bhq3_direct_science_oracle.py` (parent
controller → Amendment-A1 admission → one science child → ~0.2 s cgroup monitor →
SIGTERM exact child on abort) re-ran the same frozen direct-science pipelines on
the same stems/assets/CUDA device. Parity: production local_gpu worker persisted
`DetectionResult`s vs oracle detections, matched by physical TF IoU (≥0.30).

| case | worker | oracle | matched | box exact | class agree | name agree | conf max drift | stage counts |
|---|---|---|---|---|---|---|---|---|
| CPN stem 2 | 13 | 13 | 13 | 13/13 | 13/13 | 13/13 | 0.000e+00 | equal (13/13) |
| CPN stem 0 | 17 | 17 | 17 | 17/17 | 17/17 | 17/17 | 0.000e+00 | equal (17/17) |
| ZoomSpec stem 2 | 11 | 11 | 11 | 11/11 | 11/11 | 11/11 | 0.000e+00 | equal (13/13/13/11) |
| ZoomSpec stem 0 | 13 | 13 | 13 | 13/13 | 13/13 | 13/13 | 0.000e+00 | equal (17/17/17/13) |

Result: **ALL_PARITY_OK** (exact equality; no gross divergence).

## K. Reproduction commands

```bash
cd /root/autodl-tmp/WISA-bhq-local-gpu
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_precision_dtype_probe.py
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_local_gpu_runtime_identity.py
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/bhq3_local_gpu_acceptance.py --all
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_direct_science_oracle.py --plugin cpn --stem 2 --out /tmp/bhq3_work/direct_science_oracle_cpn_stem2.json
# (repeat for cpn/0, zoomspec/2, zoomspec/0)
```

## L. What this pre-cert evidence does NOT claim

- No production certificate has been issued at C6 (C7 is the candidate).
- No post-certificate production `AnalysisRun` (Task 11) has been run at C6.
- `local_gpu` crash/startup recovery is NOT qualified here; it is deferred to
  BHQ-5. BHQ-3 certifies only the normal local_gpu execution path.

## M. Limitations

- `float16` denotes the CUDA mixed-precision deployment mode (section D).
- The runtime generation identity is generation material, not an exhaustive
  environment hash (section C).
- Resource peaks are externally observable only; the controller cannot read the
  child's torch allocator peaks (worker VmHWM and external `nvidia-smi` are used).

---

# C8 — FINAL BHQ-3 ACCEPTANCE EVIDENCE (finalization)

Date: 2026-09-14
Stage: **FINAL (C8)**. This finalizes the pre-certification evidence (C6) above
with post-certificate facts. It does **not** contain its own commit SHA; the final
BHQ-3 seal SHA is determined externally after this commit via `git rev-parse HEAD`.

## N. Provenance (all known before this finalization commit)

```text
Sealed Phase-G baseline:               c809eb02a5286737819136f9391d13949e8a117b
Original sealed BHQ-3 plan SHA:        589bf642b08276e6146cde4d1beb68e02cb39287
Amendment A1 (docs):                   8c3f04d15f38e32b034eaf06067f2dcb7cd43573
Amendment A1 (tooling):                ceeb70958431524ba884126b791fc509e5964d36
C1 local_gpu CUDA health probe:        bc1cbd4ae1f5d4508d1d3b4cd0a681646dec846c
C2 CPN capability/gate:                562f23b9c9d72c307ea8d6b1504fa4cc0679eb4d
C3 ZoomSpec capability/gate:           3df7beaa99f4e5328690424ecb33bdf6e32b2c49
C4 pre-cert fail-closed gate:          842529fe5aa7d30431563943609e20f7187ed2fd
C5 acceptance harness/tooling:         c0362993b57f5f4bc9ee379472eec195eb839c16
C6 pre-cert evidence:                  659f02afdb999f239a96918dc0343735254e22c0
C7 certificate candidate:              6e7aa096e116104846ee462df9070cf565c98cb4
C7A legacy test alignment:             2393677a1ffb1f78f45b1b2eb5af9e75a656999e

implementation_head_before_final_evidence = 2393677a1ffb1f78f45b1b2eb5af9e75a656999e
```

## O. Post-certificate production AnalysisRun (Task 11)

Real production path with the committed C7 certificate store
(`scripts/bhq3_local_gpu_production_acceptance.py --all`); fresh Amendment-A1
admission per case; live A1 monitor active.

| case | stem | run_id | status | executor | device_type | index | precision | runtime_ref | detections | wall s | worker VmHWM | GPU peak | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CPN | 2 | run_45f525d57da040bfb0444dfb1a5174aa | completed | local_gpu | cuda | 0 | float16 | local:autodl_primary:gpu:7b958347b5af | 13 | 6.90 | 1,653,704 KB | 760 MiB | cache_guarded |
| ZoomSpec | 2 | run_0bee48ffa7124c5f945ff11d3625603b | completed | local_gpu | cuda | 0 | float16 | local:autodl_primary:gpu:7b958347b5af | 11 | 8.17 | 2,147,036 KB | 834 MiB | cache_guarded |

Provenance per run: `model_release_id = golden`; asset manifest
`7ab8a6a4…` (CPN) / `16cc0534…` (ZoomSpec); runtime descriptor
`local_gpu / cuda / 0 / float16` with
`environment_label = local:autodl_primary:gpu:7b958347b5af`. **No** `local_cpu`
fallback, **no** `remote_gpu` fallback. CPN class histogram Narrow 10 / Mid 2 /
Wide 1; ZoomSpec stage counts 13/13/13/11.

## P. Post-certificate Amendment-A1 memory evidence (Task 11)

| case | samples | Δmax | Δoom | Δoom_kill | Δhigh | PSI full peak | PSI some peak | max committed_floor | min effective_headroom |
|---|---|---|---|---|---|---|---|---|---|
| CPN stem 2 | 16 | 0 | 0 | 0 | 4767 | 1.22 | 1.22 | 3.291 GiB | 86.709 GiB |
| ZoomSpec stem 2 | 22 | 0 | 0 | 0 | 1800 | 1.00 | 1.00 | 3.695 GiB | 86.305 GiB |

No `max`/oom/oom_kill; no live-monitor abort. `high` increments and page-cache
reclaim are expected. Pre-Task-11 quiescence: three consecutive clean A1
admissions (mode `cache_guarded`, PSI some/full avg10 ≤ 0.39, `high` stable).

## Q. Executor projection (Task 11)

For both plugins, the live `/api/pipelines` read model and an independent registry
recomputation agree:

| plugin | read_model_executors_supported | recomputed_supported | read_model_recommended_executor | recomputed_recommended |
|---|---|---|---|---|
| cpn_bandwidth_tier | ["local_gpu"] | ["local_gpu"] | null | null |
| zoomspec_yolo26n_aug_combined_frn_v3 | ["local_gpu"] | ["local_gpu"] | null | null |

`local_gpu` is deployment-qualified because the intersection exists
(capability ∩ registered local_gpu provider ∩ exact C7 certificate).
`recommended_execution` is `remote_gpu`, which is not in the supported set in this
local-only harness, so `recommended_executor` is `null` (matches the recomputation).
Qualification is derived from the certificate intersection, not the declaration.

## R. Full backend regression (Task 12)

```text
1759 passed, 29 skipped, 0 failed, 0 errors, 3 warnings in 84.42s
```
Skipped tests are hardware/acceptance-gated (require explicit env/assets) and are
not counted as passes.

Control-plane import boundary:

```text
.venv: find_spec('torch') is None; find_spec('ultralytics') is None  -> VENV_NO_ML_OK
```

## S. Scope audit (Task 13)

Full branch delta `c809eb0..HEAD` is limited to: `local_executor.py`; CPN/ZoomSpec
`definition.py` + `plugin.py`; `execution_certificates.json`; focused backend
tests; `scripts/bhq3_*.py`; the plan and this evidence document.

```text
NO_FROZEN_SCIENCE_CHANGED   (preprocessing/detector/ahlp/frn/postprocess untouched)
NO_FORBIDDEN_AREAS_CHANGED  (no db/migrations/remote_execution/dataset_experiments/evaluation/frontend/label_spaces/assets)
```
Post-C7 delta (`6e7aa09..HEAD`) is only the three legacy test-expectation
corrections (C7A).

## T. Known limitations

- `precision="float16"` denotes the certified CUDA mixed-precision deployment
  mode, not all-FP16 (section D).
- The runtime generation identity is generation material, not an exhaustive
  environment hash (section C).
- **`local_gpu` crash/startup recovery is NOT qualified here and is deferred to
  BHQ-5.** BHQ-3 certifies only the normal `local_gpu` execution path.
- The temporary pre-cert certificate was never persisted; only the C7 production
  certificates are committed.
- Resource peaks are externally observable only (controller wall time, worker
  VmHWM, external `nvidia-smi`).

## U. Final seal

The final BHQ-3 seal SHA is the SHA of the commit that adds this C8 finalization,
determined externally via `git rev-parse HEAD` after committing (not embedded here).

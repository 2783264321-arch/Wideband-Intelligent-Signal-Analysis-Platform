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

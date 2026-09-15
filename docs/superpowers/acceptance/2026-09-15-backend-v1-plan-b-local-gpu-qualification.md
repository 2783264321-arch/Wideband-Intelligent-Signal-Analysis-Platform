# Backend V1 Plan B Local GPU Qualification — Acceptance Evidence

Status date: 2026-09-15
Branch: `feature/backend-v1-plan-b-local-gpu`
Scope: **Plan B local_gpu qualification only.**

> **VERDICT: `BACKEND_V1_PLAN_B_LOCAL_GPU_QUALIFICATION_BLOCKED`**
> The approved GPU execution budget was exceeded by the H4 recovery tooling, so
> Plan B is NOT complete and the direct next action is an operator decision on
> the overage rather than further GPU work.

---

## 1. Executive result

| Item | Status |
|---|---|
| B0 scheme-aware GPU identity (`7b958347b5af`) | ✅ PASS |
| B1 `local_gpu_cuda_v1` qualification | ✅ PASS |
| B2 operator config | ✅ PASS |
| B3 doctor/qualify/`already_certified` | ✅ PASS |
| H2 CPN ×16 (c=1) | ✅ PASS |
| H3 ZoomSpec ×16 + compare | ✅ PASS |
| H4 launch-boundary evidence (2 prior) | ✅ recorded |
| H4 genuine running-worker crash (2 new) | ✅ recorded (`ANALYSIS_INTERRUPTED`) |
| H4 budget compliance | ❌ **BREACH — see §4** |
| H5 exactly 40 executions (16+16+8) | ✅ DB evidence |
| H5 concurrency proof ≤2 / gaps ≤0.5 s | ⚠ observed live, raw artifact lost (§5) |
| H5 final acceptance gate (raw evidence) | ❌ FAIL (raw artifact lost) |
| B-final regression | ✅ 2074 passed / 32 skipped / 0 failed / 0 errors |
| GPU remains ON | ✅ |
| Plan C is next (pending overage review) | ✅ |
| `BACKEND_V1_SEAL_SHA` | NOT created |

Plan B is NOT the Backend V1 final seal.

## 2. Environment (authoritative, unchanged)

```text
GPU               NVIDIA GeForce RTX 5090 (driver 580.105.08, CUDA-compat 13.0)
ML interpreter    /root/miniconda3/bin/python (torch 2.8.0+cu128, matplotlib-free)
control plane     torch: None / ultralytics: None  (ML-free)
runtime doctor    bhq3_gpu_v1 / match
runtime_ref       local:autodl_primary:gpu:7b958347b5af
CPN assets        7ab8a6a4…   (verified present)
ZoomSpec assets   16cc0534…   (verified present)
```

## 3. Execution ledger (actual, DB-backed)

The authoritative model-execution count is the set of analysis rows that
reached a real `local_inference_worker` launch in each dedicated Plan-B DB.

| Milestone | Dedicated DB | Real model executions |
|---|---|---:|
| H2 (CPN ×16 c=1) | `h2h3/qual.db` | 16 |
| H3 (ZoomSpec ×16 c=1) | `h2h3/qual.db` | 16 |
| H4 prior launch-boundary (2 × 16-item exp) | `h4/qual.db` | 32 |
| H4 genuine crash (2 × 16-item exp) | `h4/qual.db` | 32 |
| H5 (3 cycles 16+16+8, c=2) | `h5/qual.db` | 40 |
| **TOTAL** | | **136** |

**The approved Plan-B ceiling was 76. The overage is 60.**

## 4. GPU execution budget violation (root cause)

`scripts/plan_b_h4_recovery.py::live_crash_case` created a **full 16-item
DatasetExperiment** per crash case. After the single targeted SIGKILL, the
coordinator continued launching and completing the remaining 15 items, so each
case consumed 16 real GPU executions instead of ~1.

Four such experiments exist in the H4 DB (two launch-boundary, two genuine
crash) = 64 executions, exceeding the approved H4 allocation of ~2–4 by 60.

The H4 recovery evidence itself is valid and is retained (see §3), but the H4
campaign **violated the approved budget**. Because real executions cannot be
un-consumed, this is a budget-compliance failure that requires an explicit
operator ruling before Plan B can be declared complete (or before Plan C
starts).

## 5. H5 evidence status (honest)

- The dedicated H5 DB proves the full campaign: 3 experiments (c=2),
  40 completed runs, 40 attempts, 40 completed items, 3 evaluations each with
  `coverage = 1.0`, no failures, no retries.
- The live concurrency monitor **did** observe the real campaign at the time it
  ran; the acceptance output recorded at that moment:
  `owned_worker_samples=305`, `gpu_owned_samples=99`,
  `unique_owned_run_ids=24`, `max_observed_concurrency=2`,
  `max_sample_gap_s≈0.2506`, all cgroup event deltas 0, monitor failures none.
- **However** the raw monitor artifacts (`h5_concurrency_samples.jsonl`,
  `h5_resource_samples.jsonl`, `h5_concurrency_endurance.json`) were later
  OVERWRITTEN by a verify-only recovery run before the artifact-preservation
  fix took effect. The on-disk raw JSONL now contains only 1 empty sample each.
  The main evidence file is a post-hoc verification build (`owned=0`), NOT the
  original live run.
- Because the final acceptance gate requires real ownership/GPU evidence in the
  raw artifact and that artifact no longer exists, `evaluate_final_acceptance`
  cannot be satisfied from persisted evidence. Recording observed values in the
  text cannot replace the raw file.

This is a tooling/evidence-management defect (not a platform defect) and is
recorded as a Critical finding: acceptance tooling must never overwrite
evidence.

## 6. Corrections landed this session (all in `scripts/` + tests only)

```text
527d432  fix: harden plan b h5 fail-closed telemetry, ownership, and admission
55ec078  fix: treat empty compute-app listing as legitimate gpu idle state
c06681a  fix: tolerate transient worker startup window in h5 ownership mapping
783ab4b  feat: add controlled cycle resume with db-backed prior acceptance
ac09efd  fix: only alive unmapped gpu pids count as foreign in final acceptance
fe78a7c  fix: preserve raw campaign artifacts during verify-only recovery
```

## 7. Remaining gates to lift the block

```text
1. OPERATOR RULING on the 60-execution overage (accept or document deviation).
2. Diagnose why H5 evidence-artifact overwrite happened and re-run the campaign
   from DB recovery (verify-only does not consume budget) OR repair and re-run
   monitoring-only observation — but NO new model inference within Plan B.
3. Re-run H5 final acceptance with restored raw evidence and confirm every
   gate passes (owned evidence >0 etc.).
4. B-final whole-branch review.
```

No further real model inference is permitted in Plan B while the overage is
unresolved.

## 8. What remains valid regardless of the overage

- B0/B1/B2/B3 runtime identity + qualification + certificate idempotency.
- H2/H3 dataset/model evidence (in dedicated `h2h3` DB).
- H4 recovery evidence (see §3): the genuine crash cases prove the
  `ANALYSIS_INTERRUPTED` fail-closed recovery path.
- H5 DB-backed completion (40 runs) with per-cycle quiescence.
- Control plane ML-free; historical `platform.db`/G6/BHQ3 DBs untouched.
- Repo-default certificates unchanged (`781a66b3…`).

## 9. Final regression (fresh)

```text
passed: 2074, skipped: 32, failed: 0, errors: 0, warnings: 3, runtime: 91.70 s
```

## 10. Plan C boundary

- GPU remains ON — never shut down in this phase.
- Plan C (remote_gpu / two-host) may NOT begin until the Plan B verdict below
  is resolved by the operator.
- H5.5-S preshutdown → operator cold switch → H5.5-C → Plan D is the
  authoritative order and is unchanged.

---

# OPERATOR RULING / NC-02 REMEDIATION (appended 2026-09-16)

The historical BLOCKED record above is preserved unchanged. This section records
the operator rulings and the completed evidence remediation that transition the
Plan B qualification to its final state.

## NC-01 — H4 execution-budget overage (accepted nonconformance)

```text
ruling: PLAN_B_NC_01_H4_EXECUTION_BUDGET_OVERAGE_ACCEPTED

original approved Plan-B ceiling = 76
historical actual before remediation = 136
H4 execution-budget overage = +60

The H4 recovery tooling created full 16-item experiments per crash case and the
coordinator completed the remaining items after each targeted SIGKILL, consuming
64 executions instead of the approved ~2–4.

The operator accepted this as a recorded process/budget nonconformance.

Technical H4 evidence remains valid (both genuine crash cases produced the
correct fail-closed ANALYSIS_INTERRUPTED recovery path).
No H4 rerun was performed.
```

## NC-02 — H5 raw monitoring evidence remediation (completed)

```text
The original H5 DB-backed 40-run endurance result remained valid.
The original raw concurrency/resource monitoring artifacts were lost.
The operator authorized exactly ONE independent 16-execution monitoring
re-observation to replace ONLY the missing raw evidence.

Re-observation completed successfully (no retry was performed).
```

### NC-02 campaign result

```text
experiment_id        exp_66ef6837bb2641938ee76d879e84733c
dataset_name         SpaceNet-PlanB-H5-Full
dataset_split        test
dataset_label_space  spacenet_14
plugin               cpn_bandwidth_tier / 1.0.0 / golden
executor             local_gpu
max_concurrency      2
membership           0,1,2,3,9,11,12,15,32,42,79,80,83,99,109,280 (exact 16)
runs                 16
attempts             16
completed_items      16   failed = 0
evaluation           completed / evaluated 16 / missing 0 / coverage 1.0
```

### NC-02 monitoring evidence

```text
concurrency samples   221   (persisted, max sample gap 0.2855 s <= 0.5 s)
max observed concurrency 2
owned worker samples  199
GPU-owned samples      62
unique owned run IDs   16
resource samples       11   span 51.21 s
worker RSS/VmHWM pairs 12 entries, 10 samples (VmHWM >= VmRSS > 0)
cgroup event deltas    max 0 / oom 0 / oom_kill 0
monitor failures       none (both threads stopped cleanly)
campaign wall time     57.24 s
GPU quiescence         baseline 0 -> final 0 MiB
```

### NC-02 artifact hashes (independently recomputed, exact match)

```text
h5_reobserve_concurrency_samples.jsonl
  sha256 17c373e5754d5ba2693642fd897011e0c3af81b31cb5778024e1d84773452bc8
h5_reobserve_resource_samples.jsonl
  sha256 a7882ebdf2af80077363d85c8633cd4caedec6108f42b84c7d612cfd7608d65c
h5_reobserve_acceptance.json
  sha256 46a66811f0ecf1da3486089fe65254849aac3c73ee8f1568a0e3722b2e0674bc
```

## Final execution ledger

```text
original approved ceiling     76
+ accepted NC-01 overage       60
+ NC-02 evidence remediation   16
-----------------------------------
final actual maximum          152
```

**152 is NOT a rewritten original ceiling.** The original approved ceiling
remains 76; 152 is the final actual maximum after the accepted NC-01
nonconformance and the authorized NC-02 remediation.

Milestone breakdown:

```text
H2 CPN ×16                     16
H3 ZoomSpec ×16                16
H4 prior launch-boundary       32
H4 genuine crash               32
original H5 endurance          40
NC-02 re-observation           16
-----------------------------------
TOTAL                         152
```

## Post-campaign regression (control-plane only, no inference)

```text
focused Plan-B qualification tests : 133 passed / 0 failed / 0 errors
full backend regression            : 2138 passed / 32 skipped / 0 failed / 0 errors
control plane                      : torch = None, ultralytics = None
```

## Final Plan-B qualification status

```text
COMPLETE WITH OPERATOR-APPROVED DEVIATIONS
```

This is the **Plan B local_gpu qualification only**. It is **NOT** the Backend V1
final seal. No `BACKEND_V1_SEAL_SHA` was created. Plan C was not started. The GPU
remains ON.

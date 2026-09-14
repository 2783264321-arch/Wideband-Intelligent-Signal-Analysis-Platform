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

# WISA V1 Research-Server Artifact Workflow Design

- **Status:** Accepted design (documentation only)
- **Date:** 2026-09-16
- **Authoritative V1 base:** `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c`
- **Supersedes for V1 product boundary:** assumes the integration baseline as the production reference and reclassifies Remote-GPU as experimental.
- **Related existing specs:**
  - `docs/superpowers/specs/2026-09-06-m8-6b-batch-analysis-package-design.md`
  - `docs/superpowers/specs/2026-09-09-m9-1-task12f-remote-platform-integration-design.md`

---

## A. V1 Product Boundary

WISA V1 is a **Windows-hosted analysis and evaluation platform**. Its production-supported execution and integration surface is:

- `local_cpu` execution;
- imported single `AnalysisRun` (`POST /api/imported-runs`);
- imported batch `AnalysisRun`s (`POST /api/imported-runs/batch`);
- Algorithm Lab and dataset evaluation over imported/executed runs.

Heavy **GPU model execution is a research activity that happens independently on the research server / AutoDL**. It is not a production integration surface in V1.

Explicitly **experimental, and NOT V1 acceptance gates**:

- `local_gpu` execution;
- `remote_gpu` execution;
- Windows-driven remote `DatasetExperiment` orchestration.

Remote-GPU source code is **retained** in the repository; it is neither deleted nor wired into any V1 acceptance gate.

Rationale: the product requirement that motivated Remote-GPU ("Windows controls a remote GPU worker as part of a normal WISA run") has not materialized. The stable, testable, and supportable integration boundary is an immutable artifact transferred one way from the research server to Windows.

---

## B. Plan-C Closeout

Plan C is recorded as **partial experimental validation**, not a production gate.

Successful real model executions:

```text
C1 = 1
C2 = 1
```

What C1/C2 actually proved:

- **C1 (loopback mechanics):** the frozen control plane can drive the existing remote path on a single AutoDL host: profile load, strict host-key SSH, fixed argv/env, remote runtime-commit + asset verification, CUDA availability, `submit`/`status`/`work`, result download, same-`AnalysisRun` ingestion, write-once.
- **C2 (true two-host single run):**

  ```text
  Windows control plane
    → SSH
    → AutoDL
    → RTX 5090 / ZoomSpec golden
    → terminal envelope + analysis_result.zip
    → download
    → same Windows AnalysisRun ingestion
  ```

  One real remote batch, one item, one model execution, valid `payload_sha256`, exact envelope identity.

**C3 never executed model inference.** The C3 `DatasetExperiment` produced zero real model executions. Two distinct C3 pre-execution blocks were observed:

1. a **prepared/READY attestation SHA mismatch** — the Host-B READY artifact attested a superseded Host-A prepared artifact, so the handshake was rejected before any submission;
2. a **DatasetExperiment worker certificate-store wiring defect** — `backend/app/dataset_experiments/wiring.py::build_control_plane_dependencies()` constructed its `ExecutorRegistry` from repository certificates only, omitting the operator certificate store `<data_root>/runtime_certificates.json`. This caused the launch to fail closed with `EXECUTION_NOT_CERTIFIED` **before Transaction A / `AnalysisRun` creation**.

The second defect has an independently confirmed, tested repair candidate:

```text
ea7dc71d0f40bb690aa8ea0358bf5bb15233760d  (branch: feature/backend-v1-plan-c-c3-cert-wiring-fix)
```

Because `remote_gpu` is **no longer a V1 production requirement**, and therefore:

- do **not** promote `ea7dc71…` to `PLAN_C_PRELIVE_SHA`;
- do **not** continue C3/C4/C5;
- do **not** requalify the remote runtime;
- do **not** spend the remaining Plan-C execution budget.

All historical evidence (certificates, prepared/READY artifacts, job roots, envelopes, payloads) is **preserved read-only**.

Authoritative status of the historical refs:

```text
feature/backend-v1-plan-c-remote-gpu          = 4fa8e7dfbbecea39469d4dcd1cdfa772ac9e34ad
feature/backend-v1-plan-c-c3-cert-wiring-fix  = ea7dc71d0f40bb690aa8ea0358bf5bb15233760d  (unmerged)
```

---

## C. Authoritative Data Ownership

- **Windows WISA owns the single authoritative application database** (`platform.db`): recordings, ground truth, `AnalysisRun`s, detections, benchmarks, evaluations.
- **Research servers MUST NOT synchronize or replicate `platform.db`.** There is **no bidirectional SQLite synchronization** and no distributed transaction in this design.
- Server-side state is **research execution state only** (datasets, model runs, intermediate results, export packages). It carries no application-database authority.
- Cross-machine integration is performed **exclusively through immutable artifacts** (Batch Analysis Package v1 ZIPs).

Any future proposal that implies database replication, shared SQLite, or distributed commits is explicitly out of scope (see §J).

---

## D. Standard Data Flow

```text
RESEARCH SERVER (AutoDL / other)
  SpaceNet / other IQ
    → model / GPU execution
    → detections + execution metadata
    → Batch Analysis Package v1
    → immutable ZIP

TRANSPORT
  SCP / SFTP / operator download  (one-way, artifact only)

WINDOWS WISA
  ZIP
    → POST /api/imported-runs/batch
    → strict validation
    → Recording identity resolution (name/split/fingerprint)
    → imported AnalysisRuns + detections (atomic)
    → Algorithm Lab / dataset benchmark evaluation
```

Transport is **not execution orchestration** and carries **no application database authority**. It moves bytes.

---

## E. Existing Contract To Reuse

V1 **reuses the existing contract**; it does not invent a new package format.

- **Endpoint:** `POST /api/imported-runs/batch`
- **Format:** **Batch Analysis Package v1** (`BatchManifest.schema_version = 1`)
- **Design authority:** `docs/superpowers/specs/2026-09-06-m8-6b-batch-analysis-package-design.md`

Existing batch import already provides:

- strict schema validation (reject unknown/missing fields, duplicate JSON keys, non-finite numbers);
- dataset name + split + Recording name mapping;
- `recording_fingerprint_v1` verification;
- ground-truth identity verification;
- `recording_manifest_hash` support;
- result provenance and transport provenance;
- archive ZIP SHA256 (declared externally; see §F archive hash semantics);
- semantic import fingerprint;
- atomic database commit;
- fail-closed handling of partial/conflicting prior state, reported as `BATCH_IMPORT_STATE_INCONSISTENT` when partial/conflicting prior **semantic batch-import** state exists;
- idempotent complete re-import.

`BATCH_IMPORT_STATE_INCONSISTENT` is the Batch Import contract error for inconsistent prior import state. Remote-GPU request/conflict semantics (`REMOTE_REQUEST_CONFLICT`) are **not** part of the batch import contract and must not be conflated with it.

A second package format MUST NOT be introduced unless a **proven** gap requires it; any such gap is a separate design task.

---

## F. Server Export Contract

V1 provides a small **research-server exporter/adapter** that produces the existing Batch Analysis Package v1 from independently computed model results.

The exporter does **NOT**:

- own a WISA database;
- call Windows APIs during inference;
- schedule Windows jobs;
- require execution certificates;
- require a `RemoteProfile`;
- perform retries on behalf of WISA.

It **MUST preserve at minimum**:

- `batch_id`;
- pipeline id / name / version;
- dataset name / split;
- `label_space`;
- Recording names;
- Recording fingerprints (`recording_fingerprint_v1`);
- execution metadata;
- detections;
- code commit (research source revision);
- configuration hash where available;
- prediction/result hashes;
- declared artifact hashes (result/model/config artifacts known before archive completion);
- export timestamp and exporter version.

The exporter is a producer of the same bytes that Windows already knows how to consume. It is deliberately out of the execution-certificate and `RemoteProfile` machinery.

### Archive hash semantics (Batch Analysis Package v1)

- `BatchManifest` v1 does **NOT** contain an `archive_sha256` field.
- `artifact_sha256` refers to **declared result/model/config artifacts that are known before archive completion**.
- The **archive ZIP SHA256** is computed only **after the final ZIP exists**, and is recorded **externally** as transport/import evidence.
- Archive SHA256 is therefore **not self-embedded inside the archive manifest** and does **not** participate in the semantic `batch_import_fingerprint_v1`.
- Semantic idempotency remains independent of ZIP timestamps, compression settings, and repackaging: the same semantic content re-zipped (even with different bytes) resolves to the same import identity.

---

## G. Transport

V1 transport is intentionally simple.

**Phase 1 (default):** manual/operator one-way file transfer — `scp`, `sftp`, AutoDL download, or equivalent.

- The ZIP SHA256 is recorded and verified as transport/import evidence.

**Optional future improvement:** a **read-only** WISA "Pull Research Package" command/UI may use SFTP to list and download completed immutable packages.

Any such feature **remains artifact transfer only**. It MUST NOT evolve implicitly into Remote-GPU execution orchestration. Adding execution control (submit/work/retry) is out of scope and requires an explicit new product requirement and design (see §M).

---

## H. Frontend

The existing frontend already has a single-run import UI (`features/imports`, `ImportRunModal`).

V1 adds a **bounded batch-import surface** for the existing `POST /api/imported-runs/batch` API.

Minimum UI:

- select a Batch Analysis Package ZIP;
- upload/import;
- display:
  - `batch_id`;
  - `item_count`;
  - `detection_count`;
  - `created_runs` / `existing_runs`;
  - `matched_recordings`;
  - `already_imported`;
  - Recording → `AnalysisRun` mapping;
- surface structured validation failures.

The frontend MUST NOT contain SSH credentials, host keys, or server job controls.

---

## I. Evaluation

Imported runs remain **normal first-class `AnalysisRun`s** and are usable for:

- spectrum visualization;
- Algorithm Lab comparison;
- dataset evaluation/benchmarking.

The system judges **scientific output independently of where inference was executed**. Provenance records the origin (imported batch, research source commit, package hashes) without making remote execution a prerequisite for evaluation.

---

## J. Non-Goals

Explicitly excluded from V1:

- bidirectional database synchronization;
- distributed database transactions;
- automatic Windows → GPU scheduling;
- remote coordinator recovery;
- remote execution retry semantics;
- GPU cluster management;
- certificate-based Remote-GPU production qualification;
- synchronizing raw SpaceNet datasets between hosts;
- synchronizing conda environments.

---

## K. V1 Acceptance

A bounded end-to-end acceptance proves the production research workflow:

1. Execute a small real SpaceNet/ZoomSpec batch independently on AutoDL using the RTX 5090.
2. Export one Batch Analysis Package v1.
3. Transfer that immutable ZIP to Windows.
4. Import using `POST /api/imported-runs/batch`.
5. Require all package Recording identities to resolve exactly.
6. Require imported `AnalysisRun`s/detections to be created atomically.
7. Re-import the same package and prove idempotency.
8. Use the imported runs in Algorithm Lab / dataset evaluation.
9. Verify **no `platform.db` synchronization** occurred.
10. Verify **no `remote_gpu` executor was invoked**.

This acceptance proves the research workflow while keeping the WISA database the single authority.

---

## L. Status Of Remote-GPU Code

- `remote_gpu` remains in the repository as **EXPERIMENTAL**.
- C1/C2 evidence is retained as **architectural feasibility evidence**.
- The known DatasetExperiment certificate wiring defect is documented in §B together with repair candidate `ea7dc71d0f40bb690aa8ea0358bf5bb15233760d`.
- The repair candidate **remains unmerged** until Remote-GPU development is explicitly resumed.

No V1 acceptance gate depends on `remote_gpu`, `local_gpu`, execution certificates, or a `RemoteProfile`.

---

## M. Future Extension

Future releases may **restore production Remote-GPU** if a real product requirement appears:

> "A user clicks **Run** in WISA and WISA must directly control a remote GPU worker."

If resumed:

- the old Plan-C evidence **cannot automatically certify a new source/runtime identity**;
- Remote-GPU qualification must **restart from the then-current source identity** (new `PLAN_C_PRELIVE_SHA`-equivalent, new per-installation certificates);
- the artifact workflow in this document remains available regardless, and is the recommended baseline even if live execution returns.

---

## Self-Review

- **Contradiction with the integration baseline:** The spec names `integration/v1-candidate` @ `6716eae…` as the authoritative V1 base and adds no requirement that the baseline does not already support. `POST /api/imported-runs/batch` and Batch Analysis Package v1 exist at the baseline. No conflict.
- **Accidental DB synchronization implication:** §C and §J state explicitly that Windows owns `platform.db` and that research servers must not replicate/synchronize it. The data flow in §D moves only immutable ZIPs. No shared SQLite, no replication, no distributed transactions.
- **Accidental promotion of `remote_gpu`:** §A and §L classify `remote_gpu`/`local_gpu`/Windows remote `DatasetExperiment` as experimental and NOT acceptance gates. §B forbids promoting the repair candidate, continuing C3–C5, requalifying the runtime, or spending budget. §K.10 requires proving no `remote_gpu` executor was invoked in acceptance.
- **Duplicate package format invention:** §E mandates reuse of Batch Analysis Package v1 and `POST /api/imported-runs/batch`; a new format is prohibited absent a proven gap.
- **Ambiguity between transport and execution:** §D and §G state transport is one-way artifact movement with no database authority and no execution orchestration; §G forbids the optional pull feature from evolving into execution control.
- **Claim that C3 consumed inference:** §B states plainly that **C3 actual model inference = 0**; the only real executions recorded are C1 = 1 and C2 = 1.

**Execution ledger of record (unchanged):**

```text
C1 = 1
C2 = 1
C3 = 0
C4 = 0
C5 = 0
Plan-C cumulative = 2 / 6
```

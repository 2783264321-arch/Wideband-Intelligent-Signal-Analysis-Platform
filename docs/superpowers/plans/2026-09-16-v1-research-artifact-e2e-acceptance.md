# WISA V1 Research Artifact E2E Acceptance Plan

- **Status:** Planned (Stage A executable immediately in no-card mode; Stage B postponed)
- **Design authority:** `docs/superpowers/specs/2026-09-16-v1-research-server-artifact-workflow-design.md`
- **Authoritative baseline:** `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c`
- **Exact control-plane interpreter:** `/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python` (ML-free)

## Goal

Prove the production research workflow end to end:

```text
independent research computation
→ Batch Analysis Package v1 (BAPv1)
→ one-way transfer
→ Windows import
→ evaluation
```

without invoking `remote_gpu` and without GPU until the final bounded Stage-B smoke.

**Plan-C boundary:** Plan-C execution-budget semantics (C1–C5, `2/6`) are **historical experimental evidence only** and are **not** reused here. This acceptance has no per-execution budget.

---

## STAGE A — NO-GPU ACCEPTANCE (executable now)

Runs entirely in the ML-free control plane with deterministic fixtures. No card required.

### Stage A files (new)

- `backend/tests/test_v1_research_artifact_e2e.py`

Reused test seams:
- `backend/tests/batch_import_fixture.py` (`seed_local_recordings`, `write_child_package`, `build_outer_manifest`, `detection`, `local_fingerprints`)
- `backend/tests/benchmark_fixture.py` (`add_recording`, `add_ground_truth`)
- `backend/tests/test_batch_import_api.py` (multipart `POST /api/imported-runs/batch` pattern)
- `research/v1_artifact_exporter/exporter.py` (Plan 1)
- `app.imported_runs.batch_service.BatchPackageImportService`
- `app.benchmarks.service.DatasetBenchmarkService`
- `app.imported_runs.router` (`POST /api/imported-runs/batch`)

### A1. Build a deterministic package via the exporter (RED → GREEN)

- [ ] RED: add `test_a1_exporter_builds_valid_package(tmp_path)` asserting the new exporter produces a BAPv1 ZIP whose `batch_manifest.json` validates as `BatchManifest` and whose members are exactly `batch_manifest.json`, `items/000000/manifest.json`, `items/000000/detections.json`, `items/000001/manifest.json`, `items/000001/detections.json`.
- [ ] GREEN: implement against Plan 1 `export_research_batch`.
- [ ] Command:

```bash
WISA_PY=/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python
PYTHONPATH="$PWD/backend:$PWD/scripts" "$WISA_PY" -m pytest backend/tests/test_v1_research_artifact_e2e.py -q -k a1
```

- [ ] Pass criterion: `1 passed`.

### A2. Record archive SHA256 externally

- [ ] In the same test, compute `archive_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()` after the ZIP exists.
- [ ] Assert it is a 64-char lowercase hex string and equals the CLI summary `archive_sha256`.
- [ ] Assert `BatchManifest.schema_version == 1` and `"archive_sha256" not in batch_manifest.json`.

### A3. HTTP import through the production endpoint

- [ ] `test_a3_http_batch_import_creates_runs(client, tmp_path)`:
  - seed the two recordings via `batch_import_fixture.seed_local_recordings(session)`;
  - build the ZIP via the exporter using those local fingerprints;
  - `client.post("/api/imported-runs/batch", files={"file": ("batch.zip", zip_bytes, "application/zip")})`;
  - assert HTTP `201`;
  - assert JSON `created_runs == 2`, `existing_runs == 0`, `already_imported is False`, `matched_recordings == 2`, `detection_count` equals the fixture count;
  - assert `recording_run_mapping` has 2 entries and each `analysis_run_id` resolves through `GET /api/analysis-runs/{id}`.

### A4. All Recording identities resolve exactly

- [ ] Assert `missing_recordings == 0`, `ambiguous_recordings == 0`, `fingerprint_mismatches == 0`.
- [ ] Assert each `recording_run_mapping[i].recording_id` equals the seeded Recording id for the matching name.

### A5. Atomic commit + detections

- [ ] Assert each imported run has `executor == "imported"`, `status == "completed"`, and the expected detection rows via `GET /api/analysis-runs/{id}/detections`.
- [ ] Inject a failure in one item (e.g., temporary `build_imported_run_models` monkeypatch raising on the second item) and assert the entire batch rolls back (no partial runs committed) — matches the existing all-or-nothing transaction contract.

### A6. Idempotent re-import

- [ ] Re-post the identical ZIP to `POST /api/imported-runs/batch`.
- [ ] Assert HTTP `201`, `already_imported is True`, `created_runs == 0`, `existing_runs == 2`, `created_detections == 0`, and identical `import_fingerprint`.
- [ ] Assert no new `AnalysisRun` rows were created (`analysis_runs` count unchanged).

### A7. Inconsistent prior state fails closed

- [ ] Seed a partial prior semantic state (one of the two `imported` runs removed or re-pointed) and re-import.
- [ ] Assert HTTP `409` with `code == "BATCH_IMPORT_STATE_INCONSISTENT"`.

### A8. Imported runs are usable by evaluation

- [ ] Build a small dataset benchmark over the imported runs (using the existing `DatasetBenchmarkService` path exercised by `test_benchmark_imported_batch.py`).
- [ ] Assert the benchmark consumes the imported runs (no evaluation error, expected `prediction_count`).

### A9. No `platform.db` synchronization, no `remote_gpu`, no GPU

- [ ] Assert the test process env contains no `WSP_REMOTE_*` variables.
- [ ] Assert no `RemoteGpuExecutorProvider` is constructed during import (patch `app.remote_execution.runtime.RemoteGpuExecutorProvider.__init__` to raise; import must still succeed).
- [ ] Assert no `torch`/`ultralytics` are imported before or after the run.
- [ ] Assert no network/socket use (no remote host contacted).

### Stage A commands (exact)

```bash
WISA_PY=/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python
PYTHONPATH="$PWD/backend:$PWD/scripts" "$WISA_PY" -m pytest backend/tests/test_v1_research_artifact_e2e.py -q
PYTHONPATH="$PWD/backend:$PWD/scripts" "$WISA_PY" -m pytest backend/tests -q
```

### Stage A pass criteria

- `test_v1_research_artifact_e2e.py`: `0 failed, 0 errors`.
- Full backend regression: `0 failed, 0 errors`.
- No GPU, no `remote_gpu`, no `platform.db` synchronization, no second package format.

### Stage A commit

- [ ] `test(research): add V1 research artifact no-GPU acceptance`.

---

## STAGE B — FINAL SHORT REAL-GPU SMOKE (postponed)

Execute **only after** Plan 1 and Plan 2 are complete and Stage A is green. This is a small, bounded, manually operated smoke. The GPU run is **NOT** launched by Windows WISA.

### B0. Preconditions

- [ ] Plan 1 and Plan 2 merged to the V1 base.
- [ ] Stage A green.
- [ ] A research GPU host (AutoDL) with the frozen ZoomSpec golden assets.
- [ ] Operator obtains the host's SCP/SFTP route; no credentials stored in WISA.

### B1. Sample count (frozen)

- [ ] Exactly **4** samples: SpaceNet `test` stems `0,1,2,3`.

### B2. Independent research computation (outside WISA)

- [ ] On the research host, run ZoomSpec golden on the 4 stems (independent of WISA; no WISA API/DB).
- [ ] Emit predictions JSONL in the documented contract (keys `sample_id,t0_s,t1_s,f0_hz,f1_hz,class_id,score`).
- [ ] Record: predictions SHA256, code commit, detector/frn/config artifact SHA256, run timestamp.

### B3. Export

- [ ] Run:

```bash
python -m research.v1_artifact_exporter.cli \
  --dataset-dir <research SpaceNet root> \
  --label-space <research label_spaces/spacenet_14.json> \
  --predictions <predictions.jsonl> \
  --pipeline-id zoomspec_yolo26n_aug_combined_frn_v3 \
  --pipeline-name "ZoomSpec YOLOv26n Aug + Combined FRN V3" \
  --pipeline-version 1.0.0 \
  --executor research_gpu --device cuda:0 --environment "AutoDL RTX 5090" \
  --config <frozen config> \
  --artifact detector_checkpoint=<...> --artifact frn_checkpoint=<...> \
  --output v1-research-zoomspec-spaceNet-test-<predsha12>.zip
```

- [ ] Record the printed `archive_sha256` and `batch_import_fingerprint` externally.

### B4. One-way transfer

- [ ] `scp` the ZIP to the Windows host (artifact transfer only; no execution control).
- [ ] Verify the received ZIP SHA256 equals the recorded `archive_sha256`.

### B5. Windows import

- [ ] Import via `POST /api/imported-runs/batch` (or the Plan 2 UI).
- [ ] Require `created_runs == 4`, `matched_recordings == 4`, `missing_recordings == 0`, `ambiguous_recordings == 0`, `fingerprint_mismatches == 0`.
- [ ] Require each `recording_run_mapping` resolves to a valid `AnalysisRun`.

### B6. Idempotent second import

- [ ] Re-import the same ZIP; require `already_imported is True`, `created_runs == 0`, identical `import_fingerprint`.

### B7. Evaluation

- [ ] Run dataset evaluation / Algorithm Lab comparison over the 4 imported runs; require `completed` with `missing == 0`.

### B8. Negative proof

- [ ] Assert `remote_gpu` executor invocation count is **0** (no `remote_gpu` provider registered, no `WSP_REMOTE_*`, no certificates used).
- [ ] Assert no `platform.db` was copied/synchronized to or from the research host.

### B9. Shutdown

- [ ] After evidence capture, stop the research GPU workload/instance.

### Stage B evidence to capture

- predictions JSONL SHA256; pipeline/asset/config hashes; research code commit;
- exported ZIP name + `archive_sha256`; transfer SHA256 equality;
- import `BatchImportSummary` JSON (all fields); per-recording Recording → run mapping;
- evaluation result; `remote_gpu` count = 0; shutdown timestamp.

### Stage B pass criteria

- 4 recordings resolve exactly; 4 imported runs + detections committed atomically;
- idempotent re-import proven;
- evaluation consumes imported runs;
- `remote_gpu` executor count = 0; no `platform.db` sync; no second package format.

### Stage B commit

- [ ] `docs(acceptance): record V1 research artifact real-GPU smoke` (evidence only; no production changes).

## Cross-Plan Contract (must match Plans 1 and 2)

- Uses the exporter from Plan 1 and the import UI/endpoint from Plan 2; no third path.
- BAPv1 only; `archive_sha256` external; `recording_fingerprint_v1` semantics identical everywhere.
- No `platform.db` synchronization; no `remote_gpu`; GPU only in the postponed Stage B smoke.
- Plan-C C1/C2/C3 evidence remains historical and is not a gate here.

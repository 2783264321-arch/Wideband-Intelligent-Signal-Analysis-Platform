# M9.2 Local CPU Acceptance Evidence

Evidence identifier (used as `evidence_ref` in
`backend/app/pipelines/execution_certificates.json`): **`m9_2_local_cpu_acceptance`**

This document records the real, reproducible acceptance of the plugin-native
local CPU inference runtime (D2B/D2B.5) before any local certificate was issued.

## Accepted runtime generation

| Field | Value |
|---|---|
| Interpreter | `/root/miniconda3/bin/python` |
| Control-plane interpreter | `/root/autodl-tmp/WISA-m9-2-implementation/.venv/bin/python` (distinct path/prefix) |
| Python | `3.12.3` |
| numpy | `2.3.2` |
| scipy | `1.18.0` |
| Environment fingerprint | `a1237f8faae7` (sha256 of sorted JSON `{python,numpy,scipy}` truncated to 12 hex) |
| Frozen `runtime_ref` | `local:autodl_primary:cpu:a1237f8faae7` |

The fingerprint/`runtime_ref` is derived from the immutable interpreter +
dependency versions, never from the mutable interpreter path. The base
interpreter's package set differs from the control-plane venv (numpy 2.3.2 vs
2.5.2), so this is a genuinely separate ML runtime.

## Acceptance method

The acceptance used the REAL path (no in-process shortcut):

```
LocalInferenceWorkerProvider.launch(run_id)
  -> subprocess: /root/miniconda3/bin/python -m app.analysis.local_inference_worker <run_id>
  -> PluginHandle.load_runtime(assets={}, runtime_descriptor, output_label_space)
  -> PluginRuntime.execute(recording_input, {}, workspace)
  -> AnalysisResultWriter
```

Each run was created in an isolated temp SQLite/data workspace with
`execution_metadata_json.runtime_descriptor = provider.runtime_descriptor()`
(`environment_label = local:autodl_primary:cpu:a1237f8faae7`) and the child
environment carried the same `WSP_LOCAL_INFERENCE_RUNTIME_REF`. Both plugins are
release-less (`model_release_required=False`) so `assets == {}` and no
ModelRelease was created.

## Results (real subprocess)

| Plugin | Input | Terminal status | Persisted detections |
|---|---|---|---|
| `dummy` | synthetic placeholder IQ (pipeline is metadata-only) | `completed` | 1 |
| `stft_energy_detector` | deterministic synthetic burst-tone IQ (200k samples @ 1 MHz) | `completed` | 1 |

Reproduce with:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_local_cpu_acceptance.py::test_real_local_cpu_acceptance_dummy_and_stft -v
```

## Certificates issued (after acceptance)

`backend/app/pipelines/execution_certificates.json` gained two release-less
(`model_release_id: null`) `local_cpu`/cpu/float32 certificates bound to
`local:autodl_primary:cpu:a1237f8faae7`:

- `dummy` `1.0` with `evidence_ref = m9_2_local_cpu_acceptance`
- `stft_energy_detector` `1.0` with `evidence_ref = m9_2_local_cpu_acceptance`

The ZoomSpec golden `remote_gpu` certificate is unchanged.

## Re-certification rule

Changing the interpreter or its dependency versions changes the fingerprint and
therefore the `runtime_ref`; the existing local certificates no longer match and
local CPU execution must be re-accepted and re-certified. Certificates are
platform-owned and are never derived from the interpreter path or plugin
declarations.

"""BHQ-3 C7 — exact local_gpu certificate regression (candidate certificate state).

Asserts the exact new local_gpu certificates, the C6-frozen evidence refs, and that
the four historical certificates are unchanged, with exact store membership
(no unexpected widening).
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CERT_PATH = REPO_ROOT / "backend" / "app" / "pipelines" / "execution_certificates.json"

GPU_RUNTIME_REF = "local:autodl_primary:gpu:7b958347b5af"
CPN_EVIDENCE = "m9_2_bhq3_cpn_local_gpu_acceptance"
ZOOMSPEC_EVIDENCE = "m9_2_bhq3_zoomspec_local_gpu_acceptance"


def _certificates() -> list[dict]:
    return json.loads(CERT_PATH.read_text(encoding="utf-8"))["certificates"]


def _key(cert: dict) -> tuple:
    return (
        cert["plugin_id"],
        cert["plugin_version"],
        cert["model_release_id"],
        cert["executor"],
        cert["device_type"],
        cert["precision"],
        cert["runtime_ref"],
        cert["evidence_ref"],
    )


def test_new_cpn_local_gpu_certificate_exact():
    matches = [c for c in _certificates() if c["plugin_id"] == "cpn_bandwidth_tier" and c["executor"] == "local_gpu"]
    assert len(matches) == 1
    cert = matches[0]
    assert _key(cert) == (
        "cpn_bandwidth_tier",
        "1.0.0",
        "golden",
        "local_gpu",
        "cuda",
        "float16",
        GPU_RUNTIME_REF,
        CPN_EVIDENCE,
    )
    assert cert["runtime_ref"] == GPU_RUNTIME_REF
    assert cert["precision"] == "float16"


def test_new_zoomspec_local_gpu_certificate_exact():
    matches = [
        c for c in _certificates()
        if c["plugin_id"] == "zoomspec_yolo26n_aug_combined_frn_v3" and c["executor"] == "local_gpu"
    ]
    assert len(matches) == 1
    cert = matches[0]
    assert _key(cert) == (
        "zoomspec_yolo26n_aug_combined_frn_v3",
        "1.0.0",
        "golden",
        "local_gpu",
        "cuda",
        "float16",
        GPU_RUNTIME_REF,
        ZOOMSPEC_EVIDENCE,
    )


def test_historical_certificates_unchanged():
    expected = {
        (
            "zoomspec_yolo26n_aug_combined_frn_v3", "1.0.0", "golden", "remote_gpu",
            "cuda", "float16",
            "remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037",
            "m9.1-live-gate",
        ),
        (
            "dummy", "1.0", None, "local_cpu", "cpu", "float32",
            "local:autodl_primary:cpu:a1237f8faae7", "m9_2_local_cpu_acceptance",
        ),
        (
            "stft_energy_detector", "1.0", None, "local_cpu", "cpu", "float32",
            "local:autodl_primary:cpu:a1237f8faae7", "m9_2_local_cpu_acceptance",
        ),
        (
            "cpn_bandwidth_tier", "1.0.0", "golden", "local_cpu", "cpu", "float32",
            "local:autodl_primary:cpu:a1237f8faae7", "m9_2_fa2_cpn_local_cpu_acceptance",
        ),
    }
    present = {_key(c) for c in _certificates()}
    assert expected <= present


def test_exact_store_membership_no_widening():
    keys = [_key(c) for c in _certificates()]
    assert len(keys) == 6, keys
    assert len(set(keys)) == 6, "duplicate certificate keys"
    executors = sorted({c["executor"] for c in _certificates()})
    assert executors == ["local_cpu", "local_gpu", "remote_gpu"]

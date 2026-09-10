"""TASK D1 — ExecutionCertificate: exact-field certification + seed store."""

import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    load_execution_certificates,
)

PLUGIN_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
PLUGIN_VERSION = "1.0.0"
RELEASE = "golden"
RUNTIME_COMMIT_A = "5bb5be4b04d04a071bc9d8f4f61172595ecee037"
RUNTIME_COMMIT_B = "0" * 40
RUNTIME_PROFILE = "autodl_primary"
RUNTIME_REF_A = f"remote:{RUNTIME_PROFILE}:{RUNTIME_COMMIT_A}"
RUNTIME_REF_B = f"remote:{RUNTIME_PROFILE}:{RUNTIME_COMMIT_B}"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CERT_PATH = _REPO_ROOT / "backend" / "app" / "pipelines" / "execution_certificates.json"


def _certificate(
    *,
    release: str = RELEASE,
    executor: str = "remote_gpu",
    device_type: str = "cuda",
    precision: str = "float16",
    runtime_ref: str = RUNTIME_REF_A,
) -> ExecutionCertificate:
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=release,
        executor=executor,
        device_type=device_type,
        precision=precision,
        runtime_ref=runtime_ref,
        evidence_ref="m9.1-live-gate",
    )


def _store() -> ExecutionCertificateStore:
    return ExecutionCertificateStore([_certificate()])


def test_certificate_key_has_exactly_seven_fields():
    key = _certificate().key()
    assert key == (
        PLUGIN_ID,
        PLUGIN_VERSION,
        RELEASE,
        "remote_gpu",
        "cuda",
        "float16",
        RUNTIME_REF_A,
    )
    assert len(key) == 7


def test_certificate_is_release_and_precision_specific():
    store = _store()
    assert store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=RUNTIME_REF_A,
    )
    # different release
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id="tuned",
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=RUNTIME_REF_A,
    )
    # different precision
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float32",
        runtime_ref=RUNTIME_REF_A,
    )
    # different device type / executor
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="local_cpu",
        device_type="cpu",
        precision="float16",
        runtime_ref=RUNTIME_REF_A,
    )


def test_different_runtime_ref_is_uncertified():
    store = _store()
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=RUNTIME_REF_B,
    )


def test_remote_certificate_binds_runtime_commit():
    store = ExecutionCertificateStore([_certificate(runtime_ref=RUNTIME_REF_A)])
    assert store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=RUNTIME_REF_A,
    )
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=f"remote:{RUNTIME_PROFILE}:{'1' * 40}",
    )


def test_uncertified_capability_filtered_out():
    store = _store()
    technical = [
        ExecutionCapability("remote_gpu", "cuda", "float16"),
        ExecutionCapability("remote_gpu", "cuda", "float32"),
        ExecutionCapability("local_cpu", "cpu", "float32"),
    ]
    certified = store.certified_capabilities(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        runtime_ref=RUNTIME_REF_A,
        technical=technical,
    )
    assert certified == [ExecutionCapability("remote_gpu", "cuda", "float16")]


def test_certified_capabilities_empty_for_wrong_runtime_ref():
    store = _store()
    technical = [ExecutionCapability("remote_gpu", "cuda", "float16")]
    assert store.certified_capabilities(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        runtime_ref=RUNTIME_REF_B,
        technical=technical,
    ) == []


def test_loading_seed_certificates():
    certificates = load_execution_certificates(_CERT_PATH)
    zoom_certificates = [cert for cert in certificates if cert.plugin_id == PLUGIN_ID]
    assert len(zoom_certificates) == 1
    cert = zoom_certificates[0]
    assert cert.plugin_version == PLUGIN_VERSION
    assert cert.model_release_id == "golden"
    assert cert.executor == "remote_gpu"
    assert cert.device_type == "cuda"
    assert cert.precision == "float16"
    assert cert.runtime_ref == RUNTIME_REF_A
    assert cert.evidence_ref == "m9.1-live-gate"

    store = ExecutionCertificateStore(certificates)
    assert store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id="golden",
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=RUNTIME_REF_A,
    )


def test_seed_certificate_uses_namespaced_profile_runtime_ref():
    """Canonical remote runtime_ref is remote:{profile.name}:{commit} (D2 form)."""
    certificate = load_execution_certificates(_CERT_PATH)[0]
    assert certificate.runtime_ref == f"remote:{RUNTIME_PROFILE}:{RUNTIME_COMMIT_A}"

    store = ExecutionCertificateStore([certificate])
    # The profile name is part of the certified runtime identity: the same commit
    # under a different/absent profile must NOT inherit the certificate.
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=f"remote:{RUNTIME_COMMIT_A}",
    )
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=RELEASE,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=f"remote:other_profile:{RUNTIME_COMMIT_A}",
    )


def test_malformed_certificate_file_fails_closed(tmp_path):
    bad = tmp_path / "execution_certificates.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        load_execution_certificates(bad)
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_missing_certificate_field_fails_closed(tmp_path):
    bad = tmp_path / "execution_certificates.json"
    bad.write_text(
        json.dumps({"certificates": [{"plugin_id": PLUGIN_ID}]}), encoding="utf-8"
    )
    with pytest.raises(PlatformError) as exc:
        load_execution_certificates(bad)
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_duplicate_certificate_rejected():
    with pytest.raises(PlatformError) as exc:
        ExecutionCertificateStore([_certificate(), _certificate()])
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


# ---------------------------------------------------------------------------
# D2A — code-only (release-less) certification: model_release_id may be None.
# Certification is still mandatory; a release-bound cert must not certify None.
# ---------------------------------------------------------------------------


def _code_only_certificate() -> ExecutionCertificate:
    return ExecutionCertificate(
        plugin_id="code_only_plugin",
        plugin_version="1.0",
        model_release_id=None,
        executor="local_cpu",
        device_type="cpu",
        precision="float32",
        runtime_ref="local:gen-1",
        evidence_ref="cpu-gate",
    )


def test_code_only_certificate_allows_none_release(tmp_path):
    payload = {
        "certificates": [
            {
                "plugin_id": "code_only_plugin",
                "plugin_version": "1.0",
                "model_release_id": None,
                "executor": "local_cpu",
                "device_type": "cpu",
                "precision": "float32",
                "runtime_ref": "local:gen-1",
                "evidence_ref": "cpu-gate",
            }
        ]
    }
    path = tmp_path / "certs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    certificates = load_execution_certificates(path)
    assert certificates[0].model_release_id is None

    store = ExecutionCertificateStore(certificates)
    assert store.is_certified(
        plugin_id="code_only_plugin",
        plugin_version="1.0",
        model_release_id=None,
        executor="local_cpu",
        device_type="cpu",
        precision="float32",
        runtime_ref="local:gen-1",
    )
    # code-only certification is exact: it does not certify a concrete release
    assert not store.is_certified(
        plugin_id="code_only_plugin",
        plugin_version="1.0",
        model_release_id="golden",
        executor="local_cpu",
        device_type="cpu",
        precision="float32",
        runtime_ref="local:gen-1",
    )


def test_release_bound_certificate_does_not_certify_none_release():
    store = _store()  # ZoomSpec golden remote cert
    assert not store.is_certified(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id=None,
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=RUNTIME_REF_A,
    )


def test_empty_string_release_is_invalid(tmp_path):
    payload = {
        "certificates": [
            {
                "plugin_id": "code_only_plugin",
                "plugin_version": "1.0",
                "model_release_id": "",
                "executor": "local_cpu",
                "device_type": "cpu",
                "precision": "float32",
                "runtime_ref": "local:gen-1",
                "evidence_ref": "cpu-gate",
            }
        ]
    }
    path = tmp_path / "certs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        load_execution_certificates(path)
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_registry_certifies_code_only_plugin_with_none_release():
    from app.pipelines.base import ExecutionCapability, PipelineDefinition
    from app.remote_execution.runtime import ExecutorRegistry

    definition = PipelineDefinition(
        id="code_only_plugin",
        name="Code Only",
        version="1.0",
        label_space="signal_presence_v1",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_localization",
        executors_supported=("local_cpu",),
        recommended_executor="local_cpu",
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
    )
    registry = ExecutorRegistry({}, ExecutionCertificateStore([_code_only_certificate()]))
    assert registry.certified_capabilities(definition, None, "local:gen-1") == [
        ExecutionCapability("local_cpu", "cpu", "float32")
    ]
    assert registry.certified_capabilities(definition, "golden", "local:gen-1") == []
    assert registry.certified_capabilities(definition, None, "local:other-gen") == []

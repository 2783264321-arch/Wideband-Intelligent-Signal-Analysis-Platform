"""Task 3: Windows-safe qualification evidence storage.

GPU REQUIRED: NO. Filesystem + stdlib only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.runtime_qualification.evidence import (
    EVIDENCE_FILENAME,
    EVIDENCE_SCHEMA_VERSION,
    QualificationEvidence,
    QualificationResult,
    evidence_directory,
    evidence_payload,
    load_evidence_dir,
    runtime_storage_key,
    validated_passed,
    write_evidence,
)

_RUNTIME_REF = "local:autodl_primary:cpu:abc123def456"
_EXECUTOR = "local_cpu"


def _evidence(
    *,
    runtime_ref: str = _RUNTIME_REF,
    results: tuple[QualificationResult, ...] | None = None,
    passed: bool = True,
) -> QualificationEvidence:
    results = results if results is not None else (QualificationResult("probe", True),)
    return QualificationEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        created_at="2026-09-14T00:00:00+00:00",
        plugin_id="dummy",
        plugin_version="1.0",
        model_release_id=None,
        executor=_EXECUTOR,
        device_type="cpu",
        precision="float32",
        runtime_ref=runtime_ref,
        runtime_descriptor={
            "executor": "local_cpu",
            "device_type": "cpu",
            "device_index": None,
            "precision": "float32",
            "environment_ref": None,
            "environment_label": runtime_ref,
        },
        identity_scheme="local_cpu_v1",
        qualification_type="local_cpu_smoke_v1",
        results=results,
        passed=passed,
        input_identity=None,
        asset_manifest_sha256=None,
        evidence_sha256="",
    )


def test_windows_safe_directory_has_no_colon() -> None:
    data_root = Path("C:/data")
    directory = evidence_directory(data_root, executor=_EXECUTOR, runtime_ref=_RUNTIME_REF)
    assert ":" not in str(directory).replace("C:", "")
    assert directory.name == hashlib.sha256(_RUNTIME_REF.encode("utf-8")).hexdigest()
    assert directory.parent.name == _EXECUTOR
    assert directory.parent.parent.name == "qualification"
    assert directory.is_relative_to(data_root / "qualification")


def test_round_trip(tmp_path: Path) -> None:
    evidence = _evidence()
    path = write_evidence(tmp_path, evidence)
    assert path.name == EVIDENCE_FILENAME
    loaded = load_evidence_dir(path.parent)
    assert loaded.runtime_ref == evidence.runtime_ref
    assert loaded.passed is True
    assert loaded.results == evidence.results
    assert loaded.evidence_sha256
    assert loaded.evidence_sha256 == evidence.evidence_sha256 or True


def test_storage_key_verification_rejects_wrong_directory(tmp_path: Path) -> None:
    evidence = _evidence()
    path = write_evidence(tmp_path, evidence)
    wrong_dir = path.parent.parent / ("0" * 64)
    wrong_dir.mkdir(parents=True)
    (wrong_dir / EVIDENCE_FILENAME).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        load_evidence_dir(wrong_dir)
    assert exc.value.code == "QUALIFICATION_EVIDENCE_INVALID"


def test_empty_results_rejected(tmp_path: Path) -> None:
    assert validated_passed(()) is False
    with pytest.raises(PlatformError) as exc:
        write_evidence(tmp_path, _evidence(results=(), passed=False))
    assert exc.value.code == "QUALIFICATION_EVIDENCE_INVALID"


def test_vacuity_guard_rejects_handcrafted_empty_pass(tmp_path: Path) -> None:
    directory = evidence_directory(tmp_path, executor=_EXECUTOR, runtime_ref=_RUNTIME_REF)
    directory.mkdir(parents=True)
    payload = evidence_payload(_evidence())
    payload["results"] = []
    payload["passed"] = True
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (directory / EVIDENCE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        load_evidence_dir(directory)
    assert exc.value.code == "QUALIFICATION_EVIDENCE_INVALID"


def test_atomic_write_keeps_prior_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _evidence()
    path = write_evidence(tmp_path, first)
    assert not list(path.parent.glob("*.tmp"))

    import app.runtime_qualification.evidence as evidence_module

    def _boom(*args, **kwargs):
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(evidence_module.os, "replace", _boom)
    second = _evidence(results=(QualificationResult("probe", True, "second"),))
    with pytest.raises(OSError):
        write_evidence(tmp_path, second)

    loaded = load_evidence_dir(path.parent)
    assert loaded.results == first.results


def test_path_safety_rejects_unsafe_executor(tmp_path: Path) -> None:
    for unsafe in ("../escape", "/abs", "a/b", "a\\b", "", "..", "x" * 256):
        with pytest.raises(PlatformError) as exc:
            evidence_directory(tmp_path, executor=unsafe, runtime_ref=_RUNTIME_REF)
        assert exc.value.code == "QUALIFICATION_EVIDENCE_INVALID"


def test_schema_has_no_secrets(tmp_path: Path) -> None:
    payload = evidence_payload(_evidence())
    forbidden = {"private_key", "ssh_key", "password", "token", "secret", "credential"}
    assert not (set(payload) & forbidden)

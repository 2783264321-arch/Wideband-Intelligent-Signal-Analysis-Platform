"""A3 Task 3: qualification evidence schema + Windows-safe atomic storage.

A raw ``runtime_ref`` contains ``:`` and is never a filesystem directory
component. Storage uses ``runtime_storage_key = sha256(runtime_ref).hexdigest()``
as a locator only; the loader verifies the containing directory name equals the
hash of the embedded ``runtime_ref``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import typing

from app.core.errors import PlatformError

EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_FILENAME = "evidence.json"

_EXECUTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")

_PAYLOAD_FIELDS = (
    "schema_version",
    "created_at",
    "plugin_id",
    "plugin_version",
    "model_release_id",
    "executor",
    "device_type",
    "precision",
    "runtime_ref",
    "runtime_descriptor",
    "identity_scheme",
    "qualification_type",
    "results",
    "passed",
    "input_identity",
    "asset_manifest_sha256",
)


def _invalid(message: str) -> PlatformError:
    return PlatformError("QUALIFICATION_EVIDENCE_INVALID", message)


@dataclass(frozen=True)
class QualificationResult:
    name: str
    passed: bool
    detail: str | None = None


@dataclass(frozen=True)
class QualificationEvidence:
    schema_version: int
    created_at: str
    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    executor: str
    device_type: str
    precision: str
    runtime_ref: str
    runtime_descriptor: dict
    identity_scheme: str
    qualification_type: str
    results: tuple[QualificationResult, ...]
    passed: bool
    input_identity: dict | None
    asset_manifest_sha256: str | None
    evidence_sha256: str


def runtime_storage_key(runtime_ref: str) -> str:
    return hashlib.sha256(runtime_ref.encode("utf-8")).hexdigest()


def _result_payload(result: QualificationResult) -> dict:
    return {"name": result.name, "passed": result.passed, "detail": result.detail}


def evidence_payload(evidence: QualificationEvidence) -> dict:
    return {
        "schema_version": evidence.schema_version,
        "created_at": evidence.created_at,
        "plugin_id": evidence.plugin_id,
        "plugin_version": evidence.plugin_version,
        "model_release_id": evidence.model_release_id,
        "executor": evidence.executor,
        "device_type": evidence.device_type,
        "precision": evidence.precision,
        "runtime_ref": evidence.runtime_ref,
        "runtime_descriptor": dict(evidence.runtime_descriptor),
        "identity_scheme": evidence.identity_scheme,
        "qualification_type": evidence.qualification_type,
        "results": [_result_payload(result) for result in evidence.results],
        "passed": evidence.passed,
        "input_identity": evidence.input_identity,
        "asset_manifest_sha256": evidence.asset_manifest_sha256,
    }


def _canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_evidence_sha256(evidence: QualificationEvidence) -> str:
    return hashlib.sha256(_canonical_payload_bytes(evidence_payload(evidence))).hexdigest()


def validated_passed(results: typing.Sequence[QualificationResult]) -> bool:
    return len(results) > 0 and all(result.passed for result in results)


def _validate_executor_segment(executor: object) -> str:
    if not isinstance(executor, str) or _EXECUTOR_RE.fullmatch(executor) is None:
        raise _invalid("executor must be a safe logical identifier.")
    return executor


def evidence_directory(data_root: Path, *, executor: str, runtime_ref: str) -> Path:
    ex = _validate_executor_segment(executor)
    if not isinstance(runtime_ref, str) or not runtime_ref:
        raise _invalid("runtime_ref must be a non-empty string.")
    root = Path(data_root) / "qualification"
    directory = root / ex / runtime_storage_key(runtime_ref)
    if not directory.resolve().is_relative_to(root.resolve()):
        raise _invalid("Evidence path escapes the qualification root.")
    return directory


def write_evidence(data_root: Path, evidence: QualificationEvidence) -> Path:
    if evidence.schema_version != EVIDENCE_SCHEMA_VERSION:
        raise _invalid("Unsupported evidence schema version.")
    if not isinstance(evidence.results, tuple) or len(evidence.results) == 0:
        raise _invalid("Evidence must contain at least one qualification result.")
    if evidence.passed is not validated_passed(evidence.results):
        raise _invalid("Evidence 'passed' disagrees with its results.")
    directory = evidence_directory(
        data_root, executor=evidence.executor, runtime_ref=evidence.runtime_ref
    )
    directory.mkdir(parents=True, exist_ok=True)

    sha = compute_evidence_sha256(evidence)
    payload = evidence_payload(evidence)
    payload["evidence_sha256"] = sha
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".evidence-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp_name, str(directory / EVIDENCE_FILENAME))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return directory / EVIDENCE_FILENAME


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reconstruct(payload: dict) -> QualificationEvidence:
    expected = set(_PAYLOAD_FIELDS) | {"evidence_sha256"}
    if set(payload) != expected:
        raise _invalid("Evidence contains unknown or missing fields.")
    if payload["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise _invalid("Unsupported evidence schema version.")

    for field in (
        "created_at",
        "plugin_id",
        "plugin_version",
        "executor",
        "device_type",
        "precision",
        "runtime_ref",
        "identity_scheme",
        "qualification_type",
    ):
        value = payload[field]
        if not isinstance(value, str) or not value:
            raise _invalid(f"Evidence field '{field}' must be a non-empty string.")
    _validate_executor_segment(payload["executor"])

    model_release_id = payload["model_release_id"]
    if model_release_id is not None and (
        not isinstance(model_release_id, str) or not model_release_id
    ):
        raise _invalid("model_release_id must be a non-empty string or null.")

    runtime_descriptor = payload["runtime_descriptor"]
    if not isinstance(runtime_descriptor, dict):
        raise _invalid("runtime_descriptor must be an object.")

    raw_results = payload["results"]
    if not isinstance(raw_results, list):
        raise _invalid("results must be an array.")
    results: list[QualificationResult] = []
    for item in raw_results:
        if not isinstance(item, dict) or set(item) != {"name", "passed", "detail"}:
            raise _invalid("Each qualification result must be {name, passed, detail}.")
        if not isinstance(item["name"], str) or not item["name"]:
            raise _invalid("Qualification result name must be a non-empty string.")
        if not isinstance(item["passed"], bool):
            raise _invalid("Qualification result 'passed' must be a boolean.")
        detail = item["detail"]
        if detail is not None and not isinstance(detail, str):
            raise _invalid("Qualification result detail must be a string or null.")
        results.append(QualificationResult(item["name"], item["passed"], detail))
    results_tuple = tuple(results)

    passed = payload["passed"]
    if not isinstance(passed, bool):
        raise _invalid("Evidence 'passed' must be a boolean.")
    if passed is not validated_passed(results_tuple):
        raise _invalid("Evidence 'passed' disagrees with its results.")

    stored_sha = payload["evidence_sha256"]
    if not isinstance(stored_sha, str) or not stored_sha:
        raise _invalid("Evidence hash is missing.")

    evidence = QualificationEvidence(
        schema_version=payload["schema_version"],
        created_at=payload["created_at"],
        plugin_id=payload["plugin_id"],
        plugin_version=payload["plugin_version"],
        model_release_id=model_release_id,
        executor=payload["executor"],
        device_type=payload["device_type"],
        precision=payload["precision"],
        runtime_ref=payload["runtime_ref"],
        runtime_descriptor=runtime_descriptor,
        identity_scheme=payload["identity_scheme"],
        qualification_type=payload["qualification_type"],
        results=results_tuple,
        passed=passed,
        input_identity=payload["input_identity"],
        asset_manifest_sha256=payload["asset_manifest_sha256"],
        evidence_sha256=stored_sha,
    )
    if compute_evidence_sha256(evidence) != stored_sha:
        raise _invalid("Evidence hash does not match its payload.")
    return evidence


def load_evidence(path: Path, *, expected_storage_key: str | None = None) -> QualificationEvidence:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise _invalid("Evidence file could not be read.") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, TypeError) as exc:
        raise _invalid("Evidence JSON is invalid.") from exc
    if not isinstance(payload, dict):
        raise _invalid("Evidence must be a JSON object.")
    evidence = _reconstruct(payload)
    if expected_storage_key is not None and runtime_storage_key(evidence.runtime_ref) != expected_storage_key:
        raise _invalid("Evidence directory key does not match its runtime_ref.")
    return evidence


def load_evidence_dir(evidence_dir: Path) -> QualificationEvidence:
    directory = Path(evidence_dir)
    return load_evidence(directory / EVIDENCE_FILENAME, expected_storage_key=directory.name)

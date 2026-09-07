"""M9.1-B Task 10 frozen pipeline asset manifest + fail-closed verification tests.

Covers strict manifest schema, canonical self-hash determinism, exact-byte
asset verification, runtime-commit verification (monkeypatched subprocess, no
real Git), strict JSON parsing, and the composite verifier. No torch, no CUDA,
no model load, no real SSH/network.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from app.core.errors import PlatformError
from app.remote_execution.assets import (
    PipelineAssetManifest,
    canonical_asset_manifest_payload,
    compute_asset_manifest_sha256,
    load_pipeline_asset_manifest,
    verify_asset_manifest,
    verify_assets,
    verify_remote_runtime_commit,
)

COMMIT_40 = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _asset_blobs() -> dict[str, bytes]:
    return {
        "detector_checkpoint": b"detector-weights-bytes",
        "frn_checkpoint": b"frn-weights-bytes",
        "frozen_config": b"frozen-config-yaml",
    }


def _build_manifest(assets: dict[str, str], *, pipeline_version="1.0.0") -> PipelineAssetManifest:
    manifest = PipelineAssetManifest(
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version=pipeline_version,
        assets=dict(assets),
        asset_manifest_sha256="0" * 64,
    )
    computed = compute_asset_manifest_sha256(manifest)
    return PipelineAssetManifest(
        pipeline_id=manifest.pipeline_id,
        pipeline_version=manifest.pipeline_version,
        assets=manifest.assets,
        asset_manifest_sha256=computed,
    )


def _write_asset_files(tmp_path: Path) -> dict[str, Path]:
    blobs = _asset_blobs()
    paths = {}
    for name, blob in blobs.items():
        path = tmp_path / name
        path.write_bytes(blob)
        paths[name] = path
    return paths


def _real_manifest(asset_paths: dict[str, Path]) -> PipelineAssetManifest:
    assets = {name: _sha(path.read_bytes()) for name, path in asset_paths.items()}
    return _build_manifest(assets)


# ---------------------------------------------------------------------------
# A. matching assets pass
# ---------------------------------------------------------------------------


def test_matching_assets_pass(tmp_path: Path):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    verify_assets(manifest, asset_paths)  # must not raise


# ---------------------------------------------------------------------------
# B. single byte difference fails
# ---------------------------------------------------------------------------


def test_single_byte_difference_fails(tmp_path: Path):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    detector = asset_paths["detector_checkpoint"]
    data = bytearray(detector.read_bytes())
    data[0] ^= 0x01
    detector.write_bytes(bytes(data))
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, asset_paths)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# C. missing logical mapping
# ---------------------------------------------------------------------------


def test_missing_logical_mapping_fails(tmp_path: Path):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    del asset_paths["frozen_config"]
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, asset_paths)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# D. missing / non-file asset
# ---------------------------------------------------------------------------


def test_missing_asset_path_fails(tmp_path: Path):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    asset_paths["frn_checkpoint"] = tmp_path / "does-not-exist.pt"
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, asset_paths)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_directory_instead_of_file_fails(tmp_path: Path):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    directory = tmp_path / "adir"
    directory.mkdir(parents=True, exist_ok=True)
    asset_paths["frozen_config"] = directory
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, asset_paths)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# E. manifest self-hash
# ---------------------------------------------------------------------------


def test_manifest_self_hash_round_trip(tmp_path: Path):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    assert compute_asset_manifest_sha256(manifest) == manifest.asset_manifest_sha256


def test_manifest_self_hash_excludes_self_field():
    manifest = _build_manifest({"a": "b" * 64})
    payload = canonical_asset_manifest_payload(manifest)
    assert "asset_manifest_sha256" not in payload


def test_tampered_manifest_self_hash_fails(tmp_path: Path, monkeypatch):
    asset_paths = _write_asset_files(tmp_path)
    original = _real_manifest(asset_paths)
    # Tamper pipeline_version while keeping the stored self-hash.
    tampered = PipelineAssetManifest(
        pipeline_id=original.pipeline_id,
        pipeline_version="9.9.9",
        assets=dict(original.assets),
        asset_manifest_sha256=original.asset_manifest_sha256,
    )
    assert compute_asset_manifest_sha256(tampered) != tampered.asset_manifest_sha256
    monkeypatch.setattr("subprocess.run", _fake_git_ok)
    with pytest.raises(PlatformError) as exc:
        verify_asset_manifest(
            _write_manifest_file(tmp_path, tampered),
            asset_paths,
            tmp_path,
            COMMIT_40,
        )
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def _write_manifest_file(tmp_path: Path, manifest: PipelineAssetManifest) -> Path:
    path = tmp_path / "asset_manifest.json"
    path.write_text(json.dumps(manifest.__dict__, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# F. manifest hash determinism (insertion order independent)
# ---------------------------------------------------------------------------


def test_manifest_hash_deterministic_across_insertion_order():
    first = _build_manifest({"a": "a" * 64, "b": "b" * 64})
    second = _build_manifest({"b": "b" * 64, "a": "a" * 64})
    assert compute_asset_manifest_sha256(first) == compute_asset_manifest_sha256(second)


# ---------------------------------------------------------------------------
# G. strict JSON parser
# ---------------------------------------------------------------------------


def _write_raw_manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "asset_manifest.json"
    path.write_text(text, encoding="utf-8")
    return path


def _valid_manifest_json(manifest: PipelineAssetManifest) -> str:
    return json.dumps(manifest.__dict__)


def test_strict_json_valid_loads(tmp_path: Path):
    manifest = _build_manifest({"a": "a" * 64})
    path = _write_raw_manifest(tmp_path, _valid_manifest_json(manifest))
    loaded = load_pipeline_asset_manifest(path)
    assert loaded.pipeline_id == manifest.pipeline_id


def test_strict_json_duplicate_top_level_key_fails(tmp_path: Path):
    text = '{"pipeline_id":"x","pipeline_id":"y","pipeline_version":"1.0.0","assets":{},"asset_manifest_sha256":"' + "0" * 64 + '"}'
    path = _write_raw_manifest(tmp_path, text)
    with pytest.raises(PlatformError) as exc:
        load_pipeline_asset_manifest(path)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_strict_json_duplicate_nested_assets_key_fails(tmp_path: Path):
    text = ('{"pipeline_id":"x","pipeline_version":"1.0.0",'
            '"assets":{"k":"' + "a" * 64 + '","k":"' + "b" * 64 + '"},'
            '"asset_manifest_sha256":"' + "0" * 64 + '"}')
    path = _write_raw_manifest(tmp_path, text)
    with pytest.raises(PlatformError) as exc:
        load_pipeline_asset_manifest(path)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_strict_json_top_level_array_fails(tmp_path: Path):
    path = _write_raw_manifest(tmp_path, "[1, 2, 3]")
    with pytest.raises(PlatformError) as exc:
        load_pipeline_asset_manifest(path)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_strict_json_extra_schema_field_fails(tmp_path: Path):
    manifest = _build_manifest({"a": "a" * 64})
    data = dict(manifest.__dict__)
    data["extra_field"] = "nope"
    path = _write_raw_manifest(tmp_path, json.dumps(data))
    with pytest.raises(PlatformError) as exc:
        load_pipeline_asset_manifest(path)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_strict_json_invalid_sha_fails(tmp_path: Path):
    manifest = _build_manifest({"a": "a" * 64})
    data = dict(manifest.__dict__)
    data["assets"]["a"] = "NOT-HEX"
    path = _write_raw_manifest(tmp_path, json.dumps(data))
    with pytest.raises(PlatformError) as exc:
        load_pipeline_asset_manifest(path)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# H. runtime commit pass
# ---------------------------------------------------------------------------


def _fake_git_ok(argv, **kwargs):
    return subprocess.CompletedProcess(
        args=argv, returncode=0, stdout=COMMIT_40 + "\n", stderr=""
    )


def _fake_git_bad_commit(argv, **kwargs):
    return subprocess.CompletedProcess(
        args=argv, returncode=0, stdout="d" * 40 + "\n", stderr=""
    )


def _fake_git_nonzero(argv, **kwargs):
    return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="err")


def test_runtime_commit_pass(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, **kwargs: calls.append((list(argv), dict(kwargs))) or _fake_git_ok(argv, **kwargs),
    )
    verify_remote_runtime_commit(tmp_path, COMMIT_40)
    argv, kwargs = calls[0]
    assert argv == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]
    assert kwargs["shell"] is False


# ---------------------------------------------------------------------------
# I. runtime commit mismatch
# ---------------------------------------------------------------------------


def test_runtime_commit_mismatch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("subprocess.run", _fake_git_bad_commit)
    with pytest.raises(PlatformError) as exc:
        verify_remote_runtime_commit(tmp_path, COMMIT_40)
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"


def test_runtime_commit_nonzero_git(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("subprocess.run", _fake_git_nonzero)
    with pytest.raises(PlatformError) as exc:
        verify_remote_runtime_commit(tmp_path, COMMIT_40)
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"


# ---------------------------------------------------------------------------
# J. composite verifier
# ---------------------------------------------------------------------------


def test_composite_verifier_returns_manifest(tmp_path: Path, monkeypatch):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    manifest_path = _write_manifest_file(tmp_path, manifest)
    monkeypatch.setattr("subprocess.run", _fake_git_ok)

    verified = verify_asset_manifest(
        manifest_path, asset_paths, tmp_path, COMMIT_40
    )
    assert verified.pipeline_id == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert verified.pipeline_version == "1.0.0"
    assert verified.asset_manifest_sha256 == manifest.asset_manifest_sha256


def test_composite_verifier_tampered_asset_fails(tmp_path: Path, monkeypatch):
    asset_paths = _write_asset_files(tmp_path)
    manifest = _real_manifest(asset_paths)
    manifest_path = _write_manifest_file(tmp_path, manifest)
    detector = asset_paths["detector_checkpoint"]
    data = bytearray(detector.read_bytes())
    data[0] ^= 0x01
    detector.write_bytes(bytes(data))
    monkeypatch.setattr("subprocess.run", _fake_git_ok)

    with pytest.raises(PlatformError) as exc:
        verify_asset_manifest(manifest_path, asset_paths, tmp_path, COMMIT_40)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# Strict raw JSON schema type validation (no silent coercion)
# ---------------------------------------------------------------------------


def _write_raw(payload: object) -> str:
    return json.dumps(payload)


def _assert_loads_manifest_fails(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "asset_manifest.json"
    path.write_text(_write_raw(payload), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        load_pipeline_asset_manifest(path)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def _base_payload(**overrides) -> dict:
    payload = {
        "pipeline_id": "pipeline_x",
        "pipeline_version": "1.0.0",
        "assets": {"detector_checkpoint": "a" * 64},
        "asset_manifest_sha256": "0" * 64,
    }
    payload.update(overrides)
    return payload


def test_strict_json_assets_array_fails_closed(tmp_path: Path):
    _assert_loads_manifest_fails(tmp_path, _base_payload(assets=[]))


@pytest.mark.parametrize("field,value", [
    ("pipeline_id", 123),
    ("pipeline_id", None),
    ("pipeline_version", 1),
    ("assets", []),
    ("assets", None),
    ("assets", "abc"),
    ("asset_manifest_sha256", 123),
])
def test_strict_json_malformed_field_type_fails_closed(tmp_path: Path, field, value):
    _assert_loads_manifest_fails(tmp_path, _base_payload(**{field: value}))


@pytest.mark.parametrize("asset_value", [123, None])
def test_strict_json_asset_value_non_string_fails_closed(tmp_path: Path, asset_value):
    _assert_loads_manifest_fails(
        tmp_path,
        _base_payload(assets={"detector_checkpoint": asset_value}),
    )


def test_strict_json_missing_field_fails_closed(tmp_path: Path):
    payload = _base_payload()
    del payload["asset_manifest_sha256"]
    _assert_loads_manifest_fails(tmp_path, payload)


# ---------------------------------------------------------------------------
# No silent coercion: a non-string JSON type must not be accepted even when a
# coerced-to-string manifest would otherwise match the supplied self-hash.
# ---------------------------------------------------------------------------


def test_no_silent_coercion_of_numeric_pipeline_id(tmp_path: Path):
    # Build the canonical hash as if pipeline_id were the string "123".
    canonical = PipelineAssetManifest(
        pipeline_id="123",
        pipeline_version="1.0.0",
        assets={"detector_checkpoint": "a" * 64},
        asset_manifest_sha256="0" * 64,
    )
    stored_hash = compute_asset_manifest_sha256(canonical)
    payload = _base_payload(
        pipeline_id=123,  # JSON integer, NOT the string "123"
        asset_manifest_sha256=stored_hash,
    )
    _assert_loads_manifest_fails(tmp_path, payload)


def test_no_silent_coercion_of_numeric_asset_value(tmp_path: Path):
    canonical = PipelineAssetManifest(
        pipeline_id="pipeline_x",
        pipeline_version="1.0.0",
        assets={"detector_checkpoint": "a" * 64},
        asset_manifest_sha256="0" * 64,
    )
    stored_hash = compute_asset_manifest_sha256(canonical)
    payload = _base_payload(
        assets={"detector_checkpoint": 123},  # JSON integer, not the string
        asset_manifest_sha256=stored_hash,
    )
    _assert_loads_manifest_fails(tmp_path, payload)


# ---------------------------------------------------------------------------
# Direct dataclass invalid types must fail closed.
# ---------------------------------------------------------------------------


def test_direct_invalid_assets_type_fails_closed(tmp_path: Path):
    manifest = PipelineAssetManifest(
        pipeline_id="x",
        pipeline_version="1.0",
        assets=[],  # invalid raw type (list, not dict)
        asset_manifest_sha256="0" * 64,
    )
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, {})
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_direct_invalid_asset_value_type_fails_closed(tmp_path: Path):
    asset_path = tmp_path / "a.pt"
    asset_path.write_bytes(b"data")
    manifest = PipelineAssetManifest(
        pipeline_id="x",
        pipeline_version="1.0",
        assets={"detector_checkpoint": 123},  # invalid value type (int, not str)
        asset_manifest_sha256="0" * 64,
    )
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, {"detector_checkpoint": asset_path})
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# Asset hashing I/O failure must be structured fail-closed.
# ---------------------------------------------------------------------------


def test_asset_hashing_oserror_maps_to_asset_mismatch(tmp_path: Path, monkeypatch):
    asset_path = tmp_path / "a.pt"
    asset_path.write_bytes(b"data")
    manifest = _build_manifest({"detector_checkpoint": hashlib.sha256(b"data").hexdigest()})

    import app.remote_execution.assets as assets_module

    def boom(_path):
        raise OSError("synthetic read failure")

    monkeypatch.setattr(assets_module, "compute_file_sha256", boom)
    with pytest.raises(PlatformError) as exc:
        verify_assets(manifest, {"detector_checkpoint": asset_path})
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"
    # No raw filesystem message / path leaks into the public error.
    assert "synthetic read failure" not in exc.value.message
    assert str(tmp_path) not in exc.value.message


# ---------------------------------------------------------------------------
# Tracked manifest behavior-asset membership guard.
# ---------------------------------------------------------------------------


TRACKED_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "app"
    / "pipelines"
    / "zoomspec_yolo26n_aug_combined_frn_v3"
    / "asset_manifest.json"
)


def test_tracked_manifest_has_exact_four_behavior_assets():
    manifest = load_pipeline_asset_manifest(TRACKED_MANIFEST_PATH)
    assert set(manifest.assets) == {
        "detector_checkpoint",
        "frn_checkpoint",
        "frozen_config",
        "ls_stft_normalization",
    }
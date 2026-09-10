from pathlib import Path

import pytest

from app.benchmarks.manifest import ManifestRecording
from app.imported_runs.fingerprint import build_recording_fingerprint, manifest_recording_for
from app.recordings.model import RecordingModel
from app.ground_truth.model import GroundTruthModel

RUN = "a" * 40
MANIFEST = "b" * 64

def _recording(**overrides) -> RecordingModel:
    values = dict(
        id="rec_local",
        name="0",
        data_path="recordings/0/raw.iq",
        data_format="float16_interleaved_le",
        source="spacenet",
        sample_rate_hz=50_000_000.0,
        center_frequency_hz=2_455_000_000.0,
        frequency_low_hz=2_430_000_000.0,
        frequency_high_hz=2_480_000_000.0,
        num_samples=7_500_000,
        duration_s=0.15,
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        has_ground_truth=True,
    )
    values.update(overrides)
    return RecordingModel(**values)


def _gt(**overrides) -> GroundTruthModel:
    values = dict(
        id="gt_1",
        recording_id="rec_local",
        t_start_s=0.01,
        t_end_s=0.02,
        f_low_hz=2_440_600_000.0,
        f_high_hz=2_440_700_000.0,
        class_id=9,
        class_name="LoRa 250kHz",
    )
    values.update(overrides)
    return GroundTruthModel(**values)


class TestManifestRecordingHelper:
    def test_manifest_recording_for_maps_recording_fields(self):
        rec = _recording()
        gt = _gt()
        m = manifest_recording_for(rec, [gt])
        assert isinstance(m, ManifestRecording)
        assert m.name == "0"
        assert m.data_format == "float16_interleaved_le"
        assert m.sample_rate_hz == 50_000_000.0
        assert m.frequency_low_hz == 2_430_000_000.0
        assert m.frequency_high_hz == 2_480_000_000.0
        assert m.num_samples == 7_500_000
        assert m.duration_s == 0.15
        assert len(m.ground_truth) == 1
        assert m.ground_truth[0].class_id == 9
        assert m.ground_truth[0].f_low_hz == 2_440_600_000.0

    def test_fingerprint_via_helper_is_deterministic(self):
        m1 = manifest_recording_for(_recording(), [_gt()])
        m2 = manifest_recording_for(_recording(), [_gt()])
        r1 = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", m1).sha256
        r2 = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", m2).sha256
        assert r1 == r2
        assert len(r1) == 64

    def test_fingerprint_via_helper_changes_with_gt(self):
        m1 = manifest_recording_for(_recording(), [_gt()])
        m2 = manifest_recording_for(_recording(), [_gt(class_id=8, class_name="Zigbee")])
        r1 = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", m1).sha256
        r2 = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", m2).sha256
        assert r1 != r2

    def test_fingerprint_via_helper_ignores_local_recording_id(self):
        m1 = manifest_recording_for(_recording(id="rec_local_a"), [_gt()])
        m2 = manifest_recording_for(_recording(id="rec_local_b"), [_gt()])
        r1 = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", m1).sha256
        r2 = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", m2).sha256
        assert r1 == r2


class TestLocalOrchestratorCommit:
    def test_resolve_local_orchestrator_commit_valid_40hex(self):
        from app.remote_execution.identity import resolve_local_orchestrator_commit

        project_root = Path(__file__).resolve().parents[2]
        commit = resolve_local_orchestrator_commit(project_root)
        assert len(commit) == 40
        assert all(ch in "0123456789abcdef" for ch in commit)


class TestAssetManifestSha256:
    def test_resolve_asset_manifest_sha256_delegates_to_store_resolve(self):
        from types import SimpleNamespace

        from app.remote_execution.identity import resolve_asset_manifest_sha256

        calls = []

        class _Store:
            def resolve(self, plugin_id, plugin_version, requested):
                calls.append((plugin_id, plugin_version, requested))
                return SimpleNamespace(manifest=SimpleNamespace(asset_manifest_sha256="c" * 64))

        sha = resolve_asset_manifest_sha256(_Store(), "zoomspec", "1.0.0", "golden")
        assert sha == "c" * 64
        assert calls == [("zoomspec", "1.0.0", "golden")]

    def test_resolve_asset_manifest_sha256_unknown_release_raises(self, tmp_path):
        from app.core.errors import PlatformError
        from app.remote_execution.identity import resolve_asset_manifest_sha256
        from app.remote_execution.model_release import ModelReleaseStore

        store = ModelReleaseStore(tmp_path / "plugins", {})
        with pytest.raises(PlatformError):
            resolve_asset_manifest_sha256(store, "missing", "1.0", None)


class TestRequestBuilder:
    KWS = dict(
        local_run_id="run_x",
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version="1.0.0",
        required_remote_runtime_commit=RUN,
        orchestrator_commit=RUN,
        asset_manifest_sha256=MANIFEST,
        remote_profile="autodl_primary",
    )

    def test_freeze_produces_all_frozen_request_keys(self):
        from app.remote_execution.request_builder import FROZEN_REQUEST_KEYS, freeze_request_provenance

        metadata = freeze_request_provenance(**self.KWS)
        assert set(FROZEN_REQUEST_KEYS) == set(metadata.keys())
        assert "coordinator_token" not in metadata
        assert "payload_sha256" not in metadata

    def test_freeze_parameters_is_empty_dict(self):
        from app.remote_execution.request_builder import freeze_request_provenance

        assert freeze_request_provenance(**self.KWS)["parameters"] == {}

    def test_batch_has_one_item_per_run_and_requires_local_run_id(self):
        from app.remote_execution.request_builder import build_batch, freeze_request_provenance

        metadata = freeze_request_provenance(**self.KWS)
        batch = build_batch(metadata)
        assert len(batch.items) == 1
        assert batch.items[0].local_run_id == metadata["local_run_id"] == "run_x"
        assert batch.items[0].request_id == metadata["request_id"]

    def test_request_sha_reconstructs_from_persisted_metadata(self):
        from app.remote_execution.canonical import compute_request_sha256
        from app.remote_execution.request_builder import build_batch, freeze_request_provenance, verify_request_sha256

        metadata = freeze_request_provenance(**self.KWS)
        batch = build_batch(metadata)
        assert compute_request_sha256(batch) == metadata["request_sha256"]
        assert verify_request_sha256(batch, metadata) is True

    def test_two_fresh_freezes_generate_distinct_ids_and_shas(self):
        from app.remote_execution.request_builder import freeze_request_provenance

        a = freeze_request_provenance(**self.KWS)
        b = freeze_request_provenance(**self.KWS)
        assert a["request_id"] != b["request_id"]
        assert a["batch_id"] != b["batch_id"]
        assert a["item_key"] != b["item_key"]
        assert a["request_sha256"] != b["request_sha256"]

    def test_deterministic_id_factory_produces_reproducible_sha(self):
        from app.remote_execution.request_builder import freeze_request_provenance

        seq = iter(["rid_1", "bid_1", "ik_1", "rid_2", "bid_2", "ik_2"])
        a = freeze_request_provenance(**self.KWS, id_factory=lambda: next(seq))
        seq2 = iter(["rid_1", "bid_1", "ik_1", "rid_2", "bid_2", "ik_2"])
        b = freeze_request_provenance(**self.KWS, id_factory=lambda: next(seq2))
        assert a["request_sha256"] == b["request_sha256"]

    def test_ids_are_system_generated_and_format_validated(self):
        import re
        from app.remote_execution.request_builder import freeze_request_provenance

        m = freeze_request_provenance(**self.KWS)
        ident = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
        for key in ("request_id", "batch_id", "item_key"):
            assert ident.fullmatch(m[key]) is not None, f"{key} not a safe identifier"

    def test_no_hardcoded_sha_in_builder(self):
        from app.remote_execution import request_builder

        source = Path(request_builder.__file__).read_text()
        assert "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08" not in source
        assert "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c" not in source
        assert "c98f4f36deb2a4e13462070c5f7c13800288591a" not in source
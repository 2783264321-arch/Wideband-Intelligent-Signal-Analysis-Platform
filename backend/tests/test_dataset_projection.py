import os
import re

from app.datasets.projection import (
    DatasetProjectionResolver,
    compute_dataset_projection_id,
    normalize_dataset_root,
)
from app.recordings.model import RecordingModel


def _recording(root, name: str, *, ground_truth: bool = True) -> RecordingModel:
    data_path = root / "test" / f"{name}.bin"
    return RecordingModel(
        id=f"rec_{root.name}_{name}",
        name=name,
        data_path=str(data_path),
        data_format="float16_interleaved_le",
        source="spacenet",
        external_path=str(data_path),
        sample_rate_hz=1.0,
        center_frequency_hz=2.0,
        frequency_low_hz=1.5,
        frequency_high_hz=2.5,
        num_samples=1,
        duration_s=1.0,
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        has_ground_truth=ground_truth,
    )


def _standalone() -> RecordingModel:
    return RecordingModel(
        id="rec_custom",
        name="custom",
        data_path="recordings/rec_custom/raw.iq",
        data_format="complex64_le",
        source="custom",
        external_path=None,
        sample_rate_hz=1.0,
        center_frequency_hz=0.0,
        frequency_low_hz=-0.5,
        frequency_high_hz=0.5,
        num_samples=1,
        duration_s=1.0,
        dataset_name=None,
        dataset_split=None,
        label_space=None,
        has_ground_truth=False,
    )


def test_root_is_parent_of_split_directory(tmp_path):
    root = tmp_path / "SpaceNet-A"
    sample = root / "test" / "a.bin"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"\x00" * 8)
    assert normalize_dataset_root(str(sample.resolve()), "test") == os.path.normcase(
        os.path.normpath(str(root.resolve()))
    )


def test_two_roots_with_same_display_fields_get_distinct_ids(tmp_path):
    id_a = compute_dataset_projection_id(
        source="spacenet",
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        normalized_root=normalize_dataset_root(str(tmp_path / "SpaceNet-A" / "test" / "a.bin"), "test"),
    )
    id_b = compute_dataset_projection_id(
        source="spacenet",
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        normalized_root=normalize_dataset_root(str(tmp_path / "SpaceNet-B" / "test" / "a.bin"), "test"),
    )
    assert id_a != id_b


def test_id_is_stable_and_url_safe(tmp_path):
    args = dict(
        source="spacenet",
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        normalized_root=str(tmp_path),
    )
    first = compute_dataset_projection_id(**args)
    assert first == compute_dataset_projection_id(**args)
    assert re.fullmatch(r"dsproj_[0-9a-f]{32}", first)


def test_find_for_recording_distinguishes_roots(tmp_path):
    resolver = DatasetProjectionResolver(session=None)
    projection_a = resolver.find_for_recording(_recording(tmp_path / "SpaceNet-A", "a1"))
    projection_b = resolver.find_for_recording(_recording(tmp_path / "SpaceNet-B", "b1"))
    assert projection_a is not None and projection_b is not None
    assert projection_a.dataset_projection_id != projection_b.dataset_projection_id


def test_find_for_recording_returns_none_for_standalone():
    resolver = DatasetProjectionResolver(session=None)
    assert resolver.find_for_recording(_standalone()) is None


def test_resolver_groups_and_scopes_two_roots(session, tmp_path):
    for root, names in [
        (tmp_path / "SpaceNet-A", ["a1", "a2"]),
        (tmp_path / "SpaceNet-B", ["b1"]),
    ]:
        for name in names:
            session.add(_recording(root, name))
    session.commit()

    resolver = DatasetProjectionResolver(session)
    projections, total = resolver.list(50, 0)
    assert total == 2

    member_counts = sorted(
        len(resolver.members(projection.dataset_projection_id)) for projection in projections
    )
    assert member_counts == [1, 2]

    largest = next(
        projection
        for projection in projections
        if len(resolver.members(projection.dataset_projection_id)) == 2
    )
    member_names = {recording.name for recording in resolver.members(largest.dataset_projection_id)}
    assert member_names == {"a1", "a2"}


def test_members_can_require_ground_truth(session, tmp_path):
    root = tmp_path / "SpaceNet-C"
    session.add(_recording(root, "with_gt", ground_truth=True))
    session.add(_recording(root, "without_gt", ground_truth=False))
    session.commit()

    resolver = DatasetProjectionResolver(session)
    projection = resolver.find_for_recording(_recording(root, "with_gt"))
    assert projection is not None
    all_members = resolver.members(projection.dataset_projection_id)
    gt_members = resolver.members(projection.dataset_projection_id, require_ground_truth=True)
    assert len(all_members) == 2
    assert [recording.name for recording in gt_members] == ["with_gt"]

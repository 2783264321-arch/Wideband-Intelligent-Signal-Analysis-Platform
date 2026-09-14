"""Plan B H5 — exact execution-membership selection (CPU-testable, no GPU).

DatasetExperimentCreate has no per-request recording subset field, and
``create_experiment`` targets a whole dataset identity. The original H5 script
therefore would have executed 16 + 16 + 16 = 48, not the approved 16 + 16 + 8 =
40, because the 8-stem tail cycle still resolved the full registered dataset.

This module fixes that WITHOUT changing the production schema/API: it registers
explicit acceptance-only dataset views inside the dedicated H5 DB, using existing
external-path semantics (no IQ copy):

  H5_FULL  dataset view -> exactly the frozen 16 H2_STEMS
  H5_TAIL8 dataset view -> exactly H2_STEMS[:8]

and asserts per-cycle membership before any experiment could ever launch.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import plan_b_common as common

H5_FULL = "SpaceNet-PlanB-H5-Full"
H5_TAIL8 = "SpaceNet-PlanB-H5-Tail8"
H5_SPLIT = "test"
H5_LABEL_SPACE = "spacenet_14"


@dataclass(frozen=True)
class H5Cycle:
    index: int
    dataset_name: str
    stems: tuple[str, ...]

    @property
    def expected_items(self) -> int:
        return len(self.stems)


def h5_cycles() -> list[H5Cycle]:
    """The approved 16 + 16 + 8 = 40 partition with explicit dataset views."""
    full = tuple(common.H2_STEMS)
    return [
        H5Cycle(0, H5_FULL, full),
        H5Cycle(1, H5_FULL, full),
        H5Cycle(2, H5_TAIL8, full[:8]),
    ]


def total_expected_executions() -> int:
    return sum(cycle.expected_items for cycle in h5_cycles())


def register_dataset_view(app, *, dataset_name: str, stems: tuple[str, ...]) -> int:
    """Register ``stems`` as an isolated acceptance-only dataset view.

    Recording rows reference the SAME external SpaceNet files (no IQ copy) and
    are registered with identical Ground Truth. Idempotent per dataset_name.
    """
    from app.datasets.spacenet import SpaceNetAdapter
    from app.ground_truth.model import GroundTruthModel
    from app.recordings.model import RecordingModel

    adapter = SpaceNetAdapter(common.SPACENET_ROOT, common.REPO / "label_spaces", "spacenet_14")
    samples = {}
    for stem in stems:
        sample = adapter.load("test", stem)
        if sample.num_samples <= 0 or not sample.signals:
            raise SystemExit(f"H5 STOP: stem {stem} has zero IQ/GT")
        samples[stem] = sample

    with app.state.database.session_factory() as session:
        existing = {
            row.name
            for row in session.query(RecordingModel)
            .filter_by(dataset_name=dataset_name, dataset_split=H5_SPLIT,
                       label_space=H5_LABEL_SPACE)
            .all()
        }
        for stem in stems:
            if stem in existing:
                continue
            sample = samples[stem]
            recording_id = f"rec_h5_{uuid.uuid4().hex}"
            session.add(RecordingModel(
                id=recording_id, name=sample.id, data_path=sample.data_path.name,
                external_path=str(sample.data_path), data_format=sample.data_format,
                source="spacenet", sample_rate_hz=sample.sample_rate_hz,
                center_frequency_hz=sample.center_frequency_hz,
                frequency_low_hz=sample.frequency_low_hz,
                frequency_high_hz=sample.frequency_high_hz,
                num_samples=sample.num_samples, duration_s=sample.duration_s,
                dataset_name=dataset_name, dataset_split=H5_SPLIT,
                label_space=H5_LABEL_SPACE, has_ground_truth=True,
            ))
            for signal in sample.signals:
                session.add(GroundTruthModel(
                    id=f"gt_{uuid.uuid4().hex}", recording_id=recording_id,
                    t_start_s=signal.t_start_s, t_end_s=signal.t_end_s,
                    f_low_hz=signal.f_low_hz, f_high_hz=signal.f_high_hz,
                    class_id=signal.class_id, class_name=signal.class_name,
                ))
        session.commit()

    with app.state.database.session_factory() as session:
        rows = (session.query(RecordingModel)
                .filter_by(dataset_name=dataset_name, dataset_split=H5_SPLIT,
                           label_space=H5_LABEL_SPACE).all())
        names = sorted(r.name for r in rows)
        if names != sorted(stems):
            raise SystemExit(
                f"H5 STOP: dataset view {dataset_name!r} membership mismatch: {names}"
            )
        return len(rows)


def assert_cycle_membership(app, cycle: H5Cycle) -> dict:
    """Fail closed unless the prepared manifest exactly matches the intended cycle."""
    from app.benchmarks.service import DatasetBenchmarkService

    with app.state.database.session_factory() as session:
        preview = DatasetBenchmarkService(session).prepare_manifest(
            cycle.dataset_name, H5_SPLIT, H5_LABEL_SPACE
        )
        order = [(entry.manifest_order, entry.recording_name) for entry in preview.entries]
    names = [name for _order, name in order]
    checks = {
        "expected_items_exact": preview.expected_recordings == cycle.expected_items,
        "names_exact": sorted(names) == sorted(cycle.stems),
        "order_deterministic": [order_index for order_index, _n in order] == list(range(len(order))),
    }
    if not all(checks.values()):
        raise SystemExit(
            f"H5 STOP: cycle {cycle.index} membership not exact: {checks} names={names}"
        )
    return {
        "dataset_name": cycle.dataset_name,
        "expected_items": preview.expected_recordings,
        "recording_manifest_hash": preview.recording_manifest_hash,
        "names": names,
        "checks": checks,
    }

"""Regression: the DatasetExperiment control-plane wiring must load the SAME
merged certificate authority as the main application.

The DatasetExperiment dependency builder previously constructed its
``ExecutorRegistry`` from repository certificates only, omitting the
per-installation operator store (``<data_root>/runtime_certificates.json``).
That caused remote DatasetExperiment launches to fail closed with
``EXECUTION_NOT_CERTIFIED`` even though the operator certificate existed.

This is a wiring-seam regression: the builder must delegate certificate-store
construction to the accepted shared merge authority
``app.runtime_qualification.install.build_certificate_store`` with the exact
repo path and the exact operator ``data_root``.

GPU REQUIRED: NO. No SSH, no GPU, no inference. The shared helper is replaced by
a deterministic spy and the remote profile is intentionally absent (fail closed).
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.remote_execution.runtime import ExecutionCertificateStore


def test_experiment_wiring_delegates_to_shared_merged_certificate_store(
    tmp_path: Path, monkeypatch
) -> None:
    from app.dataset_experiments import wiring
    from app.runtime_qualification import install as install_module

    calls: list[tuple[Path, Path]] = []

    def spy_build_certificate_store(*, repo_path: Path, data_root: Path) -> ExecutionCertificateStore:
        calls.append((Path(repo_path), Path(data_root)))
        return ExecutionCertificateStore([])

    monkeypatch.setattr(
        install_module, "build_certificate_store", spy_build_certificate_store
    )

    settings = Settings(data_root=tmp_path)
    dependencies = wiring.build_control_plane_dependencies(settings)

    # The shared merge authority must be the single certificate-store seam.
    assert len(calls) == 1, "DatasetExperiment wiring must delegate certificate hydration once"
    repo_path, data_root = calls[0]
    assert repo_path == wiring._plugins_root() / "execution_certificates.json"
    assert data_root == tmp_path

    # The returned builder must still wire the injected store into the registry.
    assert dependencies.executor_registry is not None
    assert dependencies.data_root == tmp_path

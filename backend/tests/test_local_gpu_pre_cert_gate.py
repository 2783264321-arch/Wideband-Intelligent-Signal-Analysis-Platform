"""BHQ-3 C4 — pre-certificate fail-closed gate + legacy metadata boundary.

Proves a plugin declaration cannot self-certify: with a registered local_gpu
provider AND the local_gpu technical capability present, but NO local_gpu
ExecutionCertificate, the platform still reports EXECUTION_NOT_CERTIFIED.

Also proves `executors_supported` derives from
`technical capability ∩ registered provider ∩ exact certificate` — never from the
legacy `cpu_supported` field.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.local_executor import build_local_providers
from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.analysis.job_manager import LocalJobManager
from app.core.config import Settings
from app.core.errors import PlatformError
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.pipelines.registry import create_pipeline_registry
from app.pipelines.plugin_registry import create_plugin_registry
from app.recordings.model import RecordingModel
from app.remote_execution.model_release import (
    ModelReleaseStore,
    load_model_release_defaults,
)
from app.remote_execution.runtime import (
    ExecutionCertificateStore,
    ExecutorRegistry,
    load_execution_certificates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
LABEL_ROOT = REPO_ROOT / "label_spaces"
PIPELINES = BACKEND_ROOT / "app" / "pipelines"
CERT_PATH = PIPELINES / "execution_certificates.json"
GPU_REF = "local:autodl_primary:gpu:7b958347b5af"

PLUGINS = (
    ("cpn_bandwidth_tier", "1.0.0"),
    ("zoomspec_yolo26n_aug_combined_frn_v3", "1.0.0"),
)


def _settings(tmp_path: Path) -> Settings:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    return Settings(
        project_root=REPO_ROOT,
        data_root=tmp_path / "data",
        label_space_root=LABEL_ROOT,
        database_url=f"sqlite:///{tmp_path / 'pre_cert.db'}",
        local_gpu_python_path=Path("/root/miniconda3/bin/python"),
        local_gpu_runtime_ref=GPU_REF,
        local_inference_work_root=tmp_path / "work",
    )


def _production_store() -> ExecutionCertificateStore:
    return ExecutionCertificateStore(load_execution_certificates(CERT_PATH))


def _release_store() -> ModelReleaseStore:
    return ModelReleaseStore(
        PIPELINES, load_model_release_defaults(PIPELINES / "model_release_defaults.json")
    )


def _registry(settings: Settings) -> ExecutorRegistry:
    providers = dict(build_local_providers(settings))
    assert "local_gpu" in providers, "local_gpu provider must be configured for this gate"
    return ExecutorRegistry(providers, _production_store())


def test_no_local_gpu_certificate_exists_in_production_store():
    certs = load_execution_certificates(CERT_PATH)
    assert [c for c in certs if c.executor == "local_gpu"] == []


@pytest.mark.parametrize("plugin_id,version", PLUGINS)
def test_local_gpu_capability_without_certificate_is_not_certified(tmp_path, plugin_id, version):
    settings = _settings(tmp_path)
    registry = _registry(settings)
    definition = create_plugin_registry().get(plugin_id, version).definition
    resolved = _release_store().resolve(plugin_id, version, "golden")
    recording = SimpleNamespace(label_space="spacenet_14", id="rec")

    # Technical capability is declared, provider is registered, but no certificate.
    assert ("local_gpu", "cuda", "float16") in [
        (c.executor, c.device_type, c.precision) for c in definition.technical_execution_capabilities
    ]
    result = registry.availability_for(definition, resolved, recording, "local_gpu")
    assert result.available is False
    assert result.reason_code == "EXECUTION_NOT_CERTIFIED"

    supported, _ = registry.deployment_qualified_executors(definition, "golden")
    assert "local_gpu" not in supported


@pytest.mark.parametrize("plugin_id,version", PLUGINS)
def test_analysis_service_prepare_run_local_gpu_is_uncertified(tmp_path, plugin_id, version):
    settings = _settings(tmp_path)
    registry = _registry(settings)

    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)

    with database.session_factory() as session:
        session.add(
            RecordingModel(
                id="rec_precert",
                name="precert",
                data_path="rec.bin",
                data_format="float16_interleaved_le",
                source="custom",
                sample_rate_hz=20_000_000.0,
                center_frequency_hz=2_421_000_000.0,
                frequency_low_hz=2_411_000_000.0,
                frequency_high_hz=2_431_000_000.0,
                num_samples=1_200_000,
                duration_s=0.06,
                dataset_name="SpaceNet",
                dataset_split="test",
                label_space="spacenet_14",
                has_ground_truth=False,
            )
        )
        session.commit()

        service = AnalysisService(
            session,
            create_pipeline_registry(),
            LocalJobManager(settings),
            model_release_store=_release_store(),
            project_root=REPO_ROOT,
            data_root=settings.data_root,
            executor_registry=registry,
        )
        with pytest.raises(PlatformError) as exc:
            service.prepare_run(
                recording_id="rec_precert",
                pipeline_id=plugin_id,
                executor="local_gpu",
                parameters={},
                model_release_id="golden",
            )
        assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_executors_supported_ignores_legacy_cpu_supported(tmp_path):
    """A definition with cpu_supported=True but no local_cpu technical capability
    is NOT runnable, and a registered provider without an exact certificate never
    contributes to the deployment-qualified projection."""
    settings = _settings(tmp_path)
    registry = _registry(settings)

    definition = PipelineDefinition(
        id="legacy_cpu_claim",
        name="Legacy CPU Claim",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,  # legacy metadata only
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
        recommended_execution="remote_gpu",
    )
    supported, recommended = registry.deployment_qualified_executors(definition, "golden")
    assert supported == []
    assert recommended is None

    # A registered local_cpu provider whose runtime generation has no certificate
    # for this definition must not make it runnable either.
    definition_with_cpu_cap = PipelineDefinition(
        id="cpu_cap_no_cert",
        name="CPU Capability Without Certificate",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
    )
    supported2, recommended2 = registry.deployment_qualified_executors(definition_with_cpu_cap, None)
    assert "local_cpu" not in supported2  # no local_cpu cert in the production store
    assert recommended2 is None

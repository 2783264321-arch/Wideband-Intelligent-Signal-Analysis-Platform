"""A3 Task 6: operator CLI (stdlib argparse).

    wisa runtime doctor
    wisa qualify --plugin <id> [--plugin-version <v>] --executor <e> [--model-release <id>]
    wisa certificate install --from <evidence_dir>
    wisa certificate list

Also runnable as ``python -m app.cli``. No HTTP API, no frontend.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from app.analysis.local_executor import build_local_providers
from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.registry import create_pipeline_registry
from app.remote_execution.model_release import ModelReleaseStore, load_model_release_defaults
from app.remote_execution.runtime import ExecutorRegistry
from app.runtime_qualification import doctor as doctor_module
from app.runtime_qualification import identity as identity_module
from app.runtime_qualification.evidence import load_evidence_dir, write_evidence
from app.runtime_qualification.install import (
    build_certificate_store,
    install_certificate,
    load_operator_certificates,
    load_execution_certificates,
    resolve_live_authority,
)
from app.runtime_qualification.qualification import (
    REMOTE_COMMIT_V1,
    QualificationTarget,
    build_default_target_probe,
    parse_remote_runtime_ref,
    run_qualification,
    select_runner,
)

_EXECUTORS = ("local_cpu", "local_gpu", "remote_gpu")


@dataclass
class CliContext:
    settings: Settings
    pipeline_registry: object
    model_release_store: object
    executor_registry: object
    repo_certificate_path: Path
    data_root: Path
    # Plan C C-pre-1: present only when a valid RemoteProfile is configured.
    remote_profile: object | None = None
    remote_transport: object | None = None


def _repo_certificate_path() -> Path:
    return Path(__file__).resolve().parent / "pipelines" / "execution_certificates.json"


def _build_remote_provider(settings: Settings, providers: dict) -> tuple[object | None, object | None]:
    """Register the production ``remote_gpu`` provider when a valid profile exists.

    Mirrors the application remote bootstrap. Construction performs NO network
    I/O and launches NO coordinator: ``CoordinatorJobManager`` is only a launcher
    object. A missing/invalid profile leaves local executors fully usable and the
    ``remote_gpu`` provider absent (remote qualification then fails closed).
    """
    try:
        from app.remote_execution.coordinator_job_manager import CoordinatorJobManager
        from app.remote_execution.executor import SshRemoteExecutorProbe
        from app.remote_execution.profile import RemoteProfile
        from app.remote_execution.runtime import RemoteGpuExecutorProvider
        from app.remote_execution.transport import SshRunner
    except Exception:
        return None, None
    try:
        profile = RemoteProfile.from_env(settings)
        transport = SshRunner(profile)
        probe = SshRemoteExecutorProbe(
            profile,
            transport,
            expected_runtime_commit=profile.required_remote_runtime_commit,
        )
        provider = RemoteGpuExecutorProvider(
            profile=profile,
            probe=probe,
            launcher=CoordinatorJobManager(settings),
            required_runtime_commit=profile.required_remote_runtime_commit,
        )
    except Exception:
        return None, None
    providers[provider.name] = provider
    return profile, transport


def _default_context(settings: Settings) -> CliContext:
    plugins_root = Path(__file__).resolve().parent / "pipelines"
    model_release_store = ModelReleaseStore(
        plugins_root, load_model_release_defaults(plugins_root / "model_release_defaults.json")
    )
    repo_path = _repo_certificate_path()
    providers = dict(build_local_providers(settings))
    remote_profile, remote_transport = _build_remote_provider(settings, providers)
    executor_registry = ExecutorRegistry(
        providers, build_certificate_store(repo_path=repo_path, data_root=settings.data_root)
    )
    return CliContext(
        settings=settings,
        pipeline_registry=create_pipeline_registry(),
        model_release_store=model_release_store,
        executor_registry=executor_registry,
        repo_certificate_path=repo_path,
        data_root=settings.data_root,
        remote_profile=remote_profile,
        remote_transport=remote_transport,
    )


def _providers_of(executor_registry: object) -> dict:
    getter = getattr(executor_registry, "providers", None)
    if callable(getter):
        return dict(getter())
    return dict(getattr(executor_registry, "_providers", {}) or {})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wisa")
    sub = parser.add_subparsers(dest="command")

    runtime = sub.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="runtime_command")
    runtime_sub.add_parser("doctor")

    qualify = sub.add_parser("qualify")
    qualify.add_argument("--plugin", required=True)
    qualify.add_argument("--plugin-version", default=None)
    qualify.add_argument("--executor", required=True, choices=_EXECUTORS)
    qualify.add_argument("--model-release", default=None)

    certificate = sub.add_parser("certificate")
    certificate_sub = certificate.add_subparsers(dest="certificate_command")
    install = certificate_sub.add_parser("install")
    install.add_argument("--from", dest="from_dir", required=True)
    certificate_sub.add_parser("list")

    return parser


def _make_identity_resolver(
    repo_provenance: frozenset[tuple[str, str]],
    *,
    expected_remote_runtime_ref: str | None = None,
):
    """Derive and compare each provider's identity against the interpreter material.

    ``legacy_opaque`` is provenance-based, keyed by ``(executor, runtime_ref)``, and
    is only ever assigned to an existing repo-default identity that has no supported
    A3 derivation scheme. ``local_gpu`` always uses ``bhq3_gpu_v1`` and is never
    ``legacy_opaque`` merely because its ref appears in repo defaults. The operator
    ``runtime_family`` is the family authority, never the family embedded in the ref.
    """

    def _derive(configured, scheme, settings, python_attr, family, kind):
        python_path = getattr(settings, python_attr, None)
        try:
            material = identity_module.collect_identity_material(python_path, scheme=scheme)
            generation = identity_module.derive_generation_for_scheme(scheme=scheme, material=material)
        except PlatformError:
            return (configured, None, scheme, "unavailable")
        derived = identity_module.derive_local_runtime_ref(family=family, kind=kind, generation=generation)
        return (configured, derived, scheme, "match" if derived == configured else "mismatch")

    def resolver(spec, settings):
        configured = spec.runtime_ref
        if configured is None:
            return (None, None, spec.identity_scheme, "not_configured")

        if spec.executor == "remote_gpu":
            # The remote identity is profile + exact commit only (`remote_commit_v1`).
            # `match` requires a well-formed profile-derived ref; when the CLI has a
            # configured profile it must equal that exact deployment identity.
            if parse_remote_runtime_ref(configured) is None:
                return (configured, None, REMOTE_COMMIT_V1, "mismatch")
            if (
                expected_remote_runtime_ref is not None
                and configured != expected_remote_runtime_ref
            ):
                return (configured, None, REMOTE_COMMIT_V1, "mismatch")
            return (configured, None, REMOTE_COMMIT_V1, "match")

        parsed = identity_module.parse_local_runtime_ref(configured)

        if spec.executor == "local_gpu":
            if parsed is None:
                return (configured, None, identity_module.BHQ3_GPU_V1, "mismatch")
            if settings.runtime_family is None:
                return (configured, None, identity_module.BHQ3_GPU_V1, "unavailable")
            family, kind = parsed
            if kind != "gpu" or family != settings.runtime_family:
                return (configured, None, identity_module.BHQ3_GPU_V1, "mismatch")
            return _derive(
                configured, identity_module.BHQ3_GPU_V1, settings, "local_gpu_python_path",
                settings.runtime_family, "gpu",
            )

        if spec.executor != "local_cpu":
            return (configured, None, identity_module.UNAVAILABLE, "unavailable")

        if (spec.executor, configured) in repo_provenance:
            if settings.runtime_family is not None and parsed is not None:
                family, kind = parsed
                if family != settings.runtime_family or kind != "cpu":
                    return (configured, None, identity_module.LOCAL_CPU_V1, "mismatch")
            return (configured, None, identity_module.LEGACY_OPAQUE, "legacy_opaque")

        if settings.runtime_family is None:
            return (configured, None, identity_module.LOCAL_CPU_V1, "unavailable")
        if parsed is None:
            return (configured, None, identity_module.LOCAL_CPU_V1, "mismatch")
        family, kind = parsed
        if kind != "cpu" or family != settings.runtime_family:
            return (configured, None, identity_module.LOCAL_CPU_V1, "mismatch")
        return _derive(
            configured, identity_module.LOCAL_CPU_V1, settings, "local_cpu_python_path",
            settings.runtime_family, "cpu",
        )

    return resolver


def _cmd_runtime_doctor(ctx: CliContext, out) -> int:
    repo_provenance = frozenset(
        (certificate.executor, certificate.runtime_ref)
        for certificate in load_execution_certificates(ctx.repo_certificate_path)
    )
    specs = doctor_module.build_provider_specs(ctx.settings, _providers_of(ctx.executor_registry))
    expected_remote_runtime_ref = None
    if ctx.remote_profile is not None:
        expected_remote_runtime_ref = (
            f"remote:{ctx.remote_profile.name}:"
            f"{ctx.remote_profile.required_remote_runtime_commit}"
        )
    report = doctor_module.build_runtime_doctor_report(
        settings=ctx.settings,
        provider_specs=specs,
        interpreter_probe=doctor_module.SubprocessInterpreterProbe(),
        gpu_probe=doctor_module.NvidiaSmiGpuProbe(),
        identity_resolver=_make_identity_resolver(
            repo_provenance, expected_remote_runtime_ref=expected_remote_runtime_ref
        ),
    )
    print(json.dumps(report.to_operator_json(), indent=2), file=out)
    return 0


def _cmd_qualify(ctx: CliContext, args, out) -> int:
    definition = ctx.pipeline_registry.get(args.plugin).definition
    if args.plugin_version is not None and args.plugin_version != definition.plugin_version:
        raise PlatformError("PLUGIN_NOT_FOUND", "Requested plugin version is not registered.")

    if definition.model_release_required:
        resolved = ctx.model_release_store.resolve(
            args.plugin, definition.plugin_version, args.model_release
        )
        model_release_id: str | None = resolved.release.model_release_id
        asset_manifest_sha256: str | None = resolved.manifest.asset_manifest_sha256
    else:
        if args.model_release is not None:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Release-less plugin must not carry a model release identity.",
            )
        model_release_id = None
        asset_manifest_sha256 = None

    provider = _providers_of(ctx.executor_registry).get(args.executor)
    if provider is None:
        raise PlatformError(
            "EXECUTION_CAPABILITY_UNAVAILABLE",
            f"No executor provider is registered for '{args.executor}'.",
        )
    target = QualificationTarget(
        plugin_id=args.plugin,
        plugin_version=definition.plugin_version,
        model_release_id=model_release_id,
        executor=args.executor,
        runtime_ref=provider.runtime_ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )

    input_identity: dict | None = None
    if args.executor in ("local_cpu", "local_gpu"):
        probe = build_default_target_probe(
            target=target,
            settings=ctx.settings,
            pipeline_registry=ctx.pipeline_registry,
            model_release_store=ctx.model_release_store,
            executor_registry=ctx.executor_registry,
        )
        runner = select_runner(executor=args.executor, target_probe=probe)
    elif args.executor == "remote_gpu":
        # Plan C C-pre-1: the real remote probe over the configured transport.
        # When the remote profile/transport is absent the probe fails closed.
        probe = build_default_target_probe(
            target=target,
            settings=ctx.settings,
            pipeline_registry=ctx.pipeline_registry,
            model_release_store=ctx.model_release_store,
            executor_registry=ctx.executor_registry,
            remote_profile=ctx.remote_profile,
            remote_transport=ctx.remote_transport,
        )
        runner = select_runner(executor="remote_gpu", target_probe=probe)
        if ctx.remote_profile is not None:
            input_identity = {
                "remote_profile": ctx.remote_profile.name,
                "required_remote_runtime_commit": ctx.remote_profile.required_remote_runtime_commit,
            }
    else:
        runner = select_runner(executor=args.executor, target_probe=None)

    evidence = run_qualification(
        target=target,
        runner=runner,
        asset_manifest_sha256=asset_manifest_sha256,
        input_identity=input_identity,
    )
    path = write_evidence(ctx.data_root, evidence)
    print(
        json.dumps(
            {
                "passed": evidence.passed,
                "qualification_type": evidence.qualification_type,
                "identity_scheme": evidence.identity_scheme,
                "runtime_ref": evidence.runtime_ref,
                "evidence_dir": str(path.parent),
                "results": [
                    {"name": result.name, "passed": result.passed, "detail": result.detail}
                    for result in evidence.results
                ],
            },
            indent=2,
        ),
        file=out,
    )
    return 0 if evidence.passed else 1


def _cmd_certificate_install(ctx: CliContext, args, out) -> int:
    evidence = load_evidence_dir(Path(args.from_dir))
    authority = resolve_live_authority(
        registry=ctx.pipeline_registry,
        model_release_store=ctx.model_release_store,
        executor_registry=ctx.executor_registry,
        plugin_id=evidence.plugin_id,
        plugin_version=evidence.plugin_version,
        executor=evidence.executor,
        requested_model_release_id=evidence.model_release_id,
        data_root=ctx.data_root,
    )
    result = install_certificate(
        data_root=ctx.data_root,
        repo_certificate_path=ctx.repo_certificate_path,
        evidence=evidence,
        authority=authority,
    )
    print(json.dumps({"status": result.status, "certificate": result.certificate.__dict__}, indent=2), file=out)
    print(
        "certificate becomes effective on next control-plane restart / registry rebuild",
        file=out,
    )
    return 0


def _cmd_certificate_list(ctx: CliContext, out) -> int:
    certificates = load_operator_certificates(ctx.data_root)
    print(json.dumps([certificate.__dict__ for certificate in certificates], indent=2), file=out)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    context_factory=None,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        ctx = (context_factory or _default_context)(Settings())
        if args.command == "runtime" and args.runtime_command == "doctor":
            return _cmd_runtime_doctor(ctx, out)
        if args.command == "qualify":
            return _cmd_qualify(ctx, args, out)
        if args.command == "certificate" and args.certificate_command == "install":
            return _cmd_certificate_install(ctx, args, out)
        if args.command == "certificate" and args.certificate_command == "list":
            return _cmd_certificate_list(ctx, out)
    except PlatformError as exc:
        print(f"{exc.code}: {exc.message}", file=err)
        return 1
    parser.print_usage(err)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""TASK D1 — RuntimeDescriptor: public/private projection + metadata round-trip."""

from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.remote_execution.runtime import RuntimeDescriptor

RUNTIME_COMMIT = "5bb5be4b04d04a071bc9d8f4f61172595ecee037"
RUNTIME_REF = f"remote:autodl_primary:{RUNTIME_COMMIT}"


def _cuda() -> RuntimeDescriptor:
    return RuntimeDescriptor(
        executor="remote_gpu",
        device_type="cuda",
        device_index=0,
        precision="float16",
        environment_ref="/root/miniconda3/bin/python",
        environment_label=RUNTIME_REF,
    )


def test_public_projection_cpu_and_cuda():
    cpu = RuntimeDescriptor(
        executor="local_cpu", device_type="cpu", device_index=None, precision="float32"
    )
    assert cpu.public_device() == "cpu"
    assert cpu.public_projection() == {"executor": "local_cpu", "device": "cpu", "environment": None}

    cuda = _cuda()
    assert cuda.public_device() == "cuda:0"
    assert cuda.public_projection() == {
        "executor": "remote_gpu",
        "device": "cuda:0",
        "environment": RUNTIME_REF,
    }


def test_public_device_unknown_returns_none():
    unknown = RuntimeDescriptor(
        executor="local_gpu", device_type="mps", device_index=3, precision="float32"
    )
    assert unknown.public_device() is None


def test_environment_ref_never_in_public_projection():
    descriptor = _cuda()
    projection = descriptor.public_projection()
    assert projection["environment"] == RUNTIME_REF
    assert projection["environment"] != descriptor.environment_ref
    assert descriptor.environment_ref not in projection.values()
    assert descriptor.environment_ref in descriptor.to_metadata().values()


def test_environment_label_none_projects_none():
    descriptor = RuntimeDescriptor(
        executor="local_cpu", device_type="cpu", device_index=None, precision="float32"
    )
    assert descriptor.public_projection()["environment"] is None


def test_descriptor_roundtrip_metadata():
    descriptor = _cuda()
    metadata = descriptor.to_metadata()
    assert metadata == {
        "executor": "remote_gpu",
        "device_type": "cuda",
        "device_index": 0,
        "precision": "float16",
        "environment_ref": "/root/miniconda3/bin/python",
        "environment_label": RUNTIME_REF,
    }
    assert RuntimeDescriptor.from_metadata(metadata) == descriptor


def test_from_metadata_none_is_none():
    assert RuntimeDescriptor.from_metadata(None) is None


def test_from_metadata_malformed_fails_closed():
    with pytest.raises(PlatformError) as exc:
        RuntimeDescriptor.from_metadata({"executor": "remote_gpu"})
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"

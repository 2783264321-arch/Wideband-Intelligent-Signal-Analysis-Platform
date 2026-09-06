"""Canonical request identity for remote execution batches.

The request SHA256 MUST exclude the supplied ``request_sha256`` field so that a
submit retry with an identical semantic request reproduces the same identity.
Numeric canonicalization reuses ``app.benchmarks.manifest.canonical_number`` so
floats are emitted as raw JSON number tokens (never quoted strings) and ``-0.0``
canonicalizes identically to ``0.0``.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math

from app.benchmarks.manifest import canonical_number
from app.remote_execution.schema import RemoteExecutionBatchV1


def canonical_request_payload(batch: RemoteExecutionBatchV1) -> dict:
    """Every semantic batch field except ``request_sha256``."""
    return batch.model_dump(exclude={"request_sha256"})


def _encode(value: object) -> bytes:
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number in canonical payload: {value!r}")
        return canonical_number(value).encode("ascii")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(_encode(item) for item in value) + b"]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise ValueError(f"non-string dict key in canonical payload: {key!r}")
            key_bytes = json.dumps(key, ensure_ascii=False).encode("utf-8")
            parts.append(key_bytes + b":" + _encode(value[key]))
        return b"{" + b",".join(parts) + b"}"
    raise TypeError(f"unsupported value in canonical payload: {value!r}")


def canonical_request_bytes(payload: dict) -> bytes:
    return _encode(payload)


def compute_request_sha256(batch: RemoteExecutionBatchV1) -> str:
    return sha256(canonical_request_bytes(canonical_request_payload(batch))).hexdigest()
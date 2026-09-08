"""Coordinator-token helpers for remote run metadata.

Keeps the per-launch local fencing token out of the request builder and out of
``AnalysisService``. ``coordinator_token`` is excluded from the wire batch and
from ``request_sha256``; it is added only to the FINAL LOCAL EXECUTION METADATA
layer that is persisted before coordinator launch.
"""
from __future__ import annotations

from uuid import uuid4


def build_coordinator_metadata(metadata: dict, *, coordinator_token: str | None = None) -> dict:
    """Return the final local execution metadata = frozen request metadata + a
    fresh (or supplied) coordinator token, without mutating the input."""
    token = coordinator_token or find_or_new_coordinator_token(metadata)
    result = dict(metadata)
    result["coordinator_token"] = token
    return result


def find_or_new_coordinator_token(metadata: dict) -> str:
    token = metadata.get("coordinator_token")
    if token:
        return token
    return f"coord_{uuid4().hex}"


def rotate_coordinator_token(metadata: dict) -> str:
    """Persist a NEW coordinator token (used by startup recovery before a
    replacement launch). Returns the new token. Does not alter request fields."""
    token = f"coord_{uuid4().hex}"
    metadata["coordinator_token"] = token
    return token
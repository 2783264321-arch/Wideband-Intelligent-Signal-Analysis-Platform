"""Public read models for the Auto execution-selection explanation API.

These models expose only booleans and platform-owned bounded reason text. Raw
provider reason detail, private runtime references, and certificate internals
are never part of this contract. ``reason_message`` is the public projection of
the internal ``ExecutionCandidate.safe_reason_message``.
"""
from pydantic import BaseModel


class ExecutionCandidateRead(BaseModel):
    executor: str
    technical: bool
    configured: bool
    certified: bool
    available: bool
    reason_code: str | None = None
    reason_message: str | None = None


class ExecutorSelectionRead(BaseModel):
    requested_mode: str
    resolved_executor: str | None = None
    reason_code: str
    reason: str
    workload_class: str
    candidates: list[ExecutionCandidateRead]
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DeleteBlockerRead(BaseModel):
    kind: Literal[
        "dataset_evaluation",
        "dataset_experiment",
        "dataset_experiment_attempt",
        "imported_batch",
    ]
    resource_id: str
    reference: Literal["recording", "analysis_run"]


def blocker_details(blockers) -> dict:
    return {
        "blockers": [
            DeleteBlockerRead(
                kind=blocker.kind,
                resource_id=blocker.resource_id,
                reference=blocker.reference,
            ).model_dump()
            for blocker in blockers
        ]
    }

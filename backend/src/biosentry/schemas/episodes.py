"""Body of POST /sim/runs/{id}/episodes: when the simulator's deterioration episodes began
(contract-truth v1.1). Not part of the dashboard contract. Four fields, no free text."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .base import Contract
from .ingest import Id

RunMinute = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]


class Episode(Contract):
    patient_id: Id
    kind: Literal["respiratory", "tachycardic", "hypotensive"]
    start_minute: RunMinute
    full_minute: RunMinute

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.full_minute < self.start_minute:
            raise ValueError("full_minute is not before start_minute")
        return self


class Episodes(Contract):
    episodes: Annotated[list[Episode], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _one_each(self) -> Self:
        ids = [e.patient_id for e in self.episodes]
        if len(ids) != len(set(ids)):
            raise ValueError("one episode for each patient")
        return self

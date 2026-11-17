"""A message in the signed-in person's patient conversation."""

from typing import Literal

from .base import Contract


class AssistantMessage(Contract):
    id: int
    role: Literal["user", "assistant"]
    text: str
    lab_run_id: int | None
    created_at: str

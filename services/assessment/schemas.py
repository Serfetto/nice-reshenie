"""Схемы запроса расчёта."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _naive_utc(v):
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


class SourceOverrideIn(BaseModel):
    state: Literal["disabled", "frozen"]
    at: datetime | None = None

    @field_validator("at")
    @classmethod
    def norm_at(cls, v):
        return _naive_utc(v)

    @model_validator(mode="after")
    def check_frozen(self):
        if self.state == "frozen" and self.at is None:
            raise ValueError("для frozen нужно указать at — момент заморозки")
        return self


class EvaParams(BaseModel):
    return_to_airlock_min: int = Field(30, ge=0, le=240)
    overrun_margin_min: int = Field(60, ge=0, le=240)


class RunRequest(BaseModel):
    mode: Literal["now", "replay", "review"] = "now"
    as_of: datetime | None = Field(None, description="Момент отсечки (replay) или опорный момент (review)")
    duration_min: int = Field(390, ge=60, le=480)
    earliest_start: datetime | None = None
    latest_start: datetime | None = None
    planned_start: datetime | None = None
    step_min: int | None = Field(None, ge=5, le=60)
    eva: EvaParams = EvaParams()
    mechanisms: list[Literal["radiation", "mmod"]] = ["radiation", "mmod"]
    source_overrides: dict[str, SourceOverrideIn] = {}
    radiation_model: Literal["team", "persistence", "swpc_only"] = "team"

    @field_validator("as_of", "earliest_start", "latest_start", "planned_start")
    @classmethod
    def norm_times(cls, v):
        return _naive_utc(v)

    @model_validator(mode="after")
    def check_mode(self):
        if self.mode in ("replay", "review") and self.as_of is None:
            raise ValueError("для режимов replay и review нужен as_of")
        if not self.mechanisms:
            raise ValueError("нужен хотя бы один механизм")
        return self

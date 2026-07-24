from enum import IntEnum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PriorityTier(IntEnum):
    high = 1
    normal = 2
    low = 3


class AcquireRequest(BaseModel):
    caller_id: str = Field(min_length=1)
    vram_mb: int = Field(gt=0)
    tier: PriorityTier = PriorityTier.normal
    deadline_seconds: Optional[float] = Field(default=None, gt=0)


class AcquireResponse(BaseModel):
    lease_id: Optional[str]
    result: Literal["permit", "deny", "shed"]
    vram_mb: int
    message: str
    request_id: str


class ReleaseRequest(BaseModel):
    lease_id: str = Field(min_length=1)
    caller_id: str = Field(min_length=1)


class ReleaseResponse(BaseModel):
    released: bool
    message: str
    request_id: str


class DecisionCounters(BaseModel):
    permit: int
    deny: int
    shed: int


class StatsResponse(BaseModel):
    mode: str
    vram_total_mb: int
    vram_available_mb: int
    vram_committed_mb: int
    soft_floor_mb: int
    hard_floor_mb: int
    active_leases: int
    queue_depth: int
    queue_depth_by_tier: dict[str, int]
    decision_reasons: dict[str, int]
    decisions: DecisionCounters
    wait_ms_total: float
    wait_ms_count: int


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
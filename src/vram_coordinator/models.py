from pydantic import BaseModel, Field
from enum import IntEnum
from typing import Optional
import uuid


class PriorityTier(IntEnum):
    high = 1
    normal = 2
    low = 3


class AcquireRequest(BaseModel):
    caller_id: str
    vram_mb: int = Field(gt=0)
    tier: PriorityTier = PriorityTier.normal
    deadline_seconds: Optional[float] = 30.0


class AcquireResponse(BaseModel):
    lease_id: Optional[str]
    result: str  # permit | deny | shed
    vram_mb: int
    message: str


class ReleaseRequest(BaseModel):
    lease_id: str
    caller_id: str


class ReleaseResponse(BaseModel):
    released: bool
    message: str


class StatsResponse(BaseModel):
    mode: str
    vram_total_mb: int
    vram_available_mb: int
    vram_committed_mb: int
    soft_floor_mb: int
    hard_floor_mb: int
    active_leases: int
    queue_depth: int
    decisions: dict
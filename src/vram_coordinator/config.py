from enum import Enum
from typing import Optional

from pydantic_settings import BaseSettings


class CoordinatorMode(str, Enum):
    observe = "observe"
    enforce = "enforce"


class Settings(BaseSettings):
    coordinator_mode: CoordinatorMode = CoordinatorMode.observe
    listen_host: str = "0.0.0.0"
    listen_port: int = 8787

    soft_floor_mb: int = 3072
    hard_floor_mb: int = 1536
    safety_overhead_mb: int = 768

    lease_ttl_seconds: int = 300
    max_queue_depth: int = 20
    max_queue_depth_high: int = 8
    max_queue_depth_normal: int = 8
    max_queue_depth_low: int = 4

    default_deadline_seconds: float = 30.0
    deadline_seconds_high: float = 15.0
    deadline_seconds_normal: float = 30.0
    deadline_seconds_low: float = 45.0

    low_tier_shed_under_soft_pressure: bool = True

    require_api_token: bool = False
    api_token: Optional[str] = None
    enforce_allowlist: bool = False
    allowed_callers: str = ""

    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def allowed_callers_set(self) -> set[str]:
        callers = [item.strip() for item in self.allowed_callers.split(",") if item.strip()]
        return set(callers)

    def deadline_for_tier(self, tier: int, requested: Optional[float]) -> float:
        if requested is not None:
            return requested
        if tier == 1:
            return self.deadline_seconds_high
        if tier == 2:
            return self.deadline_seconds_normal
        return self.deadline_seconds_low

    def tier_queue_limit(self, tier: int) -> int:
        if tier == 1:
            return self.max_queue_depth_high
        if tier == 2:
            return self.max_queue_depth_normal
        return self.max_queue_depth_low

    def validate_policy(self) -> None:
        if self.hard_floor_mb < 0 or self.soft_floor_mb < 0 or self.safety_overhead_mb < 0:
            raise ValueError("floor and safety settings must be non-negative")
        if self.hard_floor_mb > self.soft_floor_mb:
            raise ValueError("HARD_FLOOR_MB must be <= SOFT_FLOOR_MB")
        if self.max_queue_depth < 0:
            raise ValueError("MAX_QUEUE_DEPTH must be >= 0")
        if self.max_queue_depth_high < 0 or self.max_queue_depth_normal < 0 or self.max_queue_depth_low < 0:
            raise ValueError("per-tier queue depths must be >= 0")
        if self.default_deadline_seconds <= 0:
            raise ValueError("DEFAULT_DEADLINE_SECONDS must be > 0")
        if self.deadline_seconds_high <= 0 or self.deadline_seconds_normal <= 0 or self.deadline_seconds_low <= 0:
            raise ValueError("per-tier deadlines must be > 0")
        if self.require_api_token and not self.api_token:
            raise ValueError("REQUIRE_API_TOKEN is true but API_TOKEN is empty")
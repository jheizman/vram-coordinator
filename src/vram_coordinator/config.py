import time
from collections import deque
from enum import Enum
from typing import Optional

from pydantic_settings import BaseSettings


class CoordinatorMode(str, Enum):
    observe = "observe"
    enforce = "enforce"


class EnforceScope(str, Enum):
    low = "low"
    normal = "normal"
    all = "all"


TIER_SCOPE_THRESHOLD: dict[str, int] = {
    "low": 3,    # enforce tier 3 (low) and above
    "normal": 2, # enforce tier 2 (normal) and above
    "all": 1,    # enforce all tiers
}


class TripwireState:
    """Rolling-window deny-rate guardrail."""
    def __init__(self, window_seconds: float = 60.0, max_deny_rate: float = 0.5, min_samples: int = 10):
        self.window_seconds = window_seconds
        self.max_deny_rate = max_deny_rate
        self.min_samples = min_samples
        self._events: deque = deque()

    def record(self, denied: bool) -> None:
        now = time.monotonic()
        self._events.append((now, denied))
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def tripped(self) -> tuple[bool, float]:
        now = time.monotonic()
        self._prune(now)
        total = len(self._events)
        if total < self.min_samples:
            return False, 0.0
        deny_count = sum(1 for _, denied in self._events if denied)
        rate = deny_count / total
        return rate >= self.max_deny_rate, rate


class Settings(BaseSettings):
    coordinator_mode: CoordinatorMode = CoordinatorMode.observe
    enforce_scope: EnforceScope = EnforceScope.all
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

    tripwire_enabled: bool = True
    tripwire_window_seconds: float = 60.0
    tripwire_max_deny_rate: float = 0.5
    tripwire_min_samples: int = 10

    admin_token: Optional[str] = None

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

    def tier_is_enforced(self, tier: int) -> bool:
        if self.coordinator_mode == CoordinatorMode.observe:
            return False
        threshold = TIER_SCOPE_THRESHOLD[self.enforce_scope.value]
        return tier >= threshold

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
        if 0.0 >= self.tripwire_max_deny_rate or self.tripwire_max_deny_rate > 1.0:
            raise ValueError("TRIPWIRE_MAX_DENY_RATE must be between 0 and 1 exclusive")
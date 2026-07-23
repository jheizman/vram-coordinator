from pydantic_settings import BaseSettings
from enum import Enum


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
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
import logging
from typing import Optional

log = logging.getLogger(__name__)

_last_known: Optional[dict] = None


def query_vram() -> Optional[dict]:
    """Return dict with total_mb and used_mb, or None on failure."""
    global _last_known
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        result = {
            "total_mb": info.total // (1024 * 1024),
            "used_mb": info.used // (1024 * 1024),
        }
        _last_known = result
        return result
    except Exception as exc:
        log.warning("GPU query failed: %s — using last known value", exc)
        return _last_known
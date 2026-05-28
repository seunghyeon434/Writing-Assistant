from __future__ import annotations

from contextlib import contextmanager
import threading


_pause_event = threading.Event()
_pause_depth_lock = threading.Lock()
_pause_depth = 0


def is_realtime_reading_paused() -> bool:
    return _pause_event.is_set()


@contextmanager
def pause_realtime_reading():
    global _pause_depth
    with _pause_depth_lock:
        _pause_depth += 1
        _pause_event.set()
    try:
        yield
    finally:
        with _pause_depth_lock:
            _pause_depth = max(0, _pause_depth - 1)
            if _pause_depth == 0:
                _pause_event.clear()

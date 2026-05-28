from __future__ import annotations

import threading


_active_mode = "clipboard"
_lock = threading.Lock()


def set_active_input_mode(mode: str):
    global _active_mode
    normalized = mode if mode in {"clipboard", "drag", "realtime"} else "clipboard"
    with _lock:
        _active_mode = normalized


def get_active_input_mode() -> str:
    with _lock:
        return _active_mode


def is_input_mode_active(mode: str) -> bool:
    return get_active_input_mode() == mode

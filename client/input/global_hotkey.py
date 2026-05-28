from __future__ import annotations

import ctypes
import threading
import time
from pathlib import Path
from ctypes import wintypes

_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_HOTKEY_LOG_PATH = _LOG_DIR / "global_hotkey.log"

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_RETURN = 0x0D
APPLY_CORRECTION_HOTKEY_ID = 0x5741


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def start_global_hotkey_listener(callback):
    thread = threading.Thread(
        target=_run_hotkey_loop,
        args=(callback,),
        daemon=True,
        name="writing-assistant-hotkey",
    )
    thread.start()
    return thread


def _run_hotkey_loop(callback):
    user32 = ctypes.windll.user32
    modifiers = MOD_CONTROL | MOD_ALT
    if not user32.RegisterHotKey(None, APPLY_CORRECTION_HOTKEY_ID, modifiers, VK_RETURN):
        _log_hotkey("register failed: Ctrl+Alt+Enter may already be in use")
        return

    _log_hotkey("registered Ctrl+Alt+Enter")
    msg = MSG()
    try:
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                _log_hotkey("GetMessageW failed")
                break
            if msg.message == WM_HOTKEY and msg.wParam == APPLY_CORRECTION_HOTKEY_ID:
                try:
                    callback({"action": "apply_correction", "shortcut": "Ctrl+Alt+Enter"})
                except Exception as exc:
                    _log_hotkey(f"callback failed: {type(exc).__name__}: {exc}")
    finally:
        try:
            user32.UnregisterHotKey(None, APPLY_CORRECTION_HOTKEY_ID)
        except Exception:
            pass
        _log_hotkey("unregistered Ctrl+Alt+Enter")


def _log_hotkey(message: str):
    try:
        with _HOTKEY_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass

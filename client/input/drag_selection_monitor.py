from __future__ import annotations

import ctypes
import time
from pathlib import Path
from uuid import uuid4
import xml.etree.ElementTree as ET

import pyperclip

from client.input.input_mode_state import is_input_mode_active
from client.input.ai_grammary_text_reader import (
    NOTEPAD_PROCESS_NAMES,
    WORD_PROCESS_NAMES,
    get_foreground_hwnd,
    get_process_name,
    get_window_title,
)
from client.input.output_applier import (
    _clear_broken_word_com_cache,
    _looks_like_broken_word_com_cache,
)

try:
    import win32gui
except Exception:  # pragma: no cover - optional Windows dependency
    win32gui = None

try:
    import pythoncom
except Exception:  # pragma: no cover - optional Windows dependency
    pythoncom = None

_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_DRAG_LOG_PATH = _LOG_DIR / "drag_selection.log"
VK_LBUTTON = 0x01
VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002
DRAG_DISTANCE_THRESHOLD = 6


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def monitor_drag_selection(callback, poll_interval=0.08):
    """Read a selection only once, immediately after a mouse drag ends.

    Continuous COM polling can make Word's selection/caret flicker. Drag mode
    therefore waits for an actual left-button drag/release gesture, captures the
    resulting selection once, and stays quiet until the next drag.
    """
    was_dragging = False
    drag_start_pos = None
    drag_moved = False
    last_event_signature = None
    last_event_reader = ""
    last_event_hwnd = 0
    last_event_at = 0.0
    drag_start_hwnd = 0
    drag_start_process = ""
    while True:
        try:
            if not is_input_mode_active("drag"):
                was_dragging = False
                drag_start_pos = None
                drag_moved = False
                drag_start_hwnd = 0
                drag_start_process = ""
                time.sleep(0.18)
                continue

            if _is_left_mouse_pressed():
                position = _cursor_position()
                if drag_start_pos is None:
                    drag_start_pos = position
                    drag_moved = False
                    drag_start_hwnd = get_foreground_hwnd()
                    drag_start_process = get_process_name(drag_start_hwnd)
                elif position is not None and drag_start_pos is not None:
                    dx = abs(position[0] - drag_start_pos[0])
                    dy = abs(position[1] - drag_start_pos[1])
                    if dx >= DRAG_DISTANCE_THRESHOLD or dy >= DRAG_DISTANCE_THRESHOLD:
                        drag_moved = True
                was_dragging = True
                time.sleep(0.04)
                continue

            if not was_dragging:
                time.sleep(poll_interval)
                continue

            was_dragging = False
            release_had_drag_motion = drag_moved
            if not release_had_drag_motion:
                _log_drag("mouse release below drag threshold; probing selection")
            start_hwnd = drag_start_hwnd
            start_process = drag_start_process
            drag_start_pos = None
            drag_moved = False
            drag_start_hwnd = 0
            drag_start_process = ""
            # Let the target editor settle the final selected range.
            time.sleep(0.32)

            hwnd = get_foreground_hwnd()
            if _has_visible_editor_blocking_dialog():
                was_dragging = False
                time.sleep(0.18)
                continue
            if _is_blocking_dialog_window(hwnd):
                was_dragging = False
                time.sleep(0.18)
                continue
            process_name = get_process_name(hwnd)
            if process_name not in WORD_PROCESS_NAMES.union(NOTEPAD_PROCESS_NAMES):
                if start_process in WORD_PROCESS_NAMES.union(NOTEPAD_PROCESS_NAMES) and start_hwnd:
                    _log_drag(
                        "foreground changed after drag release; using drag-start target "
                        f"start_process={start_process!r} start_hwnd={start_hwnd} "
                        f"release_process={process_name!r} release_hwnd={hwnd}"
                    )
                    hwnd = start_hwnd
                    process_name = start_process
                else:
                    _log_drag(f"selection release skipped unsupported process={process_name!r} hwnd={hwnd}")
            event = None
            clear_reader = ""
            if process_name in WORD_PROCESS_NAMES:
                clear_reader = "word_selection"
                if _looks_like_word_document_window(hwnd):
                    deadline = time.monotonic() + 0.75
                    while time.monotonic() < deadline and event is None:
                        probe = _read_word_selection_probe(hwnd)
                        if probe is not None:
                            event = _read_word_selection_event(hwnd, probe)
                            break
                        time.sleep(0.10)
                    if event is None:
                        _log_drag(f"word selection probe timed out hwnd={hwnd}")
                else:
                    _log_drag(f"word selection skipped non-document hwnd={hwnd}")
            elif process_name in NOTEPAD_PROCESS_NAMES:
                clear_reader = "notepad_selection"
                if _looks_like_notepad_document_window(hwnd):
                    event = _read_notepad_selection_event(hwnd)
                    if event is not None and _looks_like_dialog_selection_text(event.get("text", "")):
                        _log_drag("notepad dialog selection ignored")
                        event = None

            if event is not None:
                signature = _selection_signature(event)
                now = time.monotonic()
                if signature == last_event_signature and now - last_event_at < 1.0:
                    _log_drag(f"selection duplicate ignored signature={signature!r}")
                else:
                    last_event_signature = signature
                    last_event_reader = str(event.get("reader") or "")
                    last_event_hwnd = int(event.get("window_handle") or 0)
                    last_event_at = now
                    callback(event)
                    _log_drag(
                        "selection captured once "
                        f"reader={event.get('reader')!r} len={len(event.get('text') or '')} "
                        f"signature={signature!r}"
                    )
            elif clear_reader:
                can_clear_previous_selection = (
                    bool(last_event_signature)
                    and clear_reader == last_event_reader
                    and int(hwnd or 0) == int(last_event_hwnd or 0)
                )
                if not release_had_drag_motion:
                    _log_drag(
                        f"empty selection ignored below drag threshold reader={clear_reader!r} "
                        f"hwnd={hwnd} had_previous={can_clear_previous_selection}"
                    )
                else:
                    confirmed_clear = can_clear_previous_selection
                    last_event_signature = None
                    last_event_reader = ""
                    last_event_hwnd = 0
                    _log_drag(f"selection read empty reader={clear_reader!r} hwnd={hwnd} confirmed={confirmed_clear}")
                    clear_event = _selection_cleared_event(hwnd, clear_reader)
                    if confirmed_clear:
                        clear_event["confirmed_clear"] = True
                        clear_event["style_info"]["confirmed_previous_selection"] = True
                    callback(clear_event)
                    _log_drag(f"selection cleared reader={clear_reader!r} hwnd={hwnd} confirmed={confirmed_clear}")
        except Exception as exc:
            _log_drag(f"poll failed: {type(exc).__name__}: {exc}")
        time.sleep(poll_interval)


def _has_visible_editor_blocking_dialog() -> bool:
    if win32gui is None:
        return False
    try:
        found = False

        def visit(hwnd, _):
            nonlocal found
            if found:
                return False
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if not _is_blocking_dialog_window(hwnd):
                    return True
                process_name = get_process_name(hwnd)
                owner = win32gui.GetWindow(hwnd, 4) or 0
                owner_process_name = get_process_name(owner) if owner else ""
                found = process_name in WORD_PROCESS_NAMES.union(NOTEPAD_PROCESS_NAMES) or owner_process_name in WORD_PROCESS_NAMES.union(NOTEPAD_PROCESS_NAMES)
                return not found
            except Exception:
                return True

        win32gui.EnumWindows(visit, None)
        return found
    except Exception:
        return False


def _is_blocking_dialog_window(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        class_name = win32gui.GetClassName(hwnd) or ""
        root = win32gui.GetAncestor(hwnd, 2) or hwnd
        root_class_name = win32gui.GetClassName(root) or ""
        if class_name == "#32770" or root_class_name == "#32770":
            return True
        if class_name in {"Microsoft-Windows-FileSavePicker", "NUIDialog", "Net UI Tool Window"}:
            return True
        if "Menu" in class_name or "Popup" in class_name or "DropShadow" in class_name:
            return True
        process_name = get_process_name(hwnd)
        if process_name in NOTEPAD_PROCESS_NAMES and not _looks_like_notepad_document_window(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        return _looks_like_save_prompt(title)
    except Exception:
        return False


def _looks_like_word_document_window(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        root = win32gui.GetAncestor(hwnd, 2) or hwnd
        class_name = win32gui.GetClassName(hwnd) or ""
        root_class_name = win32gui.GetClassName(root) or ""
        if class_name != "OpusApp" and root_class_name != "OpusApp":
            return False
        title = (win32gui.GetWindowText(root) or win32gui.GetWindowText(hwnd) or "").strip()
        if not title or title == "Word":
            return False
        return _has_word_editor_child(root)
    except Exception:
        return False


def _word_foreground_focus_is_document(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        import win32process

        foreground = win32gui.GetForegroundWindow()
        if not _same_root_window(foreground, hwnd):
            return False
        thread_id, _ = win32process.GetWindowThreadProcessId(foreground)
        info = win32gui.GetGUIThreadInfo(thread_id)
        focus_hwnd = (info or {}).get("hwndFocus") or (info or {}).get("hwndCaret")
        if not focus_hwnd:
            return True
        class_name = win32gui.GetClassName(focus_hwnd) or ""
        return class_name.startswith("_Ww")
    except Exception:
        return False



def _has_large_word_non_document_surface(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        root = win32gui.GetAncestor(hwnd, 2) or hwnd
        root_left, root_top, root_right, root_bottom = win32gui.GetWindowRect(root)
        root_width = max(1, root_right - root_left)
        root_height = max(1, root_bottom - root_top)
        root_area = root_width * root_height
        blocking_tokens = ("Backstage", "NUIPane", "NetUI", "MsoWorkPane", "MsoCommandBar")
        found = False

        def visit(child_hwnd, _):
            nonlocal found
            if found:
                return False
            try:
                if not win32gui.IsWindowVisible(child_hwnd):
                    return True
                class_name = win32gui.GetClassName(child_hwnd) or ""
                if not any(token in class_name for token in blocking_tokens):
                    return True
                left, top, right, bottom = win32gui.GetWindowRect(child_hwnd)
                width = max(0, right - left)
                height = max(0, bottom - top)
                if (
                    width / root_width > 0.55
                    and height / root_height > 0.45
                    and (width * height) / root_area > 0.35
                ):
                    found = True
                    return False
            except Exception:
                pass
            return True

        win32gui.EnumChildWindows(root, visit, None)
        return found
    except Exception:
        return False


def _looks_like_dialog_selection_text(text: str) -> bool:
    sample = str(text or "").strip()
    if not sample:
        return False
    return sample.startswith("[Window Title]") or "[Main Instruction]" in sample
def _same_root_window(first: int, second: int) -> bool:
    if win32gui is None:
        return False
    try:
        first = int(first or 0)
        second = int(second or 0)
        if not first or not second:
            return False
        if first == second:
            return True
        root_first = win32gui.GetAncestor(first, 2) or first
        root_second = win32gui.GetAncestor(second, 2) or second
        return int(root_first) == int(root_second)
    except Exception:
        return False

def _has_word_editor_child(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        root = win32gui.GetAncestor(hwnd, 2) or hwnd
        found = False

        def visit(child_hwnd, _):
            nonlocal found
            try:
                class_name = win32gui.GetClassName(child_hwnd) or ""
                if class_name.startswith("_Ww"):
                    found = True
                    return False
            except Exception:
                pass
            return True

        win32gui.EnumChildWindows(root, visit, None)
        return found
    except Exception:
        return False

def _looks_like_notepad_document_window(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        root = win32gui.GetAncestor(hwnd, 2) or hwnd
        class_name = win32gui.GetClassName(hwnd) or ""
        root_class_name = win32gui.GetClassName(root) or ""
        if class_name not in {"Notepad", "ApplicationFrameWindow"} and root_class_name not in {"Notepad", "ApplicationFrameWindow"}:
            return False
        class_names = []

        def visit(child_hwnd, _):
            try:
                class_names.append(win32gui.GetClassName(child_hwnd) or "")
            except Exception:
                pass
            return len(class_names) < 500

        win32gui.EnumChildWindows(root, visit, None)
        joined = " ".join(class_names)
        text_tokens = ("Edit", "RichEdit", "RichEditD2D", "TextBox", "TextBoxView")
        return any(token in joined for token in text_tokens)
    except Exception:
        return False

def _looks_like_save_prompt(title: str) -> bool:
    text = str(title or "")
    prompt_markers = (
        "\uc800\uc7a5\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c",
        "\ubcc0\uacbd \ub0b4\uc6a9",
        "\uc81c\ubaa9 \uc5c6\uc74c\uc5d0 \uc800\uc7a5",
        "Do you want to save",
        "Save changes",
    )
    return any(marker in text for marker in prompt_markers)


def _selection_cleared_event(hwnd: int, reader_name: str):
    return {
        "source": "drag",
        "reader": "selection_cleared",
        "target_reader": reader_name,
        "window_title": get_window_title(hwnd),
        "window_handle": hwnd,
        "text": "",
        "style_info": {"selection_mode": "cleared"},
    }


def _selection_signature(event: dict):
    style_info = event.get("style_info") or {}
    return (
        event.get("reader"),
        event.get("window_handle"),
        style_info.get("selection_start"),
        style_info.get("selection_end"),
        event.get("text"),
    )


def _cursor_position():
    try:
        point = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return point.x, point.y
    except Exception:
        pass
    return None


def _is_left_mouse_pressed() -> bool:
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False


def _read_notepad_selection_event(hwnd: int):
    selected_text = _copy_selection_text()
    if not selected_text or not selected_text.strip():
        return None
    return {
        "source": "drag",
        "reader": "notepad_selection",
        "window_title": get_window_title(hwnd),
        "window_handle": hwnd,
        "text": selected_text,
        "style_info": {"selection_mode": "notepad", "selection_text": selected_text},
    }


def _get_active_word_application():
    if pythoncom is None:
        return None
    import win32com.client.dynamic as dynamic

    active = pythoncom.GetActiveObject("Word.Application")
    try:
        active = active.QueryInterface(pythoncom.IID_IDispatch)
    except Exception:
        pass
    return dynamic.Dispatch(active)


def _read_word_selection_probe(hwnd: int):
    return _read_word_selection_probe_once(hwnd, allow_cache_repair=True)


def _read_word_selection_probe_once(hwnd: int, allow_cache_repair: bool):
    if pythoncom is None:
        return None
    try:
        pythoncom.CoInitialize()
        word = _get_active_word_application()
        word_hwnd = _word_application_hwnd(word) or hwnd
        selection = getattr(word, "Selection", None)
        if selection is None:
            return None
        selection_range = selection.Range.Duplicate
        document = getattr(selection_range, "Document", None)
        start = int(selection_range.Start)
        end = int(selection_range.End)
        if end <= start:
            return None
        selected_text = _clean_word_text(getattr(selection_range, "Text", "") or "")
        if not selected_text or not selected_text.strip():
            return None
        return {
            "source": "drag",
            "reader": "word_selection",
            "window_title": get_window_title(word_hwnd),
            "window_handle": word_hwnd,
            "text": selected_text,
            "style_info": {
                "selection_mode": "word",
                "selection_start": start,
                "selection_end": end,
                "document_name": _word_document_name(document),
                "document_full_name": _word_document_full_name(document),
                "selection_text": selected_text,
            },
            "_word_range": selection_range,
        }
    except Exception as exc:
        if allow_cache_repair and _looks_like_broken_word_com_cache(exc):
            _log_drag(f"broken Word COM cache detected during selection; repairing and retrying: {exc}")
            try:
                _clear_broken_word_com_cache()
            except Exception as repair_exc:
                _log_drag(f"Word COM cache repair failed during selection: {type(repair_exc).__name__}: {repair_exc}")
                return None
            return _read_word_selection_probe_once(hwnd, allow_cache_repair=False)
        _log_drag(f"word selection probe failed: {type(exc).__name__}: {exc}")
        return None


def _read_word_selection_event(hwnd: int, probe: dict | None = None):
    if pythoncom is None:
        return None
    try:
        if probe is None:
            probe = _read_word_selection_probe(hwnd)
        if probe is None:
            return None
        style_info = dict(probe.get("style_info") or {})
        style_info["style_capture_deferred"] = True
        return {
            "source": "drag",
            "reader": "word_selection",
            "window_title": probe.get("window_title", get_window_title(hwnd)),
            "window_handle": probe.get("window_handle") or hwnd,
            "text": probe.get("text", ""),
            "style_info": style_info,
        }
    except Exception as exc:
        _log_drag(f"word selection read failed: {type(exc).__name__}: {exc}")
        return None


def _word_application_hwnd(word) -> int | None:
    try:
        hwnd = int(getattr(word, "Hwnd", 0) or 0)
        return hwnd or None
    except Exception:
        return None


def _word_document_name(document) -> str:
    try:
        return str(getattr(document, "Name", "") or "")
    except Exception:
        return ""


def _word_document_full_name(document) -> str:
    try:
        return str(getattr(document, "FullName", "") or "")
    except Exception:
        return ""


def _clean_word_text(text: str) -> str:
    return str(text or "").replace("\x00", "").replace("\x07", "").replace("\r\n", "\n").replace("\r", "\n")


def _read_word_selection_style_info(selection_range) -> dict:
    style_info = _style_from_word_range(selection_range)
    style_info["segments"] = _read_word_openxml_style_segments(selection_range)
    return style_info


def _style_from_word_range(word_range) -> dict:
    try:
        font = word_range.Font
    except Exception:
        return {}
    return {
        "bold": _word_bool(getattr(font, "Bold", None)),
        "italic": _word_bool(getattr(font, "Italic", None)),
        "underline": _clean_mixed_value(getattr(font, "Underline", None)),
        "strike_through": _word_bool(getattr(font, "StrikeThrough", None)),
        "double_strike_through": _word_bool(getattr(font, "DoubleStrikeThrough", None)),
        "subscript": _word_bool(getattr(font, "Subscript", None)),
        "superscript": _word_bool(getattr(font, "Superscript", None)),
        "highlight_color_index": _clean_mixed_value(getattr(word_range, "HighlightColorIndex", None)),
        "color": _clean_mixed_value(getattr(font, "Color", None)),
    }


def _word_bool(value):
    if value in (None, 9999999, -9999999, 9999998, -9999998):
        return None
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


def _clean_mixed_value(value):
    if value in (None, 9999999, -9999999, 9999998, -9999998):
        return None
    return value


def _read_word_openxml_style_segments(selection_range) -> list[dict]:
    try:
        root = ET.fromstring(getattr(selection_range, "WordOpenXML", "") or "")
    except Exception as exc:
        _log_drag(f"word selection openxml parse failed: {type(exc).__name__}: {exc}")
        return []
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    body = root.find(".//w:body", namespace)
    paragraphs = body.findall("w:p", namespace) if body is not None else root.findall(".//w:body/w:p", namespace)
    segments: list[dict] = []
    text_index = 0
    for paragraph in paragraphs:
        for run in paragraph.findall("w:r", namespace):
            style = _word_openxml_run_style(run, namespace)
            for part_text, is_break in _word_openxml_run_text_parts(run):
                if is_break:
                    text_index += 1
                    continue
                if not part_text:
                    continue
                start = text_index
                text_index += len(part_text)
                _append_word_style_segment(segments, start, text_index, style)
        text_index += 1
    return segments


def _word_openxml_run_text_parts(run):
    for child in list(run):
        tag = _word_openxml_local_name(child.tag)
        if tag == "t":
            yield child.text or "", False
        elif tag == "tab":
            yield "\t", False
        elif tag in {"br", "cr"}:
            yield "\n", True


def _word_openxml_local_name(tag: str) -> str:
    return str(tag).split("}", 1)[-1]


def _word_openxml_run_style(run, namespace) -> dict:
    run_props = run.find("w:rPr", namespace)
    if run_props is None:
        return {}
    style: dict = {}
    style["bold"] = _word_openxml_toggle_enabled(run_props.find("w:b", namespace))
    style["italic"] = _word_openxml_toggle_enabled(run_props.find("w:i", namespace))
    underline = run_props.find("w:u", namespace)
    if underline is not None:
        value = _word_openxml_attr(underline, "val") or "single"
        style["underline"] = 0 if value == "none" else _word_openxml_underline_value(value)
        color = _word_openxml_attr(underline, "color")
        if color and color.lower() != "auto":
            style["underline_color"] = None
            style["underline_color_hex"] = f"#{color.upper()}"
    else:
        style["underline"] = 0
    double_strike = run_props.find("w:dstrike", namespace)
    strike = run_props.find("w:strike", namespace)
    style["double_strike_through"] = _word_openxml_toggle_enabled(double_strike)
    style["strike_through"] = False if style["double_strike_through"] else _word_openxml_toggle_enabled(strike)
    vertical_align = run_props.find("w:vertAlign", namespace)
    align_value = _word_openxml_attr(vertical_align, "val") if vertical_align is not None else None
    style["subscript"] = align_value == "subscript"
    style["superscript"] = align_value == "superscript"
    highlight = run_props.find("w:highlight", namespace)
    if highlight is not None:
        style["highlight_color_index"] = _word_openxml_highlight_value(_word_openxml_attr(highlight, "val"))
    else:
        style["highlight_color_index"] = 0
    color = run_props.find("w:color", namespace)
    color_value = _word_openxml_attr(color, "val") if color is not None else None
    color_hex = _word_openxml_color_hex(color_value)
    if color_hex:
        style["color_hex"] = color_hex
    else:
        style["color_hex"] = "#000000"
    return style


def _word_openxml_attr(element, name: str):
    if element is None:
        return None
    return element.get(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{name}")


def _word_openxml_toggle_enabled(element) -> bool:
    if element is None:
        return False
    value = _word_openxml_attr(element, "val")
    return value is None or str(value).lower() not in {"0", "false", "off", "none"}


def _word_openxml_underline_value(value: str):
    return {
        "single": 1,
        "words": 2,
        "double": 3,
        "dotted": 4,
        "thick": 6,
        "dash": 7,
        "dotDash": 9,
        "dotDotDash": 10,
        "wave": 11,
        "dottedHeavy": 20,
        "dashHeavy": 23,
        "dotDashHeavy": 25,
        "dotDotDashHeavy": 26,
        "wavyHeavy": 27,
        "dashLong": 39,
        "wavyDouble": 43,
        "dashLongHeavy": 55,
    }.get(str(value), 1)


def _word_openxml_highlight_value(value: str | None):
    if not value or value == "none":
        return 0
    return {
        "black": 1,
        "blue": 2,
        "cyan": 3,
        "green": 4,
        "magenta": 5,
        "red": 6,
        "yellow": 7,
        "darkBlue": 9,
        "darkCyan": 10,
        "darkGreen": 11,
        "darkMagenta": 12,
        "darkRed": 13,
        "darkYellow": 14,
        "darkGray": 15,
        "lightGray": 16,
    }.get(str(value), None)


def _word_openxml_color_hex(value: str | None):
    if not value or str(value).lower() == "auto":
        return None
    value = str(value).strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        int(value, 16)
    except Exception:
        return None
    return f"#{value.upper()}"


def _append_word_style_segment(segments: list[dict], start: int, end: int, style: dict):
    if end <= start:
        return
    segments.append({"start": start, "end": end, "style": dict(style or {})})


def _copy_selection_text() -> str:
    if win32gui is None:
        return ""
    original = _safe_paste()
    sentinel = f"__WRITING_ASSISTANT_SELECTION_EMPTY__{uuid4()}__"
    try:
        _safe_copy(sentinel)
        _send_copy_hotkey()
        time.sleep(0.08)
        copied = _safe_paste()
        if copied == sentinel:
            return ""
        return str(copied or "").replace("\r\n", "\n").replace("\r", "\n")
    finally:
        if original is not None:
            _safe_copy(original)


def _send_copy_hotkey():
    try:
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_C, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    except Exception as exc:
        _log_drag(f"copy hotkey failed: {type(exc).__name__}: {exc}")


def _safe_paste():
    for _ in range(4):
        try:
            return pyperclip.paste()
        except Exception:
            time.sleep(0.04)
    return None


def _safe_copy(text):
    for _ in range(4):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(0.04)
    return False


def _log_drag(message: str):
    try:
        with _DRAG_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass



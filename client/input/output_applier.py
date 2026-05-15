from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import time

import pyperclip

try:
    import pythoncom
except Exception:  # pragma: no cover - optional Windows dependency
    pythoncom = None

try:
    import win32gui
except Exception:  # pragma: no cover - optional Windows dependency
    win32gui = None

try:
    import win32process
except Exception:  # pragma: no cover - optional Windows dependency
    win32process = None

try:
    import psutil
except Exception:  # pragma: no cover - optional Windows dependency
    psutil = None

HWP_PROCESS_NAMES = {"hwp.exe", "hwp64.exe", "hwpviewer.exe", "hwpw.exe"}
HWP_ACTIVE_PROGIDS = (
    "HWPFrame.HwpObject.2",
    "HWPFrame.HwpObject.1",
    "HWPFrame.HwpObject",
)
HWP_IHWP_OBJECT_IID = "{5E6A8276-CF1C-42B8-BCED-319548B02AF6}"
HWP_TEXTFILE_FORMATS = (
    "HTML",
    "HWPML",
    "HWPML2X",
    "HWPML2X_S",
    "HWPML2X_P",
    "HWPML2X_STYLE",
)
HWP_TEXTFILE_OPTIONS = ("", "saveblock", "selection")
ENABLE_HWP_CURSOR_SEGMENT_SELECTION = False
HWP_TEXT_CONTROL_TYPES = ("Document", "Edit", "Pane", "Text")
HWP_EXCLUDED_TEXT_HINTS = (
    "menu",
    "toolbar",
    "status",
    "navigation",
    "ribbon",
    "dialog",
    "button",
    "tab",
    "paragraph",
    "도구",
    "메뉴",
    "상태",
)
_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_HWP_REPLACE_LOG_PATH = _LOG_DIR / "hwp_replace.log"
_WORD_REPLACE_LOG_PATH = _LOG_DIR / "word_replace.log"
_HWP_TEXTFILE_SNAPSHOT_DIR = _LOG_DIR / "hwp_textfile_snapshots"


@dataclass
class OutputTarget:
    mode: str
    window_handle: int | None = None
    window_title: str = ""
    style_info: dict | None = None


class OutputApplier:
    def inspect_replace_availability(self, target: OutputTarget | None) -> tuple[bool, str | None]:
        if target is None:
            return False, "No source window has been captured yet."
        if target.mode == "browser_extension":
            session_id = (target.style_info or {}).get("browser_session_id")
            if session_id:
                return True, None
            return False, "The browser extension has not captured an editable field yet."
        if target.mode in ("browser", "notepad"):
            if self._is_live_window(target.window_handle):
                return True, None
            return False, "The original input window is no longer available."
        if target.mode == "word":
            return True, None
        if target.mode == "hwp":
            if self._is_live_window(target.window_handle):
                return True, None
            return False, "The original HWP window is no longer available."
        return False, f"Replace mode is not supported for {target.mode}."

    def apply(self, target: OutputTarget | None, text: str):
        if not text.strip():
            raise ValueError("There is no corrected text to apply.")

        can_replace, reason = self.inspect_replace_availability(target)
        if not can_replace:
            raise RuntimeError(reason or "Replace mode is unavailable.")

        if target.mode == "browser_extension":
            self._apply_to_browser_extension(text, target.style_info)
            return

        if target.mode == "word":
            self._focus_window(target.window_handle)
            self._apply_to_active_word(text, target.style_info)
            return

        if target.mode == "hwp":
            self._focus_window(target.window_handle)
            try:
                self._apply_to_active_hwp(text, target.style_info, target.window_handle)
                self._log_hwp_replace(
                    f"applied via COM length={len(text)} "
                    f"read_method={(target.style_info or {}).get('read_method')!r} "
                    f"style_keys={sorted((target.style_info or {}).keys())!r}"
                )
                return
            except Exception as com_exc:
                self._log_hwp_replace(f"COM apply failed: {type(com_exc).__name__}: {com_exc}")
                try:
                    self._apply_to_hwp_via_uia(target.window_handle, text)
                    self._log_hwp_replace(
                        f"applied via UIA length={len(text)} read_method={(target.style_info or {}).get('read_method')!r}"
                    )
                    return
                except Exception as uia_exc:
                    self._log_hwp_replace(f"UIA apply failed: {type(uia_exc).__name__}: {uia_exc}")
                    try:
                        self._apply_to_hwp_via_keyboard_once(target.window_handle, text)
                        self._log_hwp_replace(
                            f"applied via one-shot keyboard length={len(text)} "
                            f"read_method={(target.style_info or {}).get('read_method')!r}"
                        )
                        return
                    except Exception as keyboard_exc:
                        self._log_hwp_replace(
                            f"keyboard apply failed: {type(keyboard_exc).__name__}: {keyboard_exc}"
                        )
                        raise RuntimeError(
                            "HWP replacement failed. "
                            f"COM: {com_exc}; UIA: {uia_exc}; keyboard: {keyboard_exc}"
                        ) from keyboard_exc

        self._apply_via_window_handle(target.window_handle, text)

    def _apply_to_browser_extension(self, text: str, style_info: dict | None = None):
        from client.input.browser_extension_bridge import get_browser_extension_bridge

        style_info = style_info or {}
        session_id = str(style_info.get("browser_session_id") or "")
        get_browser_extension_bridge().queue_apply(session_id, text, style_info)

    def _apply_via_window_handle(self, window_handle: int | None, text: str):
        Application, send_keys = self._load_pywinauto()
        if Application is None or send_keys is None or win32gui is None:
            raise RuntimeError("pywinauto and pywin32 are required for window replacement.")
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original input window is no longer available.")

        original_clipboard = self._read_clipboard_safely()
        try:
            app = Application(backend="win32").connect(handle=window_handle)
            window = app.window(handle=window_handle)
            win32gui.ShowWindow(window_handle, 5)
            win32gui.SetForegroundWindow(window_handle)
            window.set_focus()
            time.sleep(0.25)
            self._copy_clipboard_safely(text)
            send_keys("^a")
            time.sleep(0.08)
            send_keys("{DELETE}")
            time.sleep(0.08)
            send_keys("^v")
        finally:
            if original_clipboard is not None:
                time.sleep(0.05)
                self._copy_clipboard_safely(original_clipboard)

    def _apply_to_active_word(self, text: str, style_info: dict | None = None):
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word replacement.")
        pythoncom.CoInitialize()
        import win32com.client as win32

        word = win32.GetActiveObject("Word.Application")
        document = getattr(word, "ActiveDocument", None)
        if document is None:
            raise RuntimeError("No active Word document is available.")
        word.Visible = True
        document.Activate()
        style_info = style_info or {}
        line_styles = style_info.get("line_styles") or []
        self._log_word_replace(
            f"write text_len={len(str(text or ''))} newlines={str(text or '').count(chr(10))} "
            f"line_styles={len(line_styles)} segments={len(style_info.get('segments') or [])} "
            f"sample={str(text or '')[:80]!r}"
        )
        document.Content.Text = self._word_text_for_write(text)
        if line_styles:
            self._clear_word_direct_character_styles(document)
            self._apply_word_line_styles(document, line_styles)
        else:
            self._apply_word_style(document.Content, style_info)
        self._apply_word_style_segments(document, style_info.get("segments") or [])

    def _clear_word_direct_character_styles(self, document):
        try:
            font = document.Content.Font
            font.Bold = 0
            font.Italic = 0
            font.Underline = 0
            font.StrikeThrough = 0
            font.DoubleStrikeThrough = 0
            font.Subscript = 0
            font.Superscript = 0
            document.Content.HighlightColorIndex = 0
        except Exception:
            pass

    def _apply_word_line_styles(self, document, line_styles: list[dict]):
        if not line_styles:
            return
        try:
            paragraphs = document.Paragraphs
            paragraph_count = int(paragraphs.Count)
        except Exception:
            return

        paragraphs_by_content_line = self._word_content_paragraphs(paragraphs, paragraph_count)
        for line_style in line_styles:
            if line_style.get("is_blank"):
                continue
            paragraph_range = None
            content_line = line_style.get("content_line")
            if content_line is not None:
                try:
                    paragraph_range = paragraphs_by_content_line.get(int(content_line))
                except Exception:
                    paragraph_range = None
            if paragraph_range is None:
                try:
                    line_index = int(line_style.get("line", -1))
                    paragraph_index = line_index + 1
                    if paragraph_index < 1 or paragraph_index > paragraph_count:
                        continue
                    paragraph_range = paragraphs.Item(paragraph_index).Range.Duplicate
                except Exception:
                    continue
            try:
                raw_text = getattr(paragraph_range, "Text", "") or ""
                visible_text = raw_text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
                if not visible_text.strip():
                    continue
                if paragraph_range.End > paragraph_range.Start:
                    paragraph_range.End = paragraph_range.End - 1
                style = line_style.get("style") or {}
                self._log_word_replace(
                    f"apply line={line_style.get('line')!r} content_line={line_style.get('content_line')!r} "
                    f"text={visible_text[:60]!r} bold={style.get('bold')!r} italic={style.get('italic')!r} "
                    f"underline={style.get('underline')!r} strike={style.get('strike_through')!r} "
                    f"double_strike={style.get('double_strike_through')!r} sub={style.get('subscript')!r} "
                    f"super={style.get('superscript')!r} highlight={style.get('highlight_color_index')!r} "
                    f"color={style.get('color_hex')!r}"
                )
                self._reset_word_style_flags(paragraph_range)
                self._apply_word_style(paragraph_range, style)
                self._verify_word_style(paragraph_range, line_style)
            except Exception:
                pass

    def _reset_word_style_flags(self, word_range):
        try:
            font = word_range.Font
            font.Bold = 0
            font.Italic = 0
            font.Underline = 0
            font.StrikeThrough = 0
            font.DoubleStrikeThrough = 0
            font.Subscript = 0
            font.Superscript = 0
            word_range.HighlightColorIndex = 0
        except Exception:
            pass

    def _verify_word_style(self, word_range, line_style: dict):
        try:
            font = word_range.Font
            self._log_word_replace(
                f"verify line={line_style.get('line')!r} content_line={line_style.get('content_line')!r} "
                f"bold={getattr(font, 'Bold', None)!r} italic={getattr(font, 'Italic', None)!r} "
                f"underline={getattr(font, 'Underline', None)!r} "
                f"strike={getattr(font, 'StrikeThrough', None)!r} "
                f"double_strike={getattr(font, 'DoubleStrikeThrough', None)!r} "
                f"sub={getattr(font, 'Subscript', None)!r} super={getattr(font, 'Superscript', None)!r} "
                f"highlight={getattr(word_range, 'HighlightColorIndex', None)!r}"
            )
        except Exception as exc:
            self._log_word_replace(f"verify failed: {type(exc).__name__}: {exc}")

    def _log_word_replace(self, message: str):
        try:
            _WORD_REPLACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _WORD_REPLACE_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except Exception:
            pass

    def _word_content_paragraphs(self, paragraphs, paragraph_count: int) -> dict[int, object]:
        result = {}
        content_index = 0
        for paragraph_index in range(1, paragraph_count + 1):
            try:
                paragraph_range = paragraphs.Item(paragraph_index).Range.Duplicate
                raw_text = getattr(paragraph_range, "Text", "") or ""
                visible_text = raw_text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
            except Exception:
                continue
            if not visible_text.strip():
                continue
            result[content_index] = paragraph_range
            content_index += 1
        return result

    def _apply_word_style_segments(self, document, segments: list[dict]):
        if not segments:
            return
        try:
            content = document.Content
            content_start = int(content.Start)
            content_end = int(content.End)
        except Exception:
            return

        max_end = max(content_start, content_end - 1)
        for segment in segments:
            try:
                start = content_start + int(segment.get("start", 0))
                end = content_start + int(segment.get("end", 0))
            except Exception:
                continue
            start = max(content_start, min(start, max_end))
            end = max(content_start, min(end, max_end))
            if end <= start:
                continue
            try:
                segment_range = document.Range(Start=start, End=end)
                self._apply_word_style(segment_range, segment.get("style") or {})
            except Exception:
                pass

    def _apply_word_style(self, word_range, style_info: dict):
        if not style_info:
            return
        try:
            font = word_range.Font
        except Exception:
            return
        assignments = {
            "font_name": "Name",
            "font_size": "Size",
            "bold": "Bold",
            "italic": "Italic",
        }
        for key, attr in assignments.items():
            value = style_info.get(key)
            if value is None:
                continue
            try:
                if key in {"bold", "italic"}:
                    value = -1 if bool(value) else 0
                setattr(font, attr, value)
            except Exception:
                pass
        underline_value = self._word_underline_value(style_info.get("underline"))
        if underline_value is not None:
            try:
                font.Underline = underline_value
            except Exception:
                pass
        strike_value = style_info.get("strike_through")
        if strike_value is not None:
            try:
                font.StrikeThrough = -1 if bool(strike_value) else 0
                if bool(strike_value):
                    font.DoubleStrikeThrough = 0
            except Exception:
                pass
        double_strike_value = style_info.get("double_strike_through")
        if double_strike_value is not None:
            try:
                font.DoubleStrikeThrough = -1 if bool(double_strike_value) else 0
                if bool(double_strike_value):
                    font.StrikeThrough = 0
            except Exception:
                pass
        subscript_value = style_info.get("subscript")
        superscript_value = style_info.get("superscript")
        if subscript_value is not None:
            try:
                font.Subscript = -1 if bool(subscript_value) else 0
                if bool(subscript_value):
                    font.Superscript = 0
            except Exception:
                pass
        if superscript_value is not None:
            try:
                font.Superscript = -1 if bool(superscript_value) else 0
                if bool(superscript_value):
                    font.Subscript = 0
            except Exception:
                pass
        highlight_value = self._word_highlight_value(style_info.get("highlight_color_index"))
        if highlight_value is not None:
            try:
                word_range.HighlightColorIndex = highlight_value
            except Exception:
                pass
        color_value = self._word_color_from_hex(style_info.get("color_hex"))
        if color_value is not None:
            try:
                font.Color = color_value
            except Exception:
                pass

        underline_color = style_info.get("underline_color")
        if underline_color is None:
            underline_color = self._word_color_from_hex(style_info.get("underline_color_hex"))
        if underline_color is not None:
            try:
                font.UnderlineColor = int(underline_color)
            except Exception:
                pass

    def _word_highlight_value(self, value):
        if value is None:
            return None
        try:
            number = int(value)
        except Exception:
            return None
        if number in (9999999, -9999999, 9999998, -9999998):
            return None
        return number

    def _word_underline_value(self, value):
        if value is None:
            return None
        try:
            number = int(value)
        except Exception:
            return None
        if number in (9999999, -9999999, 9999998, -9999998):
            return None
        return number

    def _word_color_from_hex(self, color_hex):
        if not color_hex:
            return None
        try:
            value = str(color_hex).lstrip("#")
            if len(value) != 6:
                return None
            red = int(value[0:2], 16)
            green = int(value[2:4], 16)
            blue = int(value[4:6], 16)
            return blue * 65536 + green * 256 + red
        except Exception:
            return None

    def _word_text_for_write(self, text: str) -> str:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", "\r")

    def _apply_to_active_hwp(self, text: str, style_info: dict | None = None, window_handle: int | None = None):
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for HWP replacement.")
        pythoncom.CoInitialize()
        hwp = self._active_hwp_object(window_handle)
        if hwp is None:
            raise RuntimeError("No active HWP COM object is available.")
        style_info = dict(style_info or {})
        source_hwpml2x = ""
        if style_info.get("hwp_style_scope") == "mixed_or_unknown" and not style_info.get("segments"):
            self._diagnose_hwp_textfile_formats(hwp)
            source_hwpml2x = self._get_hwp_textfile(hwp, "HWPML2X", "selection")
            style_info["segments"] = self._capture_hwp_style_segments_from_hwpml2x(hwp)
            if not style_info["segments"]:
                style_info["segments"] = self._capture_hwp_style_segments(hwp, style_info.get("_source_text") or "")
        if source_hwpml2x and self._apply_hwpml2x_replacement(hwp, source_hwpml2x, text):
            return
        hwp.MovePos(2)
        hwp.Run("SelectAll")
        hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
        hwp.HParameterSet.HInsertText.Text = text
        hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
        hwp_style_info = dict(style_info)
        hwp_style_info["_replacement_text"] = text
        self._apply_hwp_style(hwp, hwp_style_info)

    def _active_hwp_object(self, window_handle: int | None = None):
        import win32com.client as win32

        hwp = self._get_hwp_object_from_native_om(window_handle)
        if hwp is not None:
            self._log_hwp_replace(f"HWP COM object resolved via NativeOM hwnd={window_handle}")
            return hwp

        for progid in HWP_ACTIVE_PROGIDS:
            try:
                hwp = self._coerce_hwp_object(win32.GetActiveObject(progid))
                if hwp is not None:
                    self._log_hwp_replace(f"HWP COM object resolved via GetActiveObject progid={progid!r}")
                    return hwp
            except Exception:
                pass

        try:
            rot = pythoncom.GetRunningObjectTable()
            enum_moniker = rot.EnumRunning()
            bind_context = pythoncom.CreateBindCtx(0)
        except Exception:
            return None

        while True:
            monikers = enum_moniker.Next(1)
            if not monikers:
                break
            moniker = monikers[0]
            try:
                display_name = moniker.GetDisplayName(bind_context, None)
            except Exception:
                display_name = ""
            lowered = str(display_name).lower()
            if "hwp" not in lowered and "hancom" not in lowered and "hword" not in lowered:
                continue
            try:
                hwp = self._coerce_hwp_object(rot.GetObject(moniker))
                if hwp is not None:
                    self._log_hwp_replace(f"HWP COM object resolved via ROT entry={display_name!r}")
                    return hwp
            except Exception:
                continue
        return None

    def _get_hwp_object_from_native_om(self, hwnd: int | None):
        if pythoncom is None or not hwnd:
            return None
        try:
            import win32com.client as win32
            from ctypes import POINTER, byref, c_long, c_void_p
            from ctypes.wintypes import HWND, HRESULT

            oleacc = ctypes.oledll.oleacc
            iid_buffer = ctypes.create_string_buffer(bytes(pythoncom.IID_IDispatch))
            pdisp = c_void_p()
            accessible_object_from_window = oleacc.AccessibleObjectFromWindow
            accessible_object_from_window.argtypes = [HWND, c_long, c_void_p, POINTER(c_void_p)]
            accessible_object_from_window.restype = HRESULT
            result = accessible_object_from_window(
                HWND(int(hwnd)),
                c_long(-16),  # OBJID_NATIVEOM
                ctypes.cast(iid_buffer, c_void_p),
                byref(pdisp),
            )
            if result != 0 or not pdisp.value:
                self._log_hwp_replace(f"HWP NativeOM failed hwnd={hwnd} result={result} pdisp={pdisp.value}")
                return None
            obj = pythoncom.ObjectFromAddress(pdisp.value, pythoncom.IID_IDispatch)
            hwp = self._coerce_hwp_object(win32.Dispatch(obj))
            if hwp is None:
                self._log_hwp_replace(f"HWP NativeOM unusable hwnd={hwnd}")
            return hwp
        except Exception as exc:
            self._log_hwp_replace(f"HWP NativeOM exception hwnd={hwnd}: {type(exc).__name__}: {exc}")
            return None

    def _coerce_hwp_object(self, obj):
        if obj is None:
            return None
        required = ("MovePos", "Run", "HAction", "HParameterSet")
        for candidate in self._hwp_dispatch_candidates(obj):
            if all(hasattr(candidate, name) for name in required):
                return candidate
        return None

    def _hwp_dispatch_candidates(self, obj):
        try:
            import win32com.client as win32
        except Exception:
            return []

        candidates = [obj]
        try:
            candidates.append(win32.Dispatch(obj))
        except Exception:
            pass

        for source in (obj, getattr(obj, "_oleobj_", None)):
            if source is None:
                continue
            query = getattr(source, "QueryInterface", None)
            if not callable(query):
                continue
            for iid in self._hwp_query_interface_iids():
                try:
                    candidates.append(win32.Dispatch(query(iid)))
                except Exception:
                    pass

        wrapped_candidates = []
        for candidate in candidates:
            wrapped_candidates.append(candidate)
            try:
                wrapped_candidates.append(win32.CastTo(candidate, "IHwpObject"))
            except Exception:
                pass
        return wrapped_candidates

    def _hwp_query_interface_iids(self):
        iids = []
        try:
            from pywintypes import IID

            iids.append(IID(HWP_IHWP_OBJECT_IID))
        except Exception:
            pass
        if pythoncom is not None:
            try:
                iids.append(pythoncom.IID_IDispatch)
            except Exception:
                pass
        return iids

    def _apply_to_hwp_via_uia(self, window_handle: int | None, text: str):
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original HWP window is no longer available.")
        wrapper = self._find_hwp_edit_wrapper(window_handle)
        if wrapper is None:
            raise RuntimeError("No writable HWP text control was found.")
        if self._set_uia_value(wrapper, text):
            return
        raise RuntimeError("The HWP text control does not expose a writable UIA value pattern.")

    def _apply_to_hwp_via_keyboard_once(self, window_handle: int | None, text: str):
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original HWP window is no longer available.")
        if not self._is_hwp_window(window_handle):
            raise RuntimeError("The captured window is not an HWP window.")

        Application, send_keys = self._load_pywinauto()
        if Application is None or send_keys is None or win32gui is None:
            raise RuntimeError("pywinauto and pywin32 are required for HWP fallback replacement.")

        original_clipboard = self._read_clipboard_safely()
        try:
            app = Application(backend="win32").connect(handle=window_handle)
            window = app.window(handle=window_handle)
            win32gui.ShowWindow(window_handle, 5)
            win32gui.SetForegroundWindow(window_handle)
            window.set_focus()
            time.sleep(0.25)
            if win32gui.GetForegroundWindow() != window_handle:
                raise RuntimeError("Could not focus the original HWP window.")

            self._copy_clipboard_safely(text)
            send_keys("^a")
            time.sleep(0.12)
            send_keys("{DELETE}")
            time.sleep(0.12)
            send_keys("^v")
            time.sleep(0.15)
        finally:
            if original_clipboard is not None:
                time.sleep(0.05)
                self._copy_clipboard_safely(original_clipboard)

    def _find_hwp_edit_wrapper(self, window_handle: int | None):
        if window_handle is None:
            return None
        try:
            from pywinauto import Desktop
            from pywinauto.uia_defines import IUIA
            from pywinauto.controls.uiawrapper import UIAWrapper
            from pywinauto.uia_element_info import UIAElementInfo
        except Exception:
            return None

        candidates = []
        try:
            desktop = Desktop(backend="uia")
            window = desktop.window(handle=window_handle).wrapper_object()
        except Exception:
            window = None

        try:
            focused_element = IUIA().get_focused_element()
            focused = UIAWrapper(UIAElementInfo(focused_element)) if focused_element else None
        except Exception:
            focused = None

        if focused is not None:
            candidates.append(("focused", focused))
            current = focused
            for depth in range(4):
                try:
                    current = current.parent() if current else None
                except Exception:
                    current = None
                if current is not None:
                    candidates.append((f"focused-parent-{depth + 1}", current))

        if window is not None:
            candidates.append(("window", window))
            candidates.extend(self._descendant_wrappers(window, max_depth=5, max_nodes=140))

        best_wrapper = None
        best_length = -1
        seen = set()
        for _source, wrapper in candidates:
            key = self._wrapper_identity(wrapper)
            if key in seen:
                continue
            seen.add(key)
            if self._is_excluded_hwp_wrapper(wrapper):
                continue
            if not self._has_uia_set_value(wrapper):
                continue
            current_text = self._extract_uia_text(wrapper)
            length = len(current_text)
            if length > best_length:
                best_wrapper = wrapper
                best_length = length
        return best_wrapper

    def _descendant_wrappers(self, root, max_depth: int, max_nodes: int):
        results = []
        queue = [(root, 0)]
        seen = set()
        visited = 0
        while queue and visited < max_nodes:
            current, depth = queue.pop(0)
            key = self._wrapper_identity(current)
            if key in seen:
                continue
            seen.add(key)
            visited += 1

            control_type, _title, class_name = self._describe_uia_wrapper(current)
            if control_type in HWP_TEXT_CONTROL_TYPES or "hwp" in class_name.lower():
                results.append((f"descendant-{depth}", current))

            if depth >= max_depth:
                continue
            try:
                children = current.children()
            except Exception:
                children = []
            for child in children:
                queue.append((child, depth + 1))
        return results

    def _set_uia_value(self, wrapper, text: str) -> bool:
        try:
            value_iface = getattr(wrapper, "iface_value", None)
            if value_iface is not None:
                value_iface.SetValue(text)
                return True
        except Exception:
            pass
        try:
            wrapper.set_edit_text(text)
            return True
        except Exception:
            return False

    def _has_uia_set_value(self, wrapper) -> bool:
        try:
            value_iface = getattr(wrapper, "iface_value", None)
            return bool(value_iface and not value_iface.CurrentIsReadOnly)
        except Exception:
            return False

    def _extract_uia_text(self, wrapper) -> str:
        readers = (
            lambda: wrapper.iface_value.CurrentValue if wrapper.iface_value else "",
            lambda: wrapper.legacy_properties().get("Value", ""),
            lambda: wrapper.legacy_properties().get("Name", ""),
            lambda: wrapper.iface_text.DocumentRange.GetText(-1)
            if wrapper.iface_text and wrapper.iface_text.DocumentRange
            else "",
            lambda: "\n".join(str(value) for value in wrapper.texts()),
            lambda: wrapper.window_text(),
        )
        values = []
        for reader in readers:
            try:
                value = reader()
            except Exception:
                continue
            normalized = self._normalize_text(str(value)) if value is not None else ""
            if normalized.strip():
                values.append(normalized)
        return max(values, key=len) if values else ""

    def _is_excluded_hwp_wrapper(self, wrapper) -> bool:
        control_type, title, class_name = self._describe_uia_wrapper(wrapper)
        hints = f"{control_type}\n{title}\n{class_name}".lower()
        return any(hint in hints for hint in HWP_EXCLUDED_TEXT_HINTS)

    def _describe_uia_wrapper(self, wrapper) -> tuple[str, str, str]:
        try:
            element_info = wrapper.element_info
            control_type = element_info.control_type or ""
            class_name = element_info.class_name or ""
        except Exception:
            control_type = ""
            class_name = ""
        try:
            title = wrapper.window_text() or ""
        except Exception:
            title = ""
        return control_type, self._normalize_text(title), class_name

    def _wrapper_identity(self, wrapper):
        try:
            info = wrapper.element_info
            return (
                getattr(wrapper, "handle", None),
                info.control_type,
                info.automation_id,
                info.name,
                info.class_name,
            )
        except Exception:
            return id(wrapper)

    def _normalize_text(self, text: str | None) -> str:
        if not text:
            return ""
        return (
            str(text)
            .replace("\x00", "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\v", "\n")
            .replace("\f", "\n")
        )

    def _log_hwp_replace(self, message: str):
        try:
            with _HWP_REPLACE_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except Exception:
            pass

    def _diagnose_hwp_textfile_formats(self, hwp):
        getter = getattr(hwp, "GetTextFile", None)
        if not callable(getter):
            self._log_hwp_replace("HWP GetTextFile diagnostic skipped: method unavailable")
            return

        for fmt in HWP_TEXTFILE_FORMATS:
            for option in HWP_TEXTFILE_OPTIONS:
                try:
                    data = getter(fmt, option)
                    if isinstance(data, bytes):
                        text = data.decode("utf-8", errors="replace")
                    else:
                        text = str(data) if data is not None else ""
                    lowered = text.lower()
                    hints = [
                        token
                        for token in (
                            "charshape",
                            "charpr",
                            "textcolor",
                            "underline",
                            "fontref",
                            "facename",
                            "hcharshape",
                        )
                        if token in lowered
                    ]
                    preview = text[:300].replace("\n", "\\n").replace("\r", "\\r")
                    self._log_hwp_replace(
                        "HWP GetTextFile "
                        f"format={fmt!r} option={option!r} length={len(text)} "
                        f"hints={hints!r} preview={preview!r}"
                    )
                    if text and hints and fmt in {"HTML", "HWPML2X"}:
                        self._write_hwp_textfile_snapshot(fmt, option, text)
                except Exception as exc:
                    self._log_hwp_replace(
                        "HWP GetTextFile failed "
                        f"format={fmt!r} option={option!r}: {type(exc).__name__}: {exc}"
                    )

    def _get_hwp_textfile(self, hwp, fmt: str, option: str) -> str:
        getter = getattr(hwp, "GetTextFile", None)
        if not callable(getter):
            return ""
        try:
            return str(getter(fmt, option) or "")
        except Exception as exc:
            self._log_hwp_replace(
                f"HWP GetTextFile direct failed format={fmt!r} option={option!r}: {type(exc).__name__}: {exc}"
            )
            return ""

    def _write_hwp_textfile_snapshot(self, fmt: str, option: str, text: str):
        try:
            _HWP_TEXTFILE_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            safe_option = option or "default"
            path = _HWP_TEXTFILE_SNAPSHOT_DIR / f"{fmt.lower()}_{safe_option}.txt"
            path.write_text(text, encoding="utf-8", errors="replace")
            self._log_hwp_replace(f"HWP GetTextFile snapshot saved path={str(path)!r}")
        except Exception as exc:
            self._log_hwp_replace(f"HWP GetTextFile snapshot failed: {type(exc).__name__}: {exc}")

    def _apply_hwpml2x_replacement(self, hwp, source_xml: str, replacement_text: str) -> bool:
        setter = getattr(hwp, "SetTextFile", None)
        if not callable(setter):
            self._log_hwp_replace("HWPML2X replacement skipped: SetTextFile unavailable")
            return False

        rich_xml = self._build_hwpml2x_replacement(source_xml, replacement_text)
        if not rich_xml:
            return False

        for option in ("insertfile", ""):
            try:
                hwp.MovePos(2)
                hwp.Run("SelectAll")
                result = setter(rich_xml, "HWPML2X", option)
                summary = self._summarize_hwpml2x_body(self._get_hwp_textfile(hwp, "HWPML2X", "selection"))
                self._log_hwp_replace(
                    f"HWPML2X SetTextFile option={option!r} result={result!r} summary={summary!r}"
                )
                if self._hwpml2x_summary_matches_text(summary, replacement_text):
                    return True
                if self._hwpml2x_summary_has_mixed_shapes(summary):
                    return True
            except Exception as exc:
                self._log_hwp_replace(
                    f"HWPML2X SetTextFile failed option={option!r}: {type(exc).__name__}: {exc}"
                )
        return False

    def _build_hwpml2x_replacement(self, source_xml: str, replacement_text: str) -> str:
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(source_xml.lstrip("\ufeff"))
        except Exception as exc:
            self._log_hwp_replace(f"HWPML2X replacement build parse failed: {type(exc).__name__}: {exc}")
            return ""

        text_nodes = root.findall(".//BODY//TEXT")
        if not text_nodes:
            self._log_hwp_replace("HWPML2X replacement build failed: no BODY TEXT nodes")
            return ""

        if "\n" in self._normalize_text(replacement_text) and self._assign_hwpml2x_lines(text_nodes, replacement_text):
            xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
            self._log_hwp_replace(
                "HWPML2X replacement built linewise "
                f"length={len(xml_body)} text_length={len(replacement_text)} "
                f"text_nodes={len(text_nodes)} lines={len(self._split_hwp_replacement_lines(replacement_text))}"
            )
            return '<?xml version="1.0" encoding="UTF-16" standalone="no" ?>' + xml_body

        original_lengths = []
        for text_node in text_nodes:
            original_lengths.append(sum(len(char_node.text or "") for char_node in text_node.findall("CHAR")))

        cursor = 0
        for index, text_node in enumerate(text_nodes):
            length = original_lengths[index]
            if index == len(text_nodes) - 1:
                chunk = replacement_text[cursor:]
            else:
                chunk = replacement_text[cursor : cursor + length]
            cursor += length
            for char_node in list(text_node.findall("CHAR")):
                text_node.remove(char_node)
            if chunk:
                char_node = ET.Element("CHAR")
                char_node.text = chunk
                text_node.append(char_node)

        xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
        self._log_hwp_replace(
            f"HWPML2X replacement built length={len(xml_body)} text_length={len(replacement_text)}"
        )
        return '<?xml version="1.0" encoding="UTF-16" standalone="no" ?>' + xml_body

    def _assign_hwpml2x_lines(self, text_nodes, replacement_text: str) -> bool:
        import xml.etree.ElementTree as ET

        lines = self._split_hwp_replacement_lines(replacement_text)
        if not lines or len(lines) > len(text_nodes):
            self._log_hwp_replace(
                "HWPML2X linewise skipped "
                f"lines={len(lines)} text_nodes={len(text_nodes)}"
            )
            return False

        for index, text_node in enumerate(text_nodes):
            line = lines[index] if index < len(lines) else ""
            for char_node in list(text_node.findall("CHAR")):
                text_node.remove(char_node)
            if line:
                char_node = ET.Element("CHAR")
                char_node.text = line
                text_node.append(char_node)
        return True

    def _split_hwp_replacement_lines(self, text: str) -> list[str]:
        normalized = self._normalize_text(text)
        lines = normalized.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def _hwpml2x_summary_has_mixed_shapes(self, summary: list[dict]) -> bool:
        shapes = {str(item.get("shape")) for item in summary if item.get("shape") is not None}
        return len(shapes) > 1 or bool(shapes - {"0"})

    def _hwpml2x_summary_matches_text(self, summary: list[dict], replacement_text: str) -> bool:
        summary_text = "".join(str(item.get("text") or "") for item in summary)
        replacement_content = "".join(line for line in self._split_hwp_replacement_lines(replacement_text) if line)
        if not replacement_content:
            return False
        return summary_text.startswith(replacement_content[: max(1, min(len(replacement_content), 24))])

    def _apply_hwp_style(self, hwp, style_info: dict):
        if not style_info:
            return
        segments = style_info.get("segments") or []
        if segments:
            self._apply_hwp_style_segments(hwp, segments, str(style_info.get("_replacement_text") or ""))
            return
        if style_info.get("hwp_style_scope") != "basic":
            self._log_hwp_replace(f"HWP style skipped scope={style_info.get('hwp_style_scope')!r}")
            return
        safe_style = self._sanitize_hwp_style_info(style_info)
        if not safe_style:
            self._log_hwp_replace(f"HWP style skipped unsafe style={style_info!r}")
            return
        if "font_name" not in safe_style or "font_size" not in safe_style:
            self._log_hwp_replace(f"HWP style skipped incomplete style={safe_style!r}")
            return
        try:
            hwp.Run("SelectAll")
            hwp.HAction.GetDefault("CharShape", hwp.HParameterSet.HCharShape.HSet)
            char_shape = hwp.HParameterSet.HCharShape
            font_name = safe_style.get("font_name")
            if font_name:
                for attr in (
                    "FaceNameHangul",
                    "FaceNameLatin",
                    "FaceNameHanja",
                    "FaceNameJapanese",
                    "FaceNameOther",
                    "FaceNameSymbol",
                    "FaceNameUser",
                ):
                    try:
                        setattr(char_shape, attr, font_name)
                    except Exception:
                        pass
            height = self._hwp_points_to_height(safe_style.get("font_size"))
            if height is not None:
                try:
                    char_shape.Height = height
                except Exception:
                    pass
            for key, attr in (
                ("color", "TextColor"),
                ("underline_type", "UnderlineType"),
                ("underline_shape", "UnderlineShape"),
                ("underline_color", "UnderlineColor"),
            ):
                value = safe_style.get(key)
                if value is None:
                    continue
                try:
                    setattr(char_shape, attr, value)
                except Exception:
                    pass
            for key, attr in (("bold", "Bold"), ("italic", "Italic")):
                value = style_info.get(key)
                if value is None:
                    continue
                try:
                    setattr(char_shape, attr, 1 if bool(value) else 0)
                except Exception:
                    pass
            hwp.HAction.Execute("CharShape", hwp.HParameterSet.HCharShape.HSet)
            self._log_hwp_replace(f"HWP style applied style={safe_style!r}")
        except Exception as exc:
            self._log_hwp_replace(f"HWP style apply failed: {type(exc).__name__}: {exc}")
        finally:
            try:
                hwp.Run("Cancel")
            except Exception:
                pass

    def _capture_hwp_style_segments(self, hwp, source_text: str) -> list[dict]:
        if not source_text:
            return []
        if len(source_text) > 500:
            self._log_hwp_replace(f"HWP segment capture skipped length={len(source_text)}")
            return []
        segments: list[dict] = []
        current_signature = None
        current_style = None
        segment_start = 0
        text_index = 0
        try:
            for char in source_text:
                if char == "\n":
                    text_index += 1
                    continue
                start_pos = self._hwp_text_index_to_position(source_text, text_index)
                end_pos = self._hwp_text_index_to_position(source_text, text_index + 1)
                style = self._read_hwp_style_for_range(hwp, start_pos, end_pos)
                signature = tuple(sorted(style.items()))
                if current_signature is None:
                    current_signature = signature
                    current_style = style
                    segment_start = text_index
                elif signature != current_signature:
                    self._append_hwp_segment(segments, segment_start, text_index, current_style)
                    current_signature = signature
                    current_style = style
                    segment_start = text_index
                text_index += 1
            if current_signature is not None:
                self._append_hwp_segment(segments, segment_start, text_index, current_style)
        except Exception as exc:
            self._log_hwp_replace(f"HWP segment capture failed: {type(exc).__name__}: {exc}")
            segments = []
        finally:
            try:
                hwp.Run("Cancel")
            except Exception:
                pass
        if len(segments) <= 1:
            self._log_hwp_replace(f"HWP segment capture not useful count={len(segments)}")
            return []
        self._log_hwp_replace(f"HWP segment capture count={len(segments)}")
        return segments

    def _capture_hwp_style_segments_from_hwpml2x(self, hwp) -> list[dict]:
        getter = getattr(hwp, "GetTextFile", None)
        if not callable(getter):
            return []
        for option in ("selection", ""):
            try:
                data = getter("HWPML2X", option)
                xml_text = str(data) if data is not None else ""
            except Exception as exc:
                self._log_hwp_replace(
                    f"HWPML2X segment capture failed option={option!r}: {type(exc).__name__}: {exc}"
                )
                continue
            segments = self._parse_hwpml2x_style_segments(xml_text)
            if segments:
                self._log_hwp_replace(
                    f"HWPML2X segment capture count={len(segments)} option={option!r}"
                )
                return segments
        self._log_hwp_replace("HWPML2X segment capture not useful")
        return []

    def _parse_hwpml2x_style_segments(self, xml_text: str) -> list[dict]:
        if not xml_text:
            return []
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml_text.lstrip("\ufeff"))
        except Exception as exc:
            self._log_hwp_replace(f"HWPML2X parse failed: {type(exc).__name__}: {exc}")
            return []

        font_names = self._parse_hwpml2x_font_names(root)
        char_shapes = self._parse_hwpml2x_char_shapes(root, font_names)
        if not char_shapes:
            return []

        segments: list[dict] = []
        position = 0
        previous_signature = None
        for paragraph in root.findall(".//BODY//P"):
            if position > 0:
                position += 1
            for text_node in paragraph.findall("TEXT"):
                chunk = "".join(char_node.text or "" for char_node in text_node.findall("CHAR"))
                if not chunk:
                    continue
                style = char_shapes.get(text_node.get("CharShape") or "")
                start = position
                end = position + len(chunk)
                position = end
                if not style:
                    continue
                signature = tuple(sorted(style.items()))
                if segments and signature == previous_signature and segments[-1]["end"] == start:
                    segments[-1]["end"] = end
                else:
                    segments.append({"start": start, "end": end, "style": dict(style)})
                previous_signature = signature

        if len(segments) <= 1:
            return []
        return segments

    def _parse_hwpml2x_font_names(self, root) -> dict[str, str]:
        font_names: dict[str, str] = {}
        for font_face in root.findall(".//FACENAMELIST/FONTFACE"):
            if font_face.get("Lang") != "Hangul":
                continue
            for font in font_face.findall("FONT"):
                font_id = font.get("Id")
                name = font.get("Name")
                if font_id is not None and name:
                    font_names[font_id] = name
            break
        return font_names

    def _parse_hwpml2x_char_shapes(self, root, font_names: dict[str, str]) -> dict[str, dict]:
        char_shapes: dict[str, dict] = {}
        for node in root.findall(".//CHARSHAPELIST/CHARSHAPE"):
            shape_id = node.get("Id")
            if shape_id is None:
                continue
            style = self._sanitize_hwp_style_info(
                {
                    "font_name": self._hwpml2x_font_name(node, font_names),
                    "font_size": self._hwp_height_to_points_value(node.get("Height")),
                    "color": self._safe_hwp_int(node.get("TextColor")),
                    "bold": node.find("BOLD") is not None,
                    "italic": node.find("ITALIC") is not None,
                    **self._hwpml2x_underline_style(node),
                    **self._hwpml2x_strikeout_style(node),
                },
                require_base=False,
            )
            if style:
                char_shapes[shape_id] = style
        return char_shapes

    def _hwpml2x_font_name(self, char_shape_node, font_names: dict[str, str]) -> str | None:
        font_id = char_shape_node.find("FONTID")
        if font_id is None:
            return None
        return font_names.get(font_id.get("Hangul") or "")

    def _hwpml2x_underline_style(self, char_shape_node) -> dict:
        underline = char_shape_node.find("UNDERLINE")
        if underline is None:
            return {"underline_type": 0}
        return {
            "underline_type": 1,
            "underline_shape": 0,
            "underline_color": self._safe_hwp_int(underline.get("Color")),
        }

    def _hwpml2x_strikeout_style(self, char_shape_node) -> dict:
        strikeout = char_shape_node.find("STRIKEOUT")
        if strikeout is None:
            return {"strikeout_type": 0}
        return {
            "strikeout_type": 1,
            "strikeout_shape": 0,
            "strikeout_color": self._safe_hwp_int(strikeout.get("Color")),
        }

    def _read_hwp_style_for_range(self, hwp, start_pos: tuple[int, int], end_pos: tuple[int, int]) -> dict:
        try:
            hwp.SelectText(start_pos[0], start_pos[1], end_pos[0], end_pos[1])
            hwp.HAction.GetDefault("CharShape", hwp.HParameterSet.HCharShape.HSet)
            char_shape = hwp.HParameterSet.HCharShape
        except Exception:
            return {}
        return self._sanitize_hwp_style_info(
            {
                "font_name": self._first_hwp_value(
                    char_shape,
                    (
                        "FaceNameHangul",
                        "FaceNameLatin",
                        "FaceNameHanja",
                        "FaceNameJapanese",
                        "FaceNameOther",
                        "FaceNameSymbol",
                        "FaceNameUser",
                    ),
                ),
                "font_size": self._hwp_height_to_points_value(self._hwp_attr(char_shape, "Height")),
                "color": self._hwp_attr(char_shape, "TextColor"),
                "bold": self._hwp_bool(self._hwp_attr(char_shape, "Bold")),
                "italic": self._hwp_bool(self._hwp_attr(char_shape, "Italic")),
                "underline_type": self._hwp_attr(char_shape, "UnderlineType"),
                "underline_shape": self._hwp_attr(char_shape, "UnderlineShape"),
                "underline_color": self._hwp_attr(char_shape, "UnderlineColor"),
            },
            require_base=False,
        )

    def _append_hwp_segment(self, segments: list[dict], start: int, end: int, style: dict | None):
        if end <= start or not style:
            return
        segments.append({"start": start, "end": end, "style": dict(style)})

    def _apply_hwp_style_segments(self, hwp, segments: list[dict], replacement_text: str = ""):
        applied = 0
        for segment in segments[:200]:
            try:
                start = max(0, int(segment.get("start", 0)))
                end = max(0, int(segment.get("end", 0)))
            except Exception:
                continue
            if end <= start:
                continue
            style = self._sanitize_hwp_style_info(segment.get("style") or {}, require_base=False)
            if not style:
                continue
            start_pos = self._hwp_text_index_to_position(replacement_text, start)
            end_pos = self._hwp_text_index_to_position(replacement_text, end)
            try:
                if applied < 8:
                    self._log_hwp_replace(
                        "HWP segment style try "
                        f"range=({start},{end}) pos={start_pos}->{end_pos} "
                        f"text={replacement_text[start:end]!r} style={style!r}"
                    )
                self._select_hwp_text_range(hwp, start_pos, end_pos, end - start)
                hwp.HAction.GetDefault("CharShape", hwp.HParameterSet.HCharShape.HSet)
                char_shape = hwp.HParameterSet.HCharShape
                self._assign_hwp_char_shape(char_shape, style)
                hwp.HAction.Execute("CharShape", hwp.HParameterSet.HCharShape.HSet)
                applied += 1
            except Exception as exc:
                self._log_hwp_replace(f"HWP segment style apply failed: {type(exc).__name__}: {exc}")
        try:
            hwp.Run("Cancel")
        except Exception:
            pass
        self._log_hwp_replace(f"HWP segment styles applied count={applied} total={len(segments)}")
        self._log_hwpml2x_body_summary(hwp, "after_segment_apply")

    def _select_hwp_text_range(self, hwp, start_pos: tuple[int, int], end_pos: tuple[int, int], length: int):
        if self._select_hwp_text_range_by_cursor(hwp, start_pos, end_pos, length):
            return
        hwp.SelectText(start_pos[0], start_pos[1], end_pos[0], end_pos[1])

    def _select_hwp_text_range_by_cursor(
        self,
        hwp,
        start_pos: tuple[int, int],
        end_pos: tuple[int, int],
        length: int,
    ) -> bool:
        if not ENABLE_HWP_CURSOR_SEGMENT_SELECTION:
            return False
        if length <= 0 or length > 500:
            return False
        if start_pos[0] != end_pos[0]:
            return False
        try:
            hwp.Run("Cancel")
        except Exception:
            pass
        try:
            hwp.SetPos(0, start_pos[0], start_pos[1])
            for _ in range(length):
                hwp.Run("MoveSelRight")
            if length <= 3:
                self._log_hwp_replace(
                    f"HWP range selected via cursor pos={start_pos}->{end_pos} length={length}"
                )
            return True
        except Exception as exc:
            self._log_hwp_replace(
                f"HWP cursor range select failed pos={start_pos}->{end_pos}: {type(exc).__name__}: {exc}"
            )
            try:
                hwp.Run("Cancel")
            except Exception:
                pass
            return False

    def _assign_hwp_char_shape(self, char_shape, style: dict):
        font_name = style.get("font_name")
        if font_name:
            for attr in (
                "FaceNameHangul",
                "FaceNameLatin",
                "FaceNameHanja",
                "FaceNameJapanese",
                "FaceNameOther",
                "FaceNameSymbol",
                "FaceNameUser",
            ):
                try:
                    setattr(char_shape, attr, font_name)
                except Exception:
                    pass
        height = self._hwp_points_to_height(style.get("font_size"))
        if height is not None:
            try:
                char_shape.Height = height
            except Exception:
                pass

    def _log_hwpml2x_body_summary(self, hwp, label: str):
        getter = getattr(hwp, "GetTextFile", None)
        if not callable(getter):
            return
        try:
            xml_text = str(getter("HWPML2X", "selection") or "")
            summary = self._summarize_hwpml2x_body(xml_text)
            self._log_hwp_replace(f"HWPML2X body summary {label}: {summary!r}")
        except Exception as exc:
            self._log_hwp_replace(f"HWPML2X body summary failed {label}: {type(exc).__name__}: {exc}")

    def _summarize_hwpml2x_body(self, xml_text: str) -> list[dict]:
        if not xml_text:
            return []
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml_text.lstrip("\ufeff"))
        except Exception:
            return []
        summary = []
        for text_node in root.findall(".//BODY//TEXT"):
            chunk = "".join(char_node.text or "" for char_node in text_node.findall("CHAR"))
            if not chunk:
                continue
            summary.append({"shape": text_node.get("CharShape"), "text": chunk[:20]})
            if len(summary) >= 30:
                break
        return summary
        for key, attr in (
            ("color", "TextColor"),
            ("underline_type", "UnderlineType"),
            ("underline_shape", "UnderlineShape"),
            ("underline_color", "UnderlineColor"),
            ("strikeout_type", "StrikeOutType"),
            ("strikeout_shape", "StrikeOutShape"),
            ("strikeout_color", "StrikeOutColor"),
        ):
            value = style.get(key)
            if value is None:
                continue
            try:
                setattr(char_shape, attr, value)
            except Exception:
                pass
        for key, attr in (("bold", "Bold"), ("italic", "Italic")):
            value = style.get(key)
            if value is None:
                continue
            try:
                setattr(char_shape, attr, 1 if bool(value) else 0)
            except Exception:
                pass

    def _hwp_points_to_height(self, value):
        if value is None:
            return None
        try:
            points = float(value)
        except Exception:
            return None
        if points < 4 or points > 200:
            return None
        return int(round(points * 100))

    def _sanitize_hwp_style_info(self, style_info: dict, require_base: bool = True) -> dict:
        safe: dict = {}
        font_name = style_info.get("font_name")
        if isinstance(font_name, str) and font_name.strip():
            safe["font_name"] = font_name.strip()

        font_size = self._safe_hwp_float(style_info.get("font_size"))
        if font_size is not None and 4 <= font_size <= 200:
            safe["font_size"] = font_size

        color = self._safe_hwp_int(style_info.get("color"))
        if color is not None and 0 <= color <= 0xFFFFFF:
            safe["color"] = color

        underline_color = self._safe_hwp_int(style_info.get("underline_color"))
        if underline_color is not None and 0 <= underline_color <= 0xFFFFFF:
            safe["underline_color"] = underline_color

        for key in ("underline_type", "underline_shape", "strikeout_type", "strikeout_shape"):
            value = self._safe_hwp_int(style_info.get(key))
            if value is not None and 0 <= value <= 20:
                safe[key] = value

        strikeout_color = self._safe_hwp_int(style_info.get("strikeout_color"))
        if strikeout_color is not None and 0 <= strikeout_color <= 0xFFFFFF:
            safe["strikeout_color"] = strikeout_color

        for key in ("bold", "italic"):
            value = style_info.get(key)
            if isinstance(value, bool):
                safe[key] = value
            elif value in (0, 1):
                safe[key] = bool(value)
        if require_base and ("font_name" not in safe or "font_size" not in safe):
            return {}
        return safe

    def _first_hwp_value(self, obj, names: tuple[str, ...]):
        for name in names:
            value = self._hwp_attr(obj, name)
            if value not in (None, ""):
                return value
        return None

    def _hwp_attr(self, obj, name: str):
        try:
            return getattr(obj, name)
        except Exception:
            return None

    def _hwp_bool(self, value):
        if value is None:
            return None
        try:
            return bool(int(value))
        except Exception:
            return bool(value)

    def _hwp_height_to_points_value(self, value):
        if value is None:
            return None
        try:
            points = float(value) / 100
        except Exception:
            return None
        if points < 4 or points > 200:
            return None
        return points

    def _hwp_text_index_to_position(self, text: str, index: int) -> tuple[int, int]:
        para = 0
        pos = 0
        for char in (text or "")[:index]:
            if char == "\n":
                para += 1
                pos = 0
            else:
                pos += 1
        return para, pos

    def _safe_hwp_int(self, value):
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _safe_hwp_float(self, value):
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _is_live_window(self, window_handle: int | None) -> bool:
        if win32gui is None or not window_handle:
            return False
        try:
            return bool(win32gui.IsWindow(window_handle))
        except Exception:
            return False

    def _is_hwp_window(self, window_handle: int | None) -> bool:
        if not self._is_live_window(window_handle):
            return False
        process_name = self._process_name_for_window(window_handle)
        if process_name:
            return process_name in HWP_PROCESS_NAMES or process_name.startswith("hwp")
        try:
            class_name = (win32gui.GetClassName(window_handle) or "").lower()
        except Exception:
            class_name = ""
        return "hwp" in class_name or "hnc" in class_name

    def _process_name_for_window(self, window_handle: int | None) -> str:
        if win32process is None or not window_handle:
            return ""
        try:
            _thread_id, process_id = win32process.GetWindowThreadProcessId(window_handle)
        except Exception:
            return ""
        if not process_id or psutil is None:
            return ""
        try:
            return psutil.Process(process_id).name().lower()
        except Exception:
            return ""

    def _focus_window(self, window_handle: int | None):
        if win32gui is None or not self._is_live_window(window_handle):
            return
        try:
            win32gui.ShowWindow(window_handle, 5)
            win32gui.SetForegroundWindow(window_handle)
            time.sleep(0.2)
        except Exception:
            pass

    def _read_clipboard_safely(self):
        for _ in range(3):
            try:
                return pyperclip.paste()
            except Exception:
                time.sleep(0.05)
        return None

    def _copy_clipboard_safely(self, text):
        for _ in range(3):
            try:
                pyperclip.copy(text)
                return True
            except Exception:
                time.sleep(0.05)
        return False

    def _load_pywinauto(self):
        try:
            from pywinauto import Application
            from pywinauto.keyboard import send_keys

            return Application, send_keys
        except Exception:
            return None, None


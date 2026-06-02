from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
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
_WORD_TYPELIB_PREFIX = "00020905-0000-0000-C000-000000000046"
_WORD_COM_CACHE_ERROR_MARKERS = (
    "win32com.gen_py",
    "CLSIDToPackageMap",
    "CLSIDToClassMap",
)
EM_REPLACESEL = 0x00C2
EM_GETSEL = 0x00B0
EM_SETSEL = 0x00B1


def _looks_like_broken_word_com_cache(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}"
    return any(marker in message for marker in _WORD_COM_CACHE_ERROR_MARKERS)


def _clear_broken_word_com_cache():
    import win32com.client.gencache as gencache

    for module_name in list(sys.modules):
        if module_name.startswith(f"win32com.gen_py.{_WORD_TYPELIB_PREFIX}"):
            sys.modules.pop(module_name, None)

    generate_path = Path(gencache.GetGeneratePath())
    if generate_path.exists():
        for cache_item in generate_path.glob(f"{_WORD_TYPELIB_PREFIX}*"):
            if cache_item.is_dir():
                shutil.rmtree(cache_item, ignore_errors=True)
            else:
                try:
                    cache_item.unlink()
                except OSError:
                    pass
        for cache_item in (generate_path / "__pycache__", generate_path / "dicts.dat"):
            if cache_item.is_dir():
                shutil.rmtree(cache_item, ignore_errors=True)
            elif cache_item.exists():
                try:
                    cache_item.unlink()
                except OSError:
                    pass
    gencache.is_readonly = False
    gencache.Rebuild()


@dataclass
class OutputTarget:
    mode: str
    window_handle: int | None = None
    window_title: str = ""
    style_info: dict | None = None


def _get_active_word_application():
    if pythoncom is None:
        raise RuntimeError("pywin32 is required for Word automation.")
    import win32com.client.dynamic as dynamic

    active = pythoncom.GetActiveObject("Word.Application")
    try:
        active = active.QueryInterface(pythoncom.IID_IDispatch)
    except Exception:
        pass
    return dynamic.Dispatch(active)


class OutputApplier:
    def inspect_replace_availability(self, target: OutputTarget | None) -> tuple[bool, str | None]:
        if target is None:
            return False, "No source window has been captured yet."
        if target.mode == "browser_extension":
            session_id = (target.style_info or {}).get("browser_session_id")
            if session_id:
                return True, None
            return False, "The browser extension has not captured an editable field yet."
        if target.mode in ("browser", "notepad", "notepad_selection"):
            if self._is_live_window(target.window_handle):
                return True, None
            return False, "The original input window is no longer available."
        if target.mode in ("word", "word_selection"):
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

        if target.mode == "notepad_selection":
            self._apply_to_notepad_selection(target.window_handle, text)
            return

        if target.mode == "word_selection":
            self._focus_window(target.window_handle)
            self._apply_word_operation_with_cache_repair(
                lambda: self._apply_to_word_selection(text, target.style_info)
            )
            return

        if target.mode == "word":
            self._focus_window(target.window_handle)
            self._apply_word_operation_with_cache_repair(
                lambda: self._apply_to_active_word(text, target.style_info)
            )
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

    def _apply_word_operation_with_cache_repair(self, operation):
        try:
            return operation()
        except Exception as exc:
            if not _looks_like_broken_word_com_cache(exc):
                raise
            self._log_word_replace(f"broken Word COM cache detected; repairing and retrying: {exc}")
            try:
                _clear_broken_word_com_cache()
            except Exception as repair_exc:
                self._log_word_replace(f"Word COM cache repair failed: {type(repair_exc).__name__}: {repair_exc}")
                raise
            self._log_word_replace("Word COM cache repaired; retrying replacement")
            return operation()

    def _apply_to_notepad_selection(self, window_handle: int | None, text: str):
        _Application, send_keys = self._load_pywinauto()
        if win32gui is None:
            raise RuntimeError("pywin32 is required for selection replacement.")
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original selection window is no longer available.")
        if send_keys is None:
            if self._apply_to_notepad_selection_direct(window_handle, text):
                return
            raise RuntimeError("pywinauto and pywin32 are required for selection replacement.")

        original_clipboard = self._read_clipboard_safely()
        try:
            self._focus_window(window_handle)
            self._copy_clipboard_safely(text)
            time.sleep(0.05)
            self._send_ctrl_key("v")
        finally:
            if original_clipboard is not None:
                time.sleep(0.08)
                self._copy_clipboard_safely(original_clipboard)

    def _apply_to_notepad_selection_direct(self, window_handle: int | None, text: str) -> bool:
        editor = self._best_notepad_editor(window_handle)
        if not editor:
            return False
        try:
            self._focus_window(window_handle)
            time.sleep(0.05)
            buffer = ctypes.c_wchar_p(str(text or ""))
            ctypes.windll.user32.SendMessageW(int(editor), EM_REPLACESEL, True, buffer)
            self._log_word_replace(f"notepad direct selection replace text_len={len(str(text or ''))} sample={str(text or '')[:80]!r}")
            return True
        except Exception as exc:
            self._log_word_replace(f"notepad direct selection replace failed: {type(exc).__name__}: {exc}")
            return False

    def apply_to_notepad_subrange(self, target: OutputTarget | None, relative_start: int, relative_end: int, text: str):
        if target is None or target.mode not in {"notepad", "notepad_selection"}:
            raise RuntimeError("Notepad subrange replacement requires a Notepad target.")
        if win32gui is None:
            raise RuntimeError("pywin32 is required for Notepad selection replacement.")
        if not self._is_live_window(target.window_handle):
            raise RuntimeError("The original Notepad window is no longer available.")

        editor = self._best_notepad_editor(target.window_handle)
        if not editor:
            raise RuntimeError("The Notepad editor control could not be found.")

        style_info = dict(target.style_info or {})
        selection_text = str(style_info.get("selection_text") or style_info.get("selected_text") or "")
        if not selection_text:
            selection_text = str(style_info.get("text") or "")

        current_start, current_end = self._get_edit_selection(editor)
        full_text = self._read_notepad_text_for_replace(target.window_handle)
        base_start = current_start if target.mode == "notepad_selection" else 0
        normalized_selection_start = 0
        if target.mode == "notepad_selection" and selection_text:
            expected_len = len(selection_text.replace("\r\n", "\n").replace("\r", "\n"))
            selected_len = max(0, current_end - current_start)
            if full_text:
                normalized_full = full_text.replace("\r\n", "\n").replace("\r", "\n")
                normalized_selection = selection_text.replace("\r\n", "\n").replace("\r", "\n")
                found = normalized_full.find(normalized_selection)
                if found >= 0:
                    normalized_selection_start = found
                    base_start = self._raw_index_from_normalized(full_text, found)
                elif selected_len != expected_len:
                    base_start = current_start
            elif selected_len != expected_len:
                base_start = current_start

        if full_text:
            absolute_start = self._raw_index_from_normalized(full_text, normalized_selection_start + int(relative_start))
            absolute_end = self._raw_index_from_normalized(full_text, normalized_selection_start + int(relative_end))
        else:
            index_text = selection_text if target.mode == "notepad_selection" and selection_text else ""
            absolute_start = base_start + self._raw_index_from_normalized(index_text, int(relative_start)) if index_text else base_start + int(relative_start)
            absolute_end = base_start + self._raw_index_from_normalized(index_text, int(relative_end)) if index_text else base_start + int(relative_end)
        if absolute_end <= absolute_start:
            raise RuntimeError("The marker range is empty.")

        original_clipboard = self._read_clipboard_safely()
        try:
            self._focus_window(target.window_handle)
            time.sleep(0.05)
            ctypes.windll.user32.SendMessageW(int(editor), EM_SETSEL, int(absolute_start), int(absolute_end))
            time.sleep(0.03)
            self._copy_clipboard_safely(text)
            time.sleep(0.03)
            self._send_ctrl_key("v")
            self._log_word_replace(
                f"notepad marker subrange write selection_base={base_start} "
                f"normalized_selection_start={normalized_selection_start} "
                f"relative=({relative_start},{relative_end}) absolute=({absolute_start},{absolute_end}) "
                f"text_len={len(str(text or ''))} sample={str(text or '')[:80]!r}"
            )
        finally:
            if original_clipboard is not None:
                time.sleep(0.08)
                self._copy_clipboard_safely(original_clipboard)

    def undo_last_notepad_action(self, window_handle: int | None = None):
        _Application, send_keys = self._load_pywinauto()
        if send_keys is None or win32gui is None:
            raise RuntimeError("pywinauto and pywin32 are required for Notepad undo.")
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original Notepad window is no longer available.")
        self._focus_window(window_handle)
        time.sleep(0.05)
        send_keys("^z")

    def redo_last_notepad_action(self, window_handle: int | None = None):
        _Application, send_keys = self._load_pywinauto()
        if send_keys is None or win32gui is None:
            raise RuntimeError("pywinauto and pywin32 are required for Notepad redo.")
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original Notepad window is no longer available.")
        self._focus_window(window_handle)
        time.sleep(0.05)
        send_keys("^+z")

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
            self._send_ctrl_key("a")
            time.sleep(0.08)
            self._send_virtual_key(0x2E)
            time.sleep(0.08)
            self._send_ctrl_key("v")
        finally:
            if original_clipboard is not None:
                time.sleep(0.05)
                self._copy_clipboard_safely(original_clipboard)


    def insert_title_at_top(self, target: OutputTarget | None, title: str):
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("There is no title to insert.")
        can_replace, reason = self.inspect_replace_availability(target)
        if not can_replace:
            raise RuntimeError(reason or "Title insertion is unavailable.")
        mode = getattr(target, "mode", "")
        if mode in {"word", "word_selection"}:
            self._focus_window(target.window_handle)
            self._apply_word_operation_with_cache_repair(lambda: self._insert_word_title_at_top(clean_title))
            return
        if mode in {"notepad", "notepad_selection"}:
            self._insert_notepad_title_at_top(target.window_handle, clean_title)
            return
        raise RuntimeError(f"Title insertion is not supported for {mode}.")

    def insert_subtitle_for_selection(self, target: OutputTarget | None, title: str):
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("There is no subtitle to insert.")
        can_replace, reason = self.inspect_replace_availability(target)
        if not can_replace:
            raise RuntimeError(reason or "Subtitle insertion is unavailable.")
        mode = getattr(target, "mode", "")
        if mode == "word_selection":
            self._focus_window(target.window_handle)
            self._apply_word_operation_with_cache_repair(lambda: self._insert_word_subtitle_for_selection(target, clean_title))
            return
        if mode == "notepad_selection":
            self._insert_notepad_subtitle_for_selection(target, clean_title)
            return
        self.insert_title_at_top(target, clean_title)

    def _insert_notepad_title_at_top(self, window_handle: int | None, title: str):
        if not self._is_live_window(window_handle):
            raise RuntimeError("The original Notepad window is no longer available.")
        try:
            from client.input.notepad_monitor import _read_window_text
            current_text, _details = _read_window_text(window_handle)
        except Exception:
            current_text = ""
        combined = f"{title}\r\n\r\n{str(current_text or '').lstrip()}"
        self._apply_via_window_handle(window_handle, combined)

    def _insert_notepad_subtitle_for_selection(self, target: OutputTarget | None, title: str):
        selected_text = str((target.style_info or {}).get("selection_text") or "").strip()
        if not selected_text:
            selected_text = str((target.style_info or {}).get("selected_text") or "")
        if not selected_text:
            raise RuntimeError("The selected Notepad text is unavailable.")
        replacement = f"{title}\r\n\r\n{selected_text}"
        self._apply_to_notepad_selection(target.window_handle, replacement)

    def _insert_word_title_at_top(self, title: str):
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word title insertion.")
        pythoncom.CoInitialize()
        word = _get_active_word_application()
        document = getattr(word, "ActiveDocument", None)
        if document is None:
            raise RuntimeError("No active Word document is available.")
        word.Visible = True
        document.Activate()
        undo_record_started = self._start_word_undo_record(word, "Writing Assistant title")
        try:
            insert_text = self._word_text_for_write(f"{title}\n\n")
            title_range = document.Range(Start=0, End=0)
            title_range.InsertBefore(insert_text)
            title_end = len(insert_text)
            inserted_range = document.Range(Start=0, End=title_end)
            title_text_range = document.Range(Start=0, End=max(0, len(title)))
            self._reset_inserted_title_style(inserted_range)
            try:
                title_text_range.Font.Bold = True
                title_text_range.Font.Size = 14
            except Exception:
                pass
            try:
                document.Paragraphs.Item(1).Range.ParagraphFormat.Alignment = 1
            except Exception:
                pass
            try:
                document.Paragraphs.Item(2).Range.ParagraphFormat.Alignment = 0
            except Exception:
                pass
            try:
                document.Range(Start=title_end, End=title_end).Select()
            except Exception:
                pass
            self._log_word_replace(f"title inserted title={title[:80]!r}")
        finally:
            if undo_record_started:
                self._end_word_undo_record(word)

    def _insert_word_subtitle_for_selection(self, target: OutputTarget | None, title: str):
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word subtitle insertion.")
        pythoncom.CoInitialize()
        word = _get_active_word_application()
        style_info = dict((target.style_info if target is not None else None) or {})
        document = self._word_document_for_style_info(word, style_info)
        if document is None:
            raise RuntimeError("No active Word document is available.")
        try:
            selection_start = int(style_info.get("selection_start"))
            selection_end = int(style_info.get("selection_end"))
        except Exception as exc:
            raise RuntimeError("The original Word selection range is unavailable.") from exc
        if selection_end <= selection_start:
            raise RuntimeError("The original Word selection range is empty.")
        word.Visible = True
        document.Activate()

        selection_range = document.Range(Start=selection_start, End=selection_end)
        subtitle_font_size = self._subtitle_font_size_from_range(selection_range)
        probe_range = document.Range(Start=selection_start, End=selection_start)
        try:
            insert_start = int(probe_range.Paragraphs.Item(1).Range.Start)
        except Exception:
            insert_start = selection_start

        undo_record_started = self._start_word_undo_record(word, "Writing Assistant subtitle")
        try:
            insert_text = self._word_text_for_write(f"{title}\n\n")
            insert_range = document.Range(Start=insert_start, End=insert_start)
            insert_range.InsertBefore(insert_text)
            insert_end = insert_start + len(insert_text)
            inserted_range = document.Range(Start=insert_start, End=insert_end)
            title_text_range = document.Range(Start=insert_start, End=insert_start + len(title))
            self._reset_inserted_title_style(inserted_range)
            try:
                title_text_range.Font.Bold = False
                title_text_range.Font.Size = subtitle_font_size
            except Exception:
                pass
            try:
                document.Range(Start=insert_start, End=insert_start + len(title)).ParagraphFormat.Alignment = 1
            except Exception:
                pass
            try:
                document.Range(Start=insert_start + len(title) + 1, End=insert_end).ParagraphFormat.Alignment = 0
            except Exception:
                pass
            try:
                document.Range(Start=insert_end, End=insert_end).Select()
            except Exception:
                pass
            self._log_word_replace(
                f"subtitle inserted selection=({selection_start},{selection_end}) "
                f"insert_start={insert_start} font_size={subtitle_font_size!r} title={title[:80]!r}"
            )
        finally:
            if undo_record_started:
                self._end_word_undo_record(word)

    def _subtitle_font_size_from_range(self, word_range) -> float:
        sizes: list[float] = []
        try:
            size = float(getattr(word_range.Font, "Size", 0) or 0)
            if size > 0:
                sizes.append(size)
        except Exception:
            pass
        try:
            for run in word_range.Words:
                try:
                    size = float(getattr(run.Font, "Size", 0) or 0)
                    if size > 0:
                        sizes.append(size)
                except Exception:
                    continue
        except Exception:
            pass
        base = min(sizes) if sizes else 10.0
        return max(7.0, min(11.0, base - 1.0))

    def _reset_inserted_title_style(self, word_range):
        try:
            font = word_range.Font
            font.Bold = 0
            font.Italic = 0
            font.Underline = 0
            font.StrikeThrough = 0
            font.DoubleStrikeThrough = 0
            font.Subscript = 0
            font.Superscript = 0
            try:
                font.Color = -16777216
            except Exception:
                pass
            try:
                font.UnderlineColor = -16777216
            except Exception:
                pass
            try:
                word_range.HighlightColorIndex = 0
            except Exception:
                pass
        except Exception:
            pass

    def _current_word_selection_info(self, word):
        try:
            selection = getattr(word, "Selection", None)
            if selection is None:
                return None
            selection_range = selection.Range.Duplicate
            start = int(selection_range.Start)
            end = int(selection_range.End)
            if end <= start:
                return None
            selected_text = self._clean_word_selection_text(getattr(selection_range, "Text", "") or "")
            if not selected_text.strip():
                return None
            return {
                "range": selection_range,
                "document": getattr(selection_range, "Document", None),
                "start": start,
                "end": end,
                "text": selected_text,
            }
        except Exception as exc:
            self._log_word_replace(f"current selection read failed: {type(exc).__name__}: {exc}")
            return None

    def _word_selection_matches_document(self, document, selection_info: dict, style_info: dict) -> bool:
        current_document = selection_info.get("document")
        if current_document is None or document is None:
            return False
        try:
            if current_document == document:
                return True
        except Exception:
            pass
        expected_full_name = str(style_info.get("document_full_name") or "")
        expected_name = str(style_info.get("document_name") or "")
        try:
            current_full_name = str(getattr(current_document, "FullName", "") or "")
            current_name = str(getattr(current_document, "Name", "") or "")
        except Exception:
            return False
        if expected_full_name and current_full_name == expected_full_name:
            return True
        if expected_name and current_name == expected_name:
            return True
        return False

    def _read_current_word_selection_style(self, selection_range) -> dict:
        try:
            from client.input.drag_selection_monitor import _read_word_selection_style_info

            return _read_word_selection_style_info(selection_range) or {}
        except Exception as exc:
            self._log_word_replace(f"current selection style refresh failed: {type(exc).__name__}: {exc}")
            return {}

    def _clean_word_selection_text(self, text: str) -> str:
        return str(text or "").replace("\x00", "").replace("\x07", "").replace("\r\n", "\n").replace("\r", "\n")

    def _best_notepad_editor(self, hwnd: int | None) -> int | None:
        if win32gui is None or not hwnd:
            return None
        candidates: list[tuple[int, int, int, int]] = []

        def add_candidate(handle):
            try:
                class_name = win32gui.GetClassName(handle) or ""
            except Exception:
                class_name = ""
            if not any(hint in class_name.lower() for hint in ("edit", "richedit")):
                return
            try:
                if not win32gui.IsWindowVisible(handle):
                    return
            except Exception:
                pass
            area = 0
            try:
                left, top, right, bottom = win32gui.GetWindowRect(handle)
                area = max(0, int(right) - int(left)) * max(0, int(bottom) - int(top))
            except Exception:
                pass
            text_len = 0
            try:
                text_len = int(ctypes.windll.user32.SendMessageW(int(handle), 0x000E, 0, 0))
            except Exception:
                pass
            candidates.append((1 if area > 0 else 0, area, text_len, int(handle)))

        add_candidate(hwnd)

        def enum_proc(child, _):
            add_candidate(child)
            return True

        try:
            win32gui.EnumChildWindows(hwnd, enum_proc, None)
        except Exception:
            pass
        if not candidates:
            return None
        return max(candidates)[3]

    def _get_edit_selection(self, editor: int) -> tuple[int, int]:
        start = wintypes.DWORD(0)
        end = wintypes.DWORD(0)
        try:
            ctypes.windll.user32.SendMessageW(int(editor), EM_GETSEL, ctypes.byref(start), ctypes.byref(end))
            return int(start.value), int(end.value)
        except Exception:
            selection = int(ctypes.windll.user32.SendMessageW(int(editor), EM_GETSEL, 0, 0))
            return selection & 0xFFFF, (selection >> 16) & 0xFFFF

    def _read_notepad_text_for_replace(self, hwnd: int | None) -> str:
        try:
            from client.input.notepad_monitor import _read_window_text

            text, _details = _read_window_text(int(hwnd or 0))
            return str(text or "")
        except Exception:
            return ""

    def apply_to_word_selection_subrange(self, target: OutputTarget | None, relative_start: int, relative_end: int, text: str):
        if target is None or target.mode != "word_selection":
            raise RuntimeError("Word selection subrange replacement requires a Word selection target.")
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word selection replacement.")
        pythoncom.CoInitialize()
        self._focus_window(target.window_handle)
        word = _get_active_word_application()
        style_info = dict(target.style_info or {})
        document = self._word_document_for_style_info(word, style_info)
        if document is None:
            raise RuntimeError("No active Word document is available.")
        try:
            selection_start = int(style_info.get("selection_start"))
            selection_end = int(style_info.get("selection_end"))
        except Exception as exc:
            raise RuntimeError("The original Word selection range is unavailable.") from exc
        if selection_end <= selection_start:
            raise RuntimeError("The original Word selection range is empty.")

        selection_text = str(document.Range(Start=selection_start, End=selection_end).Text or "")
        raw_start = self._raw_index_from_normalized(selection_text, int(relative_start))
        raw_end = self._raw_index_from_normalized(selection_text, int(relative_end))
        absolute_start = selection_start + raw_start
        absolute_end = selection_start + raw_end
        if absolute_end <= absolute_start:
            raise RuntimeError("The marker range is empty.")

        undo_record_started = self._start_word_undo_record(word, "Writing Assistant marker correction")
        try:
            replacement_text = self._word_text_for_write(text)
            target_range = document.Range(Start=absolute_start, End=absolute_end)
            character_styles = self._capture_word_character_styles(target_range)
            self._log_word_replace(
                f"marker subrange write selection=({selection_start},{selection_end}) "
                f"relative=({relative_start},{relative_end}) absolute=({absolute_start},{absolute_end}) "
                f"text_len={len(str(text or ''))} char_styles={len(character_styles)} sample={str(text or '')[:80]!r}"
            )
            target_range.Text = replacement_text
            applied_end = absolute_start + len(replacement_text)
            self._apply_word_character_styles(document, absolute_start, applied_end, character_styles)
            self._select_word_range(document, absolute_start, applied_end)
        finally:
            if undo_record_started:
                self._end_word_undo_record(word)

    def apply_to_word_document_subrange(self, target: OutputTarget | None, relative_start: int, relative_end: int, text: str):
        if target is None or target.mode != "word":
            raise RuntimeError("Word document subrange replacement requires a Word target.")
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word replacement.")
        pythoncom.CoInitialize()
        self._focus_window(target.window_handle)
        word = _get_active_word_application()
        document = self._word_document_for_style_info(word, dict(target.style_info or {}))
        if document is None:
            raise RuntimeError("No active Word document is available.")
        content_range = document.Content
        content_start = int(getattr(content_range, "Start", 0) or 0)
        content_text = str(getattr(content_range, "Text", "") or "")
        raw_start = self._raw_index_from_normalized(content_text, int(relative_start))
        raw_end = self._raw_index_from_normalized(content_text, int(relative_end))
        absolute_start = content_start + raw_start
        absolute_end = content_start + raw_end
        if absolute_end <= absolute_start:
            raise RuntimeError("The marker range is empty.")

        undo_record_started = self._start_word_undo_record(word, "Writing Assistant marker correction")
        try:
            replacement_text = self._word_text_for_write(text)
            target_range = document.Range(Start=absolute_start, End=absolute_end)
            character_styles = self._capture_word_character_styles(target_range)
            self._log_word_replace(
                f"marker document subrange write relative=({relative_start},{relative_end}) "
                f"absolute=({absolute_start},{absolute_end}) text_len={len(str(text or ''))} "
                f"char_styles={len(character_styles)} sample={str(text or '')[:80]!r}"
            )
            target_range.Text = replacement_text
            applied_end = absolute_start + len(replacement_text)
            self._apply_word_character_styles(document, absolute_start, applied_end, character_styles)
            self._select_word_range(document, absolute_start, applied_end)
        finally:
            if undo_record_started:
                self._end_word_undo_record(word)

    def _raw_index_from_normalized(self, raw_text: str, normalized_index: int) -> int:
        raw_index = 0
        normalized_count = 0
        while raw_index < len(raw_text) and normalized_count < normalized_index:
            if raw_text[raw_index] == "\r":
                if raw_index + 1 < len(raw_text) and raw_text[raw_index + 1] == "\n":
                    raw_index += 2
                else:
                    raw_index += 1
                normalized_count += 1
            else:
                raw_index += 1
                normalized_count += 1
        return raw_index

    def _apply_to_word_selection(self, text: str, style_info: dict | None = None):
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word selection replacement.")
        pythoncom.CoInitialize()
        word = _get_active_word_application()
        style_info = style_info or {}
        document = self._word_document_for_style_info(word, style_info)
        if document is None:
            raise RuntimeError("No active Word document is available.")
        try:
            start = int(style_info.get("selection_start"))
            end = int(style_info.get("selection_end"))
        except Exception as exc:
            raise RuntimeError("The original Word selection range is unavailable.") from exc
        if end <= start:
            raise RuntimeError("The original Word selection range is empty.")

        current_selection = self._current_word_selection_info(word)
        style_info = dict(style_info)
        if current_selection and self._word_selection_matches_document(document, current_selection, style_info):
            current_start = current_selection["start"]
            current_end = current_selection["end"]
            if (current_start, current_end) != (start, end):
                expected_text = str(style_info.get("selection_text") or "").strip()
                current_text = str(current_selection.get("text") or "").strip()
                if expected_text and current_text != expected_text:
                    self._log_word_replace(
                        "selection target changed before apply: "
                        f"stored=({start},{end}) current=({current_start},{current_end}) "
                        f"expected_sample={expected_text[:40]!r} current_sample={current_text[:40]!r}"
                    )
                    raise RuntimeError("The Word selection changed. Wait for the mini overlay to refresh, then try again.")
                document = current_selection["document"]
                start = current_start
                end = current_end
                style_info.update(
                    {
                        "selection_start": start,
                        "selection_end": end,
                        "selection_text": current_selection.get("text") or "",
                    }
                )

        if style_info.get("style_capture_deferred") or not style_info.get("segments"):
            target_range = document.Range(Start=start, End=end)
            refreshed_style = self._read_current_word_selection_style(target_range)
            if refreshed_style:
                refreshed_style.update(style_info)
                style_info = refreshed_style
                style_info["style_capture_deferred"] = False

        undo_record_started = self._start_word_undo_record(word, "Writing Assistant correction")
        try:
            self._select_word_range(document, start, end)
            replacement_text = self._word_text_for_write(text)
            self._log_word_replace(
                f"selection write start={start} end={end} text_len={len(str(text or ''))} "
                f"segments={len(style_info.get('segments') or [])} sample={str(text or '')[:80]!r}"
            )
            selection_range = document.Range(Start=start, End=end)
            character_styles = self._capture_word_character_styles(selection_range)
            selection_range.Text = replacement_text
            applied_end = start + len(replacement_text)
            applied_range = document.Range(Start=start, End=applied_end)
            segments = style_info.get("segments") or []
            if segments:
                self._reset_word_style_flags(applied_range)
                self._apply_word_style_segments_to_base(document, start, applied_end, segments)
            else:
                self._reset_word_style_flags(applied_range)
                self._apply_word_style(applied_range, style_info)
            self._apply_word_last_line_style_overlay(document, start, applied_end, text, style_info)
            self._apply_word_character_styles(document, start, applied_end, character_styles)
            self._select_word_range(document, start, applied_end)
        finally:
            if undo_record_started:
                self._end_word_undo_record(word)

    def _start_word_undo_record(self, word, name: str) -> bool:
        try:
            undo_record = getattr(word, "UndoRecord", None)
            if undo_record is None:
                self._log_word_replace("UndoRecord unavailable; applying without custom undo group")
                return False
            undo_record.StartCustomRecord(name)
            self._log_word_replace(f"UndoRecord started name={name!r}")
            return True
        except Exception as exc:
            self._log_word_replace(f"UndoRecord start failed: {type(exc).__name__}: {exc}")
            return False

    def _end_word_undo_record(self, word):
        try:
            undo_record = getattr(word, "UndoRecord", None)
            if undo_record is not None:
                undo_record.EndCustomRecord()
                self._log_word_replace("UndoRecord ended")
        except Exception as exc:
            self._log_word_replace(f"UndoRecord end failed: {type(exc).__name__}: {exc}")

    def undo_last_word_action(self, window_handle: int | None = None):
        def operation():
            if pythoncom is None:
                raise RuntimeError("pywin32 is required for Word undo.")
            pythoncom.CoInitialize()
            if window_handle:
                self._focus_window(window_handle)
            word = _get_active_word_application()
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                raise RuntimeError("No active Word document is available.")
            errors = []
            undo_attempts = (
                ("CommandBars.ExecuteMso('Undo')", lambda: word.CommandBars.ExecuteMso("Undo")),
                ("WordBasic.EditUndo()", lambda: word.WordBasic.EditUndo()),
                ("word.Undo(1)", lambda: word.Undo(1)),
                ("word.Undo()", lambda: word.Undo()),
                ("document.Undo()", lambda: document.Undo()),
            )
            for label, undo_call in undo_attempts:
                try:
                    undo_call()
                    self._log_word_replace(f"Undo invoked via {label} hwnd={window_handle!r}")
                    return
                except Exception as exc:
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
            try:
                _Application, send_keys = self._load_pywinauto()
                if send_keys is not None:
                    if window_handle:
                        self._focus_window(window_handle)
                    send_keys("^z")
                    self._log_word_replace(f"Undo invoked via Ctrl+Z hwnd={window_handle!r}")
                    return
            except Exception as exc:
                errors.append(f"Ctrl+Z: {type(exc).__name__}: {exc}")
            raise RuntimeError("Word undo failed: " + " | ".join(errors))

        return self._apply_word_operation_with_cache_repair(operation)

    def redo_last_word_action(self, window_handle: int | None = None):
        def operation():
            if pythoncom is None:
                raise RuntimeError("pywin32 is required for Word redo.")
            pythoncom.CoInitialize()
            if window_handle:
                self._focus_window(window_handle)
            word = _get_active_word_application()
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                raise RuntimeError("No active Word document is available.")
            errors = []
            redo_attempts = (
                ("CommandBars.ExecuteMso('Redo')", lambda: word.CommandBars.ExecuteMso("Redo")),
                ("WordBasic.EditRedo()", lambda: word.WordBasic.EditRedo()),
                ("word.Redo(1)", lambda: word.Redo(1)),
                ("word.Redo()", lambda: word.Redo()),
                ("document.Redo()", lambda: document.Redo()),
            )
            for label, redo_call in redo_attempts:
                try:
                    redo_call()
                    self._log_word_replace(f"Redo invoked via {label} hwnd={window_handle!r}")
                    return
                except Exception as exc:
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
            try:
                _Application, send_keys = self._load_pywinauto()
                if send_keys is not None:
                    if window_handle:
                        self._focus_window(window_handle)
                    send_keys("^y")
                    self._log_word_replace(f"Redo invoked via Ctrl+Y hwnd={window_handle!r}")
                    return
            except Exception as exc:
                errors.append(f"Ctrl+Y: {type(exc).__name__}: {exc}")
            raise RuntimeError("Word redo failed: " + " | ".join(errors))

        return self._apply_word_operation_with_cache_repair(operation)

    def _word_document_for_style_info(self, word, style_info: dict):
        expected_full_name = str(style_info.get("document_full_name") or "")
        expected_name = str(style_info.get("document_name") or "")
        try:
            documents = getattr(word, "Documents", None)
            count = int(getattr(documents, "Count", 0) or 0) if documents is not None else 0
        except Exception:
            count = 0
        for index in range(1, count + 1):
            try:
                document = documents.Item(index)
                full_name = str(getattr(document, "FullName", "") or "")
                name = str(getattr(document, "Name", "") or "")
            except Exception:
                continue
            if expected_full_name and full_name == expected_full_name:
                return document
            if expected_name and name == expected_name:
                return document
        return getattr(word, "ActiveDocument", None)

    def _select_word_range(self, document, start: int, end: int):
        try:
            document.Activate()
            word_range = document.Range(Start=start, End=end)
            word_range.Select()
        except Exception:
            pass

    def _apply_to_active_word(self, text: str, style_info: dict | None = None):
        if pythoncom is None:
            raise RuntimeError("pywin32 is required for Word replacement.")
        pythoncom.CoInitialize()
        word = _get_active_word_application()
        document = getattr(word, "ActiveDocument", None)
        if document is None:
            raise RuntimeError("No active Word document is available.")
        word.Visible = True
        document.Activate()
        style_info = dict(style_info or {})
        if not self._word_style_info_has_details(style_info):
            refreshed_style = self._read_active_word_document_style_info(document)
            if refreshed_style:
                refreshed_style.update(
                    {
                        key: value
                        for key, value in style_info.items()
                        if value not in (None, "", [], {})
                    }
                )
                style_info = refreshed_style
                self._log_word_replace(
                    "realtime style refreshed before write "
                    f"line_styles={len(style_info.get('line_styles') or [])} "
                    f"segments={len(style_info.get('segments') or [])}"
                )
        line_styles = style_info.get("line_styles") or []
        self._log_word_replace(
            f"write text_len={len(str(text or ''))} newlines={str(text or '').count(chr(10))} "
            f"line_styles={len(line_styles)} segments={len(style_info.get('segments') or [])} "
            f"sample={str(text or '')[:80]!r}"
        )
        undo_record_started = self._start_word_undo_record(word, "Writing Assistant realtime correction")
        try:
            document.Content.Text = self._word_text_for_write(text)
            segments = style_info.get("segments") or []
            if line_styles:
                self._clear_word_direct_character_styles(document)
                self._apply_word_line_styles(document, line_styles)
            elif segments:
                self._clear_word_direct_character_styles(document)
            else:
                self._apply_word_style(document.Content, style_info)
            self._apply_word_style_segments(document, segments)
            try:
                content = document.Content
                self._apply_word_last_line_style_overlay(
                    document,
                    int(content.Start),
                    int(content.End),
                    text,
                    style_info,
                )
            except Exception as exc:
                self._log_word_replace(f"last-line overlay dispatch failed: {type(exc).__name__}: {exc}")
        finally:
            if undo_record_started:
                self._end_word_undo_record(word)

    def _word_style_info_has_details(self, style_info: dict) -> bool:
        if not style_info:
            return False
        if style_info.get("line_styles") or style_info.get("segments"):
            return True
        return any(
            style_info.get(key) is not None
            for key in (
                "font_name",
                "font_size",
                "bold",
                "italic",
                "underline",
                "strike_through",
                "double_strike_through",
                "subscript",
                "superscript",
                "highlight_color_index",
                "color_hex",
            )
        )

    def _read_active_word_document_style_info(self, document) -> dict:
        for attempt in range(3):
            try:
                from client.input.ai_grammary_text_reader import ActiveWordReader

                style_info = ActiveWordReader(debug=False)._read_style_info_from_document(document) or {}
                if self._word_style_info_has_details(style_info):
                    return style_info
            except Exception as exc:
                if attempt == 2:
                    self._log_word_replace(f"realtime style refresh failed: {type(exc).__name__}: {exc}")
            time.sleep(0.12)
        return {}

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
            try:
                font.Color = -16777216
            except Exception:
                pass
            try:
                font.UnderlineColor = -16777216
            except Exception:
                pass
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
                original_paragraph_range = paragraph_range.Duplicate
                if paragraph_range.End > paragraph_range.Start:
                    paragraph_range.End = paragraph_range.End - 1
                style = line_style.get("style") or {}
                paragraph_alignment = line_style.get("paragraph_alignment")
                self._log_word_replace(
                    f"apply line={line_style.get('line')!r} content_line={line_style.get('content_line')!r} "
                    f"text={visible_text[:60]!r} bold={style.get('bold')!r} italic={style.get('italic')!r} "
                    f"underline={style.get('underline')!r} strike={style.get('strike_through')!r} "
                    f"double_strike={style.get('double_strike_through')!r} sub={style.get('subscript')!r} "
                    f"super={style.get('superscript')!r} highlight={style.get('highlight_color_index')!r} "
                    f"color={style.get('color_hex')!r} align={paragraph_alignment!r}"
                )
                self._reset_word_style_flags(paragraph_range)
                self._apply_word_style(paragraph_range, style)
                self._apply_word_paragraph_alignment(original_paragraph_range, paragraph_alignment)
                if self._is_last_content_line(line_style, line_styles):
                    self._apply_word_terminal_paragraph_style(original_paragraph_range, style, paragraph_alignment)
                self._verify_word_style(paragraph_range, line_style)
            except Exception:
                pass

    def _is_last_content_line(self, line_style: dict, line_styles: list[dict]) -> bool:
        try:
            current = line_style.get("content_line")
            if current is None:
                return False
            content_lines = [int(item.get("content_line")) for item in line_styles if item.get("content_line") is not None]
            return bool(content_lines) and int(current) == max(content_lines)
        except Exception:
            return False

    def _apply_word_terminal_paragraph_style(self, paragraph_range, style_info: dict, alignment):
        try:
            terminal_range = paragraph_range.Duplicate
            self._apply_word_style(terminal_range, style_info or {})
            self._apply_word_paragraph_alignment(terminal_range, alignment)
            self._log_word_replace("terminal paragraph style applied")
        except Exception as exc:
            self._log_word_replace(f"terminal paragraph style failed: {type(exc).__name__}: {exc}")

    def _apply_word_paragraph_alignment(self, word_range, alignment):
        if alignment is None:
            return
        try:
            value = int(alignment)
        except Exception:
            return
        try:
            word_range.ParagraphFormat.Alignment = value
        except Exception as exc:
            self._log_word_replace(f"paragraph alignment apply failed value={alignment!r}: {type(exc).__name__}: {exc}")

    def _apply_word_last_line_style_overlay(
        self,
        document,
        content_start: int,
        content_end: int,
        replacement_text: str,
        style_info: dict,
    ):
        line_range = self._last_line_overlay_range(replacement_text, style_info)
        if line_range is None:
            self._log_word_replace("last-line overlay skipped: covered by segments")
            return
        style, alignment = self._word_last_line_overlay_style(style_info)
        if not style and alignment is None:
            self._log_word_replace("last-line overlay skipped: no stored style")
            return
        try:
            start_offset, end_offset = line_range
            max_end = max(int(content_start), int(content_end) - 1)
            start = max(int(content_start), min(int(content_start) + start_offset, max_end))
            end = max(int(content_start), min(int(content_start) + end_offset, max_end))
            if end <= start:
                return
            line_range_obj = document.Range(Start=start, End=end)
            if style:
                self._reset_word_style_flags(line_range_obj)
                self._apply_word_style(line_range_obj, style)
            self._apply_word_paragraph_alignment(line_range_obj, alignment)
            self._log_word_replace(
                "last-line overlay applied "
                f"range=({start_offset},{end_offset}) doc=({start},{end}) "
                f"style_keys={sorted((style or {}).keys())!r} align={alignment!r}"
            )
        except Exception as exc:
            self._log_word_replace(f"last-line overlay failed: {type(exc).__name__}: {exc}")

    def _word_last_line_overlay_style(self, style_info: dict) -> tuple[dict, object]:
        line_styles = style_info.get("line_styles") or []
        last_line_style = self._last_word_content_line_style(line_styles)
        if last_line_style:
            return dict(last_line_style.get("style") or {}), last_line_style.get("paragraph_alignment")

        segments = style_info.get("segments") or []
        if segments:
            source_text = str(style_info.get("selection_text") or style_info.get("_source_text") or "")
            source_line_range = self._last_nonblank_line_range(source_text)
            if source_line_range is None:
                selected_segment = self._last_word_style_segment(segments)
            else:
                line_start, line_end = source_line_range
                selected_segment = self._last_word_style_segment(segments, line_start, line_end)
            if selected_segment:
                return dict(selected_segment.get("style") or {}), None

        base_style = {
            key: value
            for key, value in style_info.items()
            if key
            in {
                "font_name",
                "font_size",
                "bold",
                "italic",
                "underline",
                "underline_color",
                "underline_color_hex",
                "strike_through",
                "double_strike_through",
                "subscript",
                "superscript",
                "highlight_color_index",
                "color_hex",
            }
            and value is not None
        }
        return base_style, style_info.get("paragraph_alignment")

    def _last_word_content_line_style(self, line_styles: list[dict]) -> dict | None:
        best_item = None
        best_content_line = None
        for item in line_styles:
            if item.get("is_blank"):
                continue
            try:
                content_line = int(item.get("content_line"))
            except Exception:
                continue
            if best_content_line is None or content_line > best_content_line:
                best_content_line = content_line
                best_item = item
        return best_item

    def _last_word_style_segment(
        self,
        segments: list[dict],
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> dict | None:
        selected = None
        selected_end = -1
        for segment in segments:
            try:
                start = int(segment.get("start", 0) or 0)
                end = int(segment.get("end", 0) or 0)
            except Exception:
                continue
            if line_start is not None and line_end is not None and not (end > line_start and start < line_end):
                continue
            if end > selected_end:
                selected = segment
                selected_end = end
        return selected

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
            try:
                font.Color = -16777216
            except Exception:
                pass
            try:
                font.UnderlineColor = -16777216
            except Exception:
                pass
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
        self._apply_word_style_segments_to_base(document, content_start, content_end, segments)

    def _apply_word_style_segments_to_base(self, document, content_start: int, content_end: int, segments: list[dict]):
        max_end = max(content_start, content_end - 1)
        applied = 0
        failed = 0
        for segment in segments:
            try:
                start = content_start + int(segment.get("start", 0))
                end = content_start + int(segment.get("end", 0))
            except Exception:
                failed += 1
                continue
            start = max(content_start, min(start, max_end))
            end = max(content_start, min(end, max_end))
            if end <= start:
                continue
            try:
                segment_range = document.Range(Start=start, End=end)
                self._reset_word_style_flags(segment_range)
                self._apply_word_style(segment_range, segment.get("style") or {})
                applied += 1
                if applied <= 8:
                    self._log_word_replace(
                        f"apply segment start={segment.get('start')!r} end={segment.get('end')!r} "
                        f"style={segment.get('style')!r}"
                    )
            except Exception as exc:
                failed += 1
                if failed <= 5:
                    self._log_word_replace(
                        f"apply segment failed start={segment.get('start')!r} end={segment.get('end')!r}: "
                        f"{type(exc).__name__}: {exc}"
                    )
        self._log_word_replace(f"segments applied={applied} failed={failed}")

    def _capture_word_character_styles(self, word_range) -> list[dict]:
        styles: list[dict] = []
        try:
            characters = word_range.Characters
            count = int(getattr(characters, "Count", 0) or 0)
        except Exception as exc:
            self._log_word_replace(f"marker character style capture unavailable: {type(exc).__name__}: {exc}")
            return styles
        for index in range(1, count + 1):
            try:
                styles.append(self._read_word_range_style(characters.Item(index)))
            except Exception as exc:
                if len(styles) < 3:
                    self._log_word_replace(f"marker character style capture failed index={index}: {type(exc).__name__}: {exc}")
        return styles

    def _read_word_range_style(self, word_range) -> dict:
        style: dict = {}
        try:
            font = word_range.Font
        except Exception:
            return style
        for key, attr in (
            ("font_name", "Name"),
            ("font_size", "Size"),
            ("bold", "Bold"),
            ("italic", "Italic"),
            ("underline", "Underline"),
            ("strike_through", "StrikeThrough"),
            ("double_strike_through", "DoubleStrikeThrough"),
            ("subscript", "Subscript"),
            ("superscript", "Superscript"),
        ):
            try:
                value = getattr(font, attr)
            except Exception:
                continue
            if key in {"bold", "italic", "strike_through", "double_strike_through", "subscript", "superscript"}:
                style[key] = bool(value)
            else:
                style[key] = value
        try:
            style["highlight_color_index"] = int(getattr(word_range, "HighlightColorIndex"))
        except Exception:
            pass
        try:
            color_hex = self._word_hex_from_color(getattr(font, "Color"))
            if color_hex:
                style["color_hex"] = color_hex
        except Exception:
            pass
        try:
            underline_color_hex = self._word_hex_from_color(getattr(font, "UnderlineColor"))
            if underline_color_hex:
                style["underline_color_hex"] = underline_color_hex
        except Exception:
            pass
        return style

    def _apply_word_character_styles(self, document, start: int, end: int, styles: list[dict]):
        if not styles or end <= start:
            return
        applied = 0
        failed = 0
        last_style = styles[-1] if styles else {}
        for offset in range(0, max(0, end - start)):
            style = styles[offset] if offset < len(styles) else last_style
            if not style:
                continue
            try:
                char_range = document.Range(Start=start + offset, End=start + offset + 1)
                self._reset_word_style_flags(char_range)
                self._apply_word_style(char_range, style)
                applied += 1
            except Exception as exc:
                failed += 1
                if failed <= 3:
                    self._log_word_replace(
                        f"marker character style apply failed offset={offset}: {type(exc).__name__}: {exc}"
                    )
        self._log_word_replace(f"marker character styles applied={applied} failed={failed} source_styles={len(styles)}")

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

    def _word_hex_from_color(self, color):
        try:
            number = int(color)
        except Exception:
            return None
        if number < 0 or number in (9999999, -9999999, 9999998, -9999998):
            return None
        red = number & 0xFF
        green = (number >> 8) & 0xFF
        blue = (number >> 16) & 0xFF
        return f"#{red:02X}{green:02X}{blue:02X}"

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
        applied_via_hwpml2x = bool(
            source_hwpml2x and self._apply_hwpml2x_replacement(hwp, source_hwpml2x, text)
        )
        if not applied_via_hwpml2x:
            hwp.MovePos(2)
            hwp.Run("SelectAll")
            hwp.HAction.GetDefault("InsertText", hwp.HParameterSet.HInsertText.HSet)
            hwp.HParameterSet.HInsertText.Text = text
            hwp.HAction.Execute("InsertText", hwp.HParameterSet.HInsertText.HSet)
        hwp_style_info = dict(style_info)
        hwp_style_info["_replacement_text"] = text
        self._apply_hwp_style(hwp, hwp_style_info)
        self._apply_hwp_last_line_style_overlay(hwp, hwp_style_info)

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
            self._send_ctrl_key("a")
            time.sleep(0.12)
            self._send_virtual_key(0x2E)
            time.sleep(0.12)
            self._send_ctrl_key("v")
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

    def _apply_hwp_last_line_style_overlay(self, hwp, style_info: dict):
        replacement_text = str(style_info.get("_replacement_text") or "")
        if not replacement_text:
            return
        style = self._hwp_last_line_overlay_style(style_info)
        if not style:
            self._log_hwp_replace("HWP last-line overlay skipped: no stored style")
            return
        line_range = self._last_line_overlay_range(replacement_text, style_info)
        if line_range is None:
            self._log_hwp_replace("HWP last-line overlay skipped: covered by segments")
            return
        start, end = line_range
        if end <= start:
            return
        try:
            start_pos = self._hwp_text_index_to_position(replacement_text, start)
            end_pos = self._hwp_text_index_to_position(replacement_text, end)
            self._select_hwp_text_range(hwp, start_pos, end_pos, end - start)
            hwp.HAction.GetDefault("CharShape", hwp.HParameterSet.HCharShape.HSet)
            char_shape = hwp.HParameterSet.HCharShape
            self._assign_hwp_char_shape(char_shape, style)
            hwp.HAction.Execute("CharShape", hwp.HParameterSet.HCharShape.HSet)
            self._log_hwp_replace(
                "HWP last-line overlay applied "
                f"range=({start},{end}) pos={start_pos}->{end_pos} style={style!r}"
            )
        except Exception as exc:
            self._log_hwp_replace(f"HWP last-line overlay failed: {type(exc).__name__}: {exc}")
        finally:
            try:
                hwp.Run("Cancel")
            except Exception:
                pass

    def _hwp_last_line_overlay_style(self, style_info: dict) -> dict:
        segments = style_info.get("segments") or []
        if segments:
            source_text = str(style_info.get("_source_text") or "")
            line_range = self._last_nonblank_line_range(source_text)
            if line_range is None:
                line_start = max(0, max((int(segment.get("end", 0) or 0) for segment in segments), default=0) - 1)
                line_end = line_start + 1
            else:
                line_start, line_end = line_range
            for segment in reversed(segments):
                try:
                    start = int(segment.get("start", 0) or 0)
                    end = int(segment.get("end", 0) or 0)
                except Exception:
                    continue
                if end > line_start and start < line_end:
                    style = self._sanitize_hwp_style_info(segment.get("style") or {}, require_base=False)
                    if style:
                        return style
        return self._sanitize_hwp_style_info(style_info, require_base=False)

    def _last_nonblank_line_range(self, text: str) -> tuple[int, int] | None:
        normalized = self._normalize_text(text)
        if not normalized:
            return None
        cursor = 0
        last_range = None
        for line in normalized.split("\n"):
            line_start = cursor
            line_end = line_start + len(line)
            if line.strip():
                last_range = (line_start, line_end)
            cursor = line_end + 1
        return last_range

    def _last_line_overlay_range(self, replacement_text: str, style_info: dict) -> tuple[int, int] | None:
        line_range = self._last_nonblank_line_range(replacement_text)
        if line_range is None:
            return None
        segments = style_info.get("segments") or []
        if not segments:
            return line_range

        line_start, line_end = line_range
        covered_end = self._last_segment_end_in_range(segments, line_start, line_end)
        if covered_end is None:
            return None
        overlay_start = max(line_start, min(covered_end, line_end))
        if overlay_start >= line_end:
            return None
        return overlay_start, line_end

    def _last_segment_end_in_range(
        self,
        segments: list[dict],
        line_start: int,
        line_end: int,
    ) -> int | None:
        covered_end = None
        for segment in segments:
            try:
                start = int(segment.get("start", 0) or 0)
                end = int(segment.get("end", 0) or 0)
            except Exception:
                continue
            if end <= line_start or start >= line_end:
                continue
            covered_end = end if covered_end is None else max(covered_end, end)
        return covered_end

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

    def _send_ctrl_key(self, key: str):
        vk_map = {"a": 0x41, "c": 0x43, "v": 0x56}
        vk = vk_map.get(str(key).lower())
        if vk is None:
            raise ValueError(f"Unsupported ctrl key: {key}")
        self._send_key_combo((0x11, vk))

    def _send_virtual_key(self, vk: int):
        try:
            ctypes.windll.user32.keybd_event(int(vk), 0, 0, 0)
            ctypes.windll.user32.keybd_event(int(vk), 0, 0x0002, 0)
        except Exception as exc:
            raise RuntimeError(f"Failed to send virtual key {vk}: {exc}") from exc

    def _send_key_combo(self, keys):
        try:
            for vk in keys:
                ctypes.windll.user32.keybd_event(int(vk), 0, 0, 0)
            for vk in reversed(keys):
                ctypes.windll.user32.keybd_event(int(vk), 0, 0x0002, 0)
        except Exception as exc:
            raise RuntimeError(f"Failed to send key combo {keys}: {exc}") from exc
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


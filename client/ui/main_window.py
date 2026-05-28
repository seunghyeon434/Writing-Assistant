import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pyperclip
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QStyle, QSystemTrayIcon

from client.app_settings import DEFAULT_SETTINGS, load_app_settings, save_app_settings
from client.core.auth_api_client import AuthAPIClient, UnauthorizedError
from client.core.analyzer import TextAnalyzer
from client.core.line_structure import preserve_replacement_structure
from client.core.local_server import LocalServer
from client.input.clipboard_monitor import monitor_clipboard
from client.input.input_mode_state import set_active_input_mode
from client.input.realtime_reading_pause import pause_realtime_reading
from client.ui.mini_overlay import MiniOverlay, RealtimeOverlay
from client.ui.main_overlay import MainOverlay
from client.ui.result_panel import ResultPanel


_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_UI_INPUT_EVENT_LOG_PATH = _LOG_DIR / "ui_input_events.log"
_REPLACEMENT_STRUCTURE_LOG_PATH = _LOG_DIR / "replacement_structure.log"
_DRAG_APPLY_LOG_PATH = _LOG_DIR / "drag_apply.log"


class SignalBridge(QObject):
    text_signal = pyqtSignal(object)
    auth_sync_signal = pyqtSignal(object)
    hotkey_signal = pyqtSignal(object)


class App:
    def __init__(self):
        self.qt_app = QApplication(sys.argv)
        self.load_app_font()
        self.local_server = LocalServer()
        self.api_client = AuthAPIClient()
        self._startup_server_error = ""
        self._server_started = False
        self.pending_signup_username = ""
        self.initialize_auth()

        self.settings = self.normalize_settings(load_app_settings())
        self.startup_clipboard_text = self.safe_paste()

        self.panel = ResultPanel(
            initial_dark_mode=self.settings.get("default_dark_mode", False)
        )
        self.mini_overlay = MiniOverlay()
        self.realtime_overlay = RealtimeOverlay()
        self.main_overlay = MainOverlay()
        self.mini_overlay.set_avoidance_rect_provider(self._correction_overlay_avoidance_rects)
        self.realtime_overlay.set_avoidance_rect_provider(self._correction_overlay_avoidance_rects)
        self.main_overlay.set_active_mode(self.settings.get("input_mode", "clipboard"))
        self.panel.set_default_dark_mode_checked(
            self.settings.get("default_dark_mode", False)
        )
        self.panel.set_history_enabled_checked(
            self.settings.get("history_enabled", False)
        )
        self.panel.set_replace_mode_checked(
            self.settings.get("replace_mode", False)
        )

        self.analyzer = TextAnalyzer()
        self.output_applier = None
        self.last_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.last_evaluation_reason = ""
        self.last_output_target = None
        self.tone_favorites = []
        self._clear_recent_drag_snapshot()
        self.suppress_replacement_echo_until = 0.0
        self.suppress_replacement_echo_text = ""
        self.last_browser_extension_event_at = 0.0
        self.active_input_mode = self.settings.get("input_mode", "clipboard")
        set_active_input_mode(self.active_input_mode)
        self.clipboard_thread = None
        self.realtime_thread = None
        self.drag_thread = None
        self.hotkey_thread = None
        self.last_logged_keys = set()
        self.last_local_log_keys = set()
        self.drag_overlay_anchor = None
        self.main_overlay_anchor = None
        self.main_overlay_pending_target = None
        self.main_overlay_pending_target_at = 0.0
        self.main_overlay_overlap_suppress_until = 0.0
        self.drag_overlay_collapsed_by_target = {}
        self.drag_overlay_requested = False
        self.realtime_overlay_requested = False
        self.realtime_overlay_anchor = None
        self.word_undo_available_by_hwnd = {}
        self.word_redo_available_by_hwnd = {}
        self.notepad_undo_available_by_hwnd = {}
        self.notepad_redo_available_by_hwnd = {}
        self.drag_overlay_suppress_until = 0.0
        self.drag_overlay_interaction_until = 0.0
        self.drag_overlay_empty_foreground_until = 0.0
        self.drag_overlay_pending_target = None
        self.drag_overlay_pending_target_at = 0.0
        self.pending_drag_apply_retry = False
        self.last_valid_drag_snapshot = None
        self.last_valid_drag_snapshot_at = 0.0
        self.last_drag_selection_at = 0.0
        self.last_drag_selection_signature = None
        self.pending_word_clear_at = 0.0
        self.last_drag_window_log_signature = None
        self.last_drag_window_log_at = 0.0
        self.drag_overlay_timer = QTimer(self.qt_app)
        self.drag_overlay_timer.setInterval(120)
        self.drag_overlay_timer.timeout.connect(self.update_drag_overlay_presence)
        self.main_overlay_timer = QTimer(self.qt_app)
        self.main_overlay_timer.setInterval(140)
        self.main_overlay_timer.timeout.connect(self.update_main_overlay_presence)

        self.signals = SignalBridge()
        self.signals.text_signal.connect(self.handle_input_event)
        self.signals.auth_sync_signal.connect(self.handle_background_auth_sync_result)
        self.signals.hotkey_signal.connect(self.handle_hotkey_event)

        self.panel.set_input_mode(self.active_input_mode)
        self.reset_session_state()

        self.panel.copy_btn.clicked.connect(self.copy_result)
        self.panel.refresh_btn.clicked.connect(self.run_spell_check)
        self.panel.apply_correction_btn.clicked.connect(self.apply_correction_to_source)
        self.panel.quit_btn.clicked.connect(self.quit_app)
        self.panel.evaluate_btn.clicked.connect(self.run_evaluation)
        self.panel.evaluation_reason_btn.clicked.connect(self.show_evaluation_reason)
        self.panel.recommend_title_btn.clicked.connect(self.run_title_recommendation)
        self.panel.run_summary_btn.clicked.connect(self.run_summary)
        self.panel.run_tone_btn.clicked.connect(self.run_tone_change)
        self.panel.save_settings_btn.clicked.connect(self.save_settings)
        self.panel.close_settings_btn.clicked.connect(self.panel.close_settings_page)
        self.panel.login_btn.clicked.connect(self.handle_login_button)
        self.panel.login_submit_btn.clicked.connect(self.handle_login_submit)
        self.panel.signup_submit_btn.clicked.connect(self.handle_signup_submit)
        self.panel.account_manage_btn.clicked.connect(self.handle_account_manage_button)
        self.panel.account_verify_submit_btn.clicked.connect(self.handle_account_verify_submit)
        self.panel.account_save_btn.clicked.connect(lambda: self.handle_account_update())
        self.panel.account_name_edit_btn.clicked.connect(lambda: self.handle_account_update("display_name"))
        self.panel.account_username_edit_btn.clicked.connect(lambda: self.handle_account_update("username"))
        self.panel.account_password_edit_btn.clicked.connect(lambda: self.handle_account_update("password"))
        self.panel.account_delete_btn.clicked.connect(self.confirm_account_delete)
        self.panel.text_history_btn.clicked.connect(lambda: self.show_history(1))
        self.panel.spell_history_btn.clicked.connect(lambda: self.show_history(2))
        self.panel.summary_history_btn.clicked.connect(lambda: self.show_history(3))
        self.panel.tone_history_btn.clicked.connect(lambda: self.show_history(4))
        self.mini_overlay.apply_pressed.connect(self.mark_drag_overlay_interaction)
        self.mini_overlay.apply_clicked.connect(self.apply_correction_to_source)
        self.mini_overlay.open_clicked.connect(self.show_panel)
        self.mini_overlay.undo_clicked.connect(self.undo_last_drag_correction)
        self.mini_overlay.redo_clicked.connect(self.redo_last_drag_correction)
        self.mini_overlay.tone_submitted.connect(self.apply_drag_tone_change_to_source)
        self.mini_overlay.tone_requested.connect(self.handle_drag_tone_button)
        self.mini_overlay.tone_favorite_list_requested.connect(self.refresh_tone_favorites)
        self.mini_overlay.tone_favorite_add_requested.connect(self.add_tone_favorite)
        self.mini_overlay.tone_favorite_delete_requested.connect(self.delete_tone_favorite)
        self.mini_overlay.choice_spelling_requested.connect(self.apply_correction_to_source)
        self.mini_overlay.choice_tone_requested.connect(self.handle_drag_tone_button)
        self.mini_overlay.overlay_moved.connect(self.handle_correction_overlay_moved)
        self.realtime_overlay.apply_pressed.connect(lambda: None)
        self.realtime_overlay.apply_clicked.connect(self.apply_correction_to_source)
        self.realtime_overlay.open_clicked.connect(self.show_panel)
        self.realtime_overlay.undo_clicked.connect(self.undo_last_realtime_correction)
        self.realtime_overlay.redo_clicked.connect(self.redo_last_realtime_correction)
        self.realtime_overlay.tone_requested.connect(self.handle_realtime_tone_button)
        self.realtime_overlay.tone_submitted.connect(self.apply_realtime_tone_change_to_source)
        self.realtime_overlay.tone_favorite_list_requested.connect(self.refresh_tone_favorites)
        self.realtime_overlay.tone_favorite_add_requested.connect(self.add_tone_favorite)
        self.realtime_overlay.tone_favorite_delete_requested.connect(self.delete_tone_favorite)
        self.realtime_overlay.choice_spelling_requested.connect(self.apply_correction_to_source)
        self.realtime_overlay.choice_tone_requested.connect(self.handle_realtime_tone_button)
        self.realtime_overlay.overlay_moved.connect(self.handle_correction_overlay_moved)
        self.main_overlay.settings_save_requested.connect(self.handle_main_overlay_mode_save)
        self.main_overlay.evaluate_requested.connect(lambda: self.handle_main_overlay_action("evaluate"))
        self.main_overlay.evaluation_reason_requested.connect(self.show_overlay_evaluation_reason)
        self.main_overlay.summary_copy_requested.connect(self.safe_copy)
        self.main_overlay.title_requested.connect(lambda: self.handle_main_overlay_action("title"))
        self.main_overlay.title_insert_requested.connect(self.insert_recommended_title_from_overlay)
        self.main_overlay.correction_requested.connect(lambda: self.handle_main_overlay_action("correction"))
        self.main_overlay.summary_requested.connect(lambda: self.handle_main_overlay_action("summary"))
        self.main_overlay.tone_requested.connect(lambda: self.handle_main_overlay_action("tone"))

        self.init_tray()
        self.update_login_state()
        self.drag_overlay_timer.start()
        self.main_overlay_timer.start()
        QTimer.singleShot(0, self.start_restored_login_sync)

    def initialize_auth(self):
        self.api_client.try_restore_session()

    def load_app_font(self):
        font_path = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "A2Z-Medium.ttf"
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            return

        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            self.qt_app.setFont(QFont(families[0], 10))

    def init_tray(self):
        tray_icon = self.qt_app.style().standardIcon(QStyle.SP_FileDialogInfoView)
        self.tray = QSystemTrayIcon(tray_icon, self.qt_app)
        self.tray.setToolTip("Writing Assistant \uc2e4\ud589 \uc911")
        self.tray.activated.connect(self.handle_tray_activation)

        menu = QMenu()
        show_action = QAction("\ubcf4\uc774\uae30")
        self.login_action = QAction("\ub85c\uadf8\uc778")
        quit_action = QAction("\uc885\ub8cc")

        show_action.triggered.connect(self.show_panel)
        self.login_action.triggered.connect(self.handle_login_button)
        quit_action.triggered.connect(self.quit_app)

        menu.addAction(show_action)
        menu.addAction(self.login_action)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def start(self):
        self.reset_session_state()
        self.show_panel()

        self.clipboard_thread = threading.Thread(
            target=self.run_monitor,
            args=(self.startup_clipboard_text,),
            daemon=True,
        )
        self.clipboard_thread.start()
        self.ensure_realtime_monitor_started()
        self.ensure_drag_monitor_started()
        self.ensure_global_hotkey_started()

        sys.exit(self.qt_app.exec_())

    def run_monitor(self, initial_text):
        def callback(text):
            self.signals.text_signal.emit(
                {
                    "source": "clipboard",
                    "window_title": "",
                    "text": text,
                }
            )

        monitor_clipboard(callback, initial_text=initial_text)

    def run_realtime_monitor(self):
        from client.input.realtime_text_monitor import monitor_realtime_text

        def callback(event):
            self.signals.text_signal.emit(event)

        monitor_realtime_text(callback)

    def run_drag_monitor(self):
        from client.input.drag_selection_monitor import monitor_drag_selection

        def callback(event):
            self.signals.text_signal.emit(event)

        monitor_drag_selection(callback)

    def ensure_realtime_monitor_started(self):
        if self.active_input_mode != "realtime":
            return
        if self.realtime_thread and self.realtime_thread.is_alive():
            return
        self.realtime_thread = threading.Thread(
            target=self.run_realtime_monitor,
            daemon=True,
        )
        self.realtime_thread.start()

    def ensure_drag_monitor_started(self):
        if self.active_input_mode != "drag":
            return
        if self.drag_thread and self.drag_thread.is_alive():
            return
        self.drag_thread = threading.Thread(
            target=self.run_drag_monitor,
            daemon=True,
        )
        self.drag_thread.start()

    def ensure_global_hotkey_started(self):
        if self.hotkey_thread and self.hotkey_thread.is_alive():
            return
        from client.input.global_hotkey import start_global_hotkey_listener

        def callback(event):
            self.signals.hotkey_signal.emit(event)

        self.hotkey_thread = start_global_hotkey_listener(callback)

    def handle_hotkey_event(self, event):
        if not isinstance(event, dict):
            return
        if event.get("action") == "apply_correction":
            if self.active_input_mode == "drag" and self.mini_overlay.is_collapsed():
                return
            self.apply_correction_to_source()

    def _save_drag_overlay_state_for_anchor(self, anchor=None):
        anchor = anchor or self.drag_overlay_anchor
        if anchor is None:
            return
        self.drag_overlay_collapsed_by_target[anchor] = self.mini_overlay.is_collapsed()

    def _restore_drag_overlay_state_for_target(self, reader_name, hwnd):
        target = (reader_name, hwnd)
        should_collapse = bool(self.drag_overlay_collapsed_by_target.get(target, False))
        self.mini_overlay.remember_target(hwnd, reader_name)
        if should_collapse:
            self.mini_overlay.collapse()
            return True
        if self.mini_overlay.is_collapsed():
            self.mini_overlay.expand()
        return False

    def _set_drag_overlay_undo_state(self, reader_name, hwnd):
        hwnd = int(hwnd or 0)
        if reader_name == "word_selection":
            undo_available = bool(self.word_undo_available_by_hwnd.get(hwnd, False))
            redo_available = bool(self.word_redo_available_by_hwnd.get(hwnd, False))
        elif reader_name == "notepad_selection":
            undo_available = bool(self.notepad_undo_available_by_hwnd.get(hwnd, False))
            redo_available = bool(self.notepad_redo_available_by_hwnd.get(hwnd, False))
        else:
            undo_available = False
            redo_available = False
        self.mini_overlay.set_undo_available(undo_available)
        self.mini_overlay.set_redo_available(redo_available)

    def _set_realtime_overlay_undo_state(self, reader_name, hwnd):
        hwnd = int(hwnd or 0)
        reader_name = "word" if reader_name == "word_selection" else "notepad" if reader_name == "notepad_selection" else str(reader_name or "")
        if reader_name == "word":
            undo_available = bool(self.word_undo_available_by_hwnd.get(hwnd, False))
            redo_available = bool(self.word_redo_available_by_hwnd.get(hwnd, False))
        elif reader_name == "notepad":
            undo_available = bool(self.notepad_undo_available_by_hwnd.get(hwnd, False))
            redo_available = bool(self.notepad_redo_available_by_hwnd.get(hwnd, False))
        else:
            undo_available = False
            redo_available = False
        self.realtime_overlay.set_undo_available(undo_available)
        self.realtime_overlay.set_redo_available(redo_available)

    def _hide_mini_overlay(self, reason):
        try:
            self.mini_overlay.hide_with_reason(reason)
        except AttributeError:
            self.mini_overlay.hide()
        self._sync_main_overlay_correction_enabled()

    def _hide_realtime_overlay(self, reason):
        try:
            self.realtime_overlay.hide_with_reason(reason)
        except AttributeError:
            self.realtime_overlay.hide()
        self._sync_main_overlay_correction_enabled()

    def _sync_main_overlay_correction_enabled(self):
        if not hasattr(self, "main_overlay") or not hasattr(self, "mini_overlay"):
            return
        disable = False
        try:
            if self.active_input_mode == "drag":
                disable = bool(self.mini_overlay.isVisible())
                target = self.main_overlay_anchor or self.drag_overlay_anchor
                if not disable and self.drag_overlay_requested and target:
                    disable = bool(self.mini_overlay.can_show_for_target(target[1]) or self.mini_overlay.is_movable_mode())
            elif self.active_input_mode == "realtime" and hasattr(self, "realtime_overlay"):
                disable = bool(self.realtime_overlay.isVisible())
        except Exception:
            disable = False
        self.main_overlay.set_correction_enabled(not disable)

    def _hide_main_overlay(self, reason):
        try:
            self.main_overlay.hide_with_reason(reason)
        except AttributeError:
            self.main_overlay.hide()

    def update_main_overlay_presence(self):
        if not hasattr(self, "main_overlay"):
            return
        if self.panel.isVisible() and self._foreground_is_result_panel_window():
            if self.main_overlay.isVisible():
                self._hide_main_overlay("assistant_window_foreground")
            return
        if self._has_visible_editor_blocking_dialog():
            if self.main_overlay.isVisible():
                self._hide_main_overlay("blocking_dialog")
            return
        if self.main_overlay.has_overlay_focus():
            return

        target = self._foreground_drag_overlay_target()
        if target is None:
            if self.main_overlay_anchor and self._foreground_hwnd() == 0 and self._is_live_window(self.main_overlay_anchor[1]):
                if not self._is_minimized_window(self.main_overlay_anchor[1]):
                    return
            self.main_overlay_anchor = None
            self.main_overlay_pending_target = None
            self.main_overlay_pending_target_at = 0.0
            if self.main_overlay.isVisible():
                self._hide_main_overlay("main_overlay_no_target")
            return

        reader_name, hwnd = target
        if self._is_minimized_window(hwnd) or not self._is_live_window(hwnd):
            self.main_overlay_anchor = None
            if self.main_overlay.isVisible():
                self._hide_main_overlay("main_overlay_target_gone")
            return

        now = time.monotonic()
        if now < getattr(self, "main_overlay_overlap_suppress_until", 0.0):
            if self.main_overlay.isVisible():
                self._hide_main_overlay("main_overlay_overlap_suppressed")
            return
        needs_stable_show = (not self.main_overlay.isVisible()) or self.main_overlay_anchor != target
        if needs_stable_show:
            if self.main_overlay_pending_target != target:
                self.main_overlay_pending_target = target
                self.main_overlay_pending_target_at = now
                return
            if now - self.main_overlay_pending_target_at < 0.08:
                return
        self.main_overlay_pending_target = None
        self.main_overlay_pending_target_at = 0.0
        self.main_overlay_anchor = target
        self.main_overlay.set_active_mode(self.active_input_mode)
        self._sync_main_overlay_correction_enabled()
        self.main_overlay.show_for_target(reader_name, hwnd)
        if self._main_overlay_overlaps_mini_overlay():
            self.main_overlay_overlap_suppress_until = time.monotonic() + 0.35
            self._hide_main_overlay("main_overlay_overlap_mini")

    def _main_overlay_overlaps_mini_overlay(self, margin=14):
        try:
            if not hasattr(self, "main_overlay") or not hasattr(self, "mini_overlay"):
                return False
            if not self.main_overlay.isVisible() or not self.mini_overlay.isVisible():
                return False
            main_geo = self.main_overlay.frameGeometry()
            mini_geo = self.mini_overlay.frameGeometry().adjusted(-margin, -margin, margin, margin)
            return main_geo.intersects(mini_geo)
        except Exception:
            return False

    def _correction_overlay_avoidance_rects(self):
        rects = []
        try:
            overlays = [getattr(self, "main_overlay", None)]
            main_overlay = getattr(self, "main_overlay", None)
            if main_overlay is not None:
                overlays.extend(
                    [
                        getattr(main_overlay, "score_overlay", None),
                        getattr(main_overlay, "summary_overlay", None),
                    ]
                )
            for overlay in overlays:
                if overlay is None:
                    continue
                try:
                    if overlay.isVisible():
                        rects.append(overlay.frameGeometry())
                except Exception:
                    continue
        except Exception:
            return []
        return rects

    def _foreground_is_result_panel_window(self):
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            return title == "Writing Assistant" and (class_name.startswith("Qt") or "QWindow" in class_name)
        except Exception:
            return False

    def handle_main_overlay_mode_save(self, mode):
        settings = self.collect_settings_from_panel()
        settings["input_mode"] = mode if mode in {"clipboard", "drag", "realtime"} else self.active_input_mode
        self.apply_settings_state(settings)
        if self.is_logged_in():
            try:
                self.save_remote_settings()
            except Exception:
                pass
        self.main_overlay.set_active_mode(self.active_input_mode)
        self.main_overlay.show_status("\uc800\uc7a5\ub428")

    def handle_main_overlay_action(self, action):
        if action == "tone":
            if self.active_input_mode == "drag":
                self.handle_drag_tone_button()
            else:
                self.show_panel()
                try:
                    self.panel.tabs.setCurrentIndex(3)
                except Exception:
                    pass
            return
        if action == "correction" and self.active_input_mode == "drag":
            self.request_drag_overlay_from_main()
            return
        if action == "correction" and self.active_input_mode == "realtime":
            self.request_realtime_overlay_from_main()
            return
        if action == "title":
            self.run_overlay_title_recommendation()
            return
        if self.active_input_mode == "realtime":
            self._refresh_realtime_overlay_input()
        if not self.last_input:
            self.main_overlay.show_status("\ud14d\uc2a4\ud2b8 \uc5c6\uc74c")
            return
        if action == "evaluate":
            self.main_overlay.show_busy("\ud3c9\uac00 \uc9c4\ud589\uc911")
            QApplication.processEvents()
            try:
                score = self.run_evaluation()
            finally:
                self.main_overlay.hide_busy()
            if self.active_input_mode == "drag":
                self._restore_recent_drag_snapshot(max_age=30.0)
            self.main_overlay.show_evaluation_score(score, self.last_evaluation_reason)
            return
        if action == "correction":
            self.main_overlay.show_busy("\uad50\uc815 \uc9c4\ud589\uc911")
            QApplication.processEvents()
            try:
                self.run_spell_check()
            finally:
                self.main_overlay.hide_busy()
            self.main_overlay.show_status("\uad50\uc815 \uc644\ub8cc")
            return
        if action == "summary":
            self.main_overlay.show_busy("\uc694\uc57d \uc9c4\ud589\uc911")
            QApplication.processEvents()
            try:
                summary = self.run_summary()
            finally:
                self.main_overlay.hide_busy()
            if not summary:
                self.main_overlay.show_status("\uc694\uc57d \uc2e4\ud328")
                return
            if self.main_overlay.should_delegate_summary_to_panel():
                self.show_summary_panel()
            else:
                self.main_overlay.show_summary_result(self._strip_result_heading(summary))
                self.main_overlay.show_status("\uc694\uc57d \uc644\ub8cc")
                self._schedule_word_focus_restore(self.main_overlay_anchor or self.drag_overlay_anchor or self.realtime_overlay_anchor)
            return

    def _refresh_realtime_overlay_input(self):
        if self.active_input_mode != "realtime":
            return False
        text = self._read_full_text_for_title()
        if not str(text or "").strip():
            return False
        previous_target = self.last_output_target
        self.last_input = text
        self.last_corrected_text = ""
        self.last_output_target = self._output_target_from_anchor(
            self.realtime_overlay_anchor or self.main_overlay_anchor,
            previous_target=previous_target,
        )
        self.panel.set_original_text(text)
        return True

    def _output_target_from_anchor(self, anchor, previous_target=None):
        if not anchor:
            return None
        reader_name, hwnd = anchor
        reader_name = str(reader_name or "")
        mode = "word" if reader_name in {"word", "word_selection"} else "notepad" if reader_name in {"notepad", "notepad_selection"} else reader_name
        if mode not in {"browser", "browser_extension", "notepad", "word", "hwp"}:
            return None
        try:
            from client.input.output_applier import OutputTarget
            style_info = {}
            previous = previous_target or self.last_output_target
            if previous is not None:
                same_hwnd = int(getattr(previous, "window_handle", 0) or 0) == int(hwnd or 0)
                previous_mode = str(getattr(previous, "mode", "") or "")
                same_mode = previous_mode == mode or (mode == "word" and previous_mode in {"word", "word_selection"}) or (mode == "notepad" and previous_mode in {"notepad", "notepad_selection"})
                if same_hwnd and same_mode:
                    style_info = dict(getattr(previous, "style_info", None) or {})
            return OutputTarget(mode=mode, window_handle=int(hwnd or 0), style_info=style_info)
        except Exception:
            return None

    def request_realtime_overlay_from_main(self):
        if self.active_input_mode != "realtime":
            return
        target = self.main_overlay_anchor
        if target is None:
            target = self._foreground_drag_overlay_target()
        if target is not None and (not self._is_live_window(target[1]) or self._is_minimized_window(target[1])):
            target = None
        if target is None:
            self.main_overlay.show_status("\uad50\uc815 \ub300\uc0c1 \uc5c6\uc74c")
            return
        reader_name, hwnd = target
        if reader_name in {"word_selection", "notepad_selection"}:
            reader_name = "word" if reader_name == "word_selection" else "notepad"
            target = (reader_name, hwnd)
        self.realtime_overlay_anchor = target
        previous_target = self.last_output_target
        refreshed = self._refresh_realtime_overlay_input()
        if not refreshed:
            self.last_output_target = self._output_target_from_anchor(target, previous_target=previous_target)
        if not self.last_input:
            self.main_overlay.show_status("\ud14d\uc2a4\ud2b8 \uc5c6\uc74c")
            return
        self.run_spell_check()
        self.realtime_overlay_requested = True
        self.realtime_overlay.remember_target(hwnd, reader_name)
        self.realtime_overlay.set_movable_mode(True)
        self.realtime_overlay.suspend_focus_guard(1.2)
        self.realtime_overlay.show_realtime_for_target(reader_name, "", hwnd)
        self._set_realtime_overlay_undo_state(reader_name, hwnd)
        if reader_name == "word":
            try:
                self._select_word_first_visible_character(hwnd)
                self._nudge_word_document_focus(hwnd, prefer_existing_selection=True)
            except Exception:
                pass
        self._sync_main_overlay_correction_enabled()
        self.main_overlay.show_status("\uc2e4\uc2dc\uac04 \uad50\uc815 \uc624\ubc84\ub808\uc774")

    def update_realtime_overlay_presence(self):
        if not getattr(self, "realtime_overlay_requested", False):
            if self.realtime_overlay.isVisible():
                self._hide_realtime_overlay("realtime_overlay_not_requested")
            return
        if self._has_visible_editor_blocking_dialog():
            if self.realtime_overlay.isVisible():
                self._hide_realtime_overlay("realtime_overlay_blocking_dialog")
            return
        if self.realtime_overlay.has_overlay_focus():
            return
        target = self.realtime_overlay_anchor or self.main_overlay_anchor
        if target is None:
            if self.realtime_overlay.isVisible():
                self._hide_realtime_overlay("realtime_overlay_no_target")
            return
        reader_name, hwnd = target
        if reader_name in {"word_selection", "notepad_selection"}:
            reader_name = "word" if reader_name == "word_selection" else "notepad"
            target = (reader_name, hwnd)
        if not self._is_live_window(hwnd) or self._is_minimized_window(hwnd):
            if self.realtime_overlay.isVisible():
                self._hide_realtime_overlay("realtime_overlay_target_gone")
            return
        foreground = self._foreground_hwnd()
        if foreground and foreground != int(hwnd or 0):
            if self._is_assistant_qt_window(foreground):
                return
            if self.realtime_overlay.isVisible():
                self._hide_realtime_overlay("realtime_overlay_foreground_changed")
            return
        self.realtime_overlay_anchor = target
        self.realtime_overlay.remember_target(hwnd, reader_name)
        self.realtime_overlay.set_movable_mode(True)
        if self.realtime_overlay.isVisible():
            self.realtime_overlay.refresh_position()
        else:
            self.realtime_overlay.show_realtime_for_target(reader_name, "", hwnd)
        self._set_realtime_overlay_undo_state(reader_name, hwnd)
        self._sync_main_overlay_correction_enabled()

    def request_drag_overlay_from_main(self):
        if self.active_input_mode != "drag":
            return
        target = self.main_overlay_anchor if self.main_overlay_anchor else None
        if target is not None and (not self._is_live_window(target[1]) or self._is_minimized_window(target[1])):
            target = None
        if target is None:
            target = self._foreground_drag_overlay_target()
        if target is None:
            self.main_overlay.show_status("\ub4dc\ub798\uadf8 \ub300\uc0c1 \uc5c6\uc74c")
            return
        reader_name, hwnd = target
        if self._is_minimized_window(hwnd) or not self._is_live_window(hwnd):
            self.main_overlay.show_status("\ub4dc\ub798\uadf8 \ub300\uc0c1 \uc5c6\uc74c")
            return
        self.drag_overlay_suppress_until = 0.0
        self.drag_overlay_pending_target = None
        self.drag_overlay_pending_target_at = 0.0
        self.drag_overlay_anchor = target
        self.mini_overlay.remember_target(hwnd, reader_name)
        self._set_drag_overlay_undo_state(reader_name, hwnd)

        self.drag_overlay_requested = True
        self.mini_overlay.set_movable_mode(True)
        self.mini_overlay.reset_movable_position()
        same_selection = bool(
            self.last_output_target
            and self.last_input
            and int(getattr(self.last_output_target, "window_handle", 0) or 0) == int(hwnd or 0)
        )
        if not same_selection:
            captured = self._capture_current_drag_selection_from_anchor()
            self._log_drag_apply("main_correction_pre_show_capture", captured=captured, reader=reader_name, hwnd=hwnd)
            same_selection = bool(
                captured
                and self.last_output_target
                and self.last_input
                and int(getattr(self.last_output_target, "window_handle", 0) or 0) == int(hwnd or 0)
            )
        if not same_selection and reader_name == "word_selection":
            selected = self._select_word_first_visible_character(hwnd)
            self._log_drag_apply("main_correction_word_auto_select", selected=selected, hwnd=hwnd)
            if selected:
                captured = self._capture_current_drag_selection_from_anchor()
                self._log_drag_apply("main_correction_auto_select_capture", captured=captured, hwnd=hwnd)
                same_selection = bool(
                    captured
                    and self.last_output_target
                    and self.last_input
                    and int(getattr(self.last_output_target, "window_handle", 0) or 0) == int(hwnd or 0)
                )
        self.mini_overlay.suspend_focus_guard(1.2)
        if reader_name == "word_selection":
            focused = self._nudge_word_document_focus(hwnd, prefer_existing_selection=same_selection)
            self._log_drag_apply("main_correction_word_focus_nudge", focused=focused, had_selection=same_selection, hwnd=hwnd)
        if same_selection:
            self.mini_overlay.show_for_target(reader_name, "", hwnd)
        else:
            self.mini_overlay.show_waiting(reader_name, "", hwnd)
        self._sync_main_overlay_correction_enabled()
        self.main_overlay.show_status("\ub4dc\ub798\uadf8 \uc624\ubc84\ub808\uc774")

    def update_drag_overlay_presence(self):
        if self.active_input_mode != "drag":
            self.drag_overlay_requested = False
            self._save_drag_overlay_state_for_anchor()
            self.drag_overlay_anchor = None
            self.mini_overlay.set_movable_mode(False)
            if self.mini_overlay.isVisible():
                self._hide_mini_overlay("main_window_hide_call")
            if self.active_input_mode == "realtime" and getattr(self, "realtime_overlay_requested", False):
                self.update_realtime_overlay_presence()
                return
            self.realtime_overlay_requested = False
            self.realtime_overlay_anchor = None
            if hasattr(self, "realtime_overlay") and self.realtime_overlay.isVisible():
                self._hide_realtime_overlay("main_window_hide_call")
            return

        now = time.monotonic()
        if now < self.drag_overlay_suppress_until:
            if self.drag_overlay_anchor and self._is_minimized_window(self.drag_overlay_anchor[1]):
                self._log_drag_window_decision(self.drag_overlay_anchor[1], "hide minimized anchor during suppress", self.drag_overlay_anchor[1])
                self._save_drag_overlay_state_for_anchor()
                self.drag_overlay_anchor = None
                if self.mini_overlay.isVisible():
                    self._hide_mini_overlay("main_window_hide_call")
                return
            if now < self.drag_overlay_interaction_until and self.drag_overlay_anchor:
                return
            if self.mini_overlay.isVisible() and not self.mini_overlay.has_overlay_focus():
                self._hide_mini_overlay("main_window_hide_call")
            return

        if self._has_visible_editor_blocking_dialog():
            self.drag_overlay_suppress_until = time.monotonic() + 0.35
            self._log_drag_window_decision(0, "hide for visible editor dialog")
            if self.mini_overlay.isVisible():
                self._hide_mini_overlay("main_window_hide_call")
            return

        target = self._foreground_drag_overlay_target()
        if target is None:
            if now < self.drag_overlay_interaction_until and self.drag_overlay_anchor:
                return
            if self._foreground_hwnd() == 0 and self.drag_overlay_anchor and self._is_live_window(self.drag_overlay_anchor[1]):
                anchor_hwnd = self.drag_overlay_anchor[1]
                if self._is_minimized_window(anchor_hwnd):
                    self._log_drag_window_decision(0, "hide minimized anchor during empty foreground", anchor_hwnd)
                    self.drag_overlay_pending_target = None
                    self.drag_overlay_pending_target_at = 0.0
                    self.drag_overlay_empty_foreground_until = 0.0
                    self._save_drag_overlay_state_for_anchor()
                    if self.mini_overlay.isVisible():
                        self._hide_mini_overlay("main_window_hide_call")
                    return
                if self.drag_overlay_empty_foreground_until <= 0.0 or now > self.drag_overlay_empty_foreground_until:
                    self.drag_overlay_empty_foreground_until = now + 0.6
                    self._log_drag_window_decision(0, "hold current anchor for empty foreground", anchor_hwnd)
                if now < self.drag_overlay_empty_foreground_until:
                    return
            if self.mini_overlay.has_overlay_focus() or self._foreground_is_drag_overlay():
                return
            if self._foreground_is_assistant_window():
                self._save_drag_overlay_state_for_anchor()
                if self.mini_overlay.isVisible():
                    self._hide_mini_overlay("assistant_window_foreground")
                return
            if self.drag_overlay_pending_target and now - self.drag_overlay_pending_target_at > 0.6:
                self.drag_overlay_pending_target = None
                self.drag_overlay_pending_target_at = 0.0
            self._save_drag_overlay_state_for_anchor()
            if self.mini_overlay.isVisible():
                self._hide_mini_overlay("main_window_hide_call")
            return

        previous_anchor = self.drag_overlay_anchor
        if not self.drag_overlay_requested:
            self.drag_overlay_anchor = target
            reader_name, hwnd = target
            self.mini_overlay.remember_target(hwnd, reader_name)
            self.mini_overlay.set_movable_mode(False)
            if self.mini_overlay.isVisible():
                self._hide_mini_overlay("drag_overlay_wait_for_main_correction")
            return
        needs_stable_show = (not self.mini_overlay.isVisible()) or (previous_anchor is not None and previous_anchor != target)
        if needs_stable_show:
            if self.drag_overlay_pending_target != target:
                self.drag_overlay_pending_target = target
                self.drag_overlay_pending_target_at = now
                self._log_drag_window_decision(target[1], "pending overlay target", target[1])
                if previous_anchor is not None and previous_anchor != target and self.mini_overlay.isVisible():
                    self._hide_mini_overlay("main_window_hide_call")
                return
            if now - self.drag_overlay_pending_target_at < 0.08:
                return
        self.drag_overlay_pending_target = None
        self.drag_overlay_pending_target_at = 0.0
        if previous_anchor is not None and previous_anchor != target:
            self._save_drag_overlay_state_for_anchor(previous_anchor)
        self.drag_overlay_empty_foreground_until = 0.0
        self.drag_overlay_anchor = target
        reader_name, hwnd = target
        if self._is_minimized_window(hwnd):
            self._log_drag_window_decision(hwnd, "hide minimized target", hwnd)
            self.drag_overlay_anchor = None
            self._save_drag_overlay_state_for_anchor(target)
            if self.mini_overlay.isVisible():
                self._hide_mini_overlay("main_window_hide_call")
            return
        if not self._is_live_window(hwnd):
            self.drag_overlay_anchor = None
            self.mini_overlay.set_movable_mode(False)
            self._hide_mini_overlay("main_window_hide_call")
            return

        if self._has_visible_editor_blocking_dialog():
            self.drag_overlay_suppress_until = time.monotonic() + 0.35
            if self.mini_overlay.isVisible():
                self._hide_mini_overlay("main_window_hide_call")
            return

        target_changed = previous_anchor is not None and previous_anchor != target
        if target_changed:
            self.last_output_target = None
            self._clear_recent_drag_snapshot()
            self.last_drag_selection_signature = None
            self.pending_word_clear_at = 0.0
            self.mini_overlay.set_movable_mode(bool(self.drag_overlay_requested))
            self.mini_overlay.reset_movable_position()
            if self._restore_drag_overlay_state_for_target(reader_name, hwnd):
                self._set_drag_overlay_undo_state(reader_name, hwnd)
                return
            self.mini_overlay.show_waiting(reader_name, hwnd)
            self._set_drag_overlay_undo_state(reader_name, hwnd)
            return

        self._save_drag_overlay_state_for_anchor(target)
        if self.mini_overlay.is_collapsed():
            self.mini_overlay.remember_target(hwnd, reader_name)
            self._set_drag_overlay_undo_state(reader_name, hwnd)
            self.mini_overlay.refresh_position()
            return
        if self.last_output_target is not None and self.last_output_target.window_handle == hwnd:
            self.mini_overlay.show_for_target(reader_name, "", hwnd)
            self._set_drag_overlay_undo_state(reader_name, hwnd)
            return
        self.mini_overlay.show_waiting(reader_name, hwnd)
        self._set_drag_overlay_undo_state(reader_name, hwnd)

    def _foreground_drag_overlay_target(self):
        try:
            from client.input.ai_grammary_text_reader import (
                NOTEPAD_PROCESS_NAMES,
                WORD_PROCESS_NAMES,
                get_foreground_hwnd,
                get_process_name,
                get_window_title,
            )
        except Exception:
            return None
        hwnd = get_foreground_hwnd()
        if self._is_drag_overlay_window(hwnd):
            if self.drag_overlay_anchor and self._is_live_window(self.drag_overlay_anchor[1]):
                self._log_drag_window_decision(hwnd, "use current anchor from overlay focus", self.drag_overlay_anchor[1])
                return self.drag_overlay_anchor
            self._log_drag_window_decision(hwnd, "skip own overlay without anchor")
            return None
        if self._is_temporary_or_dialog_window(hwnd):
            self.drag_overlay_suppress_until = time.monotonic() + 0.35
            self._log_drag_window_decision(hwnd, "skip temporary/dialog")
            return None
        process_name = get_process_name(hwnd)
        if process_name in WORD_PROCESS_NAMES:
            if not self._looks_like_word_document_window(hwnd):
                self._log_drag_window_decision(hwnd, "skip word not document surface")
                return None
            word_hwnd = self._active_word_window_handle()
            if word_hwnd and not self._same_root_window(hwnd, word_hwnd):
                self._log_drag_window_decision(hwnd, "skip non-main word window", word_hwnd)
                return None
            anchor = word_hwnd or hwnd
            self._log_drag_window_decision(hwnd, "use word anchor", anchor)
            return "word_selection", anchor
        if process_name in NOTEPAD_PROCESS_NAMES:
            if not self._is_expected_editor_root(hwnd, {"Notepad", "ApplicationFrameWindow"}):
                self._log_drag_window_decision(hwnd, "skip notepad non-editor root")
                return None
            if not self._has_editor_text_child(hwnd, process_name):
                self._log_drag_window_decision(hwnd, "skip notepad without editor child")
                return None
            self._log_drag_window_decision(hwnd, "use notepad anchor", hwnd)
            return "notepad_selection", hwnd
        self._log_drag_window_decision(hwnd, "skip unsupported process")
        return None

    def _looks_like_word_document_window(self, hwnd):
        try:
            import win32gui

            if not self._is_expected_editor_root(hwnd, {"OpusApp"}):
                return False
            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            root_class_name = win32gui.GetClassName(root) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            if root_class_name != "OpusApp" and class_name != "OpusApp":
                return False
            title = (win32gui.GetWindowText(root) or win32gui.GetWindowText(hwnd) or "").strip()
            if not title or title == "Word":
                return False
            loading_classes = {"MsoSplash"}
            if class_name in loading_classes or root_class_name in loading_classes:
                return False
            if self._has_word_fullpage_ui(hwnd):
                return False
            if not self._has_editor_text_child(hwnd, "winword.exe"):
                return False
            if not self._word_foreground_focus_is_document(hwnd):
                return False
            return True
        except Exception:
            return False

    def _word_foreground_focus_is_document(self, hwnd):
        try:
            import win32gui
            import win32process

            foreground = win32gui.GetForegroundWindow()
            if not self._same_root_window(foreground, hwnd):
                return False
            thread_id, _ = win32process.GetWindowThreadProcessId(foreground)
            info = win32gui.GetGUIThreadInfo(thread_id)
            focus_hwnd = (info or {}).get("hwndFocus") or (info or {}).get("hwndCaret")
            if not focus_hwnd:
                return True
            class_name = win32gui.GetClassName(focus_hwnd) or ""
            return class_name.startswith("_Ww")
        except Exception:
            return True

    def _has_word_fullpage_ui(self, hwnd):
        try:
            import win32gui

            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            root_left, root_top, root_right, root_bottom = win32gui.GetWindowRect(root)
            root_width = max(1, root_right - root_left)
            root_height = max(1, root_bottom - root_top)
            root_area = root_width * root_height
            found = False

            def visit(child_hwnd, _):
                nonlocal found
                if found:
                    return False
                try:
                    if not win32gui.IsWindowVisible(child_hwnd):
                        return True
                    class_name = win32gui.GetClassName(child_hwnd) or ""
                    if class_name != "FullpageUIHost":
                        return True
                    left, top, right, bottom = win32gui.GetWindowRect(child_hwnd)
                    width = max(0, right - left)
                    height = max(0, bottom - top)
                    if (width * height) / root_area > 0.80:
                        found = True
                        return False
                except Exception:
                    pass
                return True

            win32gui.EnumChildWindows(root, visit, None)
            return found
        except Exception:
            return False
    def _has_large_word_non_document_surface(self, hwnd):
        try:
            import win32gui

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
                    # The normal ribbon is wide but shallow. Word's File/Backstage
                    # surface occupies most of the root window, while the editor is _Ww*.
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

    def _has_editor_text_child(self, hwnd, process_name=""):
        try:
            import win32gui

            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            class_names = []

            def visit(child_hwnd, _):
                try:
                    class_names.append(win32gui.GetClassName(child_hwnd) or "")
                except Exception:
                    pass
                return len(class_names) < 500

            win32gui.EnumChildWindows(root, visit, None)
            joined = " ".join(class_names)
            process_name = str(process_name or "").lower()
            if process_name == "notepad.exe":
                text_tokens = ("Edit", "RichEdit", "RichEditD2D", "TextBox", "TextBoxView")
                return any(token in joined for token in text_tokens)
            if process_name == "winword.exe":
                return any(name.startswith("_Ww") for name in class_names)
            return False
        except Exception:
            return False

    def _is_expected_editor_root(self, hwnd, allowed_classes):
        try:
            import win32gui

            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            root_class_name = win32gui.GetClassName(root) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            return root_class_name in allowed_classes or class_name in allowed_classes
        except Exception:
            return False

    def _is_live_window(self, hwnd):
        try:
            import win32gui

            return bool(hwnd and win32gui.IsWindow(hwnd))
        except Exception:
            return False

    def _foreground_hwnd(self):
        try:
            import win32gui

            return int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def _is_minimized_window(self, hwnd):
        try:
            import win32gui

            if not hwnd or not win32gui.IsWindow(hwnd):
                return True
            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            return bool(win32gui.IsIconic(root) or not win32gui.IsWindowVisible(root))
        except Exception:
            return False

    def _foreground_is_drag_overlay(self):
        try:
            import win32gui

            return self._is_drag_overlay_window(win32gui.GetForegroundWindow())
        except Exception:
            return False

    def _foreground_is_assistant_window(self):
        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not self.drag_overlay_anchor:
                return False
            return self._is_assistant_qt_window(hwnd)
        except Exception:
            return False

    def _is_assistant_qt_window(self, hwnd):
        try:
            import win32gui
            from client.input.ai_grammary_text_reader import get_process_name

            if not hwnd:
                return False
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            process_name = get_process_name(hwnd)
            if process_name and process_name != "python.exe":
                return False
            if title not in {"Writing Assistant", "Writing Assistant Mini", "Writing Assistant Main Overlay", "Writing Assistant Correction Choice", "Writing Assistant Tone", "Writing Assistant Realtime Overlay"}:
                return False
            return class_name.startswith("Qt") or "QWindow" in class_name
        except Exception:
            return False

    def _is_drag_overlay_window(self, hwnd):
        try:
            import win32gui
            from client.input.ai_grammary_text_reader import get_process_name

            if not hwnd:
                return False
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            process_name = get_process_name(hwnd)
            if title not in {"Writing Assistant Mini", "Writing Assistant Correction Choice", "Writing Assistant Tone", "Writing Assistant Realtime Overlay"}:
                return False
            if process_name and process_name != "python.exe":
                return False
            return class_name.startswith("Qt") or "QWindow" in class_name
        except Exception:
            return False

    def _is_temporary_or_dialog_window(self, hwnd):
        try:
            import win32gui

            if not hwnd:
                return True
            class_name = win32gui.GetClassName(hwnd) or ""
            title = win32gui.GetWindowText(hwnd) or ""
            owner = self._window_owner(hwnd)
            if owner:
                return True
            if class_name in {"#32770", "Microsoft-Windows-FileSavePicker", "NUIDialog", "Net UI Tool Window"}:
                return True
            if "Menu" in class_name or "Popup" in class_name or "DropShadow" in class_name:
                return True
            return self._looks_like_save_prompt(title)
        except Exception:
            return True

    def _log_drag_window_decision(self, hwnd, reason, anchor=None):
        try:
            import time
            import win32gui
            from client.input.ai_grammary_text_reader import get_process_name

            now = time.monotonic()
            class_name = win32gui.GetClassName(hwnd) if hwnd else ""
            title = win32gui.GetWindowText(hwnd) if hwnd else ""
            owner = self._window_owner(hwnd) if hwnd else 0
            process_name = get_process_name(hwnd) if hwnd else ""
            word_diag = self._word_window_diagnostics(hwnd) if process_name == "winword.exe" else ""
            signature = (int(hwnd or 0), reason, int(anchor or 0), process_name, class_name, owner, title[:120], word_diag)
            if (
                signature == self.last_drag_window_log_signature
                and now - self.last_drag_window_log_at < 1.5
            ):
                return
            self.last_drag_window_log_signature = signature
            self.last_drag_window_log_at = now
            log_path = _LOG_DIR / "drag_overlay_window.log"
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} reason={reason!r} "
                    f"hwnd={hwnd!r} anchor={anchor!r} process={process_name!r} "
                    f"class={class_name!r} owner={owner!r} title={title[:120]!r}{word_diag}\n"
                )
        except Exception:
            pass

    def _word_window_diagnostics(self, hwnd):
        try:
            from collections import Counter
            import win32gui
            import win32process

            if not hwnd:
                return ""
            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            focus_class = ""
            caret_class = ""
            try:
                foreground = win32gui.GetForegroundWindow()
                thread_id, _ = win32process.GetWindowThreadProcessId(foreground)
                info = win32gui.GetGUIThreadInfo(thread_id) or {}
                focus_hwnd = info.get("hwndFocus")
                caret_hwnd = info.get("hwndCaret")
                if focus_hwnd:
                    focus_class = win32gui.GetClassName(focus_hwnd) or ""
                if caret_hwnd:
                    caret_class = win32gui.GetClassName(caret_hwnd) or ""
            except Exception:
                pass

            root_left, root_top, root_right, root_bottom = win32gui.GetWindowRect(root)
            root_width = max(1, root_right - root_left)
            root_height = max(1, root_bottom - root_top)
            root_area = root_width * root_height
            visible_classes = Counter()
            large_children = []

            def visit(child_hwnd, _):
                try:
                    if not win32gui.IsWindowVisible(child_hwnd):
                        return True
                    class_name = win32gui.GetClassName(child_hwnd) or ""
                    visible_classes[class_name] += 1
                    left, top, right, bottom = win32gui.GetWindowRect(child_hwnd)
                    width = max(0, right - left)
                    height = max(0, bottom - top)
                    ratio = (width * height) / root_area
                    if ratio > 0.20:
                        large_children.append(f"{class_name}:{ratio:.2f}")
                except Exception:
                    pass
                return len(visible_classes) < 800

            win32gui.EnumChildWindows(root, visit, None)
            top_classes = ",".join(f"{name}:{count}" for name, count in visible_classes.most_common(8))
            large = ",".join(large_children[:6])
            return f" focus_class={focus_class!r} caret_class={caret_class!r} classes={top_classes!r} large={large!r}"
        except Exception:
            return ""


    def _has_visible_editor_blocking_dialog(self):
        try:
            import win32gui

            found = False

            def visit(hwnd, _):
                nonlocal found
                if found:
                    return False
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return True
                    if not self._is_blocking_dialog_window(hwnd):
                        return True
                    found = self._dialog_belongs_to_supported_editor(hwnd)
                    return not found
                except Exception:
                    return True

            win32gui.EnumWindows(visit, None)
            return found
        except Exception:
            return False

    def _dialog_belongs_to_supported_editor(self, hwnd):
        try:
            from client.input.ai_grammary_text_reader import get_process_name

            process_name = get_process_name(hwnd)
            if process_name in {"winword.exe", "notepad.exe"}:
                return True
            owner = self._window_owner(hwnd)
            if owner:
                owner_process_name = get_process_name(owner)
                return owner_process_name in {"winword.exe", "notepad.exe"}
            return False
        except Exception:
            return False

    def _window_owner(self, hwnd):
        try:
            import win32gui

            return win32gui.GetWindow(hwnd, 4) or 0
        except Exception:
            return 0

    def _is_blocking_dialog_window(self, hwnd):
        try:
            import win32gui

            if not hwnd:
                return False
            class_name = win32gui.GetClassName(hwnd) or ""
            root = win32gui.GetAncestor(hwnd, 2) or hwnd
            root_class_name = win32gui.GetClassName(root) or ""
            if class_name == "#32770" or root_class_name == "#32770":
                return True
            try:
                from client.input.ai_grammary_text_reader import get_process_name

                process_name = get_process_name(hwnd)
                if (
                    process_name == "notepad.exe"
                    and (class_name == "ApplicationFrameWindow" or root_class_name == "ApplicationFrameWindow")
                    and not self._has_editor_text_child(hwnd, process_name)
                ):
                    return True
            except Exception:
                pass
            title = win32gui.GetWindowText(hwnd) or ""
            return self._looks_like_save_prompt(title)
        except Exception:
            return False

    def _same_root_window(self, first, second):
        try:
            import win32gui

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

    def _looks_like_save_prompt(self, title):
        text = str(title or "")
        prompt_markers = (
            "\uc800\uc7a5\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c",
            "\ubcc0\uacbd \ub0b4\uc6a9",
            "\uc81c\ubaa9 \uc5c6\uc74c\uc5d0 \uc800\uc7a5",
            "Do you want to save",
            "Save changes",
        )
        return any(marker in text for marker in prompt_markers)

    def _active_word_window_handle(self):
        try:
            import pythoncom
            import win32com.client.dynamic as dynamic

            pythoncom.CoInitialize()
            active = pythoncom.GetActiveObject("Word.Application")
            try:
                active = active.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                pass
            word = dynamic.Dispatch(active)
            hwnd = int(getattr(word, "Hwnd", 0) or 0)
            return hwnd or None
        except Exception:
            return None

    def mark_drag_overlay_interaction(self, duration=5.0):
        self.drag_overlay_interaction_until = time.monotonic() + max(0.2, float(duration))
        try:
            self.mini_overlay.suspend_focus_guard(duration)
        except Exception:
            pass

    def _clear_recent_drag_snapshot(self):
        self.last_valid_drag_snapshot = None
        self.last_valid_drag_snapshot_at = 0.0

    def _remember_recent_drag_snapshot(self):
        if self.active_input_mode != "drag" or self.last_output_target is None or not self.last_input:
            return
        self.last_valid_drag_snapshot = {
            "input": self.last_input,
            "corrected": self.last_corrected_text,
            "correction_source": self.last_correction_source_text,
            "target": self.last_output_target,
        }
        self.last_valid_drag_snapshot_at = time.monotonic()

    def _restore_recent_drag_snapshot(self, max_age=30.0):
        snapshot = self.last_valid_drag_snapshot
        if not snapshot:
            self._log_drag_apply("restore_snapshot_failed", reason="missing")
            return False
        age = time.monotonic() - self.last_valid_drag_snapshot_at
        if age < 0 or age > max_age:
            self._log_drag_apply("restore_snapshot_failed", reason="expired", age=f"{age:.3f}", max_age=max_age)
            return False
        target = snapshot.get("target")
        hwnd = getattr(target, "window_handle", None)
        if hwnd and not self._is_live_window(hwnd):
            self._log_drag_apply("restore_snapshot_failed", reason="dead_window", hwnd=hwnd, age=f"{age:.3f}")
            return False
        input_text = snapshot.get("input", "")
        if not target or not input_text:
            self._log_drag_apply(
                "restore_snapshot_failed",
                reason="incomplete",
                has_target=bool(target),
                input_len=len(input_text or ""),
                age=f"{age:.3f}",
            )
            return False
        self.last_input = input_text
        self.last_corrected_text = snapshot.get("corrected", "")
        self.last_correction_source_text = snapshot.get("correction_source", "")
        self.last_output_target = target
        self._log_drag_apply("restored_recent_snapshot", snapshot_age=f"{age:.3f}")
        return True

    def _log_drag_apply(self, note, **values):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pieces = [f"{key}={value!r}" for key, value in values.items()]
            with _DRAG_APPLY_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{timestamp} {note} {' '.join(pieces)}\n")
        except Exception:
            pass

    def _capture_current_drag_selection_from_anchor(self):
        if self.active_input_mode != "drag" or not self.drag_overlay_anchor:
            self._log_drag_apply("immediate_selection_capture_skipped", reason="no_anchor")
            return False
        reader_name, hwnd = self.drag_overlay_anchor
        if reader_name != "word_selection" or not hwnd:
            self._log_drag_apply("immediate_selection_capture_skipped", reason="unsupported_reader", reader=reader_name, hwnd=hwnd)
            return False
        try:
            from client.input.drag_selection_monitor import _read_word_selection_event
        except Exception as exc:
            self._log_drag_apply("immediate_selection_capture_import_failed", error=str(exc))
            return False

        deadline = time.monotonic() + 0.9
        while time.monotonic() < deadline:
            event = _read_word_selection_event(int(hwnd))
            if event and event.get("text"):
                self._log_drag_apply(
                    "immediate_selection_capture_success",
                    reader=event.get("reader"),
                    hwnd=event.get("window_handle"),
                    text_len=len(event.get("text") or ""),
                )
                self.handle_input_event(event)
                return bool(self.last_output_target and self.last_input)
            time.sleep(0.08)
        self._log_drag_apply("immediate_selection_capture_failed", reader=reader_name, hwnd=hwnd)
        return False

    def handle_correction_overlay_moved(self, reader_name="", window_handle=None):
        self._schedule_editor_focus_restore((reader_name, window_handle))

    def _schedule_word_focus_restore(self, anchor=None):
        self._schedule_editor_focus_restore(anchor, word_only=True)

    def _schedule_editor_focus_restore(self, anchor=None, word_only=False):
        if not anchor:
            return
        try:
            reader_name, hwnd = anchor
        except Exception:
            return
        reader_name = str(reader_name or "")
        if not hwnd:
            return
        if reader_name in {"word", "word_selection"}:
            for delay in (80, 240, 520):
                QTimer.singleShot(delay, lambda hwnd=int(hwnd): self._restore_word_main_focus(hwnd))
            return
        if word_only or reader_name not in {"notepad", "notepad_selection"}:
            return
        for delay in (80, 240, 520):
            QTimer.singleShot(delay, lambda hwnd=int(hwnd): self._restore_notepad_main_focus(hwnd))

    def _restore_word_main_focus(self, hwnd):
        try:
            self.mini_overlay.suspend_focus_guard(0.9)
            self.realtime_overlay.suspend_focus_guard(0.9)
        except Exception:
            pass
        restored = self._nudge_word_document_focus(hwnd, prefer_existing_selection=True)
        if not restored:
            selected = self._select_word_first_visible_character(hwnd)
            if selected:
                restored = self._nudge_word_document_focus(hwnd, prefer_existing_selection=True)
        if restored:
            anchor = ("word_selection", int(hwnd))
            self.main_overlay_anchor = anchor
            if self.active_input_mode == "drag":
                self.drag_overlay_anchor = anchor
            elif self.active_input_mode == "realtime":
                self.realtime_overlay_anchor = ("word", int(hwnd))
            try:
                if self.main_overlay.isVisible():
                    self.main_overlay.show_for_target(anchor[0], anchor[1])
            except Exception:
                pass
        self._log_drag_apply("word_focus_restore_after_overlay", hwnd=hwnd, restored=restored)
        return restored

    def _restore_notepad_main_focus(self, hwnd):
        try:
            self.mini_overlay.suspend_focus_guard(0.9)
            self.realtime_overlay.suspend_focus_guard(0.9)
        except Exception:
            pass
        restored = self._focus_window_handle(hwnd)
        if restored:
            anchor = ("notepad_selection", int(hwnd))
            self.main_overlay_anchor = anchor
            if self.active_input_mode == "drag":
                self.drag_overlay_anchor = anchor
            elif self.active_input_mode == "realtime":
                self.realtime_overlay_anchor = ("notepad", int(hwnd))
            try:
                if self.main_overlay.isVisible():
                    self.main_overlay.show_for_target(anchor[0], anchor[1])
            except Exception:
                pass
        self._log_drag_apply("notepad_focus_restore_after_overlay", hwnd=hwnd, restored=restored)
        return restored

    def _focus_window_handle(self, hwnd):
        if not hwnd:
            return False
        try:
            import win32gui
            import win32con
        except Exception as exc:
            self._log_drag_apply("window_focus_import_failed", error=str(exc), hwnd=hwnd)
            return False
        try:
            target_hwnd = int(hwnd)
            if not win32gui.IsWindow(target_hwnd):
                return False
            if win32gui.IsIconic(target_hwnd):
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(target_hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(target_hwnd)
            try:
                win32gui.SetFocus(target_hwnd)
            except Exception:
                pass
            return True
        except Exception as exc:
            self._log_drag_apply("window_focus_failed", error=f"{type(exc).__name__}: {exc}", hwnd=hwnd)
            return False

    def _select_word_first_visible_character(self, hwnd):
        if not hwnd:
            return False
        try:
            import pythoncom
            import win32com.client.dynamic as dynamic
        except Exception as exc:
            self._log_drag_apply("word_auto_select_import_failed", error=str(exc))
            return False
        try:
            pythoncom.CoInitialize()
            active = pythoncom.GetActiveObject("Word.Application")
            try:
                active = active.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                pass
            word = dynamic.Dispatch(active)
            try:
                word_hwnd = int(getattr(word, "Hwnd", 0) or 0)
                if word_hwnd and int(word_hwnd) != int(hwnd):
                    self._log_drag_apply("word_auto_select_hwnd_mismatch", word_hwnd=word_hwnd, target_hwnd=hwnd)
            except Exception:
                pass
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                return False
            content = document.Content
            characters = content.Characters
            count = min(int(characters.Count), 500)
            for index in range(1, count + 1):
                char_range = characters.Item(index)
                raw = str(getattr(char_range, "Text", "") or "")
                visible = raw.replace("\x00", "").replace("\x07", "").replace("\r", "").replace("\n", "")
                if not visible.strip():
                    continue
                start = int(char_range.Start)
                end = int(char_range.End)
                if end <= start:
                    continue
                select_range = document.Range(Start=start, End=end)
                select_range.Select()
                time.sleep(0.12)
                return True
        except Exception as exc:
            self._log_drag_apply("word_auto_select_failed", error=f"{type(exc).__name__}: {exc}")
        return False

    def _nudge_word_document_focus(self, hwnd, prefer_existing_selection=False):
        if not hwnd:
            return False
        try:
            import pythoncom
            import win32gui
            import win32con
            import win32com.client.dynamic as dynamic
        except Exception as exc:
            self._log_drag_apply("word_focus_nudge_import_failed", error=str(exc))
            return False
        try:
            try:
                target_hwnd = int(hwnd)
                if win32gui.IsIconic(target_hwnd):
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(target_hwnd)
            except Exception as focus_exc:
                self._log_drag_apply("word_focus_nudge_set_foreground_failed", error=str(focus_exc), hwnd=hwnd)
            pythoncom.CoInitialize()
            active = pythoncom.GetActiveObject("Word.Application")
            try:
                active = active.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                pass
            word = dynamic.Dispatch(active)
            try:
                word.Visible = True
                word.Activate()
            except Exception:
                pass
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                return False
            try:
                document.Activate()
            except Exception:
                pass

            selection = getattr(word, "Selection", None)
            if selection is not None:
                try:
                    selection_range = selection.Range.Duplicate
                    start = int(selection_range.Start)
                    end = int(selection_range.End)
                    if end > start:
                        selection_range.Select()
                        time.sleep(0.05)
                        return True
                except Exception as exc:
                    self._log_drag_apply("word_focus_nudge_selection_reselect_failed", error=str(exc))

            # No real selection is present. Touch the first visible character with the same
            # text so Word refreshes document focus without changing user-visible content.
            content = document.Content
            characters = content.Characters
            count = min(int(characters.Count), 500)
            for index in range(1, count + 1):
                char_range = characters.Item(index)
                raw = str(getattr(char_range, "Text", "") or "")
                visible = raw.replace("\x00", "").replace("\x07", "").replace("\r", "").replace("\n", "")
                if not visible.strip():
                    continue
                start = int(char_range.Start)
                end = int(char_range.End)
                if end <= start:
                    continue
                noop_range = document.Range(Start=start, End=end)
                original = str(getattr(noop_range, "Text", "") or "")
                if not original:
                    continue
                noop_range.Text = original
                document.Range(Start=start, End=start + len(original)).Select()
                time.sleep(0.08)
                return True
        except Exception as exc:
            self._log_drag_apply("word_focus_nudge_failed", error=f"{type(exc).__name__}: {exc}")
        return False

    def clear_drag_selection(self, reader_name="", window_handle=None):
        self.last_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.last_evaluation_reason = ""
        self.last_output_target = None
        self.suppress_replacement_echo_text = ""
        self.suppress_replacement_echo_until = 0.0
        self.panel.reset_text_tab()
        self.panel.clear_spell_result()
        self.panel.clear_summary_result()
        self.panel.clear_tone_result()
        self.last_drag_selection_signature = None
        self.pending_word_clear_at = 0.0
        self.mini_overlay.clear_selection(reader_name, window_handle)

    def handle_input_event(self, event):
        if not isinstance(event, dict):
            return

        source = event.get("source", "")
        if source != self.active_input_mode:
            return

        text = event.get("text", "")
        reader_name = str(event.get("reader", "")).strip()
        self._log_input_event(event, text, reader_name)
        if source == "drag" and reader_name == "selection_cleared":
            target_reader = str(event.get("target_reader", "") or "")
            # Word can report a transient empty selection right after a valid drag.
            # Notepad clear events are stable, so let them disable the button immediately.
            now = time.monotonic()
            style_info = event.get("style_info") or {}
            confirmed_clear = bool(event.get("confirmed_clear") or style_info.get("confirmed_previous_selection"))
            if now < self.drag_overlay_interaction_until:
                self._log_input_event(event, text, reader_name, note="ignored_clear_during_overlay_interaction")
                return
            if target_reader == "word_selection" and not confirmed_clear:
                if now - self.last_drag_selection_at < 0.8:
                    self._log_input_event(event, text, reader_name, note="ignored_recent_word_clear")
                    return
                if self.pending_word_clear_at <= 0.0 or now - self.pending_word_clear_at > 1.6:
                    self.pending_word_clear_at = now
                    self._log_input_event(event, text, reader_name, note="pending_word_clear")
                    return
            if target_reader == "notepad_selection" or confirmed_clear:
                self._clear_recent_drag_snapshot()
            self.clear_drag_selection(target_reader, event.get("window_handle"))
            return
        if source == "realtime" and reader_name.endswith("_closed"):
            self.reset_session_state()
            self.panel.set_active_window_title("")
            self._hide_mini_overlay("main_window_hide_call")
            return
        if source == "realtime" and not text:
            if not self.last_input:
                self.panel.set_active_window_title(event.get("window_title", ""))
                self.last_output_target = None
                self.panel.show_text_unavailable_placeholder()
            return

        if source == "drag" and self.mini_overlay.is_collapsed():
            return

        if self._should_ignore_blank_line_downgrade(reader_name, text):
            self._log_input_event(event, text, reader_name, note="ignored_blank_line_downgrade")
            return

        if not text:
            return
        if source != "drag" and text == self.last_input:
            return

        if self._is_replacement_echo(text):
            return

        if source == "drag" and event.get("window_handle"):
            style_info = event.get("style_info") or {}
            drag_signature = (
                reader_name,
                int(event.get("window_handle") or 0),
                style_info.get("selection_start"),
                style_info.get("selection_end"),
                text,
            )
            if drag_signature == self.last_drag_selection_signature and time.monotonic() - self.last_drag_selection_at < 1.0:
                self._log_input_event(event, text, reader_name, note="ignored_duplicate_drag_selection")
                return
            self.last_drag_selection_at = time.monotonic()
            self.pending_word_clear_at = 0.0
            self.last_drag_selection_signature = drag_signature
            self.mini_overlay.remember_target(event.get("window_handle"), reader_name)

        self.panel.set_active_window_title(event.get("window_title", ""))
        if reader_name == "browser_extension":
            self.last_browser_extension_event_at = time.monotonic()
        self.last_input = text
        self.last_corrected_text = ""
        self.last_output_target = self._build_output_target(event) if source in {"realtime", "drag"} else None
        self.panel.set_original_text(text)
        self.run_spell_check()
        if source == "drag" and self.last_output_target is not None:
            self._remember_recent_drag_snapshot()
            self._log_drag_apply(
                "selection_ready",
                reader=reader_name,
                hwnd=event.get("window_handle"),
                text_len=len(text or ""),
            )
            if self.drag_overlay_requested:
                self.mini_overlay.show_for_target(reader_name, event.get("window_title", ""), event.get("window_handle"))
                self._set_drag_overlay_undo_state(reader_name, event.get("window_handle"))

    def reset_session_state(self):
        self.last_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.last_output_target = None
        self._clear_recent_drag_snapshot()
        self.suppress_replacement_echo_until = 0.0
        self.suppress_replacement_echo_text = ""
        self.panel.reset_text_tab()
        self.panel.clear_spell_result()
        self.panel.clear_summary_result()
        self.panel.clear_tone_result()
        self.panel.set_active_window_title("")
        self._hide_mini_overlay("main_window_hide_call")
        self._hide_main_overlay("main_window_hide_call")

    def copy_result(self):
        text = self.panel.get_current_text()
        if text:
            self.safe_copy(text)

    def show_panel(self):
        if self.active_input_mode == "drag" and self.mini_overlay.isVisible():
            self._hide_mini_overlay("show_panel")
        if self.active_input_mode == "realtime" and hasattr(self, "realtime_overlay") and self.realtime_overlay.isVisible():
            self._hide_realtime_overlay("show_panel")
        if self.main_overlay.isVisible():
            self._hide_main_overlay("show_panel")
        self.panel.showNormal()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def show_summary_panel(self):
        self.show_panel()
        try:
            self.panel.tabs.setCurrentIndex(2)
        except Exception:
            pass

    def handle_tray_activation(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel()

    def run_spell_check(self):
        if not self.last_input:
            return
        self.last_correction_source_text = self.last_input
        result = self.analyzer.analyze_spelling(self.last_correction_source_text)
        spelling_feedback = self.analyzer.TEMP_SPELLING_FEEDBACK
        self.last_corrected_text = self._extract_corrected_text(result)
        self.panel.set_spell_result(result)
        self.save_history_log(
            feature_type=2,
            input_text=self.last_correction_source_text,
            output_text=self.last_corrected_text,
            spelling_feedback=spelling_feedback,
        )

    def apply_correction_to_source(self):
        if self.active_input_mode == "realtime":
            if self.realtime_overlay_anchor and self.last_output_target is None:
                self.last_output_target = self._output_target_from_anchor(self.realtime_overlay_anchor)
            self._refresh_realtime_overlay_input()
            if self.last_input:
                self.run_spell_check()
        if self.active_input_mode == "drag":
            self.mark_drag_overlay_interaction()
            self._log_drag_apply(
                "apply_pressed",
                has_target=bool(self.last_output_target),
                input_len=len(self.last_input or ""),
                retry=bool(self.pending_drag_apply_retry),
                snapshot=bool(self.last_valid_drag_snapshot),
            )
            current_target_mode = getattr(self.last_output_target, "mode", "") if self.last_output_target is not None else ""
            if current_target_mode == "word_selection":
                captured = self._capture_current_drag_selection_from_anchor()
                self._log_drag_apply("apply_preflight_word_capture", captured=captured)
            if self.last_output_target is None or not self.last_input:
                captured = self._capture_current_drag_selection_from_anchor()
                self._log_drag_apply("apply_immediate_capture_check", captured=captured)
                restored = bool(self.last_output_target and self.last_input)
                if not restored:
                    restored = self._restore_recent_drag_snapshot(max_age=30.0)
                self._log_drag_apply("apply_restore_check", restored=restored)
                if not restored:
                    if not self.pending_drag_apply_retry:
                        self.pending_drag_apply_retry = True
                        QTimer.singleShot(180, self._retry_drag_apply_once)
                        return
                    self.pending_drag_apply_retry = False
                    self.mini_overlay.show_status("\ub4dc\ub798\uadf8\ub97c \ud574\uc8fc\uc138\uc694!", auto_hide_ms=1000)
                    return
            self.pending_drag_apply_retry = False
        if not self._can_apply_spelling_source_replacement():
            if self.active_input_mode == "drag":
                self.mini_overlay.show_status("\uc544\uc9c1 \uad6c\ud604 \uc911", auto_hide_ms=1200)
            elif self.active_input_mode == "realtime":
                self.realtime_overlay.show_status("\uc544\uc9c1 \uad6c\ud604 \uc911", auto_hide_ms=1200)
            else:
                self.panel.show_notice("\uc544\uc9c1 \uad6c\ud604 \uc911", "\ub9de\ucda4\ubc95 \uac80\uc0ac\ub9cc \ubcf4\ub294 \ubaa8\ub4dc\ub294 \ub2e4\uc74c \ub2e8\uacc4\uc5d0\uc11c \uc5f0\uacb0\ud569\ub2c8\ub2e4.")
            return
        text = self.last_corrected_text or self._extract_corrected_text(self.panel.spell_box.toPlainText())
        if not text:
            self.panel.set_spell_result("\ub9de\ucda4\ubc95 \uc218\uc815 \uacb0\uacfc\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.")
            if self.active_input_mode == "realtime":
                self.realtime_overlay.show_status("\uc218\uc815 \uc2e4\ud328", auto_hide_ms=1200)
            else:
                self.mini_overlay.show_status("\uc218\uc815 \uc2e4\ud328", auto_hide_ms=1200)
            return

        output_applier = self.get_output_applier()
        can_replace, reason = output_applier.inspect_replace_availability(self.last_output_target)
        if self.active_input_mode == "drag":
            self._log_drag_apply("inspect_result", can_replace=can_replace, reason=reason or "")
        if not can_replace:
            self.panel.set_spell_result(
                self.panel.spell_box.toPlainText().rstrip()
                + '\n\n[\uc6d0\ubcf8 \uc218\uc815 \uc2e4\ud328]\n'
                + (reason or "\uc218\uc815\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")
            )
            if self.active_input_mode == "realtime":
                self.realtime_overlay.show_status("\uc218\uc815 \uc2e4\ud328", auto_hide_ms=1200)
            else:
                self.mini_overlay.show_status("\uc218\uc815 \uc2e4\ud328", auto_hide_ms=1200)
            return

        busy_shown = False
        try:
            if self.active_input_mode in {"drag", "realtime"}:
                active_overlay = self.realtime_overlay if self.active_input_mode == "realtime" else self.mini_overlay
                active_overlay.show_busy("\uad50\uc815 \uc9c4\ud589\uc911")
                QApplication.processEvents()
                busy_shown = True
            previous_spell_text = self.panel.spell_box.toPlainText().rstrip()
            text = self._prepare_replacement_text(text)
            self.mark_drag_overlay_interaction(duration=6.0)
            with pause_realtime_reading():
                output_applier.apply(self.last_output_target, text)
            self.mark_drag_overlay_interaction(duration=2.0)
            self.last_corrected_text = text
            self.suppress_replacement_echo_text = text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            self.panel.set_spell_result(
                previous_spell_text
                + "\n\n\uc218\uc815\ub418\uc5c8\uc2b5\ub2c8\ub2e4."
            )
            target_reader = self.last_output_target.mode if self.last_output_target else ""
            target_handle = self.last_output_target.window_handle if self.last_output_target else None
            if self.active_input_mode == "drag":
                self._log_drag_apply("apply_success", reader=target_reader, hwnd=target_handle, text_len=len(text or ""))
                if target_reader == "word_selection" and target_handle:
                    self.word_undo_available_by_hwnd[int(target_handle)] = True
                    self.word_redo_available_by_hwnd[int(target_handle)] = False
                    self.mini_overlay.set_undo_available(True)
                    self.mini_overlay.set_redo_available(False)
                elif target_reader == "notepad_selection" and target_handle:
                    self.notepad_undo_available_by_hwnd[int(target_handle)] = True
                    self.notepad_redo_available_by_hwnd[int(target_handle)] = False
                    self.mini_overlay.set_undo_available(True)
                    self.mini_overlay.set_redo_available(False)
                self._clear_recent_drag_snapshot()
                self.clear_drag_selection(target_reader, target_handle)
            else:
                if target_reader == "word" and target_handle:
                    self.word_undo_available_by_hwnd[int(target_handle)] = True
                    self.word_redo_available_by_hwnd[int(target_handle)] = False
                    self.realtime_overlay.set_undo_available(True)
                    self.realtime_overlay.set_redo_available(False)
                elif target_reader == "notepad" and target_handle:
                    self.notepad_undo_available_by_hwnd[int(target_handle)] = True
                    self.notepad_redo_available_by_hwnd[int(target_handle)] = False
                    self.realtime_overlay.set_undo_available(True)
                    self.realtime_overlay.set_redo_available(False)
                self.realtime_overlay.show_status("\uc6d0\ubcf8 \uc218\uc815 \uc644\ub8cc")
        except Exception as exc:
            if self.active_input_mode == "drag":
                self._log_drag_apply("apply_exception", error=str(exc))
            self.panel.set_spell_result(
                self.panel.spell_box.toPlainText().rstrip()
                + f"\n\n[\uc6d0\ubcf8 \uc218\uc815 \uc2e4\ud328]\n{exc}"
            )
            if self.active_input_mode == "realtime":
                self.realtime_overlay.show_status("\uc218\uc815 \uc2e4\ud328", auto_hide_ms=1200)
            else:
                self.mini_overlay.show_status("\uc218\uc815 \uc2e4\ud328", auto_hide_ms=1200)
        finally:
            if busy_shown:
                active_overlay = self.realtime_overlay if self.active_input_mode == "realtime" else self.mini_overlay
                active_overlay.hide_busy()



    def handle_realtime_tone_button(self):
        if self.active_input_mode != "realtime":
            return
        if not self._refresh_realtime_overlay_input():
            self.realtime_overlay.show_status("\ud14d\uc2a4\ud2b8 \uc5c6\uc74c", auto_hide_ms=1000)
            return
        self.realtime_overlay.show_tone_prompt()

    def apply_realtime_tone_change_to_source(self, tone):
        if self.active_input_mode != "realtime":
            return
        tone = str(tone or "").strip()
        if not tone:
            self.realtime_overlay.show_status("\ubb38\uccb4\ub97c \uc785\ub825\ud574\uc8fc\uc138\uc694.", auto_hide_ms=1000)
            return
        if not self._refresh_realtime_overlay_input():
            self.realtime_overlay.show_status("\ud14d\uc2a4\ud2b8 \uc5c6\uc74c", auto_hide_ms=1000)
            return
        if self.last_output_target is None:
            self.last_output_target = self._output_target_from_anchor(self.realtime_overlay_anchor or self.main_overlay_anchor)
        output_applier = self.get_output_applier()
        can_replace, reason = output_applier.inspect_replace_availability(self.last_output_target)
        if not can_replace:
            self.realtime_overlay.show_status("\ubb38\uccb4 \ubcc0\uacbd \uc2e4\ud328", auto_hide_ms=1200)
            return
        busy_shown = False
        try:
            self.realtime_overlay.show_busy("\ubb38\uccb4 \ubcc0\uacbd \uc9c4\ud589\uc911")
            QApplication.processEvents()
            busy_shown = True
            source_text = self.last_input
            result = self.analyzer.analyze_tone_change(source_text, tone)
            self.panel.tone_input.setText(tone)
            self.panel.set_tone_result(result)
            replacement_text = self._prepare_replacement_text(result)
            with pause_realtime_reading():
                output_applier.apply(self.last_output_target, replacement_text)
            self.last_corrected_text = replacement_text
            self.suppress_replacement_echo_text = replacement_text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            self.save_history_log(
                feature_type=4,
                input_text=source_text,
                output_text=replacement_text,
                tone=tone,
            )
            target_reader = self.last_output_target.mode if self.last_output_target else ""
            target_handle = self.last_output_target.window_handle if self.last_output_target else None
            if target_reader == "word" and target_handle:
                self.word_undo_available_by_hwnd[int(target_handle)] = True
                self.word_redo_available_by_hwnd[int(target_handle)] = False
                self.realtime_overlay.set_undo_available(True)
                self.realtime_overlay.set_redo_available(False)
            elif target_reader == "notepad" and target_handle:
                self.notepad_undo_available_by_hwnd[int(target_handle)] = True
                self.notepad_redo_available_by_hwnd[int(target_handle)] = False
                self.realtime_overlay.set_undo_available(True)
                self.realtime_overlay.set_redo_available(False)
            self.realtime_overlay.show_status("\ubb38\uccb4 \ubcc0\uacbd \uc644\ub8cc", auto_hide_ms=1000)
            self.maybe_prompt_tone_favorite(tone, self.realtime_overlay)
        except Exception as exc:
            self.panel.set_tone_result(str(exc))
            self.realtime_overlay.show_status("\ubb38\uccb4 \ubcc0\uacbd \uc2e4\ud328", auto_hide_ms=1200)
        finally:
            if busy_shown:
                self.realtime_overlay.hide_busy()

    def handle_drag_tone_button(self):
        if self.active_input_mode != "drag":
            return
        self.mark_drag_overlay_interaction()
        self._log_drag_apply(
            "tone_button_pressed",
            has_target=bool(self.last_output_target),
            input_len=len(self.last_input or ""),
            snapshot=bool(self.last_valid_drag_snapshot),
        )
        current_target_mode = getattr(self.last_output_target, "mode", "") if self.last_output_target is not None else ""
        if current_target_mode == "word_selection":
            captured = self._capture_current_drag_selection_from_anchor()
            self._log_drag_apply("tone_button_preflight_word_capture", captured=captured)
        if self.last_output_target is None or not self.last_input:
            captured = self._capture_current_drag_selection_from_anchor()
            self._log_drag_apply("tone_button_immediate_capture_check", captured=captured)
            restored = bool(self.last_output_target and self.last_input)
            if not restored:
                restored = self._restore_recent_drag_snapshot(max_age=30.0)
            self._log_drag_apply("tone_button_restore_check", restored=restored)
            if not restored:
                self.mini_overlay.show_status("\ub4dc\ub798\uadf8\ub97c \ud574\uc8fc\uc138\uc694!", auto_hide_ms=1000)
                return
        self.mini_overlay.show_tone_prompt()

    def apply_drag_tone_change_to_source(self, tone):
        if self.active_input_mode != "drag":
            return
        tone = str(tone or "").strip()
        if not tone:
            self.mini_overlay.show_status("\ubb38\uccb4\ub97c \uc785\ub825\ud574\uc8fc\uc138\uc694.", auto_hide_ms=1000)
            return

        self.mark_drag_overlay_interaction()
        self._log_drag_apply(
            "tone_apply_pressed",
            has_target=bool(self.last_output_target),
            input_len=len(self.last_input or ""),
            snapshot=bool(self.last_valid_drag_snapshot),
            tone=tone,
        )
        current_target_mode = getattr(self.last_output_target, "mode", "") if self.last_output_target is not None else ""
        if current_target_mode == "word_selection":
            captured = self._capture_current_drag_selection_from_anchor()
            self._log_drag_apply("tone_preflight_word_capture", captured=captured)
        if self.last_output_target is None or not self.last_input:
            captured = self._capture_current_drag_selection_from_anchor()
            self._log_drag_apply("tone_immediate_capture_check", captured=captured)
            restored = bool(self.last_output_target and self.last_input)
            if not restored:
                restored = self._restore_recent_drag_snapshot(max_age=30.0)
            self._log_drag_apply("tone_restore_check", restored=restored)
            if not restored:
                self.mini_overlay.show_status("\ub4dc\ub798\uadf8\ub97c \ud574\uc8fc\uc138\uc694!", auto_hide_ms=1000)
                return
        if not self._can_apply_tone_source_replacement():
            self.mini_overlay.show_status("\ubb38\uccb4\ub97c \ubcc0\uacbd\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1200)
            return

        busy_shown = False
        try:
            self.mini_overlay.show_busy("\ubb38\uccb4 \ubcc0\uacbd \uc9c4\ud589\uc911")
            QApplication.processEvents()
            busy_shown = True
            source_text = self.last_input
            self.last_correction_source_text = source_text
            result = self.analyzer.analyze_tone_change(source_text, tone)
            self.panel.tone_input.setText(tone)
            self.panel.set_tone_result(result)
            self.save_history_log(
                feature_type=4,
                input_text=source_text,
                output_text=result,
                tone=tone,
            )

            output_applier = self.get_output_applier()
            can_replace, reason = output_applier.inspect_replace_availability(self.last_output_target)
            self._log_drag_apply("tone_inspect_result", can_replace=can_replace, reason=reason or "")
            if not can_replace:
                self.mini_overlay.show_status("\ubb38\uccb4 \ubcc0\uacbd \uc2e4\ud328", auto_hide_ms=1200)
                return

            target_reader = self.last_output_target.mode if self.last_output_target else ""
            target_handle = self.last_output_target.window_handle if self.last_output_target else None
            replacement_text = self._prepare_replacement_text(result)
            self.mark_drag_overlay_interaction(duration=6.0)
            with pause_realtime_reading():
                output_applier.apply(self.last_output_target, replacement_text)
            self.mark_drag_overlay_interaction(duration=2.0)
            self.last_corrected_text = replacement_text
            self.suppress_replacement_echo_text = replacement_text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            self._log_drag_apply("tone_apply_success", reader=target_reader, hwnd=target_handle, text_len=len(replacement_text or ""))
            if target_reader == "word_selection" and target_handle:
                self.word_undo_available_by_hwnd[int(target_handle)] = True
                self.word_redo_available_by_hwnd[int(target_handle)] = False
                self.mini_overlay.set_undo_available(True)
                self.mini_overlay.set_redo_available(False)
            elif target_reader == "notepad_selection" and target_handle:
                self.notepad_undo_available_by_hwnd[int(target_handle)] = True
                self.notepad_redo_available_by_hwnd[int(target_handle)] = False
                self.mini_overlay.set_undo_available(True)
                self.mini_overlay.set_redo_available(False)
            self.panel.tone_input.setText(tone)
            self.panel.set_tone_result(result)
            self.mini_overlay.show_status("\ubb38\uccb4 \ubcc0\uacbd \uc644\ub8cc", auto_hide_ms=1000)
            self._clear_recent_drag_snapshot()
            self.clear_drag_selection(target_reader, target_handle)
            QTimer.singleShot(80, lambda tone=tone: self.maybe_prompt_tone_favorite(tone, self.mini_overlay))
        except Exception as exc:
            self._log_drag_apply("tone_apply_exception", error=str(exc))
            self.panel.set_tone_result(str(exc))
            self.mini_overlay.show_status("\ubb38\uccb4 \ubcc0\uacbd \uc2e4\ud328", auto_hide_ms=1200)
        finally:
            if busy_shown:
                self.mini_overlay.hide_busy()

    def undo_last_drag_correction(self):
        if self.active_input_mode != "drag":
            return
        anchor = self.drag_overlay_anchor
        if not anchor:
            self.mini_overlay.show_status("\ub418\ub3cc\ub9b4 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
            return
        reader_name, hwnd = anchor
        hwnd = int(hwnd or 0)
        try:
            if reader_name == "word_selection" and self.word_undo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().undo_last_word_action(hwnd)
                self.word_undo_available_by_hwnd[hwnd] = False
                self.word_redo_available_by_hwnd[hwnd] = True
            elif reader_name == "notepad_selection" and self.notepad_undo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().undo_last_notepad_action(hwnd)
                self.notepad_undo_available_by_hwnd[hwnd] = False
                self.notepad_redo_available_by_hwnd[hwnd] = True
            else:
                self.mini_overlay.show_status("\ub418\ub3cc\ub9b4 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
                return
            self.mini_overlay.set_undo_available(False)
            self.mini_overlay.set_redo_available(True)
            self.clear_drag_selection(reader_name, hwnd)
            self.mini_overlay.show_status("\ub418\ub3cc\ub9ac\uae30 \uc644\ub8cc", auto_hide_ms=1000)
        except Exception as exc:
            self._log_drag_apply("undo_exception", reader=reader_name, hwnd=hwnd, error=str(exc))
            self.mini_overlay.show_status("\ub418\ub3cc\ub9ac\uae30 \uc2e4\ud328", auto_hide_ms=1200)

    def redo_last_drag_correction(self):
        if self.active_input_mode != "drag":
            return
        anchor = self.drag_overlay_anchor
        if not anchor:
            self.mini_overlay.show_status("\uc7ac\uc2e4\ud589\ud560 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
            return
        reader_name, hwnd = anchor
        hwnd = int(hwnd or 0)
        try:
            self.mark_drag_overlay_interaction(duration=3.0)
            if reader_name == "word_selection" and self.word_redo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().redo_last_word_action(hwnd)
                self.word_undo_available_by_hwnd[hwnd] = True
                self.word_redo_available_by_hwnd[hwnd] = False
            elif reader_name == "notepad_selection" and self.notepad_redo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().redo_last_notepad_action(hwnd)
                self.notepad_undo_available_by_hwnd[hwnd] = True
                self.notepad_redo_available_by_hwnd[hwnd] = False
            else:
                self.mini_overlay.show_status("\uc7ac\uc2e4\ud589\ud560 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
                return
            self.mini_overlay.set_undo_available(True)
            self.mini_overlay.set_redo_available(False)
            self.clear_drag_selection(reader_name, hwnd)
            self.mini_overlay.show_status("\uc7ac\uc2e4\ud589 \uc644\ub8cc", auto_hide_ms=1000)
        except Exception as exc:
            self._log_drag_apply("redo_exception", reader=reader_name, hwnd=hwnd, error=str(exc))
            self.mini_overlay.show_status("\uc7ac\uc2e4\ud589 \uc2e4\ud328", auto_hide_ms=1200)

    def undo_last_realtime_correction(self):
        if self.active_input_mode != "realtime":
            return
        anchor = self.realtime_overlay_anchor or self.main_overlay_anchor
        if not anchor:
            self.realtime_overlay.show_status("\ub418\ub3cc\ub9b4 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
            return
        reader_name, hwnd = anchor
        reader_name = "word" if reader_name == "word_selection" else "notepad" if reader_name == "notepad_selection" else str(reader_name or "")
        hwnd = int(hwnd or 0)
        try:
            if reader_name == "word" and self.word_undo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().undo_last_word_action(hwnd)
                self.word_undo_available_by_hwnd[hwnd] = False
                self.word_redo_available_by_hwnd[hwnd] = True
            elif reader_name == "notepad" and self.notepad_undo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().undo_last_notepad_action(hwnd)
                self.notepad_undo_available_by_hwnd[hwnd] = False
                self.notepad_redo_available_by_hwnd[hwnd] = True
            else:
                self.realtime_overlay.show_status("\ub418\ub3cc\ub9b4 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
                return
            self.realtime_overlay.set_undo_available(False)
            self.realtime_overlay.set_redo_available(True)
            self.realtime_overlay.show_status("\ub418\ub3cc\ub9ac\uae30 \uc644\ub8cc", auto_hide_ms=1000)
        except Exception as exc:
            self._log_drag_apply("realtime_undo_exception", reader=reader_name, hwnd=hwnd, error=str(exc))
            self.realtime_overlay.show_status("\ub418\ub3cc\ub9ac\uae30 \uc2e4\ud328", auto_hide_ms=1200)

    def redo_last_realtime_correction(self):
        if self.active_input_mode != "realtime":
            return
        anchor = self.realtime_overlay_anchor or self.main_overlay_anchor
        if not anchor:
            self.realtime_overlay.show_status("\uc7ac\uc2e4\ud589\ud560 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
            return
        reader_name, hwnd = anchor
        reader_name = "word" if reader_name == "word_selection" else "notepad" if reader_name == "notepad_selection" else str(reader_name or "")
        hwnd = int(hwnd or 0)
        try:
            if reader_name == "word" and self.word_redo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().redo_last_word_action(hwnd)
                self.word_undo_available_by_hwnd[hwnd] = True
                self.word_redo_available_by_hwnd[hwnd] = False
            elif reader_name == "notepad" and self.notepad_redo_available_by_hwnd.get(hwnd, False):
                with pause_realtime_reading():
                    self.get_output_applier().redo_last_notepad_action(hwnd)
                self.notepad_undo_available_by_hwnd[hwnd] = True
                self.notepad_redo_available_by_hwnd[hwnd] = False
            else:
                self.realtime_overlay.show_status("\uc7ac\uc2e4\ud589\ud560 \uc218\uc815\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.", auto_hide_ms=1000)
                return
            self.realtime_overlay.set_undo_available(True)
            self.realtime_overlay.set_redo_available(False)
            self.realtime_overlay.show_status("\uc7ac\uc2e4\ud589 \uc644\ub8cc", auto_hide_ms=1000)
        except Exception as exc:
            self._log_drag_apply("realtime_redo_exception", reader=reader_name, hwnd=hwnd, error=str(exc))
            self.realtime_overlay.show_status("\uc7ac\uc2e4\ud589 \uc2e4\ud328", auto_hide_ms=1200)

    def _retry_drag_apply_once(self):
        if self.active_input_mode != "drag":
            self.pending_drag_apply_retry = False
            return
        self.apply_correction_to_source()

    def _can_apply_spelling_source_replacement(self):
        return bool(self.panel.get_replace_mode_checked())

    def _can_apply_tone_source_replacement(self):
        return self.active_input_mode in {"drag", "realtime"} or self.panel.get_replace_mode_checked()

    def _build_output_target(self, event):
        reader_name = str(event.get("reader", "")).strip()
        if reader_name not in {"browser", "browser_extension", "notepad", "notepad_selection", "word", "word_selection", "hwp"}:
            return None
        from client.input.output_applier import OutputTarget

        mode = "browser_extension" if reader_name == "browser_extension" else reader_name
        if reader_name == "notepad_selection":
            mode = "notepad_selection"
        elif reader_name == "word_selection":
            mode = "word_selection"
        return OutputTarget(
            mode=mode,
            window_handle=event.get("window_handle"),
            window_title=event.get("window_title", ""),
            style_info=event.get("style_info") or {},
        )

    def get_output_applier(self):
        if self.output_applier is None:
            from client.input.output_applier import OutputApplier

            self.output_applier = OutputApplier()
        return self.output_applier

    def _extract_corrected_text(self, result):
        text = str(result or "").strip()
        if not text:
            return ""
        lines = [line.rstrip() for line in text.splitlines()]
        heading_indices = [
            index for index, line in enumerate(lines)
            if line.strip().endswith(":")
        ]
        if heading_indices:
            return "\n".join(lines[heading_indices[-1] + 1:]).strip()
        return text

    def _prepare_replacement_text(self, text):
        target = self.last_output_target
        if target and target.mode in {"notepad", "notepad_selection", "browser", "browser_extension", "word", "word_selection", "hwp"}:
            source_text = self.last_correction_source_text or self.last_input
            restored = preserve_replacement_structure(source_text, text)
            self._log_replacement_structure(source_text, text, restored, target.mode)
            return restored
        return text

    def _log_replacement_structure(self, source_text, replacement_text, restored_text, mode):
        try:
            with _REPLACEMENT_STRUCTURE_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} mode={mode!r} "
                    f"source_len={len(str(source_text or ''))} source_newlines={str(source_text or '').count(chr(10))} "
                    f"replacement_len={len(str(replacement_text or ''))} replacement_newlines={str(replacement_text or '').count(chr(10))} "
                    f"restored_len={len(str(restored_text or ''))} restored_newlines={str(restored_text or '').count(chr(10))} "
                    f"source_sample={str(source_text or '')[:80]!r} replacement_sample={str(replacement_text or '')[:80]!r} "
                    f"restored_sample={str(restored_text or '')[:80]!r}\n"
                )
        except Exception:
            pass

    def _is_replacement_echo(self, text):
        if not self.suppress_replacement_echo_text:
            return False
        if time.monotonic() > self.suppress_replacement_echo_until:
            self.suppress_replacement_echo_text = ""
            self.suppress_replacement_echo_until = 0.0
            return False
        return text.strip() == self.suppress_replacement_echo_text.strip()

    def _should_ignore_blank_line_downgrade(self, reader_name, text):
        if reader_name == "browser_extension":
            return False
        if not self.last_input or "\n\n" not in self.last_input:
            return False
        if time.monotonic() - self.last_browser_extension_event_at > 10.0:
            return False
        if self.last_output_target is None or self.last_output_target.mode != "browser_extension":
            return False
        if self._content_line_count(text) != self._content_line_count(self.last_input):
            return False
        return self._blank_line_count(text) < self._blank_line_count(self.last_input)

    def _content_line_count(self, text):
        return sum(1 for line in self._split_lines(text) if line.strip())

    def _blank_line_count(self, text):
        return sum(1 for line in self._split_lines(text) if not line.strip())

    def _split_lines(self, text):
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def _log_input_event(self, event, text, reader_name, note=""):
        try:
            with _UI_INPUT_EVENT_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"reader={reader_name!r} source={event.get('source')!r} "
                    f"title={str(event.get('window_title') or '')[:80]!r} "
                    f"text_len={len(str(text or ''))} newlines={str(text or '').count(chr(10))} "
                    f"blank_lines={self._blank_line_count(text)} note={note!r} "
                    f"sample={str(text or '')[:120]!r}\n"
                )
        except Exception:
            pass

    def show_evaluation_reason(self):
        reason = self.last_evaluation_reason or "\uac4d"
        self.panel.show_notice("\ud3c9\uac00 \uc774\uc720", reason)

    def show_overlay_evaluation_reason(self):
        self.show_panel()
        self.show_evaluation_reason()

    def run_summary(self):
        if not self.last_input:
            return ""
        result = self.analyzer.analyze_summary(self.last_input)
        self.panel.set_summary_result(result)
        self.save_history_log(
            feature_type=3,
            input_text=self.last_input,
            output_text=self._strip_result_heading(result),
        )
        return result

    def run_evaluation(self):
        if not self.last_input:
            return None
        result = self.analyzer.analyze_evaluation(self.last_input)
        score = self._parse_score(result)
        self.last_evaluation_reason = "\uac4d"
        self.panel.set_evaluation_score(result)
        self.save_history_log(
            feature_type=1,
            input_text=self.last_input,
            score=score,
            evaluation_reason=self.last_evaluation_reason,
        )
        return score

    def run_title_recommendation(self, source_text=None):
        input_text = str(source_text if source_text is not None else self.last_input or "")
        if not input_text.strip():
            return ""
        result = self.analyzer.analyze_title_recommendation(input_text)
        self.panel.set_title_recommendation(result)
        self.save_history_log(
            feature_type=1,
            input_text=input_text,
            title=result,
            score=self._parse_score(self.panel.score_label.text()),
        )
        return result

    def run_overlay_title_recommendation(self):
        self.main_overlay.show_busy("\uc81c\ubaa9 \ucd94\ucc9c\uc911")
        QApplication.processEvents()
        result = ""
        try:
            source_text = self._read_full_text_for_title()
            if not source_text.strip():
                self.main_overlay.hide_busy()
                self.main_overlay.show_status("\ud14d\uc2a4\ud2b8 \uc5c6\uc74c")
                return
            result = self.run_title_recommendation(source_text)
        finally:
            self.main_overlay.hide_busy()
        if not result:
            self.main_overlay.show_status("\uc81c\ubaa9 \uc2e4\ud328")
            return
        self.main_overlay.show_title_confirmation(result)

    def _title_target_anchor(self):
        if self.main_overlay_anchor:
            return self.main_overlay_anchor
        if self.realtime_overlay_anchor:
            return self.realtime_overlay_anchor
        if self.drag_overlay_anchor:
            return self.drag_overlay_anchor
        target = self.last_output_target
        if target is not None and getattr(target, "window_handle", None):
            return (getattr(target, "mode", ""), int(target.window_handle))
        return None

    def _read_full_text_for_title(self):
        anchor = self._title_target_anchor()
        if anchor:
            reader_name, hwnd = anchor
            if reader_name in {"word", "word_selection"}:
                text = self._read_active_word_document_text()
                if text.strip():
                    return text
            if reader_name in {"notepad", "notepad_selection"}:
                try:
                    from client.input.notepad_monitor import _read_window_text
                    text, _details = _read_window_text(int(hwnd))
                    if str(text or "").strip():
                        return text
                except Exception:
                    pass
        return self.last_input or ""

    def _read_active_word_document_text(self):
        try:
            import pythoncom
            import win32com.client.dynamic as dynamic
            pythoncom.CoInitialize()
            active = pythoncom.GetActiveObject("Word.Application")
            try:
                active = active.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                pass
            word = dynamic.Dispatch(active)
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                return ""
            text = str(getattr(document.Content, "Text", "") or "")
            return text.replace("\x00", "").replace("\x07", "").replace("\r\n", "\n").replace("\r", "\n").strip()
        except Exception:
            return ""

    def insert_recommended_title_from_overlay(self, title):
        clean_title = str(title or "").strip()
        if not clean_title:
            return
        anchor = self._title_target_anchor()
        if not anchor:
            self.main_overlay.show_status("\uc0bd\uc785 \ub300\uc0c1 \uc5c6\uc74c")
            return
        reader_name, hwnd = anchor
        mode = "word" if reader_name in {"word", "word_selection"} else "notepad" if reader_name in {"notepad", "notepad_selection"} else reader_name
        try:
            from client.input.output_applier import OutputTarget
            target = OutputTarget(mode=mode, window_handle=int(hwnd or 0))
            with pause_realtime_reading():
                self.get_output_applier().insert_title_at_top(target, clean_title)
            self.main_overlay.show_status("\uc81c\ubaa9 \uc0bd\uc785 \uc644\ub8cc")
            if mode == "word" and hwnd:
                self.word_undo_available_by_hwnd[int(hwnd)] = True
                self.word_redo_available_by_hwnd[int(hwnd)] = False
            elif mode == "notepad" and hwnd:
                self.notepad_undo_available_by_hwnd[int(hwnd)] = True
                self.notepad_redo_available_by_hwnd[int(hwnd)] = False
        except Exception as exc:
            self.main_overlay.show_status("\uc81c\ubaa9 \uc0bd\uc785 \uc2e4\ud328", auto_hide_ms=1400)
            self._log_drag_apply("title_insert_failed", error=str(exc), title=clean_title[:80])

    def run_tone_change(self):
        if not self.last_input:
            return
        tone = self.panel.tone_input.text().strip()
        result = self.analyzer.analyze_tone_change(self.last_input, tone)
        self.panel.set_tone_result(result)
        self.save_history_log(
            feature_type=4,
            input_text=self.last_input,
            output_text=result,
            tone=tone,
        )
        self.maybe_prompt_tone_favorite(tone)


    def history_feature_label(self, feature_type):
        return {
            1: "\ud14d\uc2a4\ud2b8 \uae30\ub85d",
            2: "\uad50\uc815 \uae30\ub85d",
            3: "\uc694\uc57d \uae30\ub85d",
            4: "\ubb38\uccb4 \ubcc0\uacbd \uae30\ub85d",
        }.get(int(feature_type or 0), "\uae30\ub85d")
    def save_history_log(
        self,
        feature_type,
        input_text,
        output_text="",
        title=None,
        score=None,
        tone=None,
        spelling_feedback=None,
        evaluation_reason=None,
    ):
        if not input_text:
            return
        payload = {
            "feature_type": feature_type,
            "feature_label": self.history_feature_label(feature_type),
            "input_text": input_text,
            "output_text": output_text or "",
            "title": title or self._current_title(),
            "score": score,
            "tone": tone,
            "spelling_feedback": spelling_feedback,
            "evaluation_reason": evaluation_reason,
        }
        key = (
            payload["feature_type"],
            payload["input_text"],
            payload["output_text"],
            payload.get("title") or "",
            payload.get("score"),
            payload.get("tone") or "",
            payload.get("spelling_feedback") or "",
            payload.get("evaluation_reason") or "",
        )
        if not self.is_logged_in() or not self.is_history_enabled():
            return
        self.write_local_history_log(payload)
        if key in self.last_logged_keys:
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.create_log(payload)
            self.last_logged_keys.add(key)
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception:
            pass

    def write_local_history_log(self, payload):
        key = (
            payload["feature_type"],
            payload["input_text"],
            payload["output_text"],
            payload.get("title") or "",
            payload.get("score"),
            payload.get("tone") or "",
            payload.get("spelling_feedback") or "",
            payload.get("evaluation_reason") or "",
        )
        if key in self.last_local_log_keys:
            return

        log_dir = Path(__file__).resolve().parents[2] / ".logs" / "history"
        feature_names = {
            1: "text",
            2: "spelling",
            3: "summary",
            4: "tone",
        }
        feature_name = feature_names.get(payload["feature_type"], "unknown")
        log_data = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "feature_name": feature_name,
            "db_sync_enabled": self.is_logged_in() and self.is_history_enabled(),
            **payload,
        }

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{feature_name}_logs.jsonl"
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_data, ensure_ascii=False) + "\n")
            self.last_local_log_keys.add(key)
        except Exception:
            pass

    def show_history(self, feature_type):
        if not self.ensure_history_available():
            return
        if not self.ensure_server_available():
            return
        try:
            logs = self.api_client.list_logs(feature_type)
            self.panel.show_history_list(feature_type, logs)
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("\uae30\ub85d \uc870\ud68c \uc2e4\ud328", str(exc))

    def _current_title(self):
        title = self.panel.title_label_box.text().strip()
        return "" if title in {"\uc81c\ubaa9", ""} else title

    def _strip_result_heading(self, text):
        value = str(text or "").strip()
        if "\n\n" in value:
            return value.split("\n\n", 1)[1].strip()
        return value

    def _parse_score(self, text):
        digits = "".join(ch for ch in str(text or "") if ch.isdigit())
        if not digits:
            return None
        return max(0, min(100, int(digits[:3])))

    def normalize_settings(self, settings):
        normalized = DEFAULT_SETTINGS.copy()
        if isinstance(settings, dict):
            normalized.update({key: settings[key] for key in DEFAULT_SETTINGS if key in settings})
        normalized["default_dark_mode"] = bool(normalized.get("default_dark_mode", False))
        normalized["history_enabled"] = bool(normalized.get("history_enabled", False))
        input_mode = normalized.get("input_mode")
        normalized["input_mode"] = input_mode if input_mode in {"clipboard", "drag", "realtime"} else "clipboard"
        normalized["replace_mode"] = bool(normalized.get("replace_mode", False))
        return normalized

    def collect_settings_from_panel(self):
        settings = self.settings.copy()
        settings["default_dark_mode"] = self.panel.get_default_dark_mode_checked()
        settings["input_mode"] = self.panel.get_input_mode()
        settings["replace_mode"] = self.panel.get_replace_mode_checked()
        if self.is_logged_in():
            settings["history_enabled"] = self.panel.get_history_enabled_checked()
        return self.normalize_settings(settings)

    def apply_settings_state(self, settings, persist=True):
        previous_mode = self.active_input_mode
        self.settings = self.normalize_settings(settings)
        if persist:
            save_app_settings(self.settings)

        self.panel.set_dark_mode(self.settings["default_dark_mode"], animate=False)
        self.panel.set_default_dark_mode_checked(self.settings["default_dark_mode"])
        self.panel.set_history_enabled_checked(self.settings["history_enabled"])
        self.panel.set_input_mode(self.settings["input_mode"])
        self.panel.set_replace_mode_checked(self.settings["replace_mode"])
        self.update_login_state()

        mode_changed = previous_mode != self.settings["input_mode"]
        if mode_changed:
            self.last_output_target = None
            self._clear_recent_drag_snapshot()
        self.active_input_mode = self.settings["input_mode"]
        set_active_input_mode(self.active_input_mode)
        self.ensure_realtime_monitor_started()
        self.ensure_drag_monitor_started()
        if self.active_input_mode != "drag":
            self._hide_mini_overlay("main_window_hide_call")
        if self.active_input_mode != "realtime" and hasattr(self, "realtime_overlay"):
            self.realtime_overlay_requested = False
            self.realtime_overlay_anchor = None
            self._hide_realtime_overlay("main_window_hide_call")
        if hasattr(self, "main_overlay"):
            self.main_overlay.set_active_mode(self.active_input_mode)
        if self.active_input_mode != "realtime":
            self.panel.set_active_window_title("")
        if mode_changed:
            self.reset_session_state()

    def ensure_server_available(self):
        if self._server_started:
            return True
        try:
            self.local_server.ensure_running()
            self._server_started = True
            self._startup_server_error = ""
            return True
        except Exception as exc:
            self._startup_server_error = str(exc)
            self.panel.show_notice("\uc11c\ubc84 \uc2dc\uc791 \uc2e4\ud328", self._startup_server_error)
            return False

    def save_remote_settings(self):
        if not self.is_logged_in():
            return False
        if not self.ensure_server_available():
            return False
        self.api_client.update_settings(self.settings)
        return True

    def load_remote_settings(self):
        if not self.is_logged_in():
            return None
        if not self.ensure_server_available():
            return None
        remote = self.api_client.get_settings()
        if not remote or not remote.get("has_settings"):
            return None
        return self.normalize_settings(remote)

    def sync_restored_login_settings(self):
        if not self.is_logged_in():
            return
        try:
            remote_settings = self.load_remote_settings()
            if remote_settings:
                self.apply_settings_state(remote_settings)
            else:
                self.save_remote_settings()
        except Exception as exc:
            self.panel.show_notice("\uc124\uc815 \ub3d9\uae30\ud654 \uc2e4\ud328", str(exc))

    def start_restored_login_sync(self):
        if not self.is_logged_in():
            return
        threading.Thread(target=self.run_restored_login_sync, daemon=True).start()

    def run_restored_login_sync(self):
        try:
            remote_settings = self.load_remote_settings()
            if remote_settings:
                self.signals.auth_sync_signal.emit({"settings": remote_settings})
            else:
                self.save_remote_settings()
                self.signals.auth_sync_signal.emit({"settings": None})
        except Exception as exc:
            self.signals.auth_sync_signal.emit({"error": str(exc)})

    def handle_background_auth_sync_result(self, result):
        if not isinstance(result, dict):
            return
        if result.get("error"):
            self.panel.show_notice("\uc124\uc815 \ub3d9\uae30\ud654 \uc2e4\ud328", result["error"])
            return
        remote_settings = result.get("settings")
        if remote_settings:
            self.apply_settings_state(remote_settings)
        self.refresh_account_identity()
        self.refresh_tone_favorites()

    def refresh_tone_favorites(self):
        if not self.is_logged_in():
            self.tone_favorites = []
            self._sync_tone_favorite_overlays()
            return
        try:
            if not self.ensure_server_available():
                return
            self.tone_favorites = self.api_client.list_tone_favorites() or []
            self._log_drag_apply("tone_favorite_refresh_success", count=len(self.tone_favorites))
        except Exception as exc:
            self._log_drag_apply("tone_favorite_refresh_failed", error=str(exc))
        self._sync_tone_favorite_overlays()

    def _sync_tone_favorite_overlays(self):
        try:
            enabled = self.is_logged_in()
            self.mini_overlay.set_tone_favorites_enabled(enabled)
            self.realtime_overlay.set_tone_favorites_enabled(enabled)
            self.mini_overlay.set_tone_favorites(self.tone_favorites)
            self.realtime_overlay.set_tone_favorites(self.tone_favorites)
        except Exception:
            pass

    def tone_is_favorite(self, tone):
        value = str(tone or "").strip()
        if not value:
            return True
        return any(str(item.get("tone") or "").strip() == value for item in self.tone_favorites)

    def add_tone_favorite(self, tone):
        value = str(tone or "").strip()
        if not value or not self.is_logged_in():
            self._log_drag_apply("tone_favorite_add_skipped", tone=value, logged_in=self.is_logged_in())
            return
        try:
            if not self.ensure_server_available():
                self._log_drag_apply("tone_favorite_add_server_unavailable", tone=value)
                return
            favorite = self.api_client.create_tone_favorite(value)
            if isinstance(favorite, dict):
                self.tone_favorites = [
                    item for item in self.tone_favorites
                    if str(item.get("tone") or "").strip() != value
                ]
                self.tone_favorites.insert(0, favorite)
                self.tone_favorites = self.tone_favorites[:10]
                self._sync_tone_favorite_overlays()
                self._log_drag_apply("tone_favorite_add_success", tone=value, favorite_id=favorite.get("id"))
            self.refresh_tone_favorites()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self._log_drag_apply("tone_favorite_add_failed", tone=value, error=str(exc))
            self.panel.show_notice("\uc990\uaca8\ucc3e\uae30 \uc800\uc7a5 \uc2e4\ud328", str(exc))

    def delete_tone_favorite(self, favorite_id):
        if not self.is_logged_in():
            return
        try:
            if not self.ensure_server_available():
                return
            self.api_client.delete_tone_favorite(favorite_id)
            self.tone_favorites = [
                item for item in self.tone_favorites
                if int(item.get("id") or 0) != int(favorite_id)
            ]
            self._sync_tone_favorite_overlays()
            self.refresh_tone_favorites()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("\uc990\uaca8\ucc3e\uae30 \uc0ad\uc81c \uc2e4\ud328", str(exc))

    def maybe_prompt_tone_favorite(self, tone, overlay=None):
        value = str(tone or "").strip()
        if not value or not self.is_logged_in() or self.tone_is_favorite(value):
            self._log_drag_apply(
                "tone_favorite_prompt_skipped",
                tone=value,
                logged_in=self.is_logged_in(),
                already_favorite=self.tone_is_favorite(value),
            )
            return
        if overlay is not None and hasattr(overlay, "show_tone_favorite_confirm"):
            self._log_drag_apply("tone_favorite_prompt_show_overlay", tone=value, overlay=type(overlay).__name__)
            overlay.show_tone_favorite_confirm(value)
            return
        self._log_drag_apply("tone_favorite_prompt_show_panel", tone=value)
        self.panel.show_prompt(
            "\ubb38\uccb4 \uc990\uaca8\ucc3e\uae30",
            f"{value}\n\uc990\uaca8\ucc3e\uae30\uc5d0 \ub4f1\ub85d\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?",
            yes_callback=lambda value=value: self.add_tone_favorite(value),
            yes_text="\uc608",
            no_text="\uc544\ub2c8\uc694",
        )

    def save_settings(self):
        self.apply_settings_state(self.collect_settings_from_panel())
        if self.is_logged_in():
            try:
                self.save_remote_settings()
            except UnauthorizedError as exc:
                self.handle_session_expired(str(exc))
                return
            except Exception as exc:
                self.panel.show_notice("\uc124\uc815 \uc800\uc7a5 \uc2e4\ud328", str(exc))
                return
        self.panel.show_settings_saved_notice()

    def quit_app(self):
        self.reset_session_state()
        if hasattr(self, "mini_overlay"):
            self._hide_mini_overlay("quit_app")
        if hasattr(self, "realtime_overlay"):
            self._hide_realtime_overlay("quit_app")
        self.tray.hide()
        self.local_server.stop()
        self.qt_app.quit()

    def logout(self):
        self.api_client.clear_token()
        self.tone_favorites = []
        self._sync_tone_favorite_overlays()
        save_app_settings(self.settings)
        self.panel.set_account_identity("", "")
        self.update_login_state()

    def handle_login_button(self):
        if self.is_logged_in():
            self.logout()
            return
        self.panel.show_auth_page()

    def handle_login_submit(self):
        username = self.panel.login_username_input.text().strip()
        password = self.panel.login_password_input.text().strip()
        remember_me = self.panel.login_remember_checkbox.isChecked()
        if not username or not password:
            self.panel.show_notice("\uc785\ub825 \ud544\uc694", "\uc544\uc774\ub514\uc640 \ube44\ubc00\ubc88\ud638\ub97c \uc785\ub825\ud574\uc8fc\uc138\uc694.")
            return
        if not self.ensure_server_available():
            return
        try:
            local_settings = self.collect_settings_from_panel()
            self.api_client.login(username, password, remember_me)
            self.update_login_state()
            self.panel.close_auth_page()
            self.handle_login_settings_sync(username, local_settings)
            self.refresh_account_identity()
            self.refresh_tone_favorites()
        except Exception as exc:
            self.panel.show_notice("\ub85c\uadf8\uc778 \uc2e4\ud328", str(exc))

    def handle_signup_submit(self):
        username = self.panel.signup_username_input.text().strip()
        password = self.panel.signup_password_input.text().strip()
        password_confirm = self.panel.signup_password_confirm_input.text().strip()
        if not username or not password or not password_confirm:
            self.panel.show_notice("\uc785\ub825 \ud544\uc694", "\ubaa8\ub4e0 \ud56d\ubaa9\uc744 \uc785\ub825\ud574\uc8fc\uc138\uc694.")
            return
        if password != password_confirm:
            self.panel.show_notice("\ud655\uc778 \ud544\uc694", "\ube44\ubc00\ubc88\ud638\uac00 \uc11c\ub85c \uc77c\uce58\ud558\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.")
            return
        if len(password) < 4:
            self.panel.show_notice("\uc785\ub825 \ud544\uc694", "\ube44\ubc00\ubc88\ud638\ub294 4\uae00\uc790 \uc774\uc0c1\uc774\uc5b4\uc57c \ud569\ub2c8\ub2e4.")
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.signup(username, password)
            self.pending_signup_username = username
            self.panel.login_username_input.setText(username)
            self.panel.login_password_input.clear()
            self.panel.show_login_form()
            self.panel.show_prompt(
                "\ud68c\uc6d0\uac00\uc785 \uc644\ub8cc",
                "\ud68c\uc6d0\uac00\uc785\uc774 \uc644\ub8cc\ub418\uc5c8\uc2b5\ub2c8\ub2e4.",
                yes_callback=self.panel.show_auth_page,
            )
            self.panel.prompt_no_btn.hide()
            self.panel.prompt_yes_btn.setText("\ud655\uc778")
        except Exception as exc:
            self.panel.show_notice("\ud68c\uc6d0\uac00\uc785 \uc2e4\ud328", str(exc))

    def handle_login_settings_sync(self, username, local_settings):
        try:
            remote_settings = self.load_remote_settings()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("\uc124\uc815 \ubd88\ub7ec\uc624\uae30 \uc2e4\ud328", str(exc))
            return

        is_new_signup = username == self.pending_signup_username
        if is_new_signup or remote_settings is None:
            self.apply_settings_state(local_settings)
            try:
                self.save_remote_settings()
                self.pending_signup_username = ""
                self.refresh_account_identity()
            except Exception as exc:
                self.panel.show_notice("\uc124\uc815 \uc800\uc7a5 \uc2e4\ud328", str(exc))
            return

        def keep_local_settings():
            self.apply_settings_state(local_settings)
            try:
                self.save_remote_settings()
            except Exception as exc:
                self.panel.show_notice("\uc124\uc815 \uc800\uc7a5 \uc2e4\ud328", str(exc))

        def load_account_settings():
            self.apply_settings_state(remote_settings)

        self.panel.show_prompt(
            "\uc124\uc815 \uc720\uc9c0",
            "\ube44\ub85c\uadf8\uc778 \uc0c1\ud0dc\uc5d0\uc11c \uc0ac\uc6a9\ud558\ub358 \uc124\uc815\uc744 \uc720\uc9c0\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?\n\uc720\uc9c0\ud558\uba74 \ud604\uc7ac \uc124\uc815\uc774 \uacc4\uc815\uc5d0 \uc800\uc7a5\ub429\ub2c8\ub2e4.",
            yes_callback=keep_local_settings,
            no_callback=load_account_settings,
            yes_text="\uc720\uc9c0",
            no_text="\ube44\uc720\uc9c0",
        )

    def refresh_account_identity(self):
        if not self.is_logged_in():
            return
        try:
            account = self.api_client.get_account()
            self.panel.set_account_identity(
                account.get("username", self.api_client.current_username or ""),
                account.get("display_name", ""),
            )
        except Exception:
            self.panel.set_account_identity(self.api_client.current_username or "", "")

    def handle_account_manage_button(self):
        if not self.is_logged_in():
            self.panel.show_prompt(
                "\ub85c\uadf8\uc778\uc774 \ud544\uc694\ud569\ub2c8\ub2e4.",
                "\uacc4\uc815 \uad00\ub9ac\ub294 \ub85c\uadf8\uc778\ud574\uc57c \uc0ac\uc6a9\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.\n\uc9c0\uae08 \ub85c\uadf8\uc778\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?",
                yes_callback=self.panel.show_auth_page,
            )
            return
        self.panel.show_account_verify_page()

    def handle_account_verify_submit(self):
        password = self.panel.account_verify_password_input.text().strip()
        if not password:
            self.panel.show_notice("\uc785\ub825 \ud544\uc694", "\ube44\ubc00\ubc88\ud638\ub97c \uc785\ub825\ud574\uc8fc\uc138\uc694.")
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.verify_account(password)
            account = self.api_client.get_account()
            self.panel.show_account_page(account)
            self.panel.set_account_identity(account.get("username", ""), account.get("display_name", ""))
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("\uc778\uc99d \uc2e4\ud328", str(exc))

    def handle_account_update(self, field=None):
        payload = self.panel.get_account_payload(field)
        if not payload:
            return
        if "username" in payload and not payload["username"]:
            self.panel.show_notice("\uc785\ub825 \ud544\uc694", "\uc544\uc774\ub514\ub97c \uc785\ub825\ud574\uc8fc\uc138\uc694.")
            return
        if "password" in payload and len(payload["password"]) < 4:
            self.panel.show_notice("\uc785\ub825 \ud544\uc694", "\ube44\ubc00\ubc88\ud638\ub294 4\uae00\uc790 \uc774\uc0c1\uc774\uc5b4\uc57c \ud569\ub2c8\ub2e4.")
            return
        if not self.ensure_server_available():
            return
        try:
            account = self.api_client.update_account(payload)
            self.panel.set_account_info(account)
            self.panel.set_account_identity(account.get("username", ""), account.get("display_name", ""))
            self.update_login_state()
            self.panel.show_account_saved_notice()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("\uacc4\uc815 \uc218\uc815 \uc2e4\ud328", str(exc))

    def confirm_account_delete(self):
        self.panel.show_prompt(
            "\uacc4\uc815 \ud0c8\ud1f4",
            "\uacc4\uc815\uc744 \ud0c8\ud1f4\ud558\uba74 \uc800\uc7a5\ub41c \uacc4\uc815 \uc815\ubcf4\uc640 \uae30\ub85d\uc774 \uc0ad\uc81c\ub429\ub2c8\ub2e4.\\n\uc815\ub9d0 \ud0c8\ud1f4\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?",
            yes_callback=self.delete_account,
            yes_text="\ud0c8\ud1f4",
            no_text="\ucde8\uc18c",
        )

    def delete_account(self):
        if not self.ensure_server_available():
            return
        try:
            self.api_client.delete_account()
            self.logout()
            self.panel.close_account_pages()
            self.panel.show_notice("\uacc4\uc815 \ud0c8\ud1f4 \uc644\ub8cc", "\uacc4\uc815\uc774 \uc0ad\uc81c\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("\uacc4\uc815 \ud0c8\ud1f4 \uc2e4\ud328", str(exc))

    def handle_session_expired(self, message):
        self.panel.show_notice("\uc138\uc158\uc774 \ub9cc\ub8cc\ub418\uc5c8\uc2b5\ub2c8\ub2e4.", message or "\ub2e4\uc2dc \ub85c\uadf8\uc778\ud574\uc8fc\uc138\uc694.")
        self.logout()

    def ensure_history_available(self):
        if not self.is_logged_in():
            self.panel.show_prompt(
                "\ub85c\uadf8\uc778\uc774 \ud544\uc694\ud569\ub2c8\ub2e4.",
                "\uae30\ub85d \uae30\ub2a5\uc740 \ub85c\uadf8\uc778\ud574\uc57c \uc0ac\uc6a9\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.\n\uc9c0\uae08 \ub85c\uadf8\uc778\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?",
                yes_callback=self.panel.show_auth_page,
            )
            return False
        if self.is_history_enabled():
            return True
        self.panel.show_prompt(
            "\uae30\ub85d \uae30\ub2a5 \ube44\ud65c\uc131\ud654",
            "\uae30\ub85d\uc744 \uc0ac\uc6a9\ud558\ub824\uba74 \uc124\uc815\uc5d0\uc11c \'\uae30\ub85d \uc0ac\uc6a9\'\uc744 \ucf1c \uc8fc\uc138\uc694.",
            yes_callback=self.panel.open_settings_tab,
        )
        return False

    def is_logged_in(self):
        return bool(self.api_client.access_token and self.api_client.current_username)

    def is_history_enabled(self):
        return bool(self.settings.get("history_enabled", False))

    def update_login_state(self):
        logged_in = self.is_logged_in()
        username = self.api_client.current_username or ""
        if hasattr(self, "panel"):
            self.panel.update_login_state(logged_in, username, self.is_history_enabled())
        if hasattr(self, "mini_overlay"):
            self._sync_tone_favorite_overlays()
        if hasattr(self, "login_action"):
            self.login_action.setText("\ub85c\uadf8\uc544\uc6c3" if logged_in else "\ub85c\uadf8\uc778")

    def safe_paste(self, retries=3, retry_delay=0.05):
        for _ in range(retries):
            try:
                return pyperclip.paste()
            except (pyperclip.PyperclipException, OSError):
                time.sleep(retry_delay)
        return ""

    def safe_copy(self, text, retries=3, retry_delay=0.05):
        for _ in range(retries):
            try:
                pyperclip.copy(text)
                return True
            except (pyperclip.PyperclipException, OSError):
                time.sleep(retry_delay)
        return False





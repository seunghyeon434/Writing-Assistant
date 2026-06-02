from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Callable

from PyQt5.QtCore import QEasingCurve, QPoint, QRect, Qt, QPropertyAnimation, QTimer, pyqtProperty
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


try:
    import pythoncom
    import win32com.client as win32_client
    import win32com.client.dynamic as win32_dynamic
    import win32gui
    import win32process
except Exception:  # pragma: no cover - optional Windows dependency
    pythoncom = None
    win32_client = None
    win32_dynamic = None
    win32gui = None
    win32process = None


EM_POSFROMCHAR = 0x00D6
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WORD_GUIDE_LINE_Y_OFFSET = -28
NOTEPAD_GUIDE_LINE_Y_OFFSET = -18
UNDERLINE_WINDOW_Y_OFFSET = 14
NOTEPAD_UNDERLINE_WINDOW_Y_OFFSET = 0
_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_SPELLING_OVERLAY_LOG_PATH = _LOG_DIR / "spelling_inspection_overlay.log"


def _log_spelling_overlay(note: str, **values):
    parts = [time.strftime("%Y-%m-%d %H:%M:%S"), str(note)]
    for key, value in values.items():
        parts.append(f"{key}={value!r}")
    try:
        with _SPELLING_OVERLAY_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(" ".join(parts) + "\n")
    except Exception:
        pass


@dataclass
class SpellingGuideIssue:
    original: str
    replacement: str
    reason: str
    start: int
    end: int
    category: str = "맞춤법"
    rect: QRect | None = None
    has_blank_line_above: bool = False


class SpellingGuideCard(QWidget):
    replace_requested = None

    def __init__(self, issue: SpellingGuideIssue, on_replace: Callable[[SpellingGuideIssue], None]):
        super().__init__(None)
        self.issue = issue
        self.on_replace = on_replace
        self.allowed_geometry: QRect | None = None
        self.avoidance_rects: list[QRect] = []
        self.linked_underline = None
        self.overlay_manager = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(120)
        self._topmost_timer.timeout.connect(self._force_topmost)
        self._anim = None
        self._opacity_anim = None
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._build()

    def _build(self):
        self.setObjectName("spellingGuideCard")
        self.setStyleSheet(
            """
            QWidget#spellingGuideCard {
                background: transparent;
            }
            QLabel#wordChip {
                background: #f2e7da;
                color: #2f241f;
                border-radius: 12px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#arrowLabel {
                color: #b86a3c;
                font-size: 18px;
                font-weight: 900;
            }
            QLabel#reasonLabel {
                color: #3f2f26;
                font-size: 12px;
                line-height: 150%;
            }
            QPushButton#replaceButton {
                background: #b86a3c;
                color: white;
                border: 0;
                border-radius: 12px;
                padding: 7px 16px;
                font-size: 12px;
                font-weight: 800;
            }
            QPushButton#replaceButton:hover {
                background: #9f5730;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(10)

        word_row = QHBoxLayout()
        word_row.setContentsMargins(0, 0, 0, 0)
        word_row.setSpacing(8)
        original = QLabel(self.issue.original)
        original.setObjectName("wordChip")
        replacement = QLabel(self.issue.replacement)
        replacement.setObjectName("wordChip")
        arrow = QLabel("->")
        arrow.setObjectName("arrowLabel")
        arrow.setAlignment(Qt.AlignCenter)
        word_row.addWidget(original)
        word_row.addWidget(arrow)
        word_row.addWidget(replacement)
        word_row.addStretch(1)
        layout.addLayout(word_row)

        reason = QLabel(self.issue.reason)
        reason.setObjectName("reasonLabel")
        reason.setWordWrap(True)
        reason.setMinimumWidth(360)
        reason.setMaximumWidth(460)
        layout.addWidget(reason)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        replace_button = QPushButton("교체")
        replace_button.setObjectName("replaceButton")
        replace_button.clicked.connect(lambda: self.on_replace(self.issue))
        button_row.addWidget(replace_button)
        layout.addLayout(button_row)
        self.adjustSize()

    def show_near(self, rect: QRect):
        self._hide_timer.stop()
        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        available = self.allowed_geometry or (screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080))
        self.adjustSize()
        x = rect.left()
        y = rect.bottom() + 22
        if y + self.height() > available.bottom():
            y = rect.top() - self.height() - 22
        min_x = available.left() + 8
        min_y = available.top() + 8
        max_x = max(min_x, available.right() - self.width() - 8)
        max_y = max(min_y, available.bottom() - self.height() - 8)
        x = min(max(min_x, x), max_x)
        y = min(max(min_y, y), max_y)
        self.move(x, y + 8)
        self.show()
        self.raise_()
        self._force_topmost()
        self._topmost_timer.start()
        self._refresh_underline_occlusion()
        QTimer.singleShot(50, self._force_topmost)
        QTimer.singleShot(170, self._force_topmost)
        QTimer.singleShot(280, self._force_topmost)
        QTimer.singleShot(80, self._refresh_underline_occlusion)
        QTimer.singleShot(180, self._refresh_underline_occlusion)
        self._animate_to(QPoint(x, y), 1.0)

    def _force_topmost(self):
        if win32gui is None:
            return
        try:
            hwnd = int(self.winId())
            hwnd_topmost = -1
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE, NOMOVE, NOACTIVATE, SHOWWINDOW
            win32gui.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, flags)
        except Exception:
            pass

    def schedule_hide(self):
        self._hide_timer.start(450)

    def enterEvent(self, event):
        self._hide_timer.stop()
        self.raise_()
        self._force_topmost()
        self._set_linked_label(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_linked_label(False)
        self.schedule_hide()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#fffaf4"))
        painter.setPen(QPen(QColor("#dccbbb"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)
        super().paintEvent(event)

    def _animate_to(self, point: QPoint, opacity: float):
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(150)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(point)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

        self._opacity_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._opacity_anim.setDuration(150)
        self._opacity_anim.setStartValue(self._opacity.opacity())
        self._opacity_anim.setEndValue(opacity)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_anim.start()

    def _fade_out(self):
        self._set_linked_label(False)
        self._topmost_timer.stop()
        self.show()
        self.raise_()
        self._opacity_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._opacity_anim.setDuration(140)
        self._opacity_anim.setStartValue(self._opacity.opacity())
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_anim.finished.connect(self._finish_fade_out)
        self._opacity_anim.start()

    def _finish_fade_out(self):
        self.hide()
        self._refresh_underline_occlusion()

    def _set_linked_label(self, visible: bool):
        underline = getattr(self, "linked_underline", None)
        if underline is None:
            return
        try:
            underline._animate_label(bool(visible))
        except Exception:
            pass

    def _refresh_underline_occlusion(self):
        manager = getattr(self, "overlay_manager", None)
        if manager is None:
            return
        try:
            manager.refresh_underline_occlusion()
        except Exception:
            pass


class SpellingUnderline(QWidget):
    def __init__(self, issue: SpellingGuideIssue, card: SpellingGuideCard):
        super().__init__(None)
        self.issue = issue
        self.card = card
        self._label_reveal = 0.0
        self._label_anim = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFixedHeight(28)

    def getLabelReveal(self) -> float:
        return self._label_reveal

    def setLabelReveal(self, value: float):
        self._label_reveal = max(0.0, min(1.0, float(value)))
        self.update()

    labelReveal = pyqtProperty(float, fget=getLabelReveal, fset=setLabelReveal)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        label_text = str(getattr(self.issue, "category", "") or "맞춤법")
        label_font = painter.font()
        label_font.setPointSize(6)
        label_font.setBold(True)
        painter.setFont(label_font)
        label_metrics = painter.fontMetrics()
        label_width = max(42, label_metrics.horizontalAdvance(label_text) + 16)
        line_y = 18
        reveal = self._label_reveal
        label_y = int(line_y - 14 * reveal)
        if reveal > 0.02:
            label_rect = QRect(1, label_y, min(label_width, max(42, self.width() - 2)), 14)
            painter.setOpacity(reveal)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#d92828"))
            painter.drawRoundedRect(label_rect, 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(label_rect, Qt.AlignCenter, label_text)
            painter.setOpacity(1.0)

        pen = QPen(QColor("#d92828"), 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(1, line_y, self.width() - 1, line_y)

    def enterEvent(self, event):
        self._animate_label(True)
        self.card.show_near(self.frameGeometry())
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.card.schedule_hide()
        super().leaveEvent(event)

    def _animate_label(self, visible: bool):
        self._label_anim = QPropertyAnimation(self, b"labelReveal", self)
        self._label_anim.setDuration(130)
        self._label_anim.setStartValue(self._label_reveal)
        self._label_anim.setEndValue(1.0 if visible else 0.0)
        self._label_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._label_anim.start()


class SpellingInspectionOverlayManager:
    def __init__(self, on_replace: Callable[[SpellingGuideIssue], None]):
        self.on_replace = on_replace
        self._underlines: list[SpellingUnderline] = []
        self._cards: list[SpellingGuideCard] = []
        self._avoidance_rect_provider = None
        self._last_target = None
        self._last_text = ""
        self._target_hwnd = 0
        self._hidden_for_target = False
        self._last_live_poll_at = 0.0
        self._focus_timer = QTimer()
        self._focus_timer.setInterval(180)
        self._focus_timer.timeout.connect(self._sync_target_visibility)

    def set_avoidance_rect_provider(self, provider):
        self._avoidance_rect_provider = provider if callable(provider) else None

    def clear(self):
        for widget in [*self._underlines, *self._cards]:
            try:
                widget.hide()
                widget.deleteLater()
            except Exception:
                pass
        self._underlines = []
        self._cards = []
        self._target_hwnd = 0
        self._hidden_for_target = False
        self._last_live_poll_at = 0.0
        self._focus_timer.stop()

    def has_markers(self) -> bool:
        return bool(self._underlines)

    def live_text_for_target(self, target, fallback: str) -> str:
        return self._live_text_for_target(target, fallback)

    def sync_for_target(self, target, text: str, use_live: bool = True) -> bool:
        """Refresh visible markers against the current document text.

        This keeps marker state tied to the editor contents instead of to the
        last replace action, so Word's native undo can restore removed markers.
        """
        if target is None or not self._underlines:
            return False
        if not self._same_target_context(self._last_target, target):
            return False

        previous_text = self._last_text
        current_text = self._live_text_for_target(target, text) if use_live else str(text or "")
        self._target_hwnd = int(getattr(target, "window_handle", 0) or 0) if target is not None else 0
        issues = self._test_issues(current_text)
        if use_live and self._looks_like_transient_sync(target, previous_text, current_text, issues):
            _log_spelling_overlay(
                "sync_ignored_transient_text",
                hwnd=self._target_hwnd,
                previous_len=len(previous_text or ""),
                current_len=len(current_text or ""),
                current_sample=current_text[:40],
            )
            return True

        self._last_target = target
        self._last_text = current_text
        desired = {self._issue_key(issue): issue for issue in issues}
        existing = {self._issue_key(underline.issue): underline for underline in self._underlines}

        removed = 0
        for key, underline in list(existing.items()):
            if key in desired:
                continue
            self._dispose_underline(underline)
            removed += 1

        self._underlines = [underline for underline in self._underlines if self._issue_key(underline.issue) in desired]
        self._cards = [underline.card for underline in self._underlines]

        updated = 0
        live_underlines: list[SpellingUnderline] = []
        for underline in list(self._underlines):
            key = self._issue_key(underline.issue)
            issue = desired.get(key)
            if issue is None:
                continue
            if self._update_marker_geometry(target, underline, issue):
                updated += 1
                live_underlines.append(underline)
        self._underlines = live_underlines
        self._cards = [underline.card for underline in self._underlines]

        added = 0
        live_keys = {self._issue_key(underline.issue) for underline in self._underlines}
        for key, issue in desired.items():
            if key in live_keys:
                continue
            if self._create_marker(target, issue):
                added += 1

        if self._underlines:
            self._focus_timer.start()
        else:
            self._focus_timer.stop()
        _log_spelling_overlay(
            "sync_done",
            hwnd=self._target_hwnd,
            scope=self._inspection_scope(target),
            use_live=use_live,
            desired=len(desired),
            removed=removed,
            updated=updated,
            added=added,
            underline_count=len(self._underlines),
        )
        return True

    def remove_issue(self, issue: SpellingGuideIssue):
        removed = 0
        remaining_underlines: list[SpellingUnderline] = []
        remaining_cards: list[SpellingGuideCard] = []
        for underline in self._underlines:
            if self._same_issue(underline.issue, issue):
                removed += 1
                self._dispose_underline(underline)
            else:
                remaining_underlines.append(underline)
                remaining_cards.append(underline.card)
        self._underlines = remaining_underlines
        self._cards = remaining_cards
        if not self._underlines:
            self._focus_timer.stop()
        _log_spelling_overlay("issue_removed", original=getattr(issue, "original", ""), start=getattr(issue, "start", -1), removed=removed)
        return removed

    def _same_issue(self, left: SpellingGuideIssue, right: SpellingGuideIssue) -> bool:
        return (
            str(getattr(left, "original", "")) == str(getattr(right, "original", ""))
            and int(getattr(left, "start", -1)) == int(getattr(right, "start", -1))
            and int(getattr(left, "end", -1)) == int(getattr(right, "end", -1))
            and str(getattr(left, "category", "")) == str(getattr(right, "category", ""))
        )

    def current_target(self):
        return self._last_target

    def has_visible_guide_card(self) -> bool:
        for card in self._cards:
            try:
                if card.isVisible():
                    return True
            except Exception:
                continue
        return False

    def show_for_target(self, target, text: str):
        self.clear()
        self._last_target = target
        self._last_text = self._live_text_for_target(target, text)
        self._target_hwnd = int(getattr(target, "window_handle", 0) or 0) if target is not None else 0
        if target is None or not self._last_text:
            _log_spelling_overlay("show_skipped_empty_target", has_target=bool(target), text_len=len(self._last_text))
            return 0
        _log_spelling_overlay(
            "show_start",
            version="type_label_v4",
            mode=str(getattr(target, "mode", "") or ""),
            scope=self._inspection_scope(target),
            hwnd=self._target_hwnd,
            text_len=len(self._last_text),
            text_sample=self._last_text[:80],
        )
        issues = self._test_issues(self._last_text)
        _log_spelling_overlay("issues_built", count=len(issues), originals=[issue.original for issue in issues])
        if not issues:
            return 0
        for issue in issues:
            self._create_marker(target, issue)
        if self._underlines:
            self._focus_timer.start()
        _log_spelling_overlay("show_done", underline_count=len(self._underlines))
        return len(self._underlines)

    def _create_marker(self, target, issue: SpellingGuideIssue) -> bool:
        issue.rect = self._issue_rect(target, issue)
        if issue.rect is None:
            _log_spelling_overlay("issue_skipped_no_rect", original=issue.original, start=issue.start, end=issue.end)
            return False
        card = SpellingGuideCard(issue, self.on_replace)
        card.allowed_geometry = self._allowed_overlay_rect(target)
        card.avoidance_rects = self._current_avoidance_rects()
        card.overlay_manager = self
        underline = SpellingUnderline(issue, card)
        card.linked_underline = underline
        underline.setGeometry(
            issue.rect.left(),
            issue.rect.top() + self._underline_window_y_offset(target),
            max(58, issue.rect.width()),
            28,
        )
        underline.show()
        underline.raise_()
        self._cards.append(card)
        self._underlines.append(underline)
        return True

    def _update_marker_geometry(self, target, underline: SpellingUnderline, issue: SpellingGuideIssue) -> bool:
        issue.rect = self._issue_rect(target, issue)
        if issue.rect is None:
            try:
                underline.hide()
                underline.card.hide()
            except Exception:
                pass
            _log_spelling_overlay("marker_temporarily_hidden_no_rect", original=issue.original, start=issue.start, end=issue.end)
            return True
        underline.issue = issue
        underline.card.issue = issue
        underline.card.allowed_geometry = self._allowed_overlay_rect(target)
        underline.card.avoidance_rects = self._current_avoidance_rects()
        underline.setGeometry(
            issue.rect.left(),
            issue.rect.top() + self._underline_window_y_offset(target),
            max(58, issue.rect.width()),
            28,
        )
        if self._underline_occluded_by_visible_card(underline):
            underline.hide()
        else:
            underline.show()
            underline.raise_()
        try:
            if underline.card.isVisible():
                underline.card.raise_()
                underline.card._force_topmost()
        except Exception:
            pass
        underline.update()
        return True

    def refresh_underline_occlusion(self):
        for underline in list(self._underlines):
            try:
                if self._underline_occluded_by_visible_card(underline):
                    underline.hide()
                elif getattr(underline.issue, "rect", None) is not None:
                    underline.show()
                    underline.raise_()
            except Exception:
                continue

    def _underline_occluded_by_visible_card(self, underline: SpellingUnderline) -> bool:
        try:
            underline_rect = underline.frameGeometry().adjusted(-3, -3, 3, 3)
            for card in self._cards:
                if not card.isVisible():
                    continue
                if getattr(card, "linked_underline", None) is underline:
                    continue
                card_rect = card.frameGeometry().adjusted(-8, -8, 8, 8)
                if card_rect.intersects(underline_rect):
                    return True
        except Exception:
            return False
        return False

    def _underline_window_y_offset(self, target) -> int:
        mode = str(getattr(target, "mode", "") or "")
        if mode in {"notepad", "notepad_selection"}:
            return NOTEPAD_UNDERLINE_WINDOW_Y_OFFSET
        return UNDERLINE_WINDOW_Y_OFFSET

    def _dispose_underline(self, underline: SpellingUnderline):
        self._fade_dispose_widget(getattr(underline, "card", None))
        self._fade_dispose_widget(underline)

    def _fade_dispose_widget(self, widget):
        if widget is None:
            return
        try:
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            effect.setOpacity(1.0)
            anim = QPropertyAnimation(effect, b"opacity", widget)
            anim.setDuration(160)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)

            def finish():
                try:
                    widget.hide()
                    widget.deleteLater()
                except Exception:
                    pass

            anim.finished.connect(finish)
            widget._dispose_opacity_anim = anim
            anim.start()
        except Exception:
            try:
                widget.hide()
                widget.deleteLater()
            except Exception:
                pass

    def _issue_key(self, issue: SpellingGuideIssue) -> tuple[str, str, int, int]:
        return (
            str(getattr(issue, "category", "")),
            str(getattr(issue, "original", "")),
            int(getattr(issue, "start", -1)),
            int(getattr(issue, "end", -1)),
        )

    def _same_target_context(self, left, right) -> bool:
        if left is None or right is None:
            return False
        return (
            int(getattr(left, "window_handle", 0) or 0) == int(getattr(right, "window_handle", 0) or 0)
            and self._inspection_scope(left) == self._inspection_scope(right)
        )

    def _looks_like_transient_sync(self, target, previous_text: str, current_text: str, issues: list[SpellingGuideIssue]) -> bool:
        mode = str(getattr(target, "mode", "") or "")
        if mode not in {"word_selection", "notepad_selection"} or issues or not self._underlines:
            return False
        previous_len = len(previous_text or "")
        current_len = len(current_text or "")
        if previous_len < 10:
            return False
        if current_len <= 2:
            return True
        return current_len < max(8, previous_len // 2)

    def _inspection_scope(self, target) -> str:
        mode = str(getattr(target, "mode", "") or "")
        if mode in {"word_selection", "notepad_selection"}:
            return "selection"
        if mode in {"word", "notepad", "hwp", "browser", "browser_extension"}:
            return "full"
        return "unknown"

    def _live_text_for_target(self, target, fallback: str) -> str:
        text = str(fallback or "")
        mode = str(getattr(target, "mode", "") or "") if target is not None else ""
        if mode in {"notepad", "notepad_selection"}:
            hwnd = int(getattr(target, "window_handle", 0) or 0)
            editor = self._best_notepad_editor(hwnd)
            if editor:
                live = self._window_text(editor)
                if live:
                    normalized = live.replace("\r\n", "\n").replace("\r", "\n")
                    return normalized
            live = self._read_notepad_document_text(hwnd)
            if live:
                normalized = live.replace("\r\n", "\n").replace("\r", "\n")
                _log_spelling_overlay("notepad_live_document_text", fallback_len=len(text), live_len=len(normalized), live_sample=normalized[:80])
                return normalized
            return text
        if mode not in {"word", "word_selection"} or pythoncom is None or win32_dynamic is None:
            return text
        try:
            pythoncom.CoInitialize()
            active = pythoncom.GetActiveObject("Word.Application")
            try:
                active = active.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                pass
            word = win32_dynamic.Dispatch(active)
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                return text
            if mode == "word_selection":
                style_info = dict(getattr(target, "style_info", None) or {})
                selection_start = style_info.get("selection_start")
                selection_end = style_info.get("selection_end")
                if selection_start is not None and selection_end is not None and int(selection_end) > int(selection_start):
                    live = str(document.Range(Start=int(selection_start), End=int(selection_end)).Text or "")
                    normalized = live.replace("\r", "\n")
                    _log_spelling_overlay("word_live_selection_text", fallback_len=len(text), live_len=len(normalized), live_sample=normalized[:80])
                    return normalized
            live = str(getattr(document.Content, "Text", "") or "")
            normalized = live.replace("\r", "\n")
            _log_spelling_overlay("word_live_document_text", fallback_len=len(text), live_len=len(normalized), live_sample=normalized[:80])
            return normalized
        except Exception as exc:
            _log_spelling_overlay("word_live_text_failed", mode=mode, error=f"{type(exc).__name__}: {exc}")
            return text

    def _sync_target_visibility(self):
        if win32gui is None or not self._target_hwnd:
            return
        try:
            foreground = int(win32gui.GetForegroundWindow() or 0)
            target_visible = bool(win32gui.IsWindow(self._target_hwnd)) and not bool(win32gui.IsIconic(self._target_hwnd))
            own_foreground = self._is_own_overlay_window(foreground)
            should_show = bool(target_visible and (foreground == self._target_hwnd or own_foreground))
            if should_show and self._hidden_for_target:
                self._set_marker_visibility(True)
                self._hidden_for_target = False
                _log_spelling_overlay("markers_restored", hwnd=self._target_hwnd)
            elif not should_show and not self._hidden_for_target:
                self._set_marker_visibility(False)
                self._hidden_for_target = True
                _log_spelling_overlay("markers_hidden_for_target", hwnd=self._target_hwnd, foreground=foreground, target_visible=target_visible)
            if should_show:
                self._refresh_marker_positions_if_needed()
                self._poll_live_text_if_needed()
        except Exception:
            pass

    def _is_own_overlay_window(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        for widget in [*self._underlines, *self._cards]:
            try:
                if int(widget.winId()) == int(hwnd):
                    return True
            except Exception:
                continue
        try:
            if win32gui is not None:
                title = win32gui.GetWindowText(hwnd) or ""
                class_name = win32gui.GetClassName(hwnd) or ""
                if title in {
                    "Writing Assistant Main Overlay",
                    "Writing Assistant Mini",
                    "Writing Assistant Score",
                    "Writing Assistant Evaluation Reason",
                    "Writing Assistant Summary",
                    "Writing Assistant Title",
                    "Writing Assistant Correction Choice",
                }:
                    return class_name.startswith("Qt") or "QWindow" in class_name
        except Exception:
            pass
        return False

    def _refresh_marker_positions_if_needed(self):
        if self._last_target is None or not self._underlines:
            return
        live_underlines: list[SpellingUnderline] = []
        for underline in list(self._underlines):
            try:
                if self._update_marker_geometry(self._last_target, underline, underline.issue):
                    live_underlines.append(underline)
            except Exception:
                pass
        self._underlines = live_underlines
        self._cards = [underline.card for underline in self._underlines]

    def _poll_live_text_if_needed(self):
        if self._last_target is None or not self._underlines:
            return
        now = time.monotonic()
        if now - self._last_live_poll_at < 0.8:
            return
        self._last_live_poll_at = now
        live_text = self._live_text_for_target(self._last_target, self._last_text)
        if live_text == self._last_text:
            return
        _log_spelling_overlay(
            "live_text_poll_changed",
            previous_len=len(self._last_text or ""),
            live_len=len(live_text or ""),
            live_sample=live_text[:80],
        )
        self.sync_for_target(self._last_target, live_text)

    def _set_marker_visibility(self, visible: bool):
        for underline in self._underlines:
            try:
                underline.setVisible(visible)
            except Exception:
                pass
        if not visible:
            for card in self._cards:
                try:
                    if hasattr(card, "_topmost_timer"):
                        card._topmost_timer.stop()
                    card.hide()
                except Exception:
                    pass

    def _current_avoidance_rects(self) -> list[QRect]:
        provider = self._avoidance_rect_provider
        if provider is None:
            return []
        try:
            return [rect for rect in provider() if isinstance(rect, QRect)]
        except Exception:
            return []

    def _allowed_overlay_rect(self, target) -> QRect | None:
        mode = str(getattr(target, "mode", "") or "")
        hwnd = int(getattr(target, "window_handle", 0) or 0)
        if mode in {"word", "word_selection"}:
            return self._word_document_rect(hwnd) or self._target_client_rect(hwnd)
        if mode in {"notepad", "notepad_selection"}:
            return self._notepad_editor_rect(hwnd) or self._target_client_rect(hwnd)
        return self._target_client_rect(hwnd)

    def _test_issues(self, text: str) -> list[SpellingGuideIssue]:
        specs = [
            (
                ("안녕하요", "안녕하서요", "안녕하소요"),
                "안녕하세요",
                '"{word}"의 철자가 틀렸습니다. "하세요"의 "세" 부분을 잘못 입력한 것으로 보입니다.\n'
                '"하요"/"하서요"라는 표현은 이 문맥에서 사용되지 않으며 "시어요"의 준말로 "세요"가 존재하고 "하-"랑 합쳐져 "하세요"가 된 것입니다.',
            ),
            (
                ("다시 만너요",),
                "다시 만나요",
                '"다시 만너요"의 "ㅓ" 철자가 틀렸습니다. "ㅏ" 철자와 "ㅓ" 철자를 헷갈리셔서 잘못 타이핑한 것으로 보입니다.\n'
                '"만너요"라는 말은 존재하지 않습니다. "만나요"가 "만나다"의 올바른 해요체입니다.',
            ),
            (
                ("싫어해요",),
                "반가워요",
                '문맥적으로 앞에서 "안녕하서요" (안녕하세요)가 나왔는데 "싫어해요"가 나오는 것은 인사의 일반적인 성질과\n'
                '전혀 맞지 않습니다. "싫어해요"가 아닌 "감사해요", "반가워요" 등등이 나와야합니다.',
            ),
        ]
        issues: list[SpellingGuideIssue] = []
        for spec_index, (words, replacement, reason) in enumerate(specs):
            category = "\ubb38\ub9e5" if spec_index == 2 else "\ub9de\ucda4\ubc95"
            matched = False
            for word in words:
                start = text.find(word)
                if start < 0:
                    continue
                matched = True
                _log_spelling_overlay("issue_matched", word=word, start=start, replacement=replacement, category=category)
                issues.append(
                    SpellingGuideIssue(
                        original=word,
                        replacement=replacement,
                        reason=reason.format(word=word),
                        start=start,
                        end=start + len(word),
                        category=category,
                        has_blank_line_above=self._has_blank_line_above(text, start),
                    )
                )
                break
            if not matched:
                _log_spelling_overlay("issue_not_found", candidates=list(words), text_sample=text[:120])
        return issues

    def _has_blank_line_above(self, text: str, start: int) -> bool:
        before = str(text or "")[: max(0, int(start or 0))]
        if "\n" not in before:
            return False
        previous = before.rsplit("\n", 1)[0].rsplit("\n", 1)[-1]
        return previous.strip() == ""

    def _issue_rect(self, target, issue: SpellingGuideIssue) -> QRect | None:
        mode = str(getattr(target, "mode", "") or "")
        hwnd = int(getattr(target, "window_handle", 0) or 0)
        if mode in {"notepad", "notepad_selection"}:
            return self._clamp_notepad_marker_rect(hwnd, self._notepad_rect(hwnd, issue))
        if mode in {"word", "word_selection"}:
            return self._clamp_to_rect(self._word_document_rect(hwnd) or self._target_client_rect(hwnd), self._word_rect(hwnd, issue, target))
        return self._clamp_to_target(hwnd, self._fallback_rect(hwnd, issue))

    def _notepad_rect(self, hwnd: int, issue: SpellingGuideIssue) -> QRect | None:
        if not hwnd:
            return None
        editor = self._best_notepad_editor(hwnd)
        if not editor:
            return self._fallback_rect(hwnd, issue)
        raw_text = self._window_text(editor)
        if not raw_text:
            raw_text = self._read_notepad_document_text(hwnd)
        normalized_text = self._normalize_editor_text(raw_text)
        index_text = normalized_text or self._normalize_editor_text(self._last_text)
        expected_start = max(0, min(int(getattr(issue, "start", 0) or 0), len(index_text)))
        original = str(getattr(issue, "original", "") or "")
        matched_start = expected_start
        if original and index_text[expected_start : expected_start + len(original)] != original:
            nearby_start = index_text.find(original, max(0, expected_start - 8), expected_start + len(original) + 8)
            if nearby_start < 0:
                nearby_start = index_text.find(original)
            if nearby_start >= 0:
                matched_start = nearby_start
            else:
                _log_spelling_overlay(
                    "notepad_issue_text_mismatch",
                    hwnd=hwnd,
                    original=original,
                    expected_start=expected_start,
                    text_at_expected=index_text[expected_start : expected_start + len(original) + 8],
                )
        if raw_text:
            start = self._notepad_index_from_normalized(raw_text, matched_start)
            end = self._notepad_index_from_normalized(raw_text, matched_start + len(original))
            position_start = self._notepad_position_index_from_normalized(raw_text, matched_start)
            position_end = self._notepad_position_index_from_normalized(raw_text, matched_start + len(original))
        else:
            start = matched_start
            end = matched_start + len(original)
            position_start = self._notepad_position_index_from_normalized(index_text, matched_start)
            position_end = self._notepad_position_index_from_normalized(index_text, matched_start + len(original))
        start_point = self._pos_from_char(editor, position_start)
        end_point = self._pos_from_char(editor, position_end)
        if start_point is None:
            _log_spelling_overlay("notepad_rect_fallback_no_start_point", hwnd=hwnd, original=issue.original, start=start)
            return self._fallback_rect(hwnd, issue)
        x, y = start_point
        width = 14 * len(issue.original)
        width_source = "estimate"
        if end_point and abs(end_point[1] - y) <= 3 and end_point[0] > x:
            width = max(28, end_point[0] - x)
            width_source = "end_point"
        else:
            last_point = self._pos_from_char(editor, max(position_start, position_end - 1))
            if last_point and abs(last_point[1] - y) <= 3 and last_point[0] >= x:
                char_count = max(1, len(original))
                average_width = max(12, min(24, round((last_point[0] - x) / max(1, char_count - 1))))
                width = max(width, last_point[0] - x + average_width)
                width_source = "last_char"
        rect = QRect(x, y + NOTEPAD_GUIDE_LINE_Y_OFFSET, width, 8)
        _log_spelling_overlay(
            "notepad_rect",
            hwnd=hwnd,
            editor=editor,
            original=issue.original,
            raw_len=len(raw_text or ""),
            index_len=len(index_text or ""),
            start=start,
            end=end,
            matched_start=matched_start,
            position_start=position_start,
            position_end=position_end,
            start_point=start_point,
            end_point=end_point,
            rect=(rect.left(), rect.top(), rect.width(), rect.height()),
            width_source=width_source,
            marker_line_y=rect.top() + 18,
        )
        return rect

    def _best_notepad_editor(self, hwnd: int) -> int | None:
        if win32gui is None:
            return None
        candidates: list[tuple[int, int, int, int]] = []

        def add_if_text(handle):
            class_name = ""
            try:
                class_name = win32gui.GetClassName(handle) or ""
            except Exception:
                pass
            if any(hint in class_name.lower() for hint in ("edit", "richedit")):
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
                candidates.append((1 if area > 0 else 0, area, len(self._window_text(handle)), int(handle)))

        add_if_text(hwnd)

        def enum_proc(child, _):
            add_if_text(child)
            return True

        try:
            win32gui.EnumChildWindows(hwnd, enum_proc, None)
        except Exception:
            pass
        if not candidates:
            return None
        return max(candidates)[3]

    def _window_text(self, hwnd: int) -> str:
        if not hwnd:
            return ""
        try:
            length = ctypes.windll.user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.addressof(buffer))
            return buffer.value
        except Exception:
            return ""

    def _read_notepad_document_text(self, hwnd: int) -> str:
        try:
            from client.input.notepad_monitor import _read_window_text

            text, _details = _read_window_text(int(hwnd or 0))
            return str(text or "")
        except Exception as exc:
            _log_spelling_overlay("notepad_document_text_failed", hwnd=hwnd, error=f"{type(exc).__name__}: {exc}")
            return ""

    def _pos_from_char(self, hwnd: int, index: int) -> tuple[int, int] | None:
        try:
            class_name = ""
            try:
                class_name = win32gui.GetClassName(hwnd) or "" if win32gui is not None else ""
            except Exception:
                pass
            if any(hint in class_name.lower() for hint in ("richedit", "d2d")):
                point = wintypes.POINT(0, 0)
                result = int(ctypes.windll.user32.SendMessageW(hwnd, EM_POSFROMCHAR, ctypes.addressof(point), int(index)))
                if result >= 0:
                    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
                    return int(point.x), int(point.y)

            result = int(ctypes.windll.user32.SendMessageW(hwnd, EM_POSFROMCHAR, int(index), 0))
            x = result & 0xFFFF
            y = (result >> 16) & 0xFFFF
            if x >= 0x8000:
                x -= 0x10000
            if y >= 0x8000:
                y -= 0x10000
            if x < -30000 or y < -30000:
                return None
            point = wintypes.POINT(x, y)
            ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
            return int(point.x), int(point.y)
        except Exception as exc:
            _log_spelling_overlay("pos_from_char_failed", hwnd=hwnd, index=index, error=f"{type(exc).__name__}: {exc}")
            return None

    def _notepad_index_from_normalized(self, raw_text: str, normalized_index: int) -> int:
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

    def _notepad_position_index_from_normalized(self, raw_text: str, normalized_index: int) -> int:
        raw_index = self._notepad_index_from_normalized(raw_text, normalized_index)
        normalized_prefix = self._normalize_editor_text(raw_text)[: max(0, int(normalized_index or 0))]
        line_breaks_before = normalized_prefix.count("\n")
        position_index = raw_index
        if line_breaks_before > 0 and raw_index == int(normalized_index or 0):
            position_index = raw_index + line_breaks_before
        _log_spelling_overlay(
            "notepad_position_index_v2",
            normalized_index=normalized_index,
            raw_index=raw_index,
            line_breaks_before=line_breaks_before,
            position_index=position_index,
        )
        return position_index

    def _normalize_editor_text(self, text: str) -> str:
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n")

    def _word_rect(self, hwnd: int, issue: SpellingGuideIssue, target) -> QRect | None:
        if pythoncom is None or win32_dynamic is None:
            return self._fallback_rect(hwnd, issue)
        try:
            pythoncom.CoInitialize()
            active = pythoncom.GetActiveObject("Word.Application")
            try:
                active = active.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                pass
            word = win32_dynamic.Dispatch(active)
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                return self._fallback_rect(hwnd, issue)
            style_info = dict(getattr(target, "style_info", None) or {})
            selection_start = style_info.get("selection_start")
            selection_text = str(self._last_text or style_info.get("selection_text") or "")
            mode = str(getattr(target, "mode", "") or "")
            if mode == "word_selection" and selection_start is not None and selection_text:
                raw_index = self._raw_index_from_normalized(selection_text, issue.start)
                range_start = int(selection_start) + raw_index
            else:
                content = document.Content
                raw_text = str(getattr(content, "Text", "") or "")
                raw_index = self._raw_index_from_normalized(raw_text, issue.start)
                if issue.original and raw_text[raw_index : raw_index + len(issue.original)] != issue.original:
                    nearby_index = raw_text.find(issue.original, max(0, raw_index - 12), raw_index + len(issue.original) + 12)
                    if nearby_index >= 0:
                        raw_index = nearby_index
                    else:
                        _log_spelling_overlay(
                            "word_issue_text_mismatch_keep_index",
                            hwnd=hwnd,
                            original=issue.original,
                            issue_start=issue.start,
                            raw_index=raw_index,
                            text_at_index=raw_text[raw_index : raw_index + len(issue.original) + 8],
                        )
                range_start = int(content.Start) + raw_index
            word_range = document.Range(Start=range_start, End=range_start + len(issue.original))
            rect = self._word_range_screen_rect(word, word_range, issue)
            if rect is not None:
                _log_spelling_overlay(
                    "word_rect_get_point",
                    hwnd=hwnd,
                    original=issue.original,
                    issue_start=issue.start,
                    range_start=range_start,
                    rect=(rect.left(), rect.top(), rect.width(), rect.height()),
                )
                return rect
        except Exception as exc:
            _log_spelling_overlay("word_rect_failed", hwnd=hwnd, original=issue.original, error=f"{type(exc).__name__}: {exc}")
        return None

    def _word_range_screen_rect(self, word, word_range, issue: SpellingGuideIssue) -> QRect | None:
        window = getattr(word, "ActiveWindow", None)
        if window is None or pythoncom is None or win32_client is None:
            return None
        try:
            direct_result = None
            try:
                direct_result = window.GetPoint(0, 0, 0, 0, word_range)
                _log_spelling_overlay(
                    "word_get_point_direct_result",
                    original=issue.original,
                    result=repr(direct_result),
                    result_type=type(direct_result).__name__,
                )
                direct_values = self._point_values(direct_result)
                if direct_values is not None:
                    return self._rect_from_point_values(direct_values, issue)
            except Exception as direct_exc:
                _log_spelling_overlay("word_get_point_direct_failed", original=issue.original, error=f"{type(direct_exc).__name__}: {direct_exc}")

            left = win32_client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            top = win32_client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            width = win32_client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            height = win32_client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            result = window.GetPoint(left, top, width, height, word_range)
            _log_spelling_overlay(
                "word_get_point_byref_result",
                original=issue.original,
                result=repr(result),
                values=repr((left.value, top.value, width.value, height.value)),
            )
            if isinstance(result, tuple) and len(result) >= 4:
                values = result[:4]
            else:
                values = (left.value, top.value, width.value, height.value)
            return self._rect_from_point_values(values, issue)
        except Exception as exc:
            _log_spelling_overlay("word_get_point_failed", original=issue.original, error=f"{type(exc).__name__}: {exc}")
            return None

    def _point_values(self, result):
        if isinstance(result, tuple) and len(result) >= 4:
            return result[:4]
        if isinstance(result, list) and len(result) >= 4:
            return result[:4]
        return None

    def _rect_from_point_values(self, values, issue: SpellingGuideIssue) -> QRect | None:
        x, y, w, h = [self._variant_int(v) for v in values[:4]]
        if x <= 0 or y <= 0:
            _log_spelling_overlay("word_get_point_empty", original=issue.original, values=repr(tuple(values)))
            return None
        return QRect(x, y + WORD_GUIDE_LINE_Y_OFFSET, max(34, w), 8)

    def _variant_int(self, value) -> int:
        original_repr = repr(value)
        for _ in range(12):
            if hasattr(value, "value"):
                value = value.value
                continue
            if hasattr(value, "_value"):
                value = value._value
                continue
            if hasattr(value, "Value"):
                value = value.Value
                continue
            break
        try:
            return int(value or 0)
        except Exception as exc:
            _log_spelling_overlay("variant_int_failed", original=original_repr, final=repr(value), error=f"{type(exc).__name__}: {exc}")
            return 0

    def _raw_index_from_normalized(self, raw_text: str, normalized_index: int) -> int:
        raw_index = 0
        normalized_count = 0
        while raw_index < len(raw_text) and normalized_count < normalized_index:
            char = raw_text[raw_index]
            raw_index += 1
            normalized_count += 1 if char != "\r" else 1
        return raw_index

    def _fallback_rect(self, hwnd: int, issue: SpellingGuideIssue) -> QRect | None:
        if win32gui is None or not hwnd:
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return None
        before = self._last_text[: issue.start]
        line = before.count("\n")
        column = len(before.rsplit("\n", 1)[-1])
        x = left + 82 + min(520, column * 12)
        y = top + 118 + line * 25
        width = max(34, len(issue.original) * 15)
        return QRect(x, y, width, 8)

    def _target_client_rect(self, hwnd: int) -> QRect | None:
        if win32gui is None or not hwnd:
            return None
        try:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            top_left = win32gui.ClientToScreen(hwnd, (left, top))
            bottom_right = win32gui.ClientToScreen(hwnd, (right, bottom))
            return QRect(top_left[0], top_left[1], max(1, bottom_right[0] - top_left[0]), max(1, bottom_right[1] - top_left[1]))
        except Exception:
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return QRect(left, top, max(1, right - left), max(1, bottom - top))
            except Exception:
                return None

    def _word_document_rect(self, hwnd: int) -> QRect | None:
        if win32gui is None or not hwnd:
            return None
        rects: list[QRect] = []

        def add_if_document(handle):
            try:
                class_name = win32gui.GetClassName(handle) or ""
                if not class_name.startswith("_Ww"):
                    return
                left, top, right, bottom = win32gui.GetWindowRect(handle)
                rect = QRect(left, top, max(1, right - left), max(1, bottom - top))
                if rect.width() >= 240 and rect.height() >= 160:
                    rects.append(rect)
            except Exception:
                return

        def enum_proc(child, _):
            add_if_document(child)
            return True

        try:
            win32gui.EnumChildWindows(hwnd, enum_proc, None)
        except Exception:
            pass
        if not rects:
            return None
        rect = max(rects, key=lambda item: item.width() * item.height())
        _log_spelling_overlay("word_document_rect", hwnd=hwnd, rect=(rect.left(), rect.top(), rect.width(), rect.height()))
        return rect

    def _clamp_to_target(self, hwnd: int, rect: QRect | None) -> QRect | None:
        return self._clamp_to_rect(self._target_client_rect(hwnd), rect)

    def _clamp_notepad_marker_rect(self, hwnd: int, rect: QRect | None) -> QRect | None:
        if rect is None:
            return None
        editor_rect = self._notepad_editor_rect(hwnd) or self._window_rect(hwnd)
        if editor_rect is None:
            return rect
        marker_top_allowance = 32
        allowed_rect = QRect(
            editor_rect.left(),
            editor_rect.top() - marker_top_allowance,
            editor_rect.width(),
            editor_rect.height() + marker_top_allowance,
        )
        window_rect = self._window_rect(hwnd)
        if window_rect is not None:
            allowed_rect = allowed_rect.intersected(window_rect)
        margin = 6
        if rect.bottom() < allowed_rect.top() or rect.top() > allowed_rect.bottom() or rect.right() < allowed_rect.left() or rect.left() > allowed_rect.right():
            _log_spelling_overlay(
                "notepad_rect_rejected_outside_window",
                rect=(rect.left(), rect.top(), rect.width(), rect.height()),
                target=(allowed_rect.left(), allowed_rect.top(), allowed_rect.width(), allowed_rect.height()),
            )
            return None
        min_x = allowed_rect.left() + margin
        max_x = allowed_rect.right() - rect.width() - margin
        if max_x < min_x:
            return rect
        x = min(max(rect.left(), min_x), max_x)
        min_y = allowed_rect.top() + margin
        max_y = max(min_y, allowed_rect.bottom() - rect.height() - margin)
        y = min(max(rect.top(), min_y), max_y)
        return QRect(x, y, rect.width(), rect.height())

    def _notepad_editor_rect(self, hwnd: int) -> QRect | None:
        editor = self._best_notepad_editor(hwnd)
        if not editor or win32gui is None:
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(editor)
            return QRect(left, top, max(1, right - left), max(1, bottom - top))
        except Exception:
            return None

    def _window_rect(self, hwnd: int) -> QRect | None:
        if win32gui is None or not hwnd:
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return QRect(left, top, max(1, right - left), max(1, bottom - top))
        except Exception:
            return None

    def _clamp_to_rect(self, target: QRect | None, rect: QRect | None) -> QRect | None:
        if rect is None:
            return None
        if target is None:
            return rect
        margin = 8
        if rect.bottom() < target.top() or rect.top() > target.bottom() or rect.right() < target.left() or rect.left() > target.right():
            _log_spelling_overlay(
                "rect_rejected_outside_target",
                rect=(rect.left(), rect.top(), rect.width(), rect.height()),
                target=(target.left(), target.top(), target.width(), target.height()),
            )
            return None
        min_x = target.left() + margin
        min_y = target.top() + margin
        max_x = target.right() - rect.width() - margin
        max_y = target.bottom() - rect.height() - margin
        if max_x < min_x or max_y < min_y:
            return None
        x = min(max(rect.left(), min_x), max_x)
        y = min(max(rect.top(), min_y), max_y)
        return QRect(x, y, rect.width(), rect.height())

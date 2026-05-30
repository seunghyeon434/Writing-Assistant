from __future__ import annotations

from pathlib import Path
import math
import traceback

from PyQt5.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
import time
from PyQt5.QtGui import QColor, QCursor, QFont, QIcon, QPainter
from PyQt5.QtWidgets import QApplication, QBoxLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

try:
    import win32gui
except Exception:  # pragma: no cover - optional Windows dependency
    win32gui = None

_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_MINI_OVERLAY_LOG_PATH = _LOG_DIR / "mini_overlay.log"




class BusySpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._step = 0
        self.setFixedSize(30, 30)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_step(self, step):
        self._step = int(step or 0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        radius = 10
        active = self._step % 8
        for i in range(8):
            distance = abs(i - active)
            distance = min(distance, 8 - distance)
            pulse = max(0.0, 1.0 - (distance / 3.0))
            dot_radius = 2.2 + (3.2 * pulse)
            alpha = int(85 + (170 * pulse))
            color = QColor(184, 106, 60, min(255, alpha))
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            angle = (i * 45) * math.pi / 180.0
            x = center.x() + radius * math.cos(angle)
            y = center.y() + radius * math.sin(angle)
            size = int(round(dot_radius * 2))
            painter.drawEllipse(int(round(x - dot_radius)), int(round(y - dot_radius)), size, size)

class TonePrompt(QWidget):
    submitted = pyqtSignal(str)
    favorite_list_requested = pyqtSignal()
    favorite_delete_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Tone")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._default_size = (390, 156)
        self._favorite_size = (585, 234)
        self.setFixedSize(*self._default_size)
        self._favorites = []
        self._favorite_delete_mode = False
        self._favorites_enabled = False
        self._last_window_handle = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("tonePromptCard")
        root.addWidget(self.card)

        self.setStyleSheet(
            """
            QFrame#tonePromptCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#tonePromptTitle {
                color: #2f241f;
                font-weight: 800;
                font-size: 14px;
            }
            QLabel#tonePromptGuide {
                color: #4a382f;
                font-size: 12px;
            }
            QLineEdit#tonePromptInput {
                background: #fffaf4;
                border: 1px solid #dccbbb;
                border-radius: 12px;
                padding: 7px 10px;
                color: #2f241f;
                selection-background-color: #d8a27a;
            }
            QPushButton {
                border: 0;
                border-radius: 13px;
                padding: 6px 12px;
                background: #e8d4bf;
                color: #3f2f26;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #dcc1a7;
            }
            QPushButton#tonePromptSubmit {
                background: #b86a3c;
                color: #fff8f2;
                min-width: 38px;
                max-width: 38px;
            }
            QPushButton#tonePromptClose {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                padding: 0;
                background: #ead7cf;
            }
            QPushButton#toneFavoriteButton {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                padding: 0;
                background: #ead7cf;
            }
            QFrame#toneFavoriteCover {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#toneFavoriteTitle {
                color: #2f241f;
                font-weight: 900;
                font-size: 13px;
            }
            QPushButton#toneFavoriteItem {
                text-align: left;
                padding: 5px 8px;
                border-radius: 10px;
                background: #fffaf4;
                color: #3f2f26;
                font-size: 11px;
            }
            QPushButton#toneFavoriteItem:hover {
                background: #ecd8c5;
            }
            QPushButton#toneFavoriteItem:disabled {
                color: #aa9a8b;
                background: #f1e8dd;
            }
            QPushButton#toneFavoriteDeleteItem {
                min-width: 20px;
                max-width: 20px;
                min-height: 24px;
                max-height: 24px;
                border-radius: 10px;
                padding: 0;
                border: 1px solid #c94b42;
                background: #ffffff;
                color: #6b3528;
                font-size: 10px;
                font-weight: 400;
            }
            QPushButton#toneFavoriteDeleteToggle {
                min-width: 42px;
                max-width: 42px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                padding: 0;
                border: 1px solid #c94b42;
                background: #ffffff;
                color: #6b3528;
                font-size: 11px;
                font-weight: 400;
            }
            """
        )

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.title_label = QLabel("\ubb38\uccb4 \uc785\ub825")
        self.title_label.setObjectName("tonePromptTitle")
        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("tonePromptClose")
        self.close_btn.setToolTip("\ub2eb\uae30")
        self.favorite_btn = QPushButton("")
        self.favorite_btn.setObjectName("toneFavoriteButton")
        self.favorite_btn.setToolTip("\uc990\uaca8\ucc3e\uae30")
        star_icon_path = Path(__file__).resolve().parent.parent / "icon" / "star.png"
        if not star_icon_path.exists():
            star_icon_path = Path(__file__).resolve().parent.parent / "icon" / "star.svg"
        if star_icon_path.exists():
            self.favorite_btn.setIcon(QIcon(str(star_icon_path)))
            self.favorite_btn.setIconSize(QSize(16, 16))
        else:
            self.favorite_btn.setText("*")
        top.addWidget(self.title_label, 1)
        top.addWidget(self.favorite_btn, 0, Qt.AlignRight)
        top.addWidget(self.close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)

        guide = QLabel("\uc6d0\ud558\ub294 \ubb38\uccb4/\ub9d0\ud22c\ub97c \uc785\ub825\ud574\uc8fc\uc138\uc694.")
        guide.setObjectName("tonePromptGuide")
        layout.addWidget(guide)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setObjectName("tonePromptInput")
        self.input.setPlaceholderText("\uc608: \ubd80\ub4dc\ub7fd\uace0 \uc815\uc911\ud558\uac8c")
        self.submit_btn = QPushButton("")
        self.submit_btn.setObjectName("tonePromptSubmit")
        self.submit_btn.setToolTip("\ubb38\uccb4 \ubcc0\uacbd \uc801\uc6a9")
        forward_icon_path = Path(__file__).resolve().parent.parent / "icon" / "forward.png"
        if forward_icon_path.exists():
            self.submit_btn.setIcon(QIcon(str(forward_icon_path)))
            self.submit_btn.setIconSize(QSize(16, 16))
        row.addWidget(self.input, 1)
        row.addWidget(self.submit_btn, 0)
        layout.addLayout(row)

        self.close_btn.clicked.connect(self.hide)
        self.favorite_btn.clicked.connect(self.request_favorites)
        self.submit_btn.clicked.connect(self._submit)
        self.input.returnPressed.connect(self._submit)
        self._build_favorite_cover()

    def _build_favorite_cover(self):
        self.favorite_cover = QFrame(self.card)
        self.favorite_cover.setObjectName("toneFavoriteCover")
        self.favorite_cover.hide()
        layout = QVBoxLayout(self.favorite_cover)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("\uc990\uaca8\ucc3e\uae30")
        title.setObjectName("toneFavoriteTitle")
        self.favorite_delete_toggle = QPushButton("\uc0ad\uc81c")
        self.favorite_delete_toggle.setObjectName("toneFavoriteDeleteToggle")
        self.favorite_close_btn = QPushButton("X")
        self.favorite_close_btn.setObjectName("tonePromptClose")
        self.favorite_close_btn.setToolTip("\ub2eb\uae30")
        top.addWidget(title, 1)
        top.addWidget(self.favorite_delete_toggle, 0, Qt.AlignRight)
        top.addWidget(self.favorite_close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)

        grid = QGridLayout()
        grid.setSpacing(6)
        self.favorite_item_buttons = []
        self.favorite_delete_buttons = []
        for index in range(10):
            cell = QFrame(self.favorite_cover)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(3)
            item_btn = QPushButton("")
            item_btn.setObjectName("toneFavoriteItem")
            delete_btn = QPushButton("X")
            delete_btn.setObjectName("toneFavoriteDeleteItem")
            delete_btn.hide()
            item_btn.clicked.connect(lambda _checked=False, i=index: self._select_favorite(i))
            delete_btn.clicked.connect(lambda _checked=False, i=index: self._delete_favorite(i))
            cell_layout.addWidget(item_btn, 1)
            cell_layout.addWidget(delete_btn, 0)
            grid.addWidget(cell, index // 2, index % 2)
            self.favorite_item_buttons.append(item_btn)
            self.favorite_delete_buttons.append(delete_btn)
        layout.addLayout(grid, 1)
        self.favorite_close_btn.clicked.connect(self.hide_favorites)
        self.favorite_delete_toggle.clicked.connect(self.toggle_favorite_delete_mode)
        self._refresh_favorite_buttons()

    def set_favorites(self, favorites):
        clean = []
        for item in favorites or []:
            if isinstance(item, dict):
                favorite_id = item.get("id")
                tone = str(item.get("tone") or "").strip()
            else:
                favorite_id = None
                tone = str(item or "").strip()
            if tone:
                clean.append({"id": favorite_id, "tone": tone})
        self._favorites = clean[:10]
        self._refresh_favorite_buttons()

    def favorite_tones(self):
        return [str(item.get("tone") or "") for item in self._favorites if str(item.get("tone") or "").strip()]

    def show_favorites(self):
        self._favorite_delete_mode = False
        self.setFixedSize(*self._favorite_size)
        self.resize(*self._favorite_size)
        self._move_to_target_center(self._last_window_handle)
        self.favorite_cover.setGeometry(self.card.rect())
        self._refresh_favorite_buttons()
        self.favorite_cover.show()
        self.favorite_cover.raise_()

    def request_favorites(self):
        if not self._favorites_enabled:
            return
        self.favorite_list_requested.emit()
        QTimer.singleShot(80, self.show_favorites)

    def hide_favorites(self):
        self._favorite_delete_mode = False
        self.favorite_cover.hide()
        self.setFixedSize(*self._default_size)
        self.resize(*self._default_size)
        self._move_to_target_center(self._last_window_handle)
        self._refresh_favorite_buttons()

    def set_favorites_enabled(self, enabled):
        self._favorites_enabled = bool(enabled)
        if hasattr(self, "favorite_btn"):
            self.favorite_btn.setEnabled(self._favorites_enabled)
            self.favorite_btn.setToolTip("\uc990\uaca8\ucc3e\uae30" if self._favorites_enabled else "\ub85c\uadf8\uc778 \ud6c4 \uc0ac\uc6a9")
        if not self._favorites_enabled and hasattr(self, "favorite_cover") and self.favorite_cover.isVisible():
            self.hide_favorites()

    def toggle_favorite_delete_mode(self):
        self._favorite_delete_mode = not self._favorite_delete_mode
        self._refresh_favorite_buttons()

    def _refresh_favorite_buttons(self):
        if not hasattr(self, "favorite_item_buttons"):
            return
        for index, item_btn in enumerate(self.favorite_item_buttons):
            favorite = self._favorites[index] if index < len(self._favorites) else None
            delete_btn = self.favorite_delete_buttons[index]
            if favorite:
                tone = str(favorite.get("tone") or "")
                item_btn.setText(tone)
                item_btn.setToolTip(tone)
                item_btn.setEnabled(not self._favorite_delete_mode)
                delete_btn.setVisible(self._favorite_delete_mode)
                delete_btn.setEnabled(bool(favorite.get("id")))
            else:
                item_btn.setText("\ube44\uc5b4 \uc788\uc74c")
                item_btn.setToolTip("")
                item_btn.setEnabled(False)
                delete_btn.hide()
        if hasattr(self, "favorite_delete_toggle"):
            self.favorite_delete_toggle.setText("\uc644\ub8cc" if self._favorite_delete_mode else "\uc0ad\uc81c")

    def _select_favorite(self, index):
        if index >= len(self._favorites) or self._favorite_delete_mode:
            return
        tone = str(self._favorites[index].get("tone") or "").strip()
        if not tone:
            return
        self.input.setText(tone)
        self.hide()
        QApplication.processEvents()
        self.submitted.emit(tone)

    def _delete_favorite(self, index):
        if index >= len(self._favorites):
            return
        favorite_id = self._favorites[index].get("id")
        if favorite_id is None:
            return
        self.favorite_delete_requested.emit(int(favorite_id))

    def show_for_window(self, window_handle=None):
        self.hide_favorites()
        self.setFixedSize(*self._default_size)
        self._last_window_handle = window_handle
        self._move_to_target_center(window_handle)
        self.input.selectAll()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus(Qt.OtherFocusReason)

    def _move_to_target_center(self, window_handle=None):
        rect = self._target_rect(window_handle)
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if rect is not None:
            left, top, right, bottom = rect
            x = left + max(0, (right - left - self.width()) // 2)
            y = top + max(0, (bottom - top - self.height()) // 2)
            self.move(x, y)
        elif screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.left() + max(0, (available.width() - self.width()) // 2),
                available.top() + max(0, (available.height() - self.height()) // 2),
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "favorite_cover") and self.favorite_cover.isVisible():
            self.favorite_cover.setGeometry(self.card.rect())

    def _submit(self):
        value = self.input.text().strip()
        if not value:
            self.input.setFocus(Qt.OtherFocusReason)
            return
        self.hide()
        QApplication.processEvents()
        self.submitted.emit(value)

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            left, top, right, bottom = win32gui.GetWindowRect(root)
            if right - left <= self.width() + 24 or bottom - top <= self.height() + 24:
                return None
            return left, top, right, bottom
        except Exception:
            return None


class ToneFavoriteConfirmOverlay(QWidget):
    accepted = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Tone Favorite")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(340, 158)
        self._tone = ""
        self.setStyleSheet(
            """
            QFrame#toneFavoriteConfirmCard { background: #f7efe5; border: 1px solid #dccbbb; border-radius: 18px; }
            QLabel#toneFavoriteConfirmTitle { color: #2f241f; font-size: 14px; font-weight: 900; }
            QLabel#toneFavoriteConfirmText { background: #fffaf4; border: 1px solid #dccbbb; border-radius: 12px; color: #2f241f; padding: 9px; font-size: 13px; font-weight: 850; }
            QPushButton#toneFavoriteConfirmYes { border: 0; border-radius: 13px; padding: 7px 18px; background: #b86a3c; color: #fff8f2; font-size: 13px; font-weight: 900; }
            QPushButton#toneFavoriteConfirmYes:hover { background: #9f5730; }
            QPushButton#toneFavoriteConfirmNo, QPushButton#toneFavoriteConfirmClose { border: 0; border-radius: 13px; padding: 7px 18px; background: #ead7cf; color: #3f2f26; font-size: 13px; font-weight: 900; }
            QPushButton#toneFavoriteConfirmNo:hover, QPushButton#toneFavoriteConfirmClose:hover { background: #dcc1a7; }
            QPushButton#toneFavoriteConfirmClose { min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; padding: 0; }
            """
        )
        card = QFrame(self)
        card.setObjectName("toneFavoriteConfirmCard")
        card.setGeometry(0, 0, 340, 158)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel("\ubb38\uccb4 \uc990\uaca8\ucc3e\uae30")
        title.setObjectName("toneFavoriteConfirmTitle")
        close_btn = QPushButton("X")
        close_btn.setObjectName("toneFavoriteConfirmClose")
        close_btn.clicked.connect(self.hide)
        top.addWidget(title, 1)
        top.addWidget(close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)
        self.tone_label = QLabel("")
        self.tone_label.setObjectName("toneFavoriteConfirmText")
        self.tone_label.setAlignment(Qt.AlignCenter)
        self.tone_label.setWordWrap(True)
        layout.addWidget(self.tone_label, 1)
        row = QHBoxLayout()
        yes_btn = QPushButton("\uc608")
        yes_btn.setObjectName("toneFavoriteConfirmYes")
        no_btn = QPushButton("\uc544\ub2c8\uc694")
        no_btn.setObjectName("toneFavoriteConfirmNo")
        yes_btn.clicked.connect(self._accept)
        no_btn.clicked.connect(self.hide)
        row.addStretch(1)
        row.addWidget(yes_btn)
        row.addWidget(no_btn)
        row.addStretch(1)
        layout.addLayout(row)

    def show_for_window(self, window_handle, tone):
        self._tone = str(tone or "").strip()
        self.tone_label.setText(f"{self._tone}\n\uc990\uaca8\ucc3e\uae30\uc5d0 \ub4f1\ub85d\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?")
        rect = self._target_rect(window_handle)
        if rect is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                self.show()
                self.raise_()
                return
            geo = screen.availableGeometry()
            left, top, right, bottom = geo.left(), geo.top(), geo.right(), geo.bottom()
        else:
            left, top, right, bottom = rect
        x = left + max(0, (right - left - self.width()) // 2)
        y = top + max(0, (bottom - top - self.height()) // 2)
        self.move(x, y)
        self.show()
        self.raise_()

    def _accept(self):
        tone = self._tone.strip()
        self.hide()
        if tone:
            self.accepted.emit(tone)

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            return win32gui.GetWindowRect(root)
        except Exception:
            return None



class CorrectionChoiceOverlay(QWidget):
    spelling_requested = pyqtSignal()
    tone_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Correction Choice")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(330, 126)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("choiceCard")
        root.addWidget(self.card)
        self.setStyleSheet(
            """
            QFrame#choiceCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#choiceTitle {
                color: #2f241f;
                font-size: 14px;
                font-weight: 900;
            }
            QPushButton {
                border: 0;
                border-radius: 14px;
                padding: 7px 16px;
                background: #b86a3c;
                color: #fff8f2;
                font-weight: 900;
            }
            QPushButton:hover { background: #9f5730; }
            QPushButton#choiceClose {
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                border-radius: 12px;
                padding: 0;
                background: #ead7cf;
                color: #2f241f;
            }
            """
        )
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(12)
        top = QHBoxLayout()
        self.title_label = QLabel("\uae30\ub2a5\uc744 \uc120\ud0dd\ud574\uc8fc\uc138\uc694.")
        self.title_label.setObjectName("choiceTitle")
        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("choiceClose")
        self.close_btn.clicked.connect(self.hide)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.spelling_btn = QPushButton("\ub9de\ucda4\ubc95")
        self.tone_btn = QPushButton("\ubb38\uccb4")
        self.spelling_btn.clicked.connect(self._emit_spelling)
        self.tone_btn.clicked.connect(self._emit_tone)
        actions.addWidget(self.spelling_btn)
        actions.addWidget(self.tone_btn)
        layout.addLayout(actions)

    def show_for_window(self, window_handle=None):
        rect = self._target_rect(window_handle)
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if rect is not None:
            left, top, right, bottom = rect
            x = left + max(0, (right - left - self.width()) // 2)
            y = top + max(0, (bottom - top - self.height()) // 2)
            self.move(x, y)
        elif screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.left() + max(0, (available.width() - self.width()) // 2),
                available.top() + max(0, (available.height() - self.height()) // 2),
            )
        self.show()
        self.raise_()

    def show_below_geometry(self, geometry, window_handle=None, gap=8):
        rect = self._target_rect(window_handle)
        if rect is not None:
            left, top, right, bottom = rect
        else:
            cursor = QCursor.pos()
            screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                left, top, right, bottom = available.left(), available.top(), available.right(), available.bottom()
            else:
                left, top, right, bottom = 0, 0, 9999, 9999
        x = geometry.left() + max(0, (geometry.width() - self.width()) // 2)
        y = geometry.bottom() + gap
        margin = 12
        x = min(max(x, left + margin), right - self.width() - margin)
        y = min(max(y, top + margin), bottom - self.height() - margin)
        self.move(x, y)
        self.show()
        self.raise_()

    def _emit_spelling(self):
        self.hide()
        self.spelling_requested.emit()

    def _emit_tone(self):
        self.hide()
        self.tone_requested.emit()

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            left, top, right, bottom = win32gui.GetWindowRect(root)
            if right - left <= self.width() + 24 or bottom - top <= self.height() + 24:
                return None
            return left, top, right, bottom
        except Exception:
            return None


class MiniOverlay(QWidget):
    overlay_moved = pyqtSignal(str, object)
    choice_spelling_requested = pyqtSignal()
    choice_tone_requested = pyqtSignal()
    apply_clicked = pyqtSignal()
    apply_pressed = pyqtSignal()
    open_clicked = pyqtSignal()
    undo_clicked = pyqtSignal()
    redo_clicked = pyqtSignal()
    tone_submitted = pyqtSignal(str)
    tone_requested = pyqtSignal()
    tone_favorite_list_requested = pyqtSignal()
    tone_favorite_add_requested = pyqtSignal(str)
    tone_favorite_delete_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Mini")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(386, 132)
        self._expanded_size = (386, 132)
        self._expanded_card_size = (386, 94)
        self._compact_notepad_size = (98, 104)
        self._compact_notepad_card_size = (98, 104)
        self._collapsed_size = (68, 68)
        self._last_window_handle = None
        self._last_reader_name = ""
        self._overlay_state = None
        self._state_before_status = None
        self._status_until = 0.0
        self._collapsed = False
        self._focus_guard_timer = QTimer(self)
        self._focus_guard_timer.setInterval(80)
        self._focus_guard_timer.timeout.connect(self._hide_if_target_lost_focus)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._restore_after_status)
        self._focus_guard_suspended_until = 0.0
        self._pending_hide_reason = "direct"
        self._movable_mode = False
        self._manual_position = None
        self._manual_offset = None
        self._avoidance_rect_provider = None
        self._dragging_overlay = False
        self._drag_offset = QPoint()
        self._drag_start_pos = QPoint()
        self._drag_moved = False
        self._suppress_action_until = 0.0
        self._suppress_collapsed_expand_until = 0.0
        self._notepad_button_mode = False
        self._busy_message = ""
        self._busy_step = 0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(120)
        self._busy_timer.timeout.connect(self._tick_busy_overlay)
        self.tone_prompt = TonePrompt()
        self.tone_prompt.submitted.connect(self.tone_submitted.emit)
        self.tone_prompt.favorite_list_requested.connect(self.tone_favorite_list_requested.emit)
        self.tone_prompt.favorite_delete_requested.connect(self.tone_favorite_delete_requested.emit)
        self.tone_favorite_confirm = ToneFavoriteConfirmOverlay()
        self.tone_favorite_confirm.accepted.connect(self.tone_favorite_add_requested.emit)
        self.choice_overlay = CorrectionChoiceOverlay()
        self.choice_overlay.spelling_requested.connect(self.choice_spelling_requested.emit)
        self.choice_overlay.tone_requested.connect(self.choice_tone_requested.emit)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("miniCard")
        self.setStyleSheet(
            """
            QFrame#miniCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#titleLabel {
                color: #2f241f;
                font-weight: 700;
                font-size: 13px;
            }
            QLabel#hintLabel {
                color: #7b6658;
                font-size: 11px;
            }
            QPushButton {
                border: 0;
                border-radius: 13px;
                padding: 6px 13px;
                background: #b86a3c;
                color: #fff8f2;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #9f5730;
            }
            QPushButton#applyButton {
                background: #b86a3c;
                color: #fff8f2;
            }
            QPushButton#applyButton:hover {
                background: #9f5730;
            }
            QPushButton#applyButton:disabled {
                background: #c9c9c9;
                color: #f7f7f7;
            }
            QPushButton#applyButton:disabled:hover {
                background: #c9c9c9;
            }
            QPushButton#undoButton, QPushButton#redoButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                border-radius: 16px;
                padding: 0;
                background: #e8d4bf;
            }
            QPushButton#undoButton:hover, QPushButton#redoButton:hover {
                background: #dcc1a7;
            }
            QPushButton#undoButton:disabled, QPushButton#redoButton:disabled {
                background: #d6d6d6;
            }
            QPushButton#closeButton {
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                border-radius: 12px;
                padding: 0;
                background: #ead7cf;
                color: #2f241f;
                font-weight: 900;
            }
            QPushButton#collapsedButton {
                border-radius: 28px;
                background: #b86a3c;
                color: #fff8f2;
                font-size: 12px;
                font-weight: 800;
                padding: 0;
            }
            QPushButton#collapsedButton:hover {
                background: #9f5730;
            }
            """
        )
        self.undo_btn = QPushButton("")
        self.undo_btn.setObjectName("undoButton")
        self.undo_btn.setToolTip("\ub418\ub3cc\ub9ac\uae30")
        back_icon_path = Path(__file__).resolve().parent.parent / "icon" / "back.png"
        if back_icon_path.exists():
            self.undo_btn.setIcon(QIcon(str(back_icon_path)))
            self.undo_btn.setIconSize(QSize(17, 17))
        else:
            self.undo_btn.setText("\u21b6")
        self.undo_btn.setEnabled(False)

        self.redo_btn = QPushButton("")
        self.redo_btn.setObjectName("redoButton")
        self.redo_btn.setToolTip("\uc7ac\uc2e4\ud589")
        forward_icon_path = Path(__file__).resolve().parent.parent / "icon" / "forward.png"
        if forward_icon_path.exists():
            self.redo_btn.setIcon(QIcon(str(forward_icon_path)))
            self.redo_btn.setIconSize(QSize(17, 17))
        else:
            self.redo_btn.setText("\u21b7")
        self.redo_btn.setEnabled(False)

        self.floating_actions = QHBoxLayout()
        self.floating_actions.setContentsMargins(16, 0, 0, 0)
        self.floating_actions.setSpacing(6)
        self.floating_actions.addWidget(self.undo_btn, 0, Qt.AlignLeft | Qt.AlignBottom)
        self.floating_actions.addWidget(self.redo_btn, 0, Qt.AlignLeft | Qt.AlignBottom)
        self.floating_actions.addStretch(1)
        root.addLayout(self.floating_actions)
        root.addWidget(self.card)

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(14, 10, 14, 12)
        self.card_layout.setSpacing(7)

        self.top_layout = QHBoxLayout()
        self.top_layout.setSpacing(6)
        self.title_label = QLabel("\uc120\ud0dd \uc601\uc5ed \uc778\uc2dd\ub428")
        self.title_label.setObjectName("titleLabel")
        self.hint_label = QLabel("Ctrl+Alt+Enter")
        self.hint_label.setObjectName("hintLabel")
        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setToolTip("\uc811\uae30")
        self.top_layout.addWidget(self.title_label, 1)
        self.top_layout.addWidget(self.hint_label, 0, Qt.AlignRight)
        self.top_layout.addWidget(self.close_btn, 0, Qt.AlignRight)
        self.card_layout.addLayout(self.top_layout)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(8)
        self.apply_btn = QPushButton("\ub9de\ucda4\ubc95")
        self.apply_btn.setObjectName("applyButton")
        self.tone_btn = QPushButton("\ubb38\uccb4")
        self.tone_btn.setObjectName("toneButton")
        self.tone_btn.setToolTip("\ubb38\uccb4 \ubcc0\uacbd")
        self.open_btn = QPushButton("\uc5f4\uae30")
        self._default_action_font = QFont(self.apply_btn.font())
        self.apply_btn.setEnabled(True)
        self.apply_btn.pressed.connect(self._handle_apply_pressed)
        self.undo_btn.clicked.connect(self.undo_clicked.emit)
        self.redo_btn.clicked.connect(self.redo_clicked.emit)
        self.tone_btn.clicked.connect(self.tone_requested.emit)
        self.open_btn.clicked.connect(self.open_clicked.emit)
        self.close_btn.clicked.connect(self.collapse)
        self.actions_layout.addWidget(self.apply_btn)
        self.actions_layout.addWidget(self.tone_btn)
        self.actions_layout.addWidget(self.open_btn)
        self.card_layout.addLayout(self.actions_layout)

        self.collapsed_btn = QPushButton("\uc778\uc2dd\nON", self.card)
        self.collapsed_btn.setObjectName("collapsedButton")
        self.collapsed_btn.setToolTip("\ub4dc\ub798\uadf8 \uc624\ubc84\ub808\uc774 \ud3bc\uce58\uae30")
        self.collapsed_btn.clicked.connect(self._handle_collapsed_clicked)
        self.collapsed_btn.hide()
        for widget in (self.card, self.collapsed_btn, self.undo_btn, self.redo_btn, self.title_label, self.hint_label, self.apply_btn, self.tone_btn, self.open_btn, self.close_btn):
            widget.installEventFilter(self)
        self._build_busy_cover()


    def _build_busy_cover(self):
        self.busy_cover = QFrame(self.card)
        self.busy_cover.setObjectName("busyCover")
        self.busy_cover.setStyleSheet(
            """
            QFrame#busyCover {
                background: rgba(247, 239, 229, 235);
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#busySpinner {
                color: #b86a3c;
                font-size: 26px;
                font-weight: 900;
            }
            QLabel#busyText {
                color: #2f241f;
                font-size: 14px;
                font-weight: 900;
            }
            """
        )
        layout = QVBoxLayout(self.busy_cover)
        layout.setContentsMargins(0, 8, 0, 16)
        layout.setSpacing(2)
        self.busy_spinner_label = BusySpinner(self.busy_cover)
        self.busy_text_label = QLabel("")
        self.busy_text_label.setObjectName("busyText")
        self.busy_text_label.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(self.busy_spinner_label, 0, Qt.AlignCenter)
        layout.addWidget(self.busy_text_label, 0, Qt.AlignCenter)
        layout.addStretch(2)
        self.busy_cover.hide()

    def show_busy(self, message):
        if self._collapsed or not hasattr(self, "busy_cover"):
            return
        self._busy_message = str(message or "")
        self._busy_step = 0
        self.busy_cover.setGeometry(self.card.rect())
        self._tick_busy_overlay()
        self.busy_cover.show()
        self.busy_cover.raise_()
        self.busy_cover.repaint()
        self.repaint()
        QApplication.processEvents()
        self._busy_timer.start()

    def hide_busy(self):
        if hasattr(self, "busy_cover"):
            self.busy_cover.hide()
        self._busy_timer.stop()

    def _tick_busy_overlay(self):
        dots = "." * (self._busy_step % 4)
        if hasattr(self, "busy_spinner_label"):
            self.busy_spinner_label.set_step(self._busy_step)
        if hasattr(self, "busy_text_label"):
            self.busy_text_label.setText(f"{self._busy_message}{dots}")
        self._busy_step += 1


    def set_undo_available(self, available: bool):
        self.undo_btn.setEnabled(bool(available))

    def set_redo_available(self, available: bool):
        self.redo_btn.setEnabled(bool(available))

    def set_movable_mode(self, enabled: bool):
        enabled = bool(enabled)
        if self._movable_mode == enabled:
            return
        self._movable_mode = enabled
        if not enabled:
            self._manual_position = None
            self._manual_offset = None
            self._dragging_overlay = False
        self.setToolTip("\uc624\ubc84\ub808\uc774\ub97c \ub4dc\ub798\uadf8\ud574 \uc774\ub3d9" if enabled else "")

    def is_movable_mode(self):
        return bool(self._movable_mode)

    def reset_movable_position(self):
        self._manual_position = None
        self._manual_offset = None
        self._dragging_overlay = False

    def set_avoidance_rect_provider(self, provider):
        self._avoidance_rect_provider = provider if callable(provider) else None

    def _handle_apply_pressed(self):
        self.apply_pressed.emit()
        delay = 180 if self._movable_mode else 80
        QTimer.singleShot(delay, self._emit_apply_if_not_dragging)

    def _emit_apply_if_not_dragging(self):
        if time.monotonic() < self._suppress_action_until:
            return
        if self._movable_mode and (self._dragging_overlay or self._drag_moved):
            return
        self.apply_clicked.emit()

    def remember_target(self, window_handle=None, reader_name=""):
        if window_handle:
            self._last_window_handle = window_handle
        if reader_name:
            self._last_reader_name = reader_name

    def is_collapsed(self):
        return self._collapsed

    def collapse(self):
        if self._is_notepad_reader():
            self._collapsed = False
            self._show_near_cursor(self._last_window_handle)
            return
        self._hide_timer.stop()
        self.hide_busy()
        self._collapsed = True
        self._overlay_state = ("collapsed", int(self._last_window_handle or 0))
        self._ensure_collapsed_size()
        for widget in (self.undo_btn, self.redo_btn, self.title_label, self.hint_label, self.apply_btn, self.tone_btn, self.open_btn, self.close_btn):
            widget.hide()
        self.collapsed_btn.show()
        self._show_near_cursor(self._last_window_handle)

    def _handle_collapsed_clicked(self):
        if time.monotonic() < self._suppress_collapsed_expand_until:
            return
        self.expand()

    def refresh_position(self):
        self._show_near_cursor(self._last_window_handle)


    def expand(self):
        self._collapsed = False
        self._overlay_state = None
        self._ensure_expanded_size()
        self.collapsed_btn.hide()
        for widget in (self.undo_btn, self.redo_btn, self.title_label, self.hint_label, self.apply_btn, self.tone_btn, self.open_btn, self.close_btn):
            widget.show()
        self.show_waiting(window_handle=self._last_window_handle)

    def show_waiting(self, reader_name="", window_title_or_handle=None, window_handle=None):
        if window_handle is None:
            window_handle = window_title_or_handle
        if reader_name:
            self._last_reader_name = reader_name
        if self._is_notepad_reader():
            self._collapsed = False
        if self._is_status_active():
            if window_handle:
                self._last_window_handle = window_handle
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        if self._collapsed:
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._ensure_expanded_size()
        state = ("waiting", reader_name, int(window_handle or 0))
        if self.isVisible() and self._overlay_state == state:
            self.apply_btn.setEnabled(True)
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._overlay_state = state
        self._last_window_handle = window_handle
        label = "\ub4dc\ub798\uadf8 \ub300\uae30 \uc911"
        if reader_name == "word_selection":
            label = "Word \ub4dc\ub798\uadf8 \ub300\uae30"
        elif reader_name == "notepad_selection":
            label = "\uba54\ubaa8\uc7a5 \ub4dc\ub798\uadf8 \ub300\uae30"
        self.title_label.setText(label)
        self.hint_label.setText("\uc120\ud0dd \ud6c4 \uad50\uc815")
        self.apply_btn.setEnabled(True)
        self._show_near_cursor(window_handle)

    def show_for_target(self, reader_name="", window_title="", window_handle=None):
        if reader_name:
            self._last_reader_name = reader_name
        if self._is_notepad_reader():
            self._collapsed = False
        if self._is_status_active():
            if window_handle:
                self._last_window_handle = window_handle
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        if self._collapsed:
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._ensure_expanded_size()
        state = ("selected", reader_name, int(window_handle or 0))
        if self.isVisible() and self._overlay_state == state:
            self.apply_btn.setEnabled(True)
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._overlay_state = state
        self._last_window_handle = window_handle
        label = "\uc120\ud0dd \uc601\uc5ed \uc778\uc2dd\ub428"
        if reader_name == "word_selection":
            label = "Word \uc120\ud0dd \uc601\uc5ed"
        elif reader_name == "notepad_selection":
            label = "\uba54\ubaa8\uc7a5 \uc120\ud0dd \uc601\uc5ed"
        self.title_label.setText(label)
        self.hint_label.setText("Ctrl+Alt+Enter")
        self.apply_btn.setEnabled(True)
        self._show_near_cursor(window_handle)

    def show_realtime_for_target(self, reader_name="", window_title="", window_handle=None):
        if reader_name:
            self._last_reader_name = reader_name
        if window_handle:
            self._last_window_handle = window_handle
        if self._is_notepad_reader():
            self._collapsed = False
        if self._collapsed:
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._ensure_expanded_size()
        state = ("realtime", reader_name, int(window_handle or 0))
        if self.isVisible() and self._overlay_state == state:
            self.apply_btn.setEnabled(True)
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._overlay_state = state
        label = "\uc2e4\uc2dc\uac04 \uad50\uc815"
        if reader_name == "word":
            label = "Word \uc2e4\uc2dc\uac04 \uad50\uc815"
        elif reader_name == "notepad":
            label = "\uba54\ubaa8\uc7a5 \uc2e4\uc2dc\uac04 \uad50\uc815"
        self.title_label.setText(label)
        self.hint_label.setText("\uc804\uccb4 \uae00 \uad50\uc815")
        self.apply_btn.setEnabled(True)
        self._show_near_cursor(window_handle or self._last_window_handle)

    def clear_selection(self, reader_name="", window_handle=None):
        if reader_name:
            self._last_reader_name = reader_name
        if self._is_notepad_reader():
            self._collapsed = False
        if self._is_status_active():
            if window_handle:
                self._last_window_handle = window_handle
            self._state_before_status = ("waiting", reader_name or self._last_reader_name, int(window_handle or self._last_window_handle or 0))
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        if self._collapsed:
            return
        self._ensure_expanded_size()
        if window_handle:
            self._last_window_handle = window_handle
        label = "\ub4dc\ub798\uadf8 \ub300\uae30 \uc911"
        if reader_name == "word_selection":
            label = "Word \ub4dc\ub798\uadf8 \ub300\uae30"
        elif reader_name == "notepad_selection":
            label = "\uba54\ubaa8\uc7a5 \ub4dc\ub798\uadf8 \ub300\uae30"
        self._overlay_state = ("waiting", reader_name, int(window_handle or self._last_window_handle or 0))
        self.title_label.setText(label)
        self.hint_label.setText("\uae30\ub2a5\uc744 \uc120\ud0dd\ud574\uc8fc\uc138\uc694")
        self.apply_btn.setEnabled(True)
        if self.isVisible():
            self._show_near_cursor(window_handle or self._last_window_handle)

    def show_tone_prompt(self):
        if hasattr(self, "tone_prompt"):
            self.tone_prompt.show_for_window(self._last_window_handle)

    def set_tone_favorites(self, favorites):
        if hasattr(self, "tone_prompt"):
            self.tone_prompt.set_favorites(favorites)

    def set_tone_favorites_enabled(self, enabled):
        if hasattr(self, "tone_prompt"):
            self.tone_prompt.set_favorites_enabled(enabled)

    def tone_favorite_tones(self):
        if hasattr(self, "tone_prompt"):
            return self.tone_prompt.favorite_tones()
        return []

    def show_tone_favorite_confirm(self, tone):
        if hasattr(self, "tone_favorite_confirm"):
            self.tone_favorite_confirm.show_for_window(self._last_window_handle, tone)

    def show_choice_prompt(self, window_handle=None, anchor_widget=None):
        if window_handle:
            self._last_window_handle = window_handle
        if hasattr(self, "choice_overlay"):
            if anchor_widget is not None and anchor_widget.isVisible():
                self.choice_overlay.show_below_geometry(anchor_widget.frameGeometry(), window_handle or self._last_window_handle)
            else:
                self.choice_overlay.show_for_window(window_handle or self._last_window_handle)

    def can_show_for_target(self, window_handle=None):
        rect = self._target_rect(window_handle or self._last_window_handle)
        if rect is None:
            return False
        left, top, right, bottom = rect
        return not self._should_hide_for_bounds(left, top, right, bottom, margin=18)

    def show_status(self, message: str, auto_hide_ms=1500):
        if self._collapsed:
            self._show_near_cursor(self._last_window_handle)
            return
        self._ensure_expanded_size()
        if not self._is_status_active():
            self._state_before_status = self._overlay_state
        self._status_until = time.monotonic() + max(0.2, auto_hide_ms / 1000.0)
        self._status_timer.stop()
        self._overlay_state = ("status", message, int(self._last_window_handle or 0))
        self.title_label.setText(message)
        self.hint_label.setText("\uc644\ub8cc")
        self.apply_btn.setEnabled(True)
        self._show_near_cursor(self._last_window_handle)
        self._status_timer.start(auto_hide_ms)

    def _is_status_active(self):
        return self._overlay_state is not None and self._overlay_state[0] == "status" and time.monotonic() < self._status_until

    def _restore_after_status(self):
        self._status_until = 0.0
        previous = self._state_before_status
        self._state_before_status = None
        if self._collapsed:
            return
        if previous and previous[0] == "selected":
            _, reader_name, window_handle = previous
            self._overlay_state = None
            self.show_for_target(reader_name, "", window_handle)
            return
        if previous and previous[0] == "waiting":
            _, reader_name, window_handle = previous
            self._overlay_state = None
            self.show_waiting(reader_name, window_handle)
            return
        self._overlay_state = None
        self.show_waiting(self._last_reader_name, self._last_window_handle)

    def _ensure_expanded_size(self):
        self._set_notepad_button_mode(self._is_notepad_reader())
        size = self._compact_notepad_size if self._notepad_button_mode else self._expanded_size
        card_size = self._compact_notepad_card_size if self._notepad_button_mode else self._expanded_card_size
        if self.width() != size[0] or self.height() != size[1]:
            self.setFixedSize(*size)
            self.resize(*size)
        self.card.setFixedSize(*card_size)
        self.card.resize(*card_size)
        if hasattr(self, "busy_cover"):
            self.busy_cover.setGeometry(self.card.rect())

    def _set_notepad_button_mode(self, enabled: bool):
        enabled = bool(enabled)
        if self._notepad_button_mode == enabled:
            return
        self._notepad_button_mode = enabled
        if enabled:
            self._collapsed = False
            self.collapsed_btn.hide()
            for widget in (self.undo_btn, self.redo_btn, self.title_label, self.hint_label, self.open_btn, self.close_btn):
                widget.hide()
            self.apply_btn.show()
            self.tone_btn.show()
            compact_font = QFont(self._default_action_font)
            compact_font.setPointSize(9)
            compact_font.setBold(True)
            self.apply_btn.setFont(compact_font)
            self.tone_btn.setFont(compact_font)
            self.apply_btn.setFixedSize(74, 31)
            self.tone_btn.setFixedSize(74, 31)
            self.floating_actions.setContentsMargins(0, 0, 0, 0)
            self.actions_layout.setDirection(QBoxLayout.TopToBottom)
            self.actions_layout.setSpacing(6)
            self.card_layout.setContentsMargins(12, 12, 12, 12)
        else:
            for widget in (self.undo_btn, self.redo_btn, self.title_label, self.hint_label, self.open_btn, self.close_btn):
                widget.show()
            self.apply_btn.setFont(self._default_action_font)
            self.tone_btn.setFont(self._default_action_font)
            self.apply_btn.setMinimumSize(0, 0)
            self.apply_btn.setMaximumSize(16777215, 16777215)
            self.tone_btn.setMinimumSize(0, 0)
            self.tone_btn.setMaximumSize(16777215, 16777215)
            self.floating_actions.setContentsMargins(16, 0, 0, 0)
            self.actions_layout.setDirection(QBoxLayout.LeftToRight)
            self.actions_layout.setSpacing(8)
            self.card_layout.setContentsMargins(14, 10, 14, 12)

    def _is_notepad_reader(self):
        return (self._last_reader_name or "") in {"notepad", "notepad_selection"}

    def _ensure_collapsed_size(self):
        self.setFixedSize(*self._collapsed_size)
        self.resize(*self._collapsed_size)
        self.card.setFixedSize(*self._collapsed_size)
        self.card.resize(*self._collapsed_size)
        self.collapsed_btn.setGeometry(6, 6, 56, 56)

    def _show_near_cursor(self, window_handle=None):
        self._hide_timer.stop()
        self._log_overlay(
            "show_near_cursor",
            window_handle=window_handle,
            collapsed=self._collapsed,
            state=self._overlay_state,
            movable=self._movable_mode,
            manual_position=self._manual_position,
        )
        if self._collapsed:
            self._ensure_collapsed_size()
        rect = self._target_rect(window_handle)
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if rect is not None:
            left, top, right, bottom = rect
            if self._should_hide_for_bounds(left, top, right, bottom, margin=18) and not self._movable_mode:
                self.hide_with_reason("target_too_small")
                return
            if self._movable_mode:
                if self._manual_offset is None:
                    x, y = self._movable_initial_position(left, top, right, bottom, margin=18)
                    self._manual_position = QPoint(x, y)
                    self._manual_offset = QPoint(x - left, y - top)
                else:
                    x = left + self._manual_offset.x()
                    y = top + self._manual_offset.y()
                    x, y = self._clamp_position(x, y, left, top, right, bottom, margin=8)
                    self._manual_position = QPoint(x, y)
                    self._manual_offset = QPoint(x - left, y - top)
            else:
                x, y = self._responsive_position(left, top, right, bottom, margin=18)
            self.move(x, y)
        elif screen is not None:
            available = screen.availableGeometry()
            left, top, right, bottom = available.left(), available.top(), available.right(), available.bottom()
            if self._movable_mode and self._manual_position is not None:
                x, y = self._clamp_position(self._manual_position.x(), self._manual_position.y(), left, top, right, bottom, margin=8)
                self._manual_position = QPoint(x, y)
            elif self._movable_mode:
                x, y = self._movable_initial_position(left, top, right, bottom, margin=18)
                self._manual_position = QPoint(x, y)
            else:
                x, y = self._responsive_position(left, top, right, bottom, margin=18)
            self.move(x, y)
        self.show()
        self.raise_()
        if window_handle:
            self._focus_guard_timer.start()

    def _movable_initial_position(self, left, top, right, bottom, margin=18):
        width = max(0, right - left)
        reader_name = self._last_reader_name or ""
        x, y = self._responsive_position(left, top, right, bottom, margin=margin)
        center_threshold = 640 if self._is_notepad_reader() else 1600
        if width < center_threshold and not self._is_target_maximized_or_fullscreen_like(left, top, right, bottom):
            x = left + max(margin, (width - self.width()) // 2)
        return self._clamp_position(x, y, left, top, right, bottom, margin=8)

    def _is_target_maximized_or_fullscreen_like(self, left=None, top=None, right=None, bottom=None):
        if win32gui is None or not self._last_window_handle:
            return False
        try:
            root = win32gui.GetAncestor(int(self._last_window_handle), 2) or int(self._last_window_handle)
            if win32gui.IsZoomed(root):
                return True
        except Exception:
            pass
        try:
            if left is None or top is None or right is None or bottom is None:
                return False
            center = QPoint(int((left + right) / 2), int((top + bottom) / 2))
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            if screen is None:
                return False
            available = screen.availableGeometry()
            width = max(0, right - left)
            height = max(0, bottom - top)
            width_close = width >= max(0, available.width() - 80)
            height_close = height >= max(0, available.height() - 80)
            edge_close = abs(left - available.left()) <= 40 and abs(right - available.right()) <= 80
            return bool(width_close and height_close and edge_close)
        except Exception:
            return False


    def _clamp_position(self, x, y, left, top, right, bottom, margin=8):
        overlay_width = self.width()
        overlay_height = self.height()
        if self._is_notepad_reader():
            bottom = max(top + overlay_height + margin * 2, bottom - 62)
        min_x = left + margin
        max_x = max(min_x, right - overlay_width - margin)
        min_y = top + margin
        max_y = max(min_y, bottom - overlay_height - margin)
        clamped_x = min(max(int(x), min_x), max_x)
        clamped_y = min(max(int(y), min_y), max_y)
        return self._avoid_reserved_overlay_rects(
            clamped_x,
            clamped_y,
            min_x,
            min_y,
            max_x,
            max_y,
            margin=10,
        )

    def _avoid_reserved_overlay_rects(self, x, y, min_x, min_y, max_x, max_y, margin=10):
        forbidden_rects = self._current_avoidance_rects(margin)
        if not forbidden_rects:
            return x, y

        def clamp(candidate_x, candidate_y):
            return min(max(int(candidate_x), min_x), max_x), min(max(int(candidate_y), min_y), max_y)

        original_x, original_y = int(x), int(y)
        best_x, best_y = clamp(x, y)
        best_distance = None
        for _ in range(4):
            current_rect = QRect(best_x, best_y, self.width(), self.height())
            blockers = [rect for rect in forbidden_rects if current_rect.intersects(rect)]
            if not blockers:
                return best_x, best_y
            candidates = []
            for rect in blockers:
                candidates.extend(
                    [
                        clamp(rect.left() - self.width(), best_y),
                        clamp(rect.right() + 1, best_y),
                        clamp(best_x, rect.top() - self.height()),
                        clamp(best_x, rect.bottom() + 1),
                    ]
                )
            next_position = None
            next_distance = None
            for candidate_x, candidate_y in candidates:
                candidate_rect = QRect(candidate_x, candidate_y, self.width(), self.height())
                if any(candidate_rect.intersects(rect) for rect in forbidden_rects):
                    continue
                distance = (candidate_x - original_x) ** 2 + (candidate_y - original_y) ** 2
                if next_distance is None or distance < next_distance:
                    next_position = (candidate_x, candidate_y)
                    next_distance = distance
            if next_position is not None:
                return next_position
            for candidate_x, candidate_y in candidates:
                distance = (candidate_x - original_x) ** 2 + (candidate_y - original_y) ** 2
                if best_distance is None or distance < best_distance:
                    best_x, best_y = candidate_x, candidate_y
                    best_distance = distance
        return best_x, best_y

    def _current_avoidance_rects(self, margin=10):
        provider = self._avoidance_rect_provider
        if provider is None:
            return []
        try:
            raw_rects = provider() or []
        except Exception:
            return []
        rects = []
        for rect in raw_rects:
            try:
                if rect is None or rect.isNull() or not rect.isValid():
                    continue
                rects.append(rect.adjusted(-margin, -margin, margin, margin))
            except Exception:
                continue
        return rects

    def _responsive_position(self, left, top, right, bottom, margin=18):
        width = max(0, right - left)
        height = max(0, bottom - top)
        reader_name = self._last_reader_name or ""
        overlay_width = self.width()
        overlay_height = self.height()

        if self._is_notepad_reader():
            wide_x = left + 28
            bottom_offset = 92
        else:
            wide_x = left + 28
            bottom_offset = 54

        x = min(max(wide_x, left + margin), right - overlay_width - margin)

        desired_y = bottom - overlay_height - bottom_offset
        if height < bottom_offset + overlay_height + margin * 2:
            desired_y = top + max(margin, (height - overlay_height) // 2)
        y = min(max(desired_y, top + margin), bottom - overlay_height - margin)
        return x, y

    def _should_hide_for_bounds(self, left, top, right, bottom, margin=18):
        width = max(0, right - left)
        height = max(0, bottom - top)
        reader_name = self._last_reader_name or ""
        min_width = 160 if self._is_notepad_reader() else 1600
        if width < max(self.width() + margin * 2, min_width):
            return True
        min_height = 138 if self._is_notepad_reader() else 500
        return height < min_height

    def hide_with_reason(self, reason):
        self._pending_hide_reason = reason or "direct"
        self.hide()

    def hide(self):
        reason = getattr(self, "_pending_hide_reason", "direct")
        self._pending_hide_reason = "direct"
        self._log_overlay(
            "hide_called",
            reason=reason,
            collapsed=self._collapsed,
            state=self._overlay_state,
            last_reader=self._last_reader_name,
            last_hwnd=self._last_window_handle,
            focus_guard_suspended_for=f"{max(0.0, self._focus_guard_suspended_until - time.monotonic()):.3f}",
            stack=self._compact_stack(),
        )
        self._focus_guard_timer.stop()
        if hasattr(self, "tone_prompt"):
            self.tone_prompt.hide()
        if hasattr(self, "choice_overlay"):
            self.choice_overlay.hide()
        self._overlay_state = None if not self._collapsed else self._overlay_state
        super().hide()

    def suspend_focus_guard(self, duration=2.0):
        self._focus_guard_suspended_until = max(
            self._focus_guard_suspended_until,
            time.monotonic() + max(0.2, float(duration)),
        )

    def _hide_if_target_lost_focus(self):
        if win32gui is not None and self._last_window_handle:
            try:
                if win32gui.IsWindow(int(self._last_window_handle)) and win32gui.IsIconic(int(self._last_window_handle)):
                    self._log_overlay("focus_guard_hide_minimized_target", state=self._overlay_state, last_hwnd=self._last_window_handle)
                    self.hide_with_reason("focus_guard_minimized_target")
                    return
            except Exception:
                pass
        if self._is_status_active():
            self._log_overlay("focus_guard_skip_status", state=self._overlay_state)
            return
        if time.monotonic() < self._focus_guard_suspended_until:
            self._log_overlay(
                "focus_guard_skip_suspended",
                remaining=f"{self._focus_guard_suspended_until - time.monotonic():.3f}",
                state=self._overlay_state,
            )
            return
        if win32gui is not None:
            try:
                foreground = int(win32gui.GetForegroundWindow() or 0)
                if foreground == 0 and self._last_window_handle and win32gui.IsWindow(int(self._last_window_handle)):
                    if win32gui.IsIconic(int(self._last_window_handle)):
                        self._log_overlay("focus_guard_hide_minimized_empty_foreground", state=self._overlay_state, last_hwnd=self._last_window_handle)
                        self.hide_with_reason("focus_guard_minimized_empty_foreground")
                        return
                    self._log_overlay("focus_guard_skip_empty_foreground", state=self._overlay_state, last_hwnd=self._last_window_handle)
                    return
            except Exception:
                pass
        if not self.has_target_focus():
            self._log_overlay("focus_guard_lost_focus", state=self._overlay_state, last_hwnd=self._last_window_handle)
            self.hide_with_reason("focus_guard_lost_focus")

    def eventFilter(self, watched, event):
        if self._movable_mode and event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._dragging_overlay = True
            self._drag_moved = False
            self._drag_start_pos = event.globalPos()
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return False
        if self._movable_mode and event.type() == QEvent.MouseMove and self._dragging_overlay:
            if (event.globalPos() - self._drag_start_pos).manhattanLength() >= 7:
                self._drag_moved = True
            self._move_overlay_to_global(event.globalPos())
            event.accept()
            return True
        if self._movable_mode and event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            moved = bool(self._drag_moved)
            self._dragging_overlay = False
            self._drag_moved = False
            if moved:
                self._suppress_collapsed_expand_until = time.monotonic() + 0.35
                self._suppress_action_until = time.monotonic() + 0.35
                self.overlay_moved.emit(self._last_reader_name, self._last_window_handle)
                event.accept()
                return True
            event.accept()
            return False
        return super().eventFilter(watched, event)


    def _move_overlay_to_global(self, global_pos):
        next_pos = global_pos - self._drag_offset
        rect = self._target_rect(self._last_window_handle)
        if rect is not None:
            left, top, right, bottom = rect
        else:
            screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                left, top, right, bottom = available.left(), available.top(), available.right(), available.bottom()
            else:
                left, top, right, bottom = 0, 0, 9999, 9999
        x, y = self._clamp_position(next_pos.x(), next_pos.y(), left, top, right, bottom, margin=8)
        self._manual_position = QPoint(x, y)
        self._manual_offset = QPoint(x - left, y - top)
        self.move(x, y)

    def mousePressEvent(self, event):
        if self._movable_mode and event.button() == Qt.LeftButton:
            self._dragging_overlay = True
            self._drag_moved = False
            self._drag_start_pos = event.globalPos()
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._movable_mode and self._dragging_overlay:
            if (event.globalPos() - self._drag_start_pos).manhattanLength() >= 7:
                self._drag_moved = True
            self._move_overlay_to_global(event.globalPos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._movable_mode and event.button() == Qt.LeftButton:
            self._dragging_overlay = False
            if self._drag_moved:
                self._suppress_action_until = time.monotonic() + 0.35
                self.overlay_moved.emit(self._last_reader_name, self._last_window_handle)
            self._drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _compact_stack(self):
        try:
            frames = traceback.extract_stack(limit=7)[:-2]
            return " > ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames[-4:])
        except Exception:
            return ""

    def _log_overlay(self, note, **values):
        try:
            fg_hwnd = 0
            fg_class = ""
            fg_title = ""
            if win32gui is not None:
                fg_hwnd = win32gui.GetForegroundWindow()
                if fg_hwnd:
                    fg_class = win32gui.GetClassName(fg_hwnd) or ""
                    fg_title = win32gui.GetWindowText(fg_hwnd) or ""
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            pieces = [f"{key}={value!r}" for key, value in values.items()]
            pieces.extend([f"fg_hwnd={fg_hwnd!r}", f"fg_class={fg_class!r}", f"fg_title={fg_title!r}"])
            with _MINI_OVERLAY_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{timestamp} {note} {' '.join(pieces)}\n")
        except Exception:
            pass

    def has_overlay_focus(self):
        if win32gui is None:
            return False
        try:
            foreground = win32gui.GetForegroundWindow()
            overlay_hwnd = int(self.winId())
            if self._same_root_window(foreground, overlay_hwnd):
                return True
            return self._looks_like_own_overlay_window(foreground)
        except Exception:
            return False

    def _looks_like_own_overlay_window(self, hwnd):
        try:
            if not hwnd or win32gui is None:
                return False
            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            if title not in {"Writing Assistant Mini", "Writing Assistant Tone", "Writing Assistant Correction Choice"}:
                return False
            return class_name.startswith("Qt") or "QWindow" in class_name
        except Exception:
            return False

    def has_target_focus(self):
        if win32gui is None or not self._last_window_handle:
            return False
        try:
            foreground = win32gui.GetForegroundWindow()
            if self._same_root_window(foreground, self._last_window_handle):
                return True
            return self.has_overlay_focus()
        except Exception:
            return False
    def _same_root_window(self, first, second):
        try:
            first = int(first or 0)
            second = int(second or 0)
            if not first or not second:
                return False
            if first == second:
                return True
            if win32gui is None:
                return False
            root_first = win32gui.GetAncestor(first, 2) or first
            root_second = win32gui.GetAncestor(second, 2) or second
            return int(root_first) == int(root_second)
        except Exception:
            return False

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            left, top, right, bottom = win32gui.GetWindowRect(root)
            if right - left <= 80 or bottom - top <= 80:
                return None
            return left, top, right, bottom
        except Exception:
            return None


class RealtimeOverlay(MiniOverlay):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Realtime Overlay")
        self.close_btn.setToolTip("\uc811\uae30")
        self.collapsed_btn.setToolTip("\uc2e4\uc2dc\uac04 \uc624\ubc84\ub808\uc774 \ud3bc\uce58\uae30")

    def show_realtime_for_target(self, reader_name="", window_title="", window_handle=None):
        if reader_name:
            self._last_reader_name = reader_name
        if window_handle:
            self._last_window_handle = window_handle
        if self._is_notepad_reader():
            self._collapsed = False
        if self._is_status_active():
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        if self._collapsed:
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._ensure_expanded_size()
        state = ("realtime", reader_name, int(window_handle or 0))
        if self.isVisible() and self._overlay_state == state:
            self.apply_btn.setEnabled(True)
            self._show_near_cursor(window_handle or self._last_window_handle)
            return
        self._overlay_state = state
        self._last_window_handle = window_handle or self._last_window_handle
        label = "\uc2e4\uc2dc\uac04 \uad50\uc815"
        if reader_name == "word":
            label = "Word \uc2e4\uc2dc\uac04 \uad50\uc815"
        elif reader_name == "notepad":
            label = "\uba54\ubaa8\uc7a5 \uc2e4\uc2dc\uac04 \uad50\uc815"
        self.title_label.setText(label)
        self.hint_label.setText("\uc804\uccb4 \uae00 \uad50\uc815")
        self.apply_btn.setEnabled(True)
        self._show_near_cursor(window_handle or self._last_window_handle)

    def show_waiting(self, reader_name="", window_title_or_handle=None, window_handle=None):
        if window_handle is None:
            window_handle = window_title_or_handle
        self.show_realtime_for_target(reader_name, "", window_handle)

    def show_for_target(self, reader_name="", window_title="", window_handle=None):
        self.show_realtime_for_target(reader_name, window_title, window_handle)

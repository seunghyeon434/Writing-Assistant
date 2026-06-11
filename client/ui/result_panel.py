from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher

from PyQt5.QtCore import QPoint, QRectF, QSize, Qt, QVariantAnimation, QTimer
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedLayout,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ResultPanel(QWidget):
    LIGHT_THEME = {
        "window_bg": "#00000000",
        "card_bg": "#f7efe5",
        "card_border": "#e2d4c3",
        "title": "#2f241f",
        "text": "#43332b",
        "muted": "#7b6658",
        "editor_bg": "#fffaf4",
        "editor_border": "#dccbbb",
        "tab_bg": "#efe0d0",
        "tab_selected": "#ffffff",
        "tab_text": "#4a382f",
        "button_bg": "#e8d4bf",
        "button_hover": "#dcc1a7",
        "button_text": "#3f2f26",
        "accent": "#b86a3c",
        "accent_hover": "#9f5730",
        "accent_text": "#fff8f2",
        "danger_bg": "#ead7cf",
        "danger_hover": "#dfc3b7",
        "score_bg": "#f1e1d0",
        "input_bg": "#fffaf4",
        "settings_panel_bg": "#ffffff",
        "settings_panel_border": "#00ffffff",
        "settings_text": "#2f241f",
        "settings_notice_text": "#2f241f",
        "settings_check_bg": "#fffaf4",
        "settings_check_border": "#dccbbb",
        "settings_check_checked": "#b86a3c",
    }

    DARK_THEME = {
        "window_bg": "#00000000",
        "card_bg": "#1f2329",
        "card_border": "#303741",
        "title": "#f4efe8",
        "text": "#d6dce5",
        "muted": "#98a1ad",
        "editor_bg": "#14181d",
        "editor_border": "#39414c",
        "tab_bg": "#2b3139",
        "tab_selected": "#39414c",
        "tab_text": "#f4efe8",
        "button_bg": "#2e3640",
        "button_hover": "#3a4451",
        "button_text": "#edf2f7",
        "accent": "#c77747",
        "accent_hover": "#df8a57",
        "accent_text": "#fff7f1",
        "danger_bg": "#3a3134",
        "danger_hover": "#4a3b3f",
        "score_bg": "#2b3139",
        "input_bg": "#14181d",
        "settings_panel_bg": "#ffffff",
        "settings_panel_border": "#00ffffff",
        "settings_text": "#2f241f",
        "settings_notice_text": "#ffffff",
        "settings_check_bg": "#fffaf4",
        "settings_check_border": "#dccbbb",
        "settings_check_checked": "#b86a3c",
    }

    def __init__(self, initial_dark_mode=False):
        super().__init__()

        self.last_original_text = ""
        self._showing_placeholder = True
        self.is_dark_mode = initial_dark_mode
        self.saved_default_dark_mode = False
        self.saved_input_mode = "realtime"
        self.saved_replace_mode = False
        self.saved_history_enabled = False
        self.saved_spell_scope = "current_sentence"
        self.drag_active = False
        self.drag_position = QPoint()
        self.resize_active = False
        self.resize_edge = ""
        self.resize_start_pos = QPoint()
        self.resize_start_geometry = None
        self.resize_margin = 10
        self._centered_once = False
        self._theme_mix = 1.0 if initial_dark_mode else 0.0

        self.setWindowTitle("Writing Assistant")
        self.resize(760, 560)
        self.setMinimumSize(705, 480)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.theme_animation = QVariantAnimation(self)
        self.theme_animation.setDuration(280)
        self.theme_animation.valueChanged.connect(self._on_theme_mix_changed)

        self.settings_notice_timer = QTimer(self)
        self.settings_notice_timer.setSingleShot(True)
        self.settings_notice_timer.timeout.connect(self._start_settings_notice_fade)

        self.settings_notice_animation = QVariantAnimation(self)
        self.settings_notice_animation.setDuration(900)
        self.settings_notice_animation.valueChanged.connect(self._update_settings_notice_opacity)
        self.settings_notice_animation.finished.connect(self._hide_settings_notice)

        self.build_ui()
        self.apply_shadow()
        self.apply_theme()
        self.reset_text_tab()
        self.clear_spell_result()
        self.clear_summary_result()
        self.clear_tone_result()
        self.clear_evaluation_score()
        self.clear_title_recommendation()

    def build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)

        self.card = QFrame()
        self.card.setObjectName("panelCard")
        self.card.setMouseTracking(True)
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(24, 20, 24, 24)
        self.card_layout.setSpacing(18)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)
        title_block.setContentsMargins(0, 2, 0, 0)

        self.title_label = QLabel("Writing Assistant")
        self.title_label.setObjectName("titleLabel")

        self.subtitle_label = QLabel("문장을 다듬고 핵심 내용을 빠르게 정리합니다.")
        self.subtitle_label.setObjectName("subtitleLabel")

        self.active_window_label = QLabel("")
        self.active_window_label.setObjectName("activeWindowLabel")
        self.active_window_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.user_icon_label = QLabel("")
        self.user_icon_label.setFixedSize(16, 16)
        self.user_icon_label.hide()
        person_icon_path = self._find_asset_path(("person.png", "person.svg"))
        if person_icon_path:
            self.user_icon_label.setPixmap(QIcon(str(person_icon_path)).pixmap(QSize(16, 16)))
        self.user_identity_label = QLabel("")
        self.user_identity_label.setObjectName("userIdentityLabel")
        self.user_identity_label.setFixedHeight(24)
        self.user_identity_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.user_identity_label.hide()
        self.user_status_widget = QWidget()
        self.user_status_widget.setFixedHeight(24)
        self.user_status_widget.hide()
        user_status_layout = QHBoxLayout(self.user_status_widget)
        user_status_layout.setContentsMargins(0, 0, 0, 0)
        user_status_layout.setSpacing(6)
        user_status_layout.addWidget(self.user_icon_label, 0, Qt.AlignVCenter)
        user_status_layout.addWidget(self.user_identity_label, 0, Qt.AlignVCenter)
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.subtitle_label)
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.addWidget(self.active_window_label, 1, Qt.AlignLeft)
        title_block.addLayout(status_row)

        header_layout.addLayout(title_block)
        header_layout.addStretch()

        self.login_btn = QPushButton("로그인")
        self.login_btn.setObjectName("secondaryButton")

        self.settings_btn = QPushButton("설정")
        self.settings_btn.setObjectName("iconButton")
        self.settings_btn.setToolTip("설정")
        self.settings_btn.setCheckable(True)
        self.settings_icon_path = self._find_settings_icon_path()
        if self.settings_icon_path:
            self.settings_btn.setIconSize(QSize(18, 18))
            self.settings_btn.setText("")
        self.settings_btn.clicked.connect(self.open_settings_tab)

        self.dark_mode_btn = QPushButton("")
        self.dark_mode_btn.setObjectName("iconButton")
        self.dark_mode_btn.setToolTip("\ub2e4\ud06c \ubaa8\ub4dc \ubc14\uafb8\uae30")
        self.dark_mode_btn.setCheckable(True)
        self.dark_mode_btn.setIconSize(QSize(18, 18))
        self.dark_mode_btn.clicked.connect(self.toggle_theme)

        self.hide_btn = QPushButton("숨기기")
        self.hide_btn.setObjectName("ghostButton")
        self.hide_btn.clicked.connect(self.hide)

        header_layout.addWidget(self.login_btn)
        header_layout.addWidget(self.settings_btn)
        header_layout.addWidget(self.dark_mode_btn)
        header_layout.addWidget(self.hide_btn)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("resultTabs")
        self.input_mode_status_label = QLabel("")
        self.input_mode_status_label.setObjectName("inputModeStatusLabel")
        self.input_mode_status_label.setFixedHeight(24)
        self.input_mode_status_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.bottom_status_widget = QWidget()
        self.bottom_status_widget.setFixedHeight(24)
        bottom_status_layout = QHBoxLayout(self.bottom_status_widget)
        bottom_status_layout.setContentsMargins(0, 0, 34, 0)
        bottom_status_layout.setSpacing(14)
        bottom_status_layout.addWidget(self.input_mode_status_label, 0, Qt.AlignVCenter)
        bottom_status_layout.addWidget(self.user_status_widget, 0, Qt.AlignVCenter)

        self.text_box = self._create_text_box("복사한 텍스트가 여기에 뜹니다.")
        self.spell_box = self._create_text_box("")
        self.summary_box = self._create_text_box("")
        self.tone_box = self._create_text_box("")

        self.evaluate_btn = QPushButton("평가")
        self.evaluate_btn.setObjectName("secondaryButton")
        self.evaluation_reason_btn = QPushButton("\uc774\uc720")
        self.evaluation_reason_btn.setObjectName("scoreReasonButton")
        self.evaluation_reason_btn.setEnabled(False)
        self.evaluation_reason_btn.hide()
        self.text_history_btn = self._create_history_button()
        self.recommend_title_btn = QPushButton("추천")
        self.recommend_title_btn.setObjectName("secondaryButton")
        self.refresh_btn = QPushButton("다시 분석")
        self.refresh_btn.setObjectName("secondaryButton")
        self.apply_correction_btn = QPushButton("원본 수정")
        self.apply_correction_btn.setObjectName("primaryButton")
        self.apply_correction_btn.hide()
        self.spell_history_btn = self._create_history_button()
        self.spell_scope_group = QButtonGroup(self)
        self.spell_scope_group.setExclusive(True)
        self.spell_scope_buttons = {}
        for scope, label in (
            ("current_sentence", "현재 문장"),
            ("current_paragraph", "현재 문단"),
            ("full_text", "글 전체"),
        ):
            button = QPushButton(label)
            button.setObjectName("scopeButton")
            button.setCheckable(True)
            self.spell_scope_group.addButton(button)
            self.spell_scope_buttons[scope] = button
        self.run_summary_btn = QPushButton("글 요약")
        self.run_summary_btn.setObjectName("secondaryButton")
        self.summary_history_btn = self._create_history_button()
        self.run_tone_btn = QPushButton("변경")
        self.run_tone_btn.setObjectName("secondaryButton")
        self.tone_history_btn = self._create_history_button()
        self.history_buttons = (
            self.text_history_btn,
            self.spell_history_btn,
            self.summary_history_btn,
            self.tone_history_btn,
        )
        self.save_settings_btn = QPushButton("저장")
        self.save_settings_btn.setObjectName("secondaryButton")
        self.close_settings_btn = QPushButton("X")
        self.close_settings_btn.setObjectName("closeSettingsButton")
        self.close_settings_btn.setFixedSize(34, 34)
        self.account_manage_btn = QPushButton("계정")
        self.account_manage_btn.setObjectName("secondaryButton")
        self.account_manage_btn.setToolTip("계정 관리")
        account_icon_path = self._find_asset_path(("account.png", "account.svg", "accounts.png", "accounts.svg"))
        if account_icon_path:
            self.account_manage_btn.setIcon(QIcon(str(account_icon_path)))
            self.account_manage_btn.setIconSize(QSize(18, 18))

        self.account_verify_close_btn = QPushButton("X")
        self.account_verify_close_btn.setObjectName("closeSettingsButton")
        self.account_verify_close_btn.setFixedSize(34, 34)
        self.account_verify_close_btn.clicked.connect(self.close_account_pages)
        self.account_verify_password_input = QLineEdit()
        self.account_verify_password_input.setObjectName("authInput")
        self.account_verify_password_input.setEchoMode(QLineEdit.Password)
        self.account_verify_submit_btn = QPushButton("인증하기")
        self.account_verify_submit_btn.setObjectName("authSubmitButton")
        self.account_verify_submit_btn.setFixedWidth(210)
        self.account_verify_submit_btn.setFixedHeight(32)

        self.account_close_btn = QPushButton("X")
        self.account_close_btn.setObjectName("closeSettingsButton")
        self.account_close_btn.setFixedSize(34, 34)
        self.account_close_btn.clicked.connect(self.close_account_pages)
        self.account_save_btn = QPushButton("저장")
        self.account_save_btn.setObjectName("secondaryButton")
        self.account_notice_label = QLabel("저장되었습니다.")
        self.account_notice_label.setObjectName("settingsNotice")
        self.account_notice_label.hide()
        self.account_notice_effect = QGraphicsOpacityEffect(self.account_notice_label)
        self.account_notice_effect.setOpacity(0.0)
        self.account_notice_label.setGraphicsEffect(self.account_notice_effect)
        self.account_notice_timer = QTimer(self)
        self.account_notice_timer.setSingleShot(True)
        self.account_notice_timer.timeout.connect(self._start_account_notice_fade)
        self.account_notice_animation = QVariantAnimation(self)
        self.account_notice_animation.setDuration(900)
        self.account_notice_animation.valueChanged.connect(self._update_account_notice_opacity)
        self.account_notice_animation.finished.connect(self._hide_account_notice)
        self.account_name_input = QLineEdit()
        self.account_name_input.setObjectName("accountInput")
        self.account_username_input = QLineEdit()
        self.account_username_input.setObjectName("accountInput")
        self.account_password_input = QLineEdit()
        self.account_password_input.setObjectName("accountInput")
        self.account_password_input.setEchoMode(QLineEdit.Password)
        self.account_name_edit_btn = QPushButton("수정")
        self.account_name_edit_btn.setObjectName("secondaryButton")
        self.account_username_edit_btn = QPushButton("수정")
        self.account_username_edit_btn.setObjectName("secondaryButton")
        self.account_password_edit_btn = QPushButton("수정")
        self.account_password_edit_btn.setObjectName("secondaryButton")
        self.account_delete_btn = QPushButton("계정 탈퇴")
        self.account_delete_btn.setObjectName("dangerButton")
        self._account_loaded = {"display_name": "", "username": "", "password": ""}
        for widget in (
            self.account_name_input,
            self.account_username_input,
            self.account_password_input,
        ):
            widget.textChanged.connect(self.update_account_edit_buttons)

        self.score_label = QLabel("점수")
        self.score_label.setObjectName("scoreLabel")
        self.score_label.setAlignment(Qt.AlignCenter)
        self.score_label.setMinimumWidth(78)
        self.score_widget = QFrame()
        self.score_widget.setObjectName("scoreWidget")
        score_layout = QHBoxLayout(self.score_widget)
        score_layout.setContentsMargins(0, 0, 0, 0)
        score_layout.setSpacing(4)
        score_layout.addWidget(self.score_label)
        score_layout.addWidget(self.evaluation_reason_btn)

        self.title_label_box = QLabel("제목")
        self.title_label_box.setObjectName("titleValueLabel")
        self.title_label_box.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.title_label_box.setMinimumWidth(220)
        self.title_label_box.setFixedHeight(40)

        self.tone_input = QLineEdit()
        self.tone_input.setObjectName("toneInput")
        self.tone_input.setPlaceholderText("원하는 문체")

        self.default_dark_mode_checkbox = QCheckBox("기본 다크 모드")
        self.default_dark_mode_checkbox.setObjectName("settingsCheck")
        self.clipboard_mode_checkbox = QCheckBox("클립보드 인식 사용")
        self.clipboard_mode_checkbox.setObjectName("settingsCheck")
        self.drag_mode_checkbox = QCheckBox("드래그 인식 사용")
        self.drag_mode_checkbox.setObjectName("settingsCheck")
        self.realtime_mode_checkbox = QCheckBox("실시간 인식 사용")
        self.realtime_mode_checkbox.setObjectName("settingsCheck")
        self.replace_mode_checkbox = QCheckBox("수정 방식 사용")
        self.replace_mode_checkbox.setObjectName("settingsSubCheck")
        self.replace_mode_checkbox.setText("\ub9de\ucda4\ubc95 \uc218\uc815 \ubc29\uc2dd \uc0ac\uc6a9")
        self.realtime_replace_mode_checkbox = self.replace_mode_checkbox
        self.drag_replace_mode_checkbox = QCheckBox("\ub9de\ucda4\ubc95 \uc218\uc815 \ubc29\uc2dd \uc0ac\uc6a9")
        self.drag_replace_mode_checkbox.setObjectName("settingsSubCheck")
        self.history_enabled_checkbox = QCheckBox("기록 사용")
        self.history_enabled_checkbox.setObjectName("settingsCheck")
        self.clipboard_mode_checkbox.toggled.connect(self._sync_input_mode_checks)
        self.drag_mode_checkbox.toggled.connect(self._sync_input_mode_checks)
        self.realtime_mode_checkbox.toggled.connect(self._sync_input_mode_checks)
        self.clipboard_mode_checkbox.toggled.connect(self._update_replace_mode_availability)
        self.drag_mode_checkbox.toggled.connect(self._update_replace_mode_availability)
        self.realtime_mode_checkbox.toggled.connect(self._update_replace_mode_availability)
        self.drag_replace_mode_checkbox.toggled.connect(lambda checked: self._sync_replace_mode_checks("drag", checked))
        self.realtime_replace_mode_checkbox.toggled.connect(lambda checked: self._sync_replace_mode_checks("realtime", checked))

        self.settings_notice_label = QLabel("저장되었습니다.")
        self.settings_notice_label.setObjectName("settingsNotice")
        self.settings_notice_label.hide()
        self.settings_notice_effect = QGraphicsOpacityEffect(self.settings_notice_label)
        self.settings_notice_effect.setOpacity(0.0)
        self.settings_notice_label.setGraphicsEffect(self.settings_notice_effect)

        self.tabs.addTab(self._create_text_tab(), "텍스트")
        self.tabs.addTab(self._create_spell_tab(), "교정")
        self.tabs.addTab(self._create_action_tab(self.summary_box, self.run_summary_btn), "요약")
        self.tabs.addTab(self._create_tone_tab(), "문체")
        self.tabs.currentChanged.connect(self.update_copy_button_label)

        self.settings_page = self._create_settings_tab()
        self.history_page = self._create_history_page()
        self.auth_page = self._create_auth_page()
        self.prompt_page = self._create_prompt_page()
        self.account_verify_page = self._create_account_verify_page()
        self.account_page = self._create_account_page()
        self.content_container = QWidget()
        self.content_stack = QStackedLayout(self.content_container)
        self.content_stack.setContentsMargins(0, 0, 0, 0)
        self.content_stack.addWidget(self.tabs)
        self.content_stack.addWidget(self.settings_page)
        self.content_stack.addWidget(self.history_page)
        self.content_stack.addWidget(self.auth_page)
        self.content_stack.addWidget(self.prompt_page)
        self.content_stack.addWidget(self.account_verify_page)
        self.content_stack.addWidget(self.account_page)
        self.content_stack.setCurrentIndex(0)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(10)

        self.copy_btn = QPushButton("원본 복사")
        self.copy_btn.setObjectName("primaryButton")

        self.quit_btn = QPushButton("종료")
        self.quit_btn.setObjectName("secondaryButton")

        self.resize_grip = QSizeGrip(self.card)
        self.resize_grip.setObjectName("resizeGrip")
        self.resize_grip.setFixedSize(18, 18)
        self.resize_grip.setToolTip("? ?? ??")

        button_layout.addWidget(self.copy_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.bottom_status_widget, 0, Qt.AlignCenter)
        button_layout.addStretch()
        button_layout.addWidget(self.quit_btn)
        button_layout.addSpacing(-27)
        button_layout.addWidget(self.resize_grip, 0, Qt.AlignRight | Qt.AlignBottom)

        self.card_layout.addLayout(header_layout)
        self.card_layout.addWidget(self.content_container)
        self.card_layout.addLayout(button_layout)
        root_layout.addWidget(self.card)

    def _find_settings_icon_path(self):
        return self._find_asset_path(("settings.png", "settings.svg"))

    def _find_theme_icon_path(self):
        if self.is_dark_mode:
            return self._find_asset_path(("dark_mode.png", "dark_mode.svg"))
        return self._find_asset_path(("brightness.png", "brightness.svg"))

    def _update_settings_icon(self, color_value):
        if not self.settings_icon_path:
            return

        base_icon = QIcon(str(self.settings_icon_path))
        base_pixmap = base_icon.pixmap(QSize(18, 18))
        if base_pixmap.isNull():
            return

        tinted_pixmap = QPixmap(base_pixmap.size())
        tinted_pixmap.fill(Qt.transparent)

        painter = QPainter(tinted_pixmap)
        painter.drawPixmap(0, 0, base_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted_pixmap.rect(), QColor(color_value))
        painter.end()

        self.settings_btn.setIcon(QIcon(tinted_pixmap))

    def _update_theme_icon(self, color_value):
        icon_path = self._find_theme_icon_path()
        if not icon_path:
            self.dark_mode_btn.setText("\ub2e4")
            return

        base_icon = QIcon(str(icon_path))
        base_pixmap = base_icon.pixmap(QSize(18, 18))
        if base_pixmap.isNull():
            return

        tinted_pixmap = QPixmap(base_pixmap.size())
        tinted_pixmap.fill(Qt.transparent)

        painter = QPainter(tinted_pixmap)
        painter.drawPixmap(0, 0, base_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted_pixmap.rect(), QColor(color_value))
        painter.end()

        self.dark_mode_btn.setText("")
        self.dark_mode_btn.setIcon(QIcon(tinted_pixmap))

    def _create_text_box(self, placeholder):
        text_box = QTextEdit()
        text_box.setReadOnly(True)
        text_box.setPlaceholderText(placeholder)
        text_box.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        text_box.setLineWrapMode(QTextEdit.WidgetWidth)
        text_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return text_box

    def _create_text_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 8, 0)
        title_row.setSpacing(10)
        title_row.addWidget(self.title_label_box, 1)
        title_row.addWidget(self.recommend_title_btn)
        layout.addLayout(title_row)

        layout.addWidget(self.text_box, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 8, 0)
        action_row.setSpacing(10)
        action_row.addStretch()
        action_row.addWidget(self.score_widget)
        action_row.addWidget(self.evaluate_btn)
        action_row.addWidget(self.text_history_btn)
        layout.addLayout(action_row)
        return page

    def _create_auth_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 14, 22, 10)
        panel_layout.setSpacing(4)

        top_row = QHBoxLayout()
        self.auth_title_label = QLabel("로그인")
        self.auth_title_label.setObjectName("sectionTitle")
        top_row.addWidget(self.auth_title_label)
        top_row.addStretch()
        self.auth_mode_link_btn = QPushButton("회원가입")
        self.auth_mode_link_btn.setObjectName("authLinkButton")
        self.auth_mode_link_btn.clicked.connect(self.show_signup_form)
        top_row.addWidget(self.auth_mode_link_btn)
        self.auth_close_btn = QPushButton("X")
        self.auth_close_btn.setObjectName("closeSettingsButton")
        self.auth_close_btn.setFixedSize(34, 34)
        self.auth_close_btn.clicked.connect(self.close_auth_page)
        top_row.addWidget(self.auth_close_btn)
        panel_layout.addLayout(top_row)

        self.auth_stack_container = QWidget()
        self.auth_stack_container.setMinimumHeight(200)
        self.auth_stack = QStackedLayout(self.auth_stack_container)
        self.auth_stack.addWidget(self._create_login_form())
        self.auth_stack.addWidget(self._create_signup_form())
        self.auth_stack.addWidget(self._create_login_required_page())
        panel_layout.addWidget(self.auth_stack_container, 1)

        layout.addWidget(panel)
        return page

    def _create_login_required_page(self):
        page = QWidget()
        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, -2, 0, 0)
        outer_layout.setSpacing(10)

        message_box = QWidget()
        message_box.setFixedWidth(360)
        layout = QVBoxLayout(message_box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("이 기능은 로그인해야 사용할 수 있습니다.")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        message = QLabel("기록은 계정에 연결된 문서별 데이터로 저장됩니다.\n로그인 후 현재 문서의 기록을 확인할 수 있습니다.")
        message.setObjectName("authLabel")
        message.setAlignment(Qt.AlignCenter)
        message.setWordWrap(True)
        layout.addWidget(message)

        self.auth_required_login_btn = QPushButton("로그인하러 가기")
        self.auth_required_login_btn.setObjectName("authSubmitButton")
        self.auth_required_login_btn.setFixedWidth(210)
        self.auth_required_login_btn.setFixedHeight(32)
        self.auth_required_login_btn.clicked.connect(self.show_login_form)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.auth_required_login_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        outer_layout.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(message_box)
        row.addStretch()
        outer_layout.addLayout(row)
        outer_layout.addStretch()
        return page

    def _create_login_form(self):
        page = QWidget()
        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, -4, 0, 0)
        outer_layout.setSpacing(0)

        form = QWidget()
        form.setFixedWidth(310)
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        username_label = QLabel("아이디")
        username_label.setObjectName("authLabel")
        username_label.setFixedHeight(19)
        layout.addWidget(username_label)
        self.login_username_input = QLineEdit()
        self.login_username_input.setObjectName("authInput")
        self.login_username_input.setFixedHeight(34)
        layout.addWidget(self.login_username_input)
        layout.addSpacing(10)
        password_label = QLabel("비밀번호")
        password_label.setObjectName("authLabel")
        password_label.setFixedHeight(19)
        layout.addWidget(password_label)
        self.login_password_input = QLineEdit()
        self.login_password_input.setObjectName("authInput")
        self.login_password_input.setFixedHeight(34)
        self.login_password_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.login_password_input)
        layout.addSpacing(2)
        self.login_remember_checkbox = QCheckBox("로그인 유지")
        self.login_remember_checkbox.setObjectName("settingsSubCheck")
        layout.addWidget(self.login_remember_checkbox)
        layout.addSpacing(5)
        self.login_submit_btn = QPushButton("로그인")
        self.login_submit_btn.setObjectName("authSubmitButton")
        self.login_submit_btn.setFixedWidth(210)
        self.login_submit_btn.setFixedHeight(32)
        submit_row = QHBoxLayout()
        submit_row.setContentsMargins(0, 0, 0, 0)
        submit_row.addStretch()
        submit_row.addWidget(self.login_submit_btn)
        submit_row.addStretch()
        layout.addLayout(submit_row)
        self.login_password_input.returnPressed.connect(self.login_submit_btn.click)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(form)
        row.addStretch()
        outer_layout.addLayout(row)
        outer_layout.addStretch()
        return page

    def _create_signup_form(self):
        page = QWidget()
        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, -8, 0, 0)
        outer_layout.setSpacing(0)

        form = QWidget()
        form.setFixedWidth(310)
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        username_label = QLabel("아이디")
        username_label.setObjectName("authLabel")
        username_label.setFixedHeight(17)
        layout.addWidget(username_label)
        self.signup_username_input = QLineEdit()
        self.signup_username_input.setObjectName("authInput")
        self.signup_username_input.setFixedHeight(29)
        layout.addWidget(self.signup_username_input)
        layout.addSpacing(2)
        password_label = QLabel("비밀번호")
        password_label.setObjectName("authLabel")
        password_label.setFixedHeight(17)
        layout.addWidget(password_label)
        self.signup_password_input = QLineEdit()
        self.signup_password_input.setObjectName("authInput")
        self.signup_password_input.setFixedHeight(29)
        self.signup_password_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.signup_password_input)
        layout.addSpacing(2)
        confirm_label = QLabel("비밀번호 확인")
        confirm_label.setObjectName("authLabel")
        confirm_label.setFixedHeight(17)
        layout.addWidget(confirm_label)
        self.signup_password_confirm_input = QLineEdit()
        self.signup_password_confirm_input.setObjectName("authInput")
        self.signup_password_confirm_input.setFixedHeight(29)
        self.signup_password_confirm_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.signup_password_confirm_input)
        layout.addSpacing(4)
        self.signup_submit_btn = QPushButton("가입하기")
        self.signup_submit_btn.setObjectName("authSubmitButton")
        self.signup_submit_btn.setFixedWidth(210)
        self.signup_submit_btn.setFixedHeight(30)
        submit_row = QHBoxLayout()
        submit_row.setContentsMargins(0, 0, 0, 0)
        submit_row.addStretch()
        submit_row.addWidget(self.signup_submit_btn)
        submit_row.addStretch()
        layout.addLayout(submit_row)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(form)
        row.addStretch()
        outer_layout.addLayout(row)
        outer_layout.addStretch()
        return page

    def _create_prompt_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 24)
        panel_layout.setSpacing(14)

        self.prompt_title_label = QLabel("")
        self.prompt_title_label.setObjectName("sectionTitle")
        self.prompt_message_label = QLabel("")
        self.prompt_message_label.setObjectName("promptMessage")
        self.prompt_message_label.setWordWrap(True)
        panel_layout.addWidget(self.prompt_title_label)
        panel_layout.addWidget(self.prompt_message_label, 1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.prompt_no_btn = QPushButton("아니요")
        self.prompt_no_btn.setObjectName("secondaryButton")
        self.prompt_yes_btn = QPushButton("예")
        self.prompt_yes_btn.setObjectName("primaryButton")
        button_row.addWidget(self.prompt_no_btn)
        button_row.addWidget(self.prompt_yes_btn)
        panel_layout.addLayout(button_row)
        layout.addWidget(panel)
        return page

    def _create_spell_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.spell_scope_widget = QWidget()
        scope_row = QHBoxLayout(self.spell_scope_widget)
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(8)
        scope_label = QLabel("검사 범위")
        scope_label.setObjectName("historyFieldLabel")
        scope_row.addWidget(scope_label)
        for scope in ("current_sentence", "current_paragraph", "full_text"):
            scope_row.addWidget(self.spell_scope_buttons[scope])
        scope_row.addStretch()
        layout.addWidget(self.spell_scope_widget)

        layout.addWidget(self.spell_box, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 8, 0)
        button_row.addStretch()
        button_row.addWidget(self.apply_correction_btn)
        button_row.addWidget(self.refresh_btn)
        button_row.addWidget(self.spell_history_btn)
        layout.addLayout(button_row)
        return page

    def _create_action_tab(self, text_box, action_button):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addWidget(text_box, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 8, 0)
        button_row.addStretch()
        button_row.addWidget(action_button)
        if text_box is self.summary_box:
            button_row.addWidget(self.summary_history_btn)
        layout.addLayout(button_row)
        return page

    def _create_tone_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        control_row = QHBoxLayout()
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.setSpacing(10)
        control_row.addWidget(self.tone_input, 1)
        control_row.addWidget(self.run_tone_btn)
        control_row.addWidget(self.tone_history_btn)

        layout.addLayout(control_row)
        layout.addWidget(self.tone_box, 1)
        return page

    def _create_history_button(self):
        button = QPushButton("")
        button.setObjectName("iconButton")
        button.setFixedSize(40, 40)
        button.setToolTip("기록 보기")
        icon_path = self._find_asset_path(("list.png", "list.svg"))
        if icon_path:
            button.setIcon(QIcon(str(icon_path)))
            button.setIconSize(QSize(18, 18))
        else:
            button.setText("≡")
        return button

    def _find_asset_path(self, names):
        base_dir = Path(__file__).resolve().parent.parent
        for search_dir in (base_dir / "icon", base_dir):
            for name in names:
                candidate = search_dir / name
                if candidate.exists():
                    return candidate
        return None

    def _create_settings_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 14)
        panel_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(4, 0, 0, 0)
        top_row.setSpacing(8)

        section_title = QLabel("설정")
        section_title.setObjectName("sectionTitle")
        top_row.addWidget(section_title)
        top_row.addStretch()
        top_row.addWidget(self.close_settings_btn)

        panel_layout.addLayout(top_row)

        self.settings_scroll_area = QScrollArea()
        self.settings_scroll_area.setObjectName("settingsScrollArea")
        self.settings_scroll_area.setWidgetResizable(True)
        self.settings_scroll_area.setFrameShape(QFrame.NoFrame)
        self.settings_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("settingsScrollContent")

        section = QVBoxLayout()
        section.setContentsMargins(4, 4, 6, 4)
        section.setSpacing(8)

        section.addWidget(self.default_dark_mode_checkbox)
        section.addSpacing(4)
        section.addWidget(self.history_enabled_checkbox)
        section.addSpacing(4)
        section.addWidget(self.clipboard_mode_checkbox)
        section.addSpacing(4)
        section.addWidget(self.drag_mode_checkbox)
        self.drag_replace_mode_container = QWidget()
        drag_sub_option_row = QHBoxLayout(self.drag_replace_mode_container)
        drag_sub_option_row.setContentsMargins(34, 0, 0, 0)
        drag_sub_option_row.setSpacing(0)
        drag_sub_option_row.addWidget(self.drag_replace_mode_checkbox)
        drag_sub_option_row.addStretch()
        section.addWidget(self.drag_replace_mode_container)
        section.addSpacing(4)
        section.addWidget(self.realtime_mode_checkbox)
        self.realtime_replace_mode_container = QWidget()
        realtime_sub_option_row = QHBoxLayout(self.realtime_replace_mode_container)
        realtime_sub_option_row.setContentsMargins(34, 0, 0, 0)
        realtime_sub_option_row.setSpacing(0)
        realtime_sub_option_row.addWidget(self.realtime_replace_mode_checkbox)
        realtime_sub_option_row.addStretch()
        section.addWidget(self.realtime_replace_mode_container)
        section.addStretch()

        scroll_content.setLayout(section)
        self.settings_scroll_area.setWidget(scroll_content)
        panel_layout.addWidget(self.settings_scroll_area, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 2, 0)
        button_row.addWidget(self.account_manage_btn)
        button_row.addSpacing(8)
        button_row.addWidget(self.settings_notice_label, 0, Qt.AlignLeft | Qt.AlignBottom)
        button_row.addStretch()
        button_row.addWidget(self.save_settings_btn)
        panel_layout.addLayout(button_row)

        layout.addWidget(panel)
        return page

    def _create_account_verify_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        panel = QFrame()
        panel.setObjectName("settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 16, 22, 14)
        panel_layout.setSpacing(10)

        top_row = QHBoxLayout()
        title = QLabel("계정 관리 인증")
        title.setObjectName("sectionTitle")
        top_row.addWidget(title)
        top_row.addStretch()
        top_row.addWidget(self.account_verify_close_btn)
        panel_layout.addLayout(top_row)

        guide = QLabel("계정 정보를 변경하려면 현재 비밀번호를 한 번 더 확인합니다.")
        guide.setObjectName("promptMessage")
        guide.setWordWrap(True)
        panel_layout.addWidget(guide)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(185, 12, 185, 0)
        form_layout.setSpacing(6)
        label = QLabel("비밀번호")
        label.setObjectName("authLabel")
        form_layout.addWidget(label)
        self.account_verify_password_input.setFixedHeight(34)
        form_layout.addWidget(self.account_verify_password_input)
        form_layout.addSpacing(4)
        submit_row = QHBoxLayout()
        submit_row.addStretch()
        submit_row.addWidget(self.account_verify_submit_btn)
        submit_row.addStretch()
        form_layout.addLayout(submit_row)
        panel_layout.addWidget(form, 0, Qt.AlignTop)
        panel_layout.addStretch()

        layout.addWidget(panel)
        return page

    def _create_account_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        panel = QFrame()
        panel.setObjectName("settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 14)
        panel_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(4, 0, 0, 0)
        title = QLabel("계정 관리")
        title.setObjectName("sectionTitle")
        top_row.addWidget(title)
        top_row.addStretch()
        top_row.addWidget(self.account_close_btn)
        panel_layout.addLayout(top_row)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 6, 4)
        content_layout.setSpacing(14)

        info_title = QLabel("계정 정보")
        info_title.setObjectName("accountSectionTitle")
        content_layout.addWidget(info_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        rows = (
            ("이름:", self.account_name_input, self.account_name_edit_btn),
            ("아이디:", self.account_username_input, self.account_username_edit_btn),
            ("비밀번호:", self.account_password_input, self.account_password_edit_btn),
        )
        for row, (label_text, input_widget, button) in enumerate(rows):
            label = QLabel(label_text)
            label.setObjectName("accountFieldLabel")
            input_widget.setFixedHeight(34)
            button.setFixedWidth(64)
            grid.addWidget(label, row, 0)
            grid.addWidget(input_widget, row, 1)
            grid.addWidget(button, row, 2)
        content_layout.addLayout(grid)

        delete_title = QLabel("계정 탈퇴")
        delete_title.setObjectName("accountSectionTitle")
        content_layout.addSpacing(10)
        content_layout.addWidget(delete_title)
        delete_desc = QLabel("계정을 탈퇴하면 저장된 계정 정보와 기록이 삭제됩니다. 이 작업은 되돌릴 수 없습니다.")
        delete_desc.setObjectName("promptMessage")
        delete_desc.setWordWrap(True)
        content_layout.addWidget(delete_desc)
        delete_row = QHBoxLayout()
        delete_row.addStretch()
        delete_row.addWidget(self.account_delete_btn)
        content_layout.addLayout(delete_row)
        content_layout.addStretch()
        scroll_area.setWidget(content)
        panel_layout.addWidget(scroll_area, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.account_notice_label, 0, Qt.AlignLeft | Qt.AlignBottom)
        button_row.addStretch()
        button_row.addWidget(self.account_save_btn)
        panel_layout.addLayout(button_row)
        layout.addWidget(panel)
        return page

    def _create_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 14)
        panel_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(4, 0, 0, 0)
        self.history_title_label = QLabel("기록")
        self.history_title_label.setObjectName("sectionTitle")
        top_row.addWidget(self.history_title_label)
        top_row.addStretch()
        self.history_back_btn = QPushButton("")
        self.history_back_btn.setObjectName("closeSettingsButton")
        self.history_back_btn.setFixedSize(34, 34)
        back_path = self._find_asset_path(("back.png", "back.svg"))
        if back_path:
            self.history_back_btn.setIcon(QIcon(str(back_path)))
            self.history_back_btn.setIconSize(QSize(18, 18))
        else:
            self.history_back_btn.setText("<")
        top_row.addWidget(self.history_back_btn)
        self.history_close_btn = QPushButton("X")
        self.history_close_btn.setObjectName("closeSettingsButton")
        self.history_close_btn.setFixedSize(34, 34)
        self.history_close_btn.clicked.connect(self.close_history_page)
        top_row.addWidget(self.history_close_btn)
        panel_layout.addLayout(top_row)

        self.history_scroll = QScrollArea()
        self.history_scroll.setObjectName("settingsScrollArea")
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.NoFrame)
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel_layout.addWidget(self.history_scroll, 1)

        layout.addWidget(panel)
        return page

    def show_history_list(self, feature_type, logs):
        self._history_feature_type = feature_type
        self._last_history_logs = list(logs)
        self.history_title_label.setText(self._history_title(feature_type))
        self._disconnect_history_back()
        self.history_back_btn.clicked.connect(self.close_history_page)
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 6, 4)
        layout.setSpacing(8)

        if not logs:
            empty_label = QLabel("\uc800\uc7a5\ub41c \uae30\ub85d\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.")
            empty_label.setObjectName("historyEmptyLabel")
            layout.addWidget(empty_label)
        for entry in self._group_history_logs(logs):
            if entry.get("kind") == "group":
                button = QPushButton(self._history_group_label(entry))
                button.setObjectName("historyListButton")
                button.setMinimumHeight(64)
                button.clicked.connect(lambda checked=False, item=entry: self.show_history_group(feature_type, item))
            else:
                log = entry.get("log", {})
                button = QPushButton(self._history_list_label(log))
                button.setObjectName("historyListButton")
                button.setMinimumHeight(58)
                button.clicked.connect(lambda checked=False, item=log: self.show_history_detail(feature_type, item))
            layout.addWidget(button)
        layout.addStretch()
        self.history_scroll.setWidget(content)
        self.content_stack.setCurrentIndex(2)
        self.settings_btn.setChecked(False)

    def show_history_group(self, feature_type, group):
        self.history_title_label.setText(self._history_group_title(group))
        self._disconnect_history_back()
        self.history_back_btn.clicked.connect(lambda: self.show_history_list(feature_type, getattr(self, "_last_history_logs", [])))
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 6, 4)
        layout.setSpacing(8)
        for log in group.get("logs", []):
            button = QPushButton(self._history_list_label(log))
            button.setObjectName("historyListButton")
            button.setMinimumHeight(58)
            button.clicked.connect(lambda checked=False, item=log: self.show_history_detail(feature_type, item))
            layout.addWidget(button)
        layout.addStretch()
        self.history_scroll.setWidget(content)

    def show_history_detail(self, feature_type, log):
        self.history_title_label.setText("기록 상세")
        self._disconnect_history_back()
        self.history_back_btn.clicked.connect(lambda: self.show_history_list(feature_type, getattr(self, "_last_history_logs", [])))
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 6, 4)
        layout.setSpacing(10)
        detail_feature_type = log.get("feature_type") if int(feature_type or 0) == 0 else feature_type
        for label, value, long_text in self._history_detail_fields(detail_feature_type, log):
            row_label = QLabel(label)
            row_label.setObjectName("historyFieldLabel")
            layout.addWidget(row_label)
            if long_text:
                value_box = QTextEdit()
                value_box.setObjectName("historyLongTextBox")
                value_box.setReadOnly(True)
                value_box.setLineWrapMode(QTextEdit.WidgetWidth)
                value_box.setPlainText(str(value or ""))
                value_box.setMinimumHeight(96)
                value_box.setMaximumHeight(170)
                layout.addWidget(value_box)
            else:
                value_label = QLabel(str(value or ""))
                value_label.setWordWrap(True)
                value_label.setObjectName("historyShortValue")
                layout.addWidget(value_label)
        layout.addStretch()
        self.history_scroll.setWidget(content)

    def close_history_page(self):
        self.content_stack.setCurrentIndex(0)

    def _disconnect_history_back(self):
        try:
            self.history_back_btn.clicked.disconnect()
        except Exception:
            pass

    def show_auth_page(self):
        self.show_login_form()
        self.content_stack.setCurrentIndex(3)
        self.settings_btn.setChecked(False)
        self.login_username_input.setFocus()

    def show_login_required_page(self):
        self.auth_title_label.setText("로그인 필요")
        self.auth_mode_link_btn.setText("회원가입")
        try:
            self.auth_mode_link_btn.clicked.disconnect()
        except Exception:
            pass
        self.auth_mode_link_btn.clicked.connect(self.show_signup_form)
        self.auth_stack.setCurrentIndex(2)
        self.content_stack.setCurrentIndex(3)
        self.settings_btn.setChecked(False)
        self.auth_required_login_btn.setFocus()

    def close_auth_page(self):
        self.content_stack.setCurrentIndex(0)

    def show_login_form(self):
        self.auth_title_label.setText("로그인")
        self.auth_mode_link_btn.setText("회원가입")
        try:
            self.auth_mode_link_btn.clicked.disconnect()
        except Exception:
            pass
        self.auth_mode_link_btn.clicked.connect(self.show_signup_form)
        self.auth_stack.setCurrentIndex(0)

    def show_signup_form(self):
        self.auth_title_label.setText("회원가입")
        self.auth_mode_link_btn.setText("로그인")
        try:
            self.auth_mode_link_btn.clicked.disconnect()
        except Exception:
            pass
        self.auth_mode_link_btn.clicked.connect(self.show_login_form)
        self.auth_stack.setCurrentIndex(1)

    def show_prompt(
        self,
        title,
        message,
        yes_callback=None,
        no_callback=None,
        yes_text="예",
        no_text="아니요",
    ):
        self.prompt_title_label.setText(title)
        self.prompt_message_label.setText(message)
        self._disconnect_prompt_buttons()
        self.prompt_no_btn.show()
        self.prompt_yes_btn.setText(yes_text)
        self.prompt_no_btn.setText(no_text)
        self.prompt_yes_btn.clicked.connect(lambda: self._resolve_prompt(yes_callback))
        self.prompt_no_btn.clicked.connect(lambda: self._resolve_prompt(no_callback))
        self.content_stack.setCurrentIndex(4)
        self.settings_btn.setChecked(False)

    def show_notice(self, title, message):
        self.prompt_title_label.setText(title)
        self.prompt_message_label.setText(message)
        self._disconnect_prompt_buttons()
        self.prompt_no_btn.hide()
        self.prompt_yes_btn.setText("확인")
        self.prompt_yes_btn.clicked.connect(lambda: self._resolve_prompt(None))
        self.content_stack.setCurrentIndex(4)
        self.settings_btn.setChecked(False)

    def _resolve_prompt(self, callback):
        self.prompt_no_btn.show()
        self.prompt_yes_btn.setText("예")
        self.prompt_no_btn.setText("아니요")
        self.content_stack.setCurrentIndex(0)
        if callback:
            callback()

    def _disconnect_prompt_buttons(self):
        for button in (self.prompt_yes_btn, self.prompt_no_btn):
            try:
                button.clicked.disconnect()
            except Exception:
                pass

    def _history_title(self, feature_type):
        return {
            0: "\ud604\uc7ac \ubb38\uc11c \uae30\ub85d",
            1: "\ud14d\uc2a4\ud2b8 \uae30\ub85d",
            2: "\uad50\uc815 \uae30\ub85d",
            3: "\uc694\uc57d \uae30\ub85d",
            4: "\ubb38\uccb4 \ubcc0\uacbd \uae30\ub85d",
        }.get(feature_type, "\uae30\ub85d")

    def _history_kind_label(self, feature_type, feature_label=None):
        label = str(feature_label or "").strip()
        if label:
            return label
        return self._history_title(feature_type)

    def _first_meaningful_line(self, text):
        for line in str(text or "").splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    def _history_display_source(self, log):
        title = (log.get("title") or "").strip()
        if title:
            return title
        return (
            self._first_meaningful_line(log.get("input_text"))
            or self._first_meaningful_line(log.get("output_text"))
            or "\uc81c\ubaa9 \uc5c6\uc74c"
        )

    def _history_preview(self, log):
        source = self._history_display_source(log).strip()
        if not source:
            return "\uc81c\ubaa9 \uc5c6\uc74c..."
        return source if len(source) <= 18 else source[:18] + "..."

    def _format_history_time(self, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return raw.replace("T", " ")[:19]

    def _history_group_key(self, log):
        return " ".join(self._history_display_source(log).lower().split())

    def _group_history_logs(self, logs):
        groups = []
        for log in list(logs or []):
            key = self._history_group_key(log)
            if not key:
                groups.append({"key": "", "title_key": "", "logs": [log]})
                continue
            title_key = key if (log.get("title") or "").strip() else ""
            matched = None
            for group in groups:
                group_key = group.get("key", "")
                group_title_key = group.get("title_key", "")
                if title_key and group_title_key and title_key == group_title_key:
                    matched = group
                    break
                if group_key and SequenceMatcher(None, key, group_key).ratio() >= 0.60:
                    matched = group
                    break
            if matched is None:
                groups.append({"key": key, "title_key": title_key, "logs": [log]})
            else:
                matched["logs"].append(log)
                if title_key and not matched.get("title_key"):
                    matched["title_key"] = title_key
                    matched["key"] = key
        entries = []
        for group in groups:
            if group.get("title_key") or len(group.get("logs", [])) >= 2:
                entries.append({"kind": "group", **group})
            else:
                entries.append({"kind": "single", "log": group.get("logs", [{}])[0]})
        return entries

    def _history_group_title(self, group):
        logs = group.get("logs", [])
        if not logs:
            return "\uae30\ub85d \ubb36\uc74c"
        return self._history_preview(logs[0]) + " \uae30\ub85d"

    def _history_group_label(self, group):
        logs = group.get("logs", [])
        first = logs[0] if logs else {}
        title = self._history_preview(first)
        created_at = self._format_history_time(first.get("created_at", ""))
        return f"{title} \uae30\ub85d\n{len(logs)}\uac1c \uae30\ub85d  {created_at}"

    def _history_list_label(self, log):
        preview = self._history_preview(log)
        kind = self._history_kind_label(log.get("feature_type"), log.get("feature_label"))
        created_at = self._format_history_time(log.get("created_at", ""))
        return f"{preview} {kind}\n{created_at}"

    def _history_detail_fields(self, feature_type, log):
        try:
            feature_type = int(feature_type or 0)
        except Exception:
            feature_type = 0
        created_at = self._format_history_time(log.get("created_at", ""))
        kind_label = self._history_kind_label(log.get("feature_type"), log.get("feature_label"))
        if feature_type == 1:
            return [
                ("\uc6d0\ubcf8", log.get("input_text"), True),
                ("\uc6d0\ubcf8 \uc810\uc218", log.get("score"), False),
                ("\ud3c9\uac00 \uc774\uc720", log.get("evaluation_reason"), True),
                ("\ubb38\uc11c \uc774\ub984", log.get("title"), False),
                ("\uc885\ub958", kind_label, False),
                ("\uc800\uc7a5 \uc2dc\uac04", created_at, False),
            ]
        if feature_type == 2:
            return [
                ("\uc6d0\ubcf8 \uae00", log.get("input_text"), True),
                ("\uad50\uc815 \ud3c9\uac00", log.get("spelling_feedback"), True),
                ("\uad50\uc815\ub41c \uae00", log.get("output_text"), True),
                ("\ubb38\uc11c \uc774\ub984", log.get("title"), False),
                ("\uc885\ub958", kind_label, False),
                ("\uc800\uc7a5 \uc2dc\uac04", created_at, False),
            ]
        if feature_type == 3:
            return [
                ("\uc6d0\ubcf8 \uae00", log.get("input_text"), True),
                ("\uc694\uc57d\uae00", log.get("output_text"), True),
                ("\ubb38\uc11c \uc774\ub984", log.get("title"), False),
                ("\uc885\ub958", kind_label, False),
                ("\uc800\uc7a5 \uc2dc\uac04", created_at, False),
            ]
        return [
            ("\uc6d0\ubcf8 \uae00", log.get("input_text"), True),
            ("\uc81c\uc2dc\ud55c \ubb38\uccb4", log.get("tone"), False),
            ("\ubb38\uccb4 \ubcc0\uacbd\ub41c \uae00", log.get("output_text"), True),
            ("\ubb38\uc11c \uc774\ub984", log.get("title"), False),
            ("\uc885\ub958", kind_label, False),
            ("\uc800\uc7a5 \uc2dc\uac04", created_at, False),
        ]

    def apply_shadow(self):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(35)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.card.setGraphicsEffect(shadow)

    def _blended_colors(self):
        return {
            key: self._blend_color(self.LIGHT_THEME[key], self.DARK_THEME[key], self._theme_mix)
            for key in self.LIGHT_THEME
        }

    def _blend_color(self, start, end, mix):
        start_color = QColor(start)
        end_color = QColor(end)
        r = round(start_color.red() + (end_color.red() - start_color.red()) * mix)
        g = round(start_color.green() + (end_color.green() - start_color.green()) * mix)
        b = round(start_color.blue() + (end_color.blue() - start_color.blue()) * mix)
        a = round(start_color.alpha() + (end_color.alpha() - start_color.alpha()) * mix)
        return QColor(r, g, b, a).name(QColor.HexArgb if a < 255 else QColor.HexRgb)

    def _on_theme_mix_changed(self, value):
        self._theme_mix = float(value)
        self.apply_theme()

    def apply_theme(self):
        colors = self._blended_colors()
        self.dark_mode_btn.setChecked(self.is_dark_mode)

        self.setStyleSheet(
            f"""
            QWidget {{
                background: {colors["window_bg"]};
                color: {colors["text"]};
                font-size: 14px;
            }}
            QFrame#panelCard {{
                background: {colors["card_bg"]};
                border: 1px solid {colors["card_border"]};
                border-radius: 28px;
            }}
            QFrame#settingsPanel {{
                background: {colors["settings_panel_bg"]};
                border: 1px solid {colors["settings_panel_border"]};
                border-radius: 22px;
            }}
            QScrollArea#settingsScrollArea,
            QWidget#settingsScrollContent {{
                background: {colors["settings_panel_bg"]};
                border: none;
            }}
            QLabel#titleLabel {{
                color: {colors["title"]};
                font-size: 28px;
                font-weight: 700;
                letter-spacing: 0.4px;
            }}
            QLabel#subtitleLabel {{
                color: {colors["muted"]};
                font-size: 13px;
            }}
            QLabel#inputModeStatusLabel {{
                color: {colors["muted"]};
                font-size: 12px;
                font-weight: 600;
                padding: 0px;
            }}
            QLabel#activeWindowLabel {{
                color: {colors["muted"]};
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#userIdentityLabel {{
                color: {colors["muted"]};
                font-size: 12px;
                font-weight: 700;
                padding: 0px;
            }}
            QLabel#sectionTitle {{
                color: {colors["settings_text"]};
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#authLabel {{
                color: #2f241f;
                background: transparent;
                font-size: 13px;
                font-weight: 700;
                padding: 0px;
            }}
            QLabel#scoreLabel {{
                background: {colors["score_bg"]};
                color: {colors["text"]};
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#titleValueLabel {{
                background: {colors["score_bg"]};
                color: {colors["text"]};
                border-radius: 12px;
                padding: 0 14px;
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#settingsNotice {{
                background: {colors["score_bg"]};
                color: {colors["settings_notice_text"]};
                border-radius: 12px;
                border: 1px solid {colors["settings_panel_border"]};
                padding: 7px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QCheckBox#settingsCheck {{
                color: {colors["settings_text"]};
                spacing: 10px;
                font-size: 14px;
            }}
            QCheckBox#settingsCheck:disabled {{
                color: {colors["muted"]};
            }}
            QCheckBox#settingsSubCheck {{
                color: {colors["settings_text"]};
                spacing: 8px;
                font-size: 11px;
            }}
            QCheckBox#settingsSubCheck:disabled {{
                color: {colors["muted"]};
            }}
            QCheckBox#settingsCheck::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 5px;
                border: 1px solid {colors["settings_check_border"]};
                background: {colors["settings_check_bg"]};
            }}
            QCheckBox#settingsCheck::indicator:disabled {{
                border: 1px solid #ddd4ca;
                background: #eee8e1;
            }}
            QCheckBox#settingsSubCheck::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid {colors["settings_check_border"]};
                background: {colors["settings_check_bg"]};
            }}
            QCheckBox#settingsSubCheck::indicator:disabled {{
                border: 1px solid #ddd4ca;
                background: #eee8e1;
            }}
            QCheckBox#settingsCheck::indicator:checked {{
                background: {colors["settings_check_checked"]};
                border: 1px solid {colors["settings_check_checked"]};
            }}
            QCheckBox#settingsSubCheck::indicator:checked {{
                background: {colors["settings_check_checked"]};
                border: 1px solid {colors["settings_check_checked"]};
            }}
            QLineEdit#toneInput {{
                background: {colors["input_bg"]};
                color: {colors["text"]};
                border: 1px solid {colors["editor_border"]};
                border-radius: 14px;
                padding: 10px 14px;
            }}
            QLineEdit#authInput {{
                background: #fffaf4;
                color: #2f241f;
                border: 1px solid #dccbbb;
                border-radius: 13px;
                padding: 0px 12px;
                font-size: 13px;
            }}
            QLineEdit#authInput:focus {{
                border: 1px solid {colors["accent"]};
            }}
            QLineEdit#accountInput {{
                background: #fffaf4;
                color: #2f241f;
                border: 1px solid #dccbbb;
                border-radius: 13px;
                padding: 0px 12px;
                font-size: 13px;
            }}
            QLineEdit#accountInput:focus {{
                border: 1px solid {colors["accent"]};
            }}
            QLineEdit#toneInput::placeholder {{
                color: {colors["muted"]};
            }}
            QTabWidget::pane {{
                border: 1px solid {colors["editor_border"]};
                border-radius: 20px;
                background: {colors["editor_bg"]};
                top: -1px;
            }}
            QTabWidget::tab-bar {{
                left: 20px;
            }}
            QTabBar::tab {{
                background: {colors["tab_bg"]};
                color: {colors["tab_text"]};
                padding: 10px 18px;
                margin-right: 8px;
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
                min-width: 88px;
                font-weight: 600;
                outline: none;
            }}
            QTabBar::tab:focus {{
                outline: none;
            }}
            QTabBar::tab:selected {{
                background: {colors["tab_selected"]};
            }}
            QTextEdit {{
                background: {colors["editor_bg"]};
                color: {colors["text"]};
                border: none;
                border-radius: 18px;
                padding: 14px;
                selection-background-color: {colors["accent"]};
                selection-color: {colors["accent_text"]};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 12px 6px 12px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {colors["button_bg"]};
                border-radius: 5px;
                min-height: 28px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {colors["button_hover"]};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
                height: 0px;
            }}
            QPushButton {{
                border: none;
                border-radius: 14px;
                padding: 10px 16px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#primaryButton {{
                background: {colors["accent"]};
                color: {colors["accent_text"]};
            }}
            QPushButton#primaryButton:hover {{
                background: {colors["accent_hover"]};
            }}
            QPushButton#authSubmitButton {{
                background: {colors["accent"]};
                color: {colors["accent_text"]};
                border: none;
                border-radius: 13px;
                padding: 0px 12px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#authSubmitButton:hover {{
                background: {colors["accent_hover"]};
            }}
            QPushButton#scoreReasonButton {{
                background: {colors["button_bg"]};
                color: {colors["button_text"]};
                border-radius: 12px;
                padding: 6px 10px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#scoreReasonButton:hover {{
                background: {colors["button_hover"]};
            }}
            QPushButton#scoreReasonButton:disabled {{
                background: #e1e1e1;
                color: #8a8a8a;
            }}
            QPushButton#secondaryButton {{
                background: {colors["button_bg"]};
                color: {colors["button_text"]};
            }}
            QPushButton#secondaryButton:hover {{
                background: {colors["button_hover"]};
            }}
            QPushButton#secondaryButton:disabled {{
                background: #e1e1e1;
                color: #8a8a8a;
            }}
            QPushButton#scopeButton {{
                background: {colors["button_bg"]};
                color: {colors["button_text"]};
                border-radius: 14px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton#scopeButton:hover {{
                background: {colors["button_hover"]};
            }}
            QPushButton#scopeButton:checked {{
                background: {colors["accent"]};
                color: {colors["accent_text"]};
            }}
            QPushButton#dangerButton {{
                background: transparent;
                color: #b64040;
                border: 1px solid #d34a4a;
            }}
            QPushButton#dangerButton:hover {{
                background: #f7e7e7;
            }}
            QPushButton#authLinkButton {{
                background: transparent;
                color: {colors["accent"]};
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 7px 10px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#authLinkButton:hover {{
                border: 1px solid {colors["accent"]};
            }}
            QPushButton#ghostButton {{
                background: {colors["danger_bg"]};
                color: {colors["button_text"]};
                padding-left: 14px;
                padding-right: 14px;
            }}
            QPushButton#ghostButton:hover {{
                background: {colors["danger_hover"]};
            }}
            QPushButton#closeSettingsButton {{
                background: {colors["danger_bg"]};
                color: {colors["button_text"]};
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
                padding: 0px;
                border-radius: 12px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#closeSettingsButton:hover {{
                background: {colors["danger_hover"]};
            }}
            QPushButton#iconButton {{
                background: {colors["button_bg"]};
                color: {colors["button_text"]};
                min-width: 40px;
                max-width: 40px;
                min-height: 40px;
                max-height: 40px;
                padding: 0px;
                border-radius: 14px;
            }}
            QPushButton#iconButton:hover {{
                background: {colors["button_hover"]};
            }}
            QPushButton#iconButton[loginRequired="true"] {{
                background: #d8d8d8;
                color: #777777;
            }}
            QPushButton#iconButton[loginRequired="true"]:hover {{
                background: #cfcfcf;
            }}
            QPushButton#historyListButton {{
                background: {colors["input_bg"]};
                color: {colors["text"]};
                border: 1px solid {colors["editor_border"]};
                border-radius: 10px;
                padding: 9px 12px;
                text-align: left;
                font-size: 13px;
            }}
            QPushButton#historyListButton:hover {{
                background: {colors["button_bg"]};
            }}
            QLabel#historyEmptyLabel,
            QLabel#historyShortValue,
            QLabel#historyLongValue,
            QLabel#promptMessage {{
                color: {colors["text"]};
                background: {colors["input_bg"]};
                border: 1px solid {colors["editor_border"]};
                border-radius: 10px;
                padding: 9px 11px;
            }}
            QTextEdit#historyLongTextBox {{
                color: {colors["text"]};
                background: {colors["input_bg"]};
                border: 1px solid {colors["editor_border"]};
                border-radius: 10px;
                padding: 8px 10px;
                font-size: 13px;
            }}
            QTextEdit#historyLongTextBox QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 7px 3px 7px 0;
            }}
            QTextEdit#historyLongTextBox QScrollBar::handle:vertical {{
                background: {colors["button_bg"]};
                border-radius: 4px;
                min-height: 28px;
            }}
            QTextEdit#historyLongTextBox QScrollBar::handle:vertical:hover {{
                background: {colors["accent"]};
            }}
            QTextEdit#historyLongTextBox QScrollBar::add-line:vertical,
            QTextEdit#historyLongTextBox QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: transparent;
            }}
            QTextEdit#historyLongTextBox QScrollBar::add-page:vertical,
            QTextEdit#historyLongTextBox QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QLabel#historyFieldLabel {{
                color: {colors["settings_text"]};
                font-size: 12px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#accountSectionTitle {{
                color: {colors["settings_text"]};
                font-size: 15px;
                font-weight: 800;
                background: transparent;
            }}
            QLabel#accountFieldLabel {{
                color: {colors["settings_text"]};
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }}
            """
        )
        self._update_settings_icon(colors["button_text"])
        self._update_theme_icon("#ffffff" if self.is_dark_mode else "#1f1f1f")
        self.update_login_state(
            getattr(self, "_is_logged_in", False),
            getattr(self, "_login_username", ""),
            getattr(self, "_history_enabled", False),
        )
        self._refresh_text_box()

    def _refresh_text_box(self):
        if self._showing_placeholder:
            self._render_placeholder_text()
        else:
            self._render_original_text()

    def toggle_theme(self):
        self.set_dark_mode(not self.is_dark_mode)

    def set_dark_mode(self, enabled, animate=True):
        self.is_dark_mode = enabled
        if not animate:
            self._theme_mix = 1.0 if enabled else 0.0
            self.apply_theme()
            return

        start = self._theme_mix
        end = 1.0 if enabled else 0.0
        self.theme_animation.stop()
        self.theme_animation.setStartValue(start)
        self.theme_animation.setEndValue(end)
        self.theme_animation.start()

    def center_on_screen(self):
        screen = self.screen() or self.windowHandle().screen()
        if not screen:
            return

        geometry = screen.availableGeometry()
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + (geometry.height() - self.height()) // 2
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered_once:
            self.center_on_screen()
            self._centered_once = True

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edge = self._resize_edge_at(event.pos())
            if edge:
                self.resize_active = True
                self.resize_edge = edge
                self.resize_start_pos = event.globalPos()
                self.resize_start_geometry = self.geometry()
                event.accept()
                return
            if event.pos().y() <= 90:
                self.drag_active = True
                self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.resize_active and event.buttons() & Qt.LeftButton:
            self._resize_from_mouse(event.globalPos())
            event.accept()
            return
        if self.drag_active and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()
            return
        self._update_resize_cursor(event.pos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_active = False
        self.resize_active = False
        self.resize_edge = ""
        self.resize_start_geometry = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def _resize_edge_at(self, pos):
        margin = self.resize_margin
        left = pos.x() <= margin
        right = pos.x() >= self.width() - margin
        top = pos.y() <= margin
        bottom = pos.y() >= self.height() - margin
        if top and left:
            return "top_left"
        if top and right:
            return "top_right"
        if bottom and left:
            return "bottom_left"
        if bottom and right:
            return "bottom_right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return ""

    def _update_resize_cursor(self, pos):
        edge = self._resize_edge_at(pos)
        if edge in {"left", "right"}:
            self.setCursor(Qt.SizeHorCursor)
        elif edge in {"top", "bottom"}:
            self.setCursor(Qt.SizeVerCursor)
        elif edge in {"top_left", "bottom_right"}:
            self.setCursor(Qt.SizeFDiagCursor)
        elif edge in {"top_right", "bottom_left"}:
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.unsetCursor()

    def _resize_from_mouse(self, global_pos):
        if self.resize_start_geometry is None:
            return
        delta = global_pos - self.resize_start_pos
        geometry = self.resize_start_geometry
        x, y = geometry.x(), geometry.y()
        width, height = geometry.width(), geometry.height()
        min_width, min_height = self.minimumWidth(), self.minimumHeight()

        if "right" in self.resize_edge:
            width = max(min_width, width + delta.x())
        if "bottom" in self.resize_edge:
            height = max(min_height, height + delta.y())
        if "left" in self.resize_edge:
            new_width = max(min_width, width - delta.x())
            x = x + (width - new_width)
            width = new_width
        if "top" in self.resize_edge:
            new_height = max(min_height, height - delta.y())
            y = y + (height - new_height)
            height = new_height
        self.setGeometry(x, y, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        outline = QPainterPath()
        outline.addRoundedRect(QRectF(self.rect().adjusted(8, 8, -8, -8)), 30, 30)
        painter.setPen(QPen(QColor(0, 0, 0, 0)))
        painter.drawPath(outline)

        super().paintEvent(event)

    def open_settings_tab(self):
        showing_settings = self.content_stack.currentIndex() == 1
        if not showing_settings:
            self.default_dark_mode_checkbox.setChecked(self.saved_default_dark_mode)
            self.history_enabled_checkbox.setChecked(self.saved_history_enabled)
            self.set_input_mode(self.saved_input_mode)
            self.set_replace_mode_checked(getattr(self, "saved_replace_mode", False))
            self._sync_history_setting_access()
            self.settings_scroll_area.verticalScrollBar().setValue(0)
        self.content_stack.setCurrentIndex(0 if showing_settings else 1)
        self.settings_btn.setChecked(not showing_settings)

    def close_settings_page(self):
        self.default_dark_mode_checkbox.setChecked(self.saved_default_dark_mode)
        self.history_enabled_checkbox.setChecked(self.saved_history_enabled)
        self.set_input_mode(self.saved_input_mode)
        self.set_replace_mode_checked(getattr(self, "saved_replace_mode", False))
        self._sync_history_setting_access()
        self.content_stack.setCurrentIndex(0)
        self.settings_btn.setChecked(False)

    def show_settings_saved_notice(self):
        self.settings_notice_timer.stop()
        self.settings_notice_animation.stop()
        self.settings_notice_label.show()
        self.settings_notice_label.raise_()
        self.settings_notice_effect.setOpacity(1.0)
        self.settings_notice_label.updateGeometry()
        self.settings_notice_label.repaint()
        QApplication.processEvents()
        self.settings_notice_timer.start(3000)

    def _start_settings_notice_fade(self):
        self.settings_notice_animation.setStartValue(1.0)
        self.settings_notice_animation.setEndValue(0.0)
        self.settings_notice_animation.start()

    def _update_settings_notice_opacity(self, value):
        self.settings_notice_effect.setOpacity(float(value))

    def _hide_settings_notice(self):
        if self.settings_notice_effect.opacity() <= 0.01:
            self.settings_notice_label.hide()

    def show_account_saved_notice(self):
        self.account_notice_timer.stop()
        self.account_notice_animation.stop()
        self.account_notice_label.show()
        self.account_notice_label.raise_()
        self.account_notice_effect.setOpacity(1.0)
        self.account_notice_label.updateGeometry()
        self.account_notice_label.repaint()
        QApplication.processEvents()
        self.account_notice_timer.start(3000)

    def _start_account_notice_fade(self):
        self.account_notice_animation.setStartValue(1.0)
        self.account_notice_animation.setEndValue(0.0)
        self.account_notice_animation.start()

    def _update_account_notice_opacity(self, value):
        self.account_notice_effect.setOpacity(float(value))

    def _hide_account_notice(self):
        if self.account_notice_effect.opacity() <= 0.01:
            self.account_notice_label.hide()

    def set_default_dark_mode_checked(self, enabled):
        checked = bool(enabled)
        self.saved_default_dark_mode = checked
        self.default_dark_mode_checkbox.setChecked(checked)

    def get_default_dark_mode_checked(self):
        return self.default_dark_mode_checkbox.isChecked()

    def set_history_enabled_checked(self, enabled):
        checked = bool(enabled)
        self.saved_history_enabled = checked
        self.history_enabled_checkbox.setChecked(checked)
        self._sync_history_setting_access()

    def get_history_enabled_checked(self):
        return self.history_enabled_checkbox.isEnabled() and self.history_enabled_checkbox.isChecked()

    def _sync_history_setting_access(self):
        is_logged_in = bool(getattr(self, "_is_logged_in", False))
        self.history_enabled_checkbox.blockSignals(True)
        self.history_enabled_checkbox.setEnabled(is_logged_in)
        self.history_enabled_checkbox.setChecked(self.saved_history_enabled if is_logged_in else False)
        self.history_enabled_checkbox.blockSignals(False)

    def show_account_verify_page(self):
        self.account_verify_password_input.clear()
        self.content_stack.setCurrentIndex(5)
        self.settings_btn.setChecked(False)
        self.account_verify_password_input.setFocus()

    def show_account_page(self, account):
        self.set_account_info(account)
        self.content_stack.setCurrentIndex(6)
        self.settings_btn.setChecked(False)

    def close_account_pages(self):
        self.content_stack.setCurrentIndex(0)

    def set_account_info(self, account):
        display_name = (account.get("display_name") or "").strip()
        username = (account.get("username") or "").strip()
        self._account_loaded = {
            "display_name": display_name,
            "username": username,
            "password": "",
        }
        self.account_name_input.setText(display_name)
        self.account_username_input.setText(username)
        self.account_password_input.clear()
        self.account_password_input.setPlaceholderText("변경할 때만 입력")
        self.update_account_edit_buttons()

    def get_account_payload(self, field=None):
        payload = {}
        display_name = self.account_name_input.text().strip()
        username = self.account_username_input.text().strip()
        password = self.account_password_input.text()
        if field in (None, "display_name") and display_name != self._account_loaded.get("display_name", ""):
            payload["display_name"] = display_name
        if field in (None, "username") and username != self._account_loaded.get("username", ""):
            payload["username"] = username
        if field in (None, "password") and password:
            payload["password"] = password
        return payload

    def update_account_edit_buttons(self):
        self.account_name_edit_btn.setEnabled(bool(self.get_account_payload("display_name")))
        self.account_username_edit_btn.setEnabled(bool(self.get_account_payload("username")))
        self.account_password_edit_btn.setEnabled(bool(self.get_account_payload("password")))
        self.account_save_btn.setEnabled(bool(self.get_account_payload()))

    def set_input_mode(self, mode):
        normalized = mode if mode in {"clipboard", "drag", "realtime"} else "clipboard"
        self.saved_input_mode = normalized
        self.clipboard_mode_checkbox.blockSignals(True)
        self.drag_mode_checkbox.blockSignals(True)
        self.realtime_mode_checkbox.blockSignals(True)
        self.clipboard_mode_checkbox.setChecked(normalized == "clipboard")
        self.drag_mode_checkbox.setChecked(normalized == "drag")
        self.realtime_mode_checkbox.setChecked(normalized == "realtime")
        self.clipboard_mode_checkbox.blockSignals(False)
        self.drag_mode_checkbox.blockSignals(False)
        self.realtime_mode_checkbox.blockSignals(False)
        mode_names = {
            "clipboard": "클립보드 모드",
            "drag": "드래그 모드",
            "realtime": "실시간 모드",
        }
        self.input_mode_status_label.setText(f"{mode_names[normalized]} 인식 사용중")
        self._update_spell_scope_visibility()
        self._update_replace_mode_availability()

    def get_input_mode(self):
        if self.drag_mode_checkbox.isChecked():
            return "drag"
        if self.realtime_mode_checkbox.isChecked():
            return "realtime"
        return "clipboard"

    def set_replace_mode_checked(self, enabled):
        checked = bool(enabled)
        self.saved_replace_mode = checked
        for checkbox in (self.drag_replace_mode_checkbox, self.realtime_replace_mode_checkbox):
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self.apply_correction_btn.setVisible(checked or self.saved_input_mode == "drag")
        self._update_replace_mode_availability()

    def get_replace_mode_checked(self):
        if self.drag_mode_checkbox.isChecked():
            return self.drag_replace_mode_checkbox.isChecked()
        if self.realtime_mode_checkbox.isChecked():
            return self.realtime_replace_mode_checkbox.isChecked()
        return self.drag_replace_mode_checkbox.isChecked() or self.realtime_replace_mode_checkbox.isChecked()

    def set_spell_scope(self, scope):
        normalized = scope if scope in {"current_sentence", "current_paragraph", "full_text"} else "current_sentence"
        self.saved_spell_scope = normalized
        for value, button in self.spell_scope_buttons.items():
            button.blockSignals(True)
            button.setChecked(value == normalized)
            button.blockSignals(False)
        self._update_spell_scope_visibility()

    def get_spell_scope(self):
        for value, button in self.spell_scope_buttons.items():
            if button.isChecked():
                return value
        return self.saved_spell_scope or "current_sentence"

    def _update_spell_scope_visibility(self):
        widget = getattr(self, "spell_scope_widget", None)
        if widget is not None:
            widget.setVisible(self.saved_input_mode == "realtime")

    def _sync_replace_mode_checks(self, source_mode, checked):
        other = self.realtime_replace_mode_checkbox if source_mode == "drag" else self.drag_replace_mode_checkbox
        other.blockSignals(True)
        other.setChecked(bool(checked))
        other.blockSignals(False)

    def set_active_window_title(self, title):
        normalized = str(title).strip()
        self.active_window_label.setText(f"인식 중: {normalized}" if normalized else "")

    def _sync_input_mode_checks(self):
        sender = self.sender()
        checkboxes = (
            self.clipboard_mode_checkbox,
            self.drag_mode_checkbox,
            self.realtime_mode_checkbox,
        )
        if sender in checkboxes and sender.isChecked():
            for checkbox in checkboxes:
                if checkbox is sender:
                    continue
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

        if not any(checkbox.isChecked() for checkbox in checkboxes):
            fallback_checkbox = self.clipboard_mode_checkbox
            if sender is self.clipboard_mode_checkbox:
                fallback_checkbox = self.drag_mode_checkbox
            fallback_checkbox.blockSignals(True)
            fallback_checkbox.setChecked(True)
            fallback_checkbox.blockSignals(False)
        self._update_replace_mode_availability()

    def _update_replace_mode_availability(self):
        is_drag = self.drag_mode_checkbox.isChecked()
        is_realtime = self.realtime_mode_checkbox.isChecked()
        if hasattr(self, "drag_replace_mode_container"):
            self.drag_replace_mode_container.setVisible(is_drag)
        if hasattr(self, "realtime_replace_mode_container"):
            self.realtime_replace_mode_container.setVisible(is_realtime)
        self.drag_replace_mode_checkbox.setVisible(is_drag)
        self.realtime_replace_mode_checkbox.setVisible(is_realtime)
        self.drag_replace_mode_checkbox.setEnabled(is_drag)
        self.realtime_replace_mode_checkbox.setEnabled(is_realtime)
        self.apply_correction_btn.setVisible(is_drag or self.get_replace_mode_checked())

    def reset_text_tab(self):
        self._showing_placeholder = True
        self.last_original_text = ""
        self.set_active_window_title("")
        self._render_placeholder_text()
        self.clear_evaluation_score()
        self.clear_title_recommendation()

    def _render_placeholder_text(self):
        self.text_box.clear()
        self.text_box.setHtml(
            '<div style="color: #9b8a7f;">'
            '<div>복사한 텍스트가 여기에 뜹니다.</div>'
            "</div>"
        )

    def show_text_unavailable_placeholder(self):
        muted_color = self._blended_colors()["muted"]
        self._showing_placeholder = True
        self.last_original_text = ""
        self.text_box.clear()
        self.text_box.setHtml(
            f'<div style="color: {muted_color};">'
            "<div>텍스트가 인식되지 않습니다.</div>"
            "</div>"
        )
        self.clear_summary_result()
        self.clear_evaluation_score()
        self.clear_title_recommendation()
        self.clear_tone_result()
        self.clear_spell_result()

    def set_original_text(self, text):
        previous_text = self.last_original_text
        self._showing_placeholder = False
        self.last_original_text = text
        self._render_original_text(previous_text=previous_text)
        self.clear_summary_result()
        self.clear_evaluation_score()
        self.clear_title_recommendation()
        self.clear_tone_result()

    def _render_original_text(self, previous_text=""):
        scrollbar = self.text_box.verticalScrollBar()
        previous_value = scrollbar.value()
        previous_maximum = scrollbar.maximum()
        was_near_bottom = previous_maximum - previous_value <= 24
        is_appending = bool(previous_text) and self.last_original_text.startswith(previous_text)

        self.text_box.clear()
        self.text_box.setPlainText(self.last_original_text)

        if is_appending or was_near_bottom:
            scrollbar.setValue(scrollbar.maximum())
        elif previous_maximum > 0:
            ratio = previous_value / previous_maximum
            scrollbar.setValue(round(scrollbar.maximum() * ratio))

    def clear_evaluation_score(self):
        self.score_label.setText("\uc810\uc218")
        self.evaluation_reason_btn.setEnabled(False)
        self.evaluation_reason_btn.hide()

    def set_evaluation_score(self, score_text):
        self.score_label.setText(score_text)
        self.evaluation_reason_btn.setEnabled(True)
        self.evaluation_reason_btn.show()

    def clear_title_recommendation(self):
        self.title_label_box.setText("제목")

    def set_title_recommendation(self, title_text):
        self.title_label_box.setText(title_text)

    def clear_spell_result(self):
        self.spell_box.clear()
        self.spell_box.setHtml(
            '<div style="color: #9b8a7f;">'
            '<div>교정 결과가 여기에 뜹니다.</div>'
            "</div>"
        )

    def clear_summary_result(self):
        self.summary_box.clear()
        self.summary_box.setHtml(
            '<div style="color: #9b8a7f;">'
            '<div>글을 요약한 게 여기에 뜹니다.</div>'
            "</div>"
        )

    def clear_tone_result(self):
        self.tone_box.clear()
        self.tone_box.setHtml(
            '<div style="color: #9b8a7f;">'
            '<div>문체 변경 결과가 여기에 뜹니다.</div>'
            "</div>"
        )

    def set_spell_result(self, text):
        self.spell_box.setPlainText(text)

    def set_summary_result(self, text):
        self.summary_box.setPlainText(text)

    def set_tone_result(self, text):
        self.tone_box.setPlainText(text)

    def update_login_state(self, is_logged_in, username="", history_enabled=False):
        self._is_logged_in = bool(is_logged_in)
        self._login_username = username or ""
        self._history_enabled = bool(history_enabled)
        self.login_btn.setText("로그아웃" if is_logged_in else "로그인")
        display = getattr(self, "_account_display_name", "") or self._login_username
        if is_logged_in and display:
            self.user_identity_label.setText(f"{display} 님")
            self.user_icon_label.show()
            self.user_identity_label.show()
            self.user_status_widget.show()
        else:
            self.user_identity_label.clear()
            self.user_icon_label.hide()
            self.user_identity_label.hide()
            self.user_status_widget.hide()
        self._sync_history_setting_access()
        for button in self.history_buttons:
            button.setProperty("loginRequired", (not is_logged_in) or (not history_enabled))
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def set_account_identity(self, username="", display_name=""):
        self._account_display_name = (display_name or "").strip()
        self._login_username = username or getattr(self, "_login_username", "")
        self.update_login_state(
            getattr(self, "_is_logged_in", False),
            self._login_username,
            getattr(self, "_history_enabled", False),
        )

    def update_copy_button_label(self, index=None):
        current_tab = self.tabs.currentIndex() if index is None else index
        self.copy_btn.setText("원본 복사" if current_tab == 0 else "결과 복사")

    def get_current_text(self):
        current_tab = self.tabs.currentIndex()
        if current_tab == 0:
            return self.text_box.toPlainText()
        if current_tab == 1:
            return self.spell_box.toPlainText()
        if current_tab == 2:
            return self.summary_box.toPlainText()
        if current_tab == 3:
            return self.tone_box.toPlainText()
        return ""

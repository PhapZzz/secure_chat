# UI/chat.py  -- bố cục mới: thẻ header nổi, avatar cạnh tin nhắn, giờ trong bong bóng, ô nhập dạng thẻ nổi
import os
import re
import json
from datetime import datetime, timedelta, timezone

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QFrame, QScrollArea, QShortcut
)
from PyQt5.QtCore import Qt, QSize, QTimer, QEvent, QVariantAnimation, QEasingCurve
from PyQt5.QtGui import QKeySequence
from PyQt5.QtCore import QRect
from PyQt5.QtGui import (
    QIcon, QPixmap, QPainter, QPainterPath, QColor, QFont,
    QLinearGradient, QPen, QPalette
)

from services import api_client, crypto_client

# =====================================================================
#  CHỦ ĐỀ MÀU  (bấm nút 🎨 trên header để đổi, hoặc đổi DEFAULT_THEME)
# =====================================================================
THEMES = {
    "Đêm tím": dict(
        page="#13111F", header1="#3B1F8E", header2="#B02FC7", status="#B9F6CA",
        chat_bg="#161327",
        me1="#7C4DFF", me2="#E040FB", me_text="#FFFFFF",
        other_bg="#2A2545", other_text="#ECE9FA", other_border="#3A3460",
        input_area="#1C1833", input_bg="#2A2545", input_bg_focus="#1F1B38",
        input_text="#F3F0FF", accent="#E040FB", muted="#9A94C4",
        sys_bg="#2A2545", sys_text="#9A94C4",
        scroll="#4A4378", scroll_hover="#6A5FB0",
    ),
    "Hoàng hôn": dict(
        page="#FFF8F3", header1="#FF5F6D", header2="#FFA751", status="#FFFFFF",
        chat_bg="#FFF3EA",
        me1="#FF6B6B", me2="#FF9A5A", me_text="#FFFFFF",
        other_bg="#FFFFFF", other_text="#4E342E", other_border="#F6DCCB",
        input_area="#FFFFFF", input_bg="#FFF0E6", input_bg_focus="#FFFFFF",
        input_text="#4E342E", accent="#FF6B6B", muted="#B08A78",
        sys_bg="#FBE3D3", sys_text="#A1735D",
        scroll="#F1C9B0", scroll_hover="#E8A987",
    ),
    "Biển xanh": dict(
        page="#FFFFFF", header1="#0D47A1", header2="#00ACC1", status="#B9F6CA",
        chat_bg="#EAF6FB",
        me1="#1976D2", me2="#00BCD4", me_text="#FFFFFF",
        other_bg="#FFFFFF", other_text="#16324F", other_border="#D3E7F1",
        input_area="#FFFFFF", input_bg="#E9F3F8", input_bg_focus="#FFFFFF",
        input_text="#16324F", accent="#00ACC1", muted="#7D9BAE",
        sys_bg="#D6EAF3", sys_text="#5C7F95",
        scroll="#B5D5E4", scroll_hover="#8DBBD1",
    ),
    "Rừng xanh": dict(
        page="#FFFFFF", header1="#00695C", header2="#66BB6A", status="#E8F5E9",
        chat_bg="#F1F8F4",
        me1="#00897B", me2="#43A047", me_text="#FFFFFF",
        other_bg="#FFFFFF", other_text="#1B3A2D", other_border="#D5E9DC",
        input_area="#FFFFFF", input_bg="#E8F3EC", input_bg_focus="#FFFFFF",
        input_text="#1B3A2D", accent="#00897B", muted="#7FA08F",
        sys_bg="#DCEDE2", sys_text="#5E8570",
        scroll="#B8D8C4", scroll_hover="#8FC0A4",
    ),
}
THEME_ORDER = list(THEMES.keys())
DEFAULT_THEME = "Đêm tím"

# T luôn giữ bảng màu đang dùng
T = dict(THEMES[DEFAULT_THEME])

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 16          # cỡ chữ tin nhắn (px)
BUBBLE_PAD_X = 16
BUBBLE_PAD_Y = 10
AVATAR_SMALL = 34

ASSETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Server lưu giờ UTC (mặc định của hầu hết database) -> tự đổi sang giờ máy.
# Nếu giờ hiển thị lệch (vd. đúng 7 tiếng) thì thử đổi True <-> False.
SERVER_TIME_IS_UTC = True
# Bật True để in giá trị thời gian thô server trả về ra console (để kiểm tra)
DEBUG_TIME = False
TIME_KEYS = ("created_at", "timestamp", "createdAt", "sent_at", "time", "date")


def _parse_time(value):
    """Đọc mọi kiểu thời gian server có thể trả về -> datetime theo giờ máy (hoặc None)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            ts = float(value)
            if ts > 1e11:          # mili-giây
                ts /= 1000
            return datetime.fromtimestamp(ts)
        else:
            s = str(value).strip()
            try:
                s2 = s.replace("Z", "+00:00")
                s2 = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s2)
                dt = datetime.fromisoformat(s2)
            except ValueError:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            if not SERVER_TIME_IS_UTC:
                return dt
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None


def _date_label(d):
    today = datetime.now().date()
    if d == today:
        return "Hôm nay"
    if d == today - timedelta(days=1):
        return "Hôm qua"
    return d.strftime("%d/%m/%Y")


AVATAR_PALETTE = [
    ("#7C4DFF", "#E040FB"), ("#1976D2", "#00BCD4"), ("#FF6B6B", "#FF9A5A"),
    ("#00897B", "#43A047"), ("#F9A825", "#FF7043"), ("#5C6BC0", "#26C6DA"),
]


def make_avatar(size, name="?", dot=None, use_image=False, colorful=True, ring=None, badge=0):
    """
    Avatar tròn: dùng user.png nếu có, không thì chữ cái đầu trên nền gradient.
      dot   : True = chấm xanh (online), False = chấm xám (ngoại tuyến), None = không chấm
      badge : số tin chưa đọc (>0 thì vẽ huy hiệu đỏ ở góc trên phải)
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    path = QPainterPath()
    path.addEllipse(1, 1, size - 2, size - 2)
    p.setClipPath(path)

    icon_path = os.path.join(ASSETS_PATH, "user.png")
    icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
    if use_image and not icon.isNull():
        p.fillRect(0, 0, size, size, QColor("white"))
        p.drawPixmap(0, 0, icon.pixmap(size, size).scaled(
            size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
    else:
        if colorful:
            c1, c2 = AVATAR_PALETTE[sum(ord(ch) for ch in name) % len(AVATAR_PALETTE)]
        else:
            c1, c2 = T['me1'], T['me2']
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor(c1))
        g.setColorAt(1, QColor(c2))
        p.fillRect(0, 0, size, size, g)
        p.setPen(QColor("white"))
        f = QFont(FONT_FAMILY)
        f.setPixelSize(max(10, int(size * 0.42)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, (name.strip()[:1] or "?").upper())

    p.setClipping(False)
    ring_color = QColor(ring or T['other_bg'])
    if dot is not None:   # chấm trạng thái ở góc dưới phải: xanh = online, xám = ngoại tuyến
        r = max(10, size // 4)
        p.setPen(QPen(ring_color, 2))
        p.setBrush(QColor("#4CAF50") if dot else QColor("#9E9E9E"))
        p.drawEllipse(size - r - 1, size - r - 1, r, r)
    if badge and badge > 0:   # huy hiệu tin chưa đọc ở góc trên phải
        br = max(18, size // 2)
        p.setPen(QPen(ring_color, 2))
        p.setBrush(QColor("#F44336"))
        p.drawEllipse(size - br, 0, br - 1, br - 1)
        p.setPen(QColor("white"))
        bf = QFont(FONT_FAMILY)
        bf.setPixelSize(int(br * 0.55))
        bf.setBold(True)
        p.setFont(bf)
        p.drawText(QRect(size - br, 0, br - 1, br - 1), Qt.AlignCenter,
                   str(badge) if badge < 10 else "9+")
    p.end()
    return pm


def build_qss():
    return f"""
        QWidget {{
            font-family: '{FONT_FAMILY}', sans-serif;
            font-size: {FONT_SIZE}px;
        }}
        QWidget#ChatPage {{ background-color: {T['chat_bg']}; }}
        QLabel {{ background: transparent; border: none; }}

        /* ---------- Header: thẻ nổi ---------- */
        QFrame#header {{
            background-color: {T['other_bg']};
            border: 1px solid {T['other_border']};
            border-radius: 24px;
        }}
        QLabel#title {{ color: {T['other_text']}; font-size: 19px; font-weight: 700; }}
        QLabel#status {{ color: {T['accent']}; font-size: 13px; }}
        QFrame#actions {{
            background-color: {T['input_bg']}; border: none; border-radius: 22px;
        }}
        QPushButton[role="action"] {{
            background-color: transparent; border: none;
            border-radius: 18px; color: {T['muted']}; font-size: 17px;
        }}
        QPushButton[role="action"]:hover {{ background-color: {T['other_border']}; }}
        QPushButton[role="action"]:pressed {{ background-color: {T['scroll']}; }}

        /* ---------- Vùng tin nhắn ---------- */
        QScrollArea#messages {{ background-color: transparent; border: none; }}
        QWidget#msg_container {{ background-color: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{
            background: {T['scroll']}; border-radius: 4px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {T['scroll_hover']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

        /* ---------- Ô nhập: thẻ nổi ---------- */
        QFrame#composer {{
            background-color: {T['other_bg']};
            border: 1px solid {T['other_border']};
            border-radius: 26px;
        }}
        QFrame#composer[focused="true"] {{
            background-color: {T['input_bg_focus']};
            border: 2px solid {T['accent']};
        }}
        QLineEdit#input {{
            border: none; background: transparent; padding: 10px 6px;
            color: {T['input_text']};
        }}
        QPushButton#attach_btn {{
            background: {T['input_bg']}; border: none; border-radius: 18px;
            color: {T['muted']}; font-size: 17px;
        }}
        QPushButton#attach_btn:hover {{ background-color: {T['other_border']}; }}
        QPushButton#send_btn {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 {T['me1']}, stop:1 {T['me2']});
            border: none; border-radius: 16px; color: white; font-size: 18px;
        }}
        QPushButton#send_btn:hover {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 {T['me2']}, stop:1 {T['me1']});
        }}
        QPushButton#send_btn:pressed {{ background: {T['me1']}; }}
    """


class MessageRow(QWidget):
    """
    Một dòng tin nhắn:
      - normal: bong bóng (giờ nằm trong bong bóng); tin người khác có avatar nhỏ bên trái
      - date  : vạch ngày có đường kẻ hai bên
      - system: thông báo giữa màn hình
    """

    def __init__(self, text, is_me, kind="normal", time_text="", avatar_name="?"):
        super().__init__()
        self.kind = kind
        self.text = text
        self.is_me = is_me
        self.time_label = None
        self.avatar_label = None
        self.avatar_name = avatar_name
        self.lines = []

        font = QFont(FONT_FAMILY)
        font.setPixelSize(FONT_SIZE if kind == "normal" else 12)

        self.label = QLabel(text)
        self.label.setFont(font)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 2, 20, 2)
        row.setSpacing(10)

        if kind in ("system", "date"):
            self.label.setAlignment(Qt.AlignCenter)
            if kind == "date":
                for side in (0, 1):
                    line = QFrame()
                    line.setFixedHeight(1)
                    self.lines.append(line)
                    if side == 0:
                        row.addWidget(line, 1, Qt.AlignVCenter)
                        row.addWidget(self.label)
                    else:
                        row.addWidget(line, 1, Qt.AlignVCenter)
            else:
                row.addStretch()
                row.addWidget(self.label)
                row.addStretch()
            self.apply_style()
            return

        # ----- bong bóng
        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(BUBBLE_PAD_X, BUBBLE_PAD_Y, BUBBLE_PAD_X, 6)
        bl.setSpacing(2)
        bl.addWidget(self.label)
        if time_text:
            self.time_label = QLabel(time_text)
            tf = QFont(FONT_FAMILY)
            tf.setPixelSize(11)
            self.time_label.setFont(tf)
            bl.addWidget(self.time_label, 0, Qt.AlignRight)
        else:
            bl.setContentsMargins(BUBBLE_PAD_X, BUBBLE_PAD_Y, BUBBLE_PAD_X, BUBBLE_PAD_Y)

        if is_me:
            row.addStretch()
            row.addWidget(self.bubble)
        else:
            self.avatar_label = QLabel()
            self.avatar_label.setFixedSize(AVATAR_SMALL, AVATAR_SMALL)
            self.avatar_label.setPixmap(make_avatar(AVATAR_SMALL, avatar_name))
            row.addWidget(self.avatar_label, 0, Qt.AlignBottom)
            row.addWidget(self.bubble)
            row.addStretch()
        self.apply_style()

    def set_avatar(self, name):
        if self.avatar_label:
            self.avatar_name = name
            self.avatar_label.setPixmap(make_avatar(AVATAR_SMALL, name))

    def apply_style(self):
        """Áp màu theo chủ đề hiện tại (gọi lại khi đổi theme)."""
        if self.kind == "system":
            self.label.setStyleSheet(
                f"background: {T['sys_bg']}; color: {T['sys_text']};"
                f"border-radius: 11px; padding: 3px 14px;"
            )
            return
        if self.kind == "date":
            self.label.setStyleSheet(
                f"background: {T['sys_bg']}; color: {T['sys_text']};"
                f"border-radius: 11px; padding: 3px 14px;"
            )
            for line in self.lines:
                line.setStyleSheet(f"background: {T['other_border']}; border: none;")
            return

        if self.is_me:
            self.bubble.setStyleSheet(
                f"QFrame#bubble {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 {T['me1']}, stop:1 {T['me2']});"
                f"border-radius: 18px; border-bottom-right-radius: 5px; }}"
            )
            self.label.setStyleSheet(f"background: transparent; color: {T['me_text']};")
            if self.time_label:
                self.time_label.setStyleSheet("background: transparent; color: rgba(255,255,255,175);")
        else:
            self.bubble.setStyleSheet(
                f"QFrame#bubble {{ background: {T['other_bg']};"
                f"border: 1px solid {T['other_border']};"
                f"border-radius: 18px; border-bottom-left-radius: 5px; }}"
            )
            self.label.setStyleSheet(f"background: transparent; color: {T['other_text']};")
            if self.time_label:
                self.time_label.setStyleSheet(f"background: transparent; color: {T['muted']};")
            self.set_avatar(self.avatar_name)

    def fit(self, max_width):
        if self.kind != "normal":
            return
        fm = self.label.fontMetrics()
        reserve = 0 if self.is_me else AVATAR_SMALL + 10
        inner_max = max(120, max_width - 2 * BUBBLE_PAD_X - 2 - reserve)
        rect = fm.boundingRect(0, 0, inner_max, 100000, Qt.TextWordWrap, self.text)
        w = min(rect.width() + 4, inner_max)
        if self.time_label:
            w = max(w, self.time_label.sizeHint().width())
        self.label.setFixedSize(w, rect.height() + 2)


class MessageArea(QScrollArea):
    """Vùng tin nhắn, có clear() / verticalScrollBar() giống QTextEdit."""

    def __init__(self):
        super().__init__()
        self.setObjectName("messages")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container.setObjectName("msg_container")
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(0, 14, 0, 14)
        self.vbox.setSpacing(6)
        self.vbox.addStretch()
        self.setWidget(self.container)

    def _max_bubble_width(self):
        return int(self.viewport().width() * 0.72)

    def clear(self):
        while self.vbox.count() > 1:
            item = self.vbox.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def add_row(self, row):
        row.fit(self._max_bubble_width())
        self.vbox.insertWidget(self.vbox.count() - 1, row)
        self.scroll_to_bottom()

    def restyle_all(self):
        for i in range(self.vbox.count() - 1):
            row = self.vbox.itemAt(i).widget()
            if isinstance(row, MessageRow):
                row.apply_style()

    def set_other_name(self, name):
        for i in range(self.vbox.count() - 1):
            row = self.vbox.itemAt(i).widget()
            if isinstance(row, MessageRow):
                row.set_avatar(name)

    def scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        QTimer.singleShot(30, lambda: bar.setValue(bar.maximum()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self._max_bubble_width()
        for i in range(self.vbox.count() - 1):
            row = self.vbox.itemAt(i).widget()
            if isinstance(row, MessageRow):
                row.fit(w)


class ChatPage(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent  # HomePage
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.assets_path = os.path.join(current_dir, "assets")
        self.theme_name = DEFAULT_THEME
        self.icon_buttons = []   # (button, tên icon, màu | None, size)
        self._last_date = None   # ngày của tin nhắn cuối (để chèn vạch ngăn ngày)
        self._presence = False   # True = online, False = ngoại tuyến, None = nhóm
        self.init_ui()
        self.set_presence(False)

    # ------------------------------------------------------------ helpers
    def get_icon(self, name):
        path = os.path.join(self.assets_path, name)
        if os.path.exists(path):
            return QIcon(path)
        return QIcon()

    def tinted_icon(self, name, color, size):
        """Đổi icon sang một màu (để icon luôn nhìn rõ trên mọi chủ đề)."""
        icon = self.get_icon(name)
        if icon.isNull():
            return QIcon()
        pm = icon.pixmap(size, size)
        p = QPainter(pm)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), QColor(color))
        p.end()
        return QIcon(pm)

    def make_avatar(self, size, name="?"):
        return make_avatar(size, name, dot=self._presence)

    def _icon_button(self, icon_name, tooltip, fallback_text,
                     role=None, color="muted", size=22, dim=40):
        btn = QPushButton()
        if self.get_icon(icon_name).isNull():
            btn.setText(fallback_text)
        else:
            self.icon_buttons.append((btn, icon_name, color, size))
        if role:
            btn.setProperty("role", role)
        btn.setFixedSize(dim, dim)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        return btn

    def _apply_icons(self):
        for btn, name, color, size in self.icon_buttons:
            col = T[color] if color in T else color
            btn.setIcon(self.tinted_icon(name, col, size))
            btn.setIconSize(QSize(size, size))

    # ------------------------------------------------------------ UI
    def init_ui(self):
        self.setObjectName("ChatPage")
        self.setStyleSheet(build_qss())

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- Header: thẻ nổi có lề xung quanh
        header_wrap = QVBoxLayout()
        header_wrap.setContentsMargins(18, 16, 18, 4)

        header_frame = QFrame()
        header_frame.setObjectName("header")
        header_frame.setFixedHeight(76)
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(16, 0, 14, 0)
        header.setSpacing(14)

        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(50, 50)
        self.avatar_label.setPixmap(self.make_avatar(50, "C"))

        title_layout = QVBoxLayout()
        title_layout.setSpacing(1)
        title_layout.setAlignment(Qt.AlignVCenter)
        self.title_label = QLabel("Cuộc hội thoại")
        self.title_label.setObjectName("title")
        self.status_label = QLabel("Ngoại tuyến")
        self.status_label.setObjectName("status")
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.status_label)

        # nhóm nút dạng viên thuốc
        actions = QFrame()
        actions.setObjectName("actions")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(6, 4, 6, 4)
        actions_layout.setSpacing(2)

        self.theme_button = self._icon_button("palette.png", "Đổi giao diện", "🎨",
                                              role="action", size=20, dim=38)
        self.theme_button.clicked.connect(self.cycle_theme)
        self.call_button = self._icon_button("call.png", "Gọi thoại", "📞",
                                             role="action", size=20, dim=38)
        self.video_button = self._icon_button("video.png", "Gọi video", "🎥",
                                              role="action", size=20, dim=38)
        self.info_button = self._icon_button("info.png", "Thông tin", "ⓘ",
                                             role="action", size=20, dim=38)
        for b in (self.theme_button, self.call_button, self.video_button, self.info_button):
            actions_layout.addWidget(b)

        # nút ẩn/hiện thanh bên trái (Ctrl+B)
        self.menu_button = self._icon_button("menu.png", "Ẩn/hiện danh sách (Ctrl+B)", "☰",
                                             role="action", size=22, dim=40)
        self.menu_button.clicked.connect(self.toggle_sidebar)
        self._sidebar_anim = None
        self._sidebar_width = 380
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_sidebar)

        header.addWidget(self.menu_button)
        header.addWidget(self.avatar_label)
        header.addLayout(title_layout)
        header.addStretch()
        header.addWidget(actions)
        header_wrap.addWidget(header_frame)
        main_layout.addLayout(header_wrap)

        # ---- Vùng tin nhắn
        self.messages = MessageArea()
        main_layout.addWidget(self.messages, 1)

        # ---- Ô nhập: thẻ nổi
        composer_wrap = QVBoxLayout()
        composer_wrap.setContentsMargins(18, 4, 18, 18)

        self.input_bg = QFrame()
        self.input_bg.setObjectName("composer")
        self.input_bg.setProperty("focused", False)
        composer = QHBoxLayout(self.input_bg)
        composer.setContentsMargins(10, 6, 8, 6)
        composer.setSpacing(8)

        self.attach_btn = self._icon_button("attach.png", "Đính kèm file", "📎",
                                            color="muted", size=20, dim=36)
        self.attach_btn.setObjectName("attach_btn")

        self.input = QLineEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("Nhập tin nhắn...")
        self.input.installEventFilter(self)

        self.send_btn = self._icon_button("send.png", "Gửi", "➤",
                                          color="#FFFFFF", size=22, dim=44)
        self.send_btn.setObjectName("send_btn")

        composer.addWidget(self.attach_btn)
        composer.addWidget(self.input, 1)
        composer.addWidget(self.send_btn)
        composer_wrap.addWidget(self.input_bg)
        main_layout.addLayout(composer_wrap)

        self.send_btn.clicked.connect(self.send)
        self.input.returnPressed.connect(self.send)

        self._apply_icons()
        self._apply_placeholder_color()

    def _apply_placeholder_color(self):
        role = getattr(QPalette, "PlaceholderText", None)
        if role is not None:
            pal = self.input.palette()
            pal.setColor(role, QColor(T['muted']))
            self.input.setPalette(pal)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self.input_bg.setProperty("focused", event.type() == QEvent.FocusIn)
            self.input_bg.style().unpolish(self.input_bg)
            self.input_bg.style().polish(self.input_bg)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ sidebar
    def toggle_sidebar(self):
        """Thu gọn / mở rộng thanh bên trái (danh sách chat) có hiệu ứng trượt."""
        if hasattr(self.parent, "toggle_sidebar"):   # HomePage tự thu gọn thành thanh mỏng
            self.parent.toggle_sidebar()
            return
        sidebar = self.parent.findChild(QFrame, "sidebar")
        if sidebar is None:
            return
        if self._sidebar_anim is not None:
            self._sidebar_anim.stop()

        showing = sidebar.isVisible() and sidebar.width() > 0
        if showing:
            self._sidebar_width = max(sidebar.width(), 1)
            start, end = sidebar.width(), 0
        else:
            sidebar.setFixedWidth(0)
            sidebar.show()
            start, end = 0, self._sidebar_width

        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.valueChanged.connect(lambda v: sidebar.setFixedWidth(int(v)))

        def finished():
            if end == 0:
                sidebar.hide()
            sidebar.setFixedWidth(end if end else self._sidebar_width)

        anim.finished.connect(finished)
        self._sidebar_anim = anim
        anim.start()

    # ------------------------------------------------------------ theme
    def apply_theme(self, name):
        self.theme_name = name
        T.clear()
        T.update(THEMES[name])
        self.setStyleSheet(build_qss())
        self.messages.restyle_all()
        self.avatar_label.setPixmap(self.make_avatar(50, self.title_label.text()))
        self._apply_icons()
        self._apply_placeholder_color()
        self.theme_button.setToolTip(f"Giao diện: {name} (bấm để đổi)")
        self.set_presence(self._presence)

    def cycle_theme(self):
        i = (THEME_ORDER.index(self.theme_name) + 1) % len(THEME_ORDER)
        self.apply_theme(THEME_ORDER[i])

    def set_header(self, name):
        self.title_label.setText(name)
        self.avatar_label.setPixmap(self.make_avatar(50, name))
        self.messages.set_other_name(name)

    def set_presence(self, online):
        """online: True = đang hoạt động (chấm xanh), False = ngoại tuyến (chấm xám), None = nhóm."""
        self._presence = online
        if online is True:
            self.status_label.setText("Đang hoạt động")
            self.status_label.setStyleSheet("color: #4CAF50;")
        elif online is False:
            self.status_label.setText("Ngoại tuyến")
            self.status_label.setStyleSheet(f"color: {T['muted']};")
        else:
            self.status_label.setText("Nhóm")
            self.status_label.setStyleSheet(f"color: {T['muted']};")
        self.avatar_label.setPixmap(self.make_avatar(50, self.title_label.text()))

    # ------------------------------------------------------------ logic
    def clear_chat(self):
        self.messages.clear()
        self._last_date = None
        self.input.clear()
        self.set_header("Chat")
        self.set_presence(False)

    def load_chat(self, chat_id):
        from .home import sio

        token = self.parent.parent.token
        user_id = self.parent.parent.user_id
        private_key = self.parent.parent.private_key

        if not sio.connected:
            try:
                self.parent.connect_socket()
            except Exception:
                pass
        else:
            sio.emit("join_chat", {"chat_id": chat_id, "user_id": user_id})

        status, chat = api_client.get_chat_detail(token, chat_id)
        if status == 200:
            self.set_header(chat.get('name', 'Hội thoại'))
            if not chat.get('is_group'):
                members = chat.get('members', [])
                other_id = next((m for m in members if m != user_id), None)
                self.parent.current_other_user_id = other_id
            else:
                self.parent.current_other_user_id = None

        status, msgs = api_client.get_messages(token, chat_id)
        self.messages.clear()
        self._last_date = None
        if status == 200:
            for msg in msgs:
                is_me = msg['sender_id'] == user_id
                try:
                    key_dict = json.loads(msg['aes_key_encrypted'])
                    wrapped = key_dict.get(str(user_id))
                    if wrapped:
                        aes_key = crypto_client.unwrap_aes_key(wrapped, private_key)
                        text = crypto_client.decrypt_aes_gcm(
                            msg['content'], aes_key, msg['iv'], msg['tag'])
                    else:
                        text = "[Không có khóa]"
                except Exception as e:
                    text = f"[Lỗi giải mã: {e}]"
                raw = next((msg[k] for k in TIME_KEYS if msg.get(k)), None)
                when = _parse_time(raw)
                if DEBUG_TIME:
                    print("[time]", repr(raw), "->", when)
                # không đọc được giờ -> không hiện giờ (time_text="")
                self.add_message(msg['sender_id'], text, is_me,
                                 time_text="" if when is None else None, when=when)

    def send(self):
        from .home import sio
        text = self.input.text().strip()
        if not text:
            return

        token = self.parent.parent.token
        user_id = self.parent.parent.user_id
        chat_id = self.parent.current_chat_id

        if not chat_id:
            return

        status, chat = api_client.get_chat_detail(token, chat_id)
        if status != 200:
            return

        aes_key = crypto_client.generate_aes_key()
        enc = crypto_client.encrypt_aes_gcm(text, aes_key)
        wrapped_keys = {}
        for mid in chat["members"]:
            s, info = api_client.get_user_info(token, mid)
            if s == 200:
                wrapped = crypto_client.wrap_aes_key(aes_key, info["public_key"])
                wrapped_keys[str(mid)] = wrapped

        payload = {
            "chat_id": chat_id, "sender_id": user_id,
            "content": enc["ciphertext"], "aes_key_encrypted": wrapped_keys,
            "iv": enc["iv"], "tag": enc["tag"]
        }
        try:
            sio.emit("send_message", payload)
            self.input.clear()
        except Exception as e:
            print(e)

    def add_message(self, sender_id, text, is_me, time_text=None, when=None):
        """
        when      : datetime (giờ máy) của tin nhắn, dùng để hiện giờ + vạch ngăn ngày
        time_text=None, when=None : tin realtime -> dùng giờ hiện tại
        time_text=""              : không hiện giờ (không đọc được thời gian)
        """
        if sender_id == "System":
            self.messages.add_row(MessageRow(text, False, kind="system"))
            return

        if time_text is None:
            when = when or datetime.now()
            time_text = when.strftime("%H:%M")

        if when is not None and when.date() != self._last_date:
            self._last_date = when.date()
            self.messages.add_row(MessageRow(_date_label(when.date()), False, kind="date"))

        self.messages.add_row(MessageRow(
            text, is_me, time_text=time_text,
            avatar_name=self.title_label.text()))
import os
import threading
import socketio
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QBoxLayout,
    QPushButton, QLabel, QFrame, QListWidgetItem, QLineEdit,
    QStackedLayout, QGraphicsDropShadowEffect, QAbstractItemView
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QSize, QVariantAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QColor, QIcon, QPixmap
from services import api_client, crypto_client

from .chat import ChatPage, make_avatar
from .profile import ProfilePage
from .info_panel import InfoPanel

SOCKET_URL = "http://127.0.0.1:5001"
sio = socketio.Client(reconnection=True)

SIDEBAR_W = 380      # chiều rộng khi mở rộng
RAIL_W = 84          # chiều rộng khi thu gọn (thanh mỏng)
ICON_EXPANDED = 40   # cỡ avatar trong danh sách khi mở rộng
ICON_COLLAPSED = 48  # cỡ avatar khi thu gọn

# Vai trò dữ liệu lưu trong mỗi item
ROLE_ID = Qt.UserRole          # chat_id / user_id
ROLE_NAME = Qt.UserRole + 1    # tên gốc (không kèm số tin chưa đọc)
ROLE_OTHER = Qt.UserRole + 2   # id người còn lại (chat 1-1), None nếu là nhóm / chưa biết
ROLE_UNREAD = Qt.UserRole + 3  # số tin chưa đọc


class SocketSignals(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    online_users_received = pyqtSignal(list)
    new_message_received = pyqtSignal(dict)
    # Cập nhật 1 item chat cụ thể (thêm mới hoặc đẩy lên đầu)
    chat_updated = pyqtSignal(dict)


class HomePage(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.online_users = {}
        self.socket_connected = False
        self._socket_initialized = False
        self.current_chat_id = None
        self.current_other_user_id = None
        self.sidebar_collapsed = False
        self._profile_text = "  Tôi"
        self._sb_anim = None

        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.assets_path = os.path.join(current_dir, "assets")

        self.socket_signals = SocketSignals()
        self.init_ui()

        self.socket_signals.connected.connect(self.on_socket_connected)
        self.socket_signals.disconnected.connect(self.on_socket_disconnected)
        self.socket_signals.online_users_received.connect(self.handle_online_users)
        self.socket_signals.new_message_received.connect(self.handle_new_message)
        self.socket_signals.chat_updated.connect(self.upsert_chat_item)

    # ------------------------------------------------------------ helpers
    def get_icon(self, name):
        path = os.path.join(self.assets_path, name)
        if os.path.exists(path):
            return QIcon(path)
        return QIcon()

    def avatar_icon(self, name, size, dot=None, badge=0):
        """Avatar tròn theo tên: dot True = xanh, False = xám, None = không chấm."""
        return QIcon(make_avatar(size, name or "?", dot=dot, use_image=False,
                                 colorful=True, ring="#FFFFFF", badge=badge))

    def _icon_px(self):
        return ICON_COLLAPSED if self.sidebar_collapsed else ICON_EXPANDED

    def _other_id_from(self, chat):
        """Id người còn lại của chat 1-1; None nếu là nhóm hoặc không có danh sách thành viên."""
        if chat.get("is_group"):
            return None
        members = chat.get("members") or []
        return next((m for m in members if m != self.parent.user_id), None)

    # ------------------------------------------------------------ UI
    def init_ui(self):
        self.setStyleSheet("""
            QWidget {
                font-family: 'Segoe UI', sans-serif;
                background-color: #F0F4F8;
                color: #263238;
                font-size: 18px;
            }
            QFrame#sidebar {
                background-color: #FFFFFF;
                border-right: 1px solid #CFD8DC;
            }
            QLineEdit#search_bar {
                background-color: #F5F7FA;
                border: 1px solid #CFD8DC;
                border-radius: 20px;
                padding: 12px 18px 12px 40px;
                font-size: 18px;
            }
            QLineEdit#search_bar:focus {
                border: 1px solid #1E88E5;
                background-color: #FFFFFF;
            }
            QLabel.section_title {
                color: #78909C;
                font-size: 16px;
                font-weight: bold;
                text-transform: uppercase;
                margin-top: 25px;
                padding-left: 20px;
            }
            QListWidget {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                padding: 12px;
                border-radius: 12px;
                margin: 5px 12px;
                color: #37474F;
                font-weight: 500;
                font-size: 18px;
            }
            QListWidget::item:hover { background-color: #F5F7FA; }
            QListWidget::item:selected {
                background-color: #E3F2FD;
                color: #1565C0;
            }
            QFrame#user_footer {
                background-color: #FFFFFF;
                border-top: 1px solid #CFD8DC;
            }
            QPushButton#profile_btn {
                background-color: transparent;
                border: none;
                text-align: left;
                padding: 12px;
                font-weight: bold;
                color: #263238;
                font-size: 18px;
            }
            QPushButton#profile_btn:hover { background-color: #F5F7FA; }
            QPushButton#logout_btn {
                background-color: #FFEBEE;
                border-radius: 8px;
                border: none;
            }
            QPushButton#logout_btn:hover { background-color: #FFCDD2; }
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. SIDEBAR (mở rộng <-> thanh mỏng)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(SIDEBAR_W)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 15))
        shadow.setOffset(2, 0)
        self.sidebar.setGraphicsEffect(shadow)
        self.sidebar.raise_()

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)

        # Header (Search)
        self.sidebar_header = QFrame()
        self.sidebar_header.setFixedHeight(90)
        header_layout = QVBoxLayout(self.sidebar_header)
        header_layout.setContentsMargins(20, 20, 20, 0)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Tìm kiếm...")
        self.search_bar.setObjectName("search_bar")
        self.search_bar.addAction(self.get_icon("search.png"), QLineEdit.LeadingPosition)
        header_layout.addWidget(self.search_bar)
        sidebar_layout.addWidget(self.sidebar_header)

        # Lists
        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_chat = QLabel("  Tin nhắn")
        self.lbl_chat.setProperty("class", "section_title")
        list_layout.addWidget(self.lbl_chat)

        self.chat_list = QListWidget()
        self.chat_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_list.setIconSize(QSize(ICON_EXPANDED, ICON_EXPANDED))
        self.chat_list.itemClicked.connect(self.on_chat_clicked)
        list_layout.addWidget(self.chat_list, 2)

        # vạch ngăn giữa 2 danh sách (chỉ hiện khi thu gọn)
        self.rail_divider = QFrame()
        self.rail_divider.setFixedHeight(1)
        self.rail_divider.setStyleSheet(
            "background-color: #CFD8DC; margin-left: 22px; margin-right: 22px;")
        self.rail_divider.hide()
        list_layout.addWidget(self.rail_divider)

        self.lbl_online = QLabel("  Đang hoạt động")
        self.lbl_online.setProperty("class", "section_title")
        list_layout.addWidget(self.lbl_online)

        self.online_list = QListWidget()
        self.online_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.online_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.online_list.setIconSize(QSize(ICON_EXPANDED, ICON_EXPANDED))
        self.online_list.itemClicked.connect(self.start_chat_with_user)
        list_layout.addWidget(self.online_list, 1)

        sidebar_layout.addLayout(list_layout)

        # Footer
        user_footer = QFrame()
        user_footer.setObjectName("user_footer")
        self.footer_layout = QBoxLayout(QBoxLayout.LeftToRight, user_footer)
        self.footer_layout.setContentsMargins(20, 20, 20, 20)
        self.footer_layout.setSpacing(6)

        self.profile_btn = QPushButton(self._profile_text)
        self.profile_btn.setObjectName("profile_btn")
        self.profile_btn.setIcon(self.get_icon("user.png"))
        self.profile_btn.setIconSize(QSize(32, 32))
        self.profile_btn.setCursor(Qt.PointingHandCursor)

        self.logout_btn = QPushButton()
        self.logout_btn.setObjectName("logout_btn")
        self.logout_btn.setIcon(self.get_icon("logout.png"))
        self.logout_btn.setFixedSize(50, 50)
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.setToolTip("Đăng xuất")

        self.footer_layout.addWidget(self.profile_btn, 1)
        self.footer_layout.addWidget(self.logout_btn)

        sidebar_layout.addWidget(user_footer)
        main_layout.addWidget(self.sidebar)

        # 2. CONTENT STACK
        content_frame = QFrame()
        content_frame.setStyleSheet("background-color: #F0F4F8;")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)

        self.content_stack = QStackedLayout()

        # Placeholder
        placeholder_widget = QWidget()
        p_layout = QVBoxLayout(placeholder_widget)
        p_layout.setAlignment(Qt.AlignCenter)

        p_icon = QLabel()
        logo = QPixmap(os.path.join(self.assets_path, "logo.png"))
        if not logo.isNull():
            p_icon.setPixmap(logo.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            p_icon.setStyleSheet("opacity: 0.3;")
        p_layout.addWidget(p_icon, 0, Qt.AlignCenter)

        p_text = QLabel("Chọn một cuộc hội thoại để bắt đầu")
        p_text.setStyleSheet("color: #90A4AE; font-size: 24px; margin-top: 25px; font-weight: 500;")
        p_layout.addWidget(p_text, 0, Qt.AlignCenter)

        self.content_stack.addWidget(placeholder_widget)

        # Chat Page
        self.chat_page = ChatPage(self)
        self.content_stack.addWidget(self.chat_page)

        content_layout.addLayout(self.content_stack)
        main_layout.addWidget(content_frame, 1)

        # 3. INFO PANEL
        self.info_panel = InfoPanel(self.parent)
        self.info_panel.close_btn.clicked.connect(self.toggle_info_panel)
        self.info_panel.hide()
        main_layout.addWidget(self.info_panel)

        self.search_bar.textChanged.connect(self.filter_lists)
        self.profile_btn.clicked.connect(self.show_profile)
        self.logout_btn.clicked.connect(self.logout)
        self.chat_page.info_button.clicked.connect(self.toggle_info_panel)

    # ------------------------------------------------------------ SIDEBAR: thu gọn / mở rộng
    def toggle_sidebar(self):
        self.set_sidebar_collapsed(not self.sidebar_collapsed)

    def set_sidebar_collapsed(self, collapsed):
        """Thu gọn thành thanh mỏng chỉ có avatar, hoặc mở rộng lại đầy đủ."""
        self.sidebar_collapsed = collapsed

        # ẩn / hiện các thành phần chữ
        self.sidebar_header.setVisible(not collapsed)
        self.lbl_chat.setVisible(not collapsed)
        self.lbl_online.setVisible(not collapsed)
        self.rail_divider.setVisible(collapsed)
        if collapsed:
            self.search_bar.clear()

        # footer: xếp dọc khi thu gọn
        if collapsed:
            self.footer_layout.setDirection(QBoxLayout.TopToBottom)
            self.footer_layout.setContentsMargins(12, 14, 12, 14)
            self.footer_layout.setStretchFactor(self.profile_btn, 0)
            self.footer_layout.setAlignment(self.profile_btn, Qt.AlignHCenter)
            self.footer_layout.setAlignment(self.logout_btn, Qt.AlignHCenter)
            self.profile_btn.setText("")
            self.profile_btn.setToolTip(self._profile_text.strip())
        else:
            self.footer_layout.setDirection(QBoxLayout.LeftToRight)
            self.footer_layout.setContentsMargins(20, 20, 20, 20)
            self.footer_layout.setStretchFactor(self.profile_btn, 1)
            self.footer_layout.setAlignment(self.profile_btn, Qt.Alignment())
            self.footer_layout.setAlignment(self.logout_btn, Qt.Alignment())
            self.profile_btn.setText(self._profile_text)
            self.profile_btn.setToolTip("")

        # danh sách: item gọn lại, chỉ còn icon
        item_qss = ("QListWidget::item { padding: 6px; margin: 4px 8px; border-radius: 14px; }"
                    if collapsed else "")
        for lst in (self.chat_list, self.online_list):
            lst.setStyleSheet(item_qss)
            lst.setIconSize(QSize(self._icon_px(), self._icon_px()))
        for i in range(self.chat_list.count()):
            self._refresh_item(self.chat_list.item(i))
        for i in range(self.online_list.count()):
            self._refresh_online_item(self.online_list.item(i))

        self._animate_sidebar(RAIL_W if collapsed else SIDEBAR_W)

    def _animate_sidebar(self, target):
        if self._sb_anim is not None:
            self._sb_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(200)
        anim.setStartValue(self.sidebar.width())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.valueChanged.connect(lambda v: self.sidebar.setFixedWidth(int(v)))
        anim.finished.connect(lambda: self.sidebar.setFixedWidth(target))
        self._sb_anim = anim
        anim.start()

    # ------------------------------------------------------------ ITEM CHAT
    def _refresh_item(self, item):
        """Vẽ lại 1 item chat theo dữ liệu đã lưu (tên, chưa đọc, trạng thái người kia)."""
        name = item.data(ROLE_NAME) or ""
        unread = item.data(ROLE_UNREAD) or 0
        other_id = item.data(ROLE_OTHER)
        dot = None if other_id is None else (other_id in self.online_users)

        item.setIcon(self.avatar_icon(name, self._icon_px(), dot=dot, badge=unread))
        text = f"{name}   ({unread})" if unread > 0 else name
        item.setText("" if self.sidebar_collapsed else text)
        item.setToolTip(text)

        font = item.font()
        if unread > 0:
            font.setBold(True)
            item.setForeground(QColor("#1E88E5"))
        else:
            font.setBold(False)
            item.setForeground(QColor("#37474F"))
        item.setFont(font)
        if self.sidebar_collapsed:
            item.setSizeHint(QSize(RAIL_W - 16, ICON_COLLAPSED + 12))
        else:
            item.setSizeHint(QSize())  # QSize() không hợp lệ -> Qt tự tính kích thước mặc định

    def _refresh_online_item(self, item):
        name = item.data(ROLE_NAME) or ""
        item.setIcon(self.avatar_icon(name, self._icon_px(), dot=True))
        item.setText("" if self.sidebar_collapsed else name)
        item.setToolTip(name)
        if self.sidebar_collapsed:
            item.setSizeHint(QSize(RAIL_W - 16, ICON_COLLAPSED + 12))
        else:
            item.setSizeHint(QSize())

    def _make_chat_item(self, chat_id, name, unread, other_id):
        item = QListWidgetItem()
        item.setData(ROLE_ID, chat_id)
        item.setData(ROLE_NAME, name)
        item.setData(ROLE_OTHER, other_id)
        item.setData(ROLE_UNREAD, unread)
        self._refresh_item(item)
        return item

    def _find_chat_row(self, chat_id):
        for i in range(self.chat_list.count()):
            if self.chat_list.item(i).data(ROLE_ID) == chat_id:
                return i
        return -1

    def upsert_chat_item(self, chat_data):
        """
        Chạy trên GUI Thread thông qua Signal.
        - Có sẵn trong list: đẩy lên đầu, in đậm, cập nhật số chưa đọc.
        - Chưa có: tạo mới, chèn vào đầu.
        """
        chat_id = chat_data["chat_id"]
        name = chat_data.get("name", "Chat")
        unread = chat_data.get("unread_count", 1)

        row = self._find_chat_row(chat_id)
        old_other = None
        if row >= 0:
            old_other = self.chat_list.item(row).data(ROLE_OTHER)
            self.chat_list.takeItem(row)

        if "other_id" in chat_data:
            other_id = chat_data["other_id"]
        elif "members" in chat_data or chat_data.get("is_group"):
            other_id = self._other_id_from(chat_data)
        else:
            other_id = old_other

        new_item = self._make_chat_item(chat_id, name, unread, other_id)
        self.chat_list.insertItem(0, new_item)
        if chat_id == self.current_chat_id:
            self.chat_list.setCurrentItem(new_item)

    def fetch_specific_chat(self, chat_id):
        """Chạy trong thread phụ để lấy thông tin chat mới."""
        status, chat = api_client.get_chat_detail(self.parent.token, chat_id)
        if status == 200:
            chat["unread_count"] = 1
            self.socket_signals.chat_updated.emit(chat)

    # ------------------------------------------------------------ DANH SÁCH
    def update_online_list(self):
        self.online_list.clear()
        for uid in self.online_users.keys():
            status, data = api_client.get_user_info(self.parent.token, uid)
            name = f"User {uid}"
            if status == 200:
                name = data.get("full_name") or data.get("username")

            item = QListWidgetItem()
            item.setData(ROLE_ID, uid)
            item.setData(ROLE_NAME, name)
            self._refresh_online_item(item)
            self.online_list.addItem(item)
        self.filter_lists()

    def refresh_chats(self):
        status, chats = api_client.get_chats(self.parent.token)
        self.chat_list.clear()
        if status == 200:
            chats_sorted = sorted(chats, key=lambda c: c.get('unread_count', 0), reverse=True)
            for c in chats_sorted:
                item = self._make_chat_item(
                    c["chat_id"], c['name'], c.get('unread_count', 0), self._other_id_from(c))
                self.chat_list.addItem(item)
                if c["chat_id"] == self.current_chat_id:
                    self.chat_list.setCurrentItem(item)
            self.filter_lists()

    def refresh_chat_presence(self):
        """Cập nhật chấm xanh/xám của các chat khi danh sách online thay đổi."""
        for i in range(self.chat_list.count()):
            self._refresh_item(self.chat_list.item(i))
        self.update_chat_presence()

    def update_chat_presence(self):
        """Cập nhật trạng thái (chấm xanh/xám) ở header của cuộc trò chuyện đang mở."""
        if self.current_chat_id is None:
            return
        oid = self.current_other_user_id
        self.chat_page.set_presence(None if oid is None else (oid in self.online_users))

    def set_current_user_label(self):
        if self.parent.user_id:
            status, data = api_client.get_user_info(self.parent.token, self.parent.user_id)
            if status == 200:
                name = data.get("full_name") or self.parent.user_id
                self._profile_text = f"  {name}"
            else:
                self._profile_text = f"  User {self.parent.user_id}"
            if self.sidebar_collapsed:
                self.profile_btn.setToolTip(self._profile_text.strip())
            else:
                self.profile_btn.setText(self._profile_text)

    # ------------------------------------------------------------ SOCKET
    def on_socket_connected(self):
        self.socket_connected = True
        print("[Socket] Kết nối thành công")

    def on_socket_disconnected(self):
        self.socket_connected = False
        print("[Socket] Mất kết nối")

    def handle_online_users(self, users):
        self.online_users = {uid: True for uid in users if uid != self.parent.user_id}
        self.update_online_list()
        self.refresh_chat_presence()

    def handle_new_message(self, msg):
        chat_id = msg["chat_id"]
        sender = msg["sender_id"]

        # TRƯỜNG HỢP 1: Đang mở chat này -> Hiện tin nhắn
        if self.content_stack.currentWidget() == self.chat_page and self.current_chat_id == chat_id:
            try:
                key_dict = msg["aes_key_encrypted"]
                wrapped = key_dict.get(str(self.parent.user_id))
                if wrapped:
                    aes_key = crypto_client.unwrap_aes_key(wrapped, self.parent.private_key)
                    text = crypto_client.decrypt_aes_gcm(msg["content"], aes_key, msg["iv"], msg["tag"])
                else:
                    text = "[Không có khóa giải mã]"
            except Exception as e:
                text = f"[Lỗi: {e}]"
            self.chat_page.add_message(sender, text, sender == self.parent.user_id)

            token = self.parent.token
            threading.Thread(target=api_client.mark_chat_read, args=(token, chat_id), daemon=True).start()

        # TRƯỜNG HỢP 2: Chat khác / Chat mới -> cập nhật list ngay
        else:
            row = self._find_chat_row(chat_id)
            if row >= 0:
                item = self.chat_list.item(row)
                self.upsert_chat_item({
                    "chat_id": chat_id,
                    "name": item.data(ROLE_NAME),
                    "unread_count": (item.data(ROLE_UNREAD) or 0) + 1,
                })
            else:
                threading.Thread(target=self.fetch_specific_chat, args=(chat_id,), daemon=True).start()

    def filter_lists(self):
        search_text = self.search_bar.text().lower().strip()
        for lst in (self.chat_list, self.online_list):
            for i in range(lst.count()):
                item = lst.item(i)
                item.setHidden(search_text not in (item.data(ROLE_NAME) or "").lower())

    def show_profile(self):
        profile_dialog = ProfilePage(self.parent)
        profile_dialog.exec_()

    def toggle_info_panel(self):
        if self.info_panel.isVisible():
            self.info_panel.hide()
        else:
            if self.current_other_user_id:
                self.info_panel.load_user_info(self.current_other_user_id)
                self.info_panel.show()
            else:
                print("Không có thông tin user để hiển thị")

    def on_chat_clicked(self, item):
        chat_id = item.data(ROLE_ID)
        self.current_chat_id = chat_id
        self.info_panel.hide()

        # Đánh dấu đã đọc: bỏ in đậm, bỏ số, bỏ huy hiệu
        item.setData(ROLE_UNREAD, 0)
        self._refresh_item(item)

        token = self.parent.token
        if token and chat_id:
            threading.Thread(target=api_client.mark_chat_read, args=(token, chat_id), daemon=True).start()

        self.chat_page.load_chat(chat_id)
        self.update_chat_presence()
        self.content_stack.setCurrentWidget(self.chat_page)

    def start_chat_with_user(self, item):
        uid = item.data(ROLE_ID)
        if uid == self.parent.user_id:
            return
        status, chat = api_client.create_chat(self.parent.token, name=f"Chat {uid}", is_group=False, members=[uid])
        if status in (200, 201):
            chat_id = chat["chat_id"]
            self.current_chat_id = chat_id
            self.info_panel.hide()
            self.chat_page.load_chat(chat_id)
            self.content_stack.setCurrentWidget(self.chat_page)
            self.upsert_chat_item({"chat_id": chat_id, "name": chat["name"],
                                   "unread_count": 0, "other_id": uid})
            self.update_chat_presence()

    def logout(self):
        try:
            if self.socket_connected:
                sio.disconnect()
            self._socket_initialized = False
            self.socket_connected = False
        except Exception:
            pass
        api_client.logout(self.parent.token)
        self.parent.token = None
        self.parent.user_id = None
        self.parent.private_key = None
        self.current_other_user_id = None
        self.current_chat_id = None
        self.online_users = {}
        self.online_list.clear()
        self.chat_page.clear_chat()
        self.parent.layout.setCurrentWidget(self.parent.login_page)

    def connect_socket(self):
        if self._socket_initialized:
            try:
                if not self.socket_connected:
                    sio.connect(SOCKET_URL, auth={"token": self.parent.token}, wait_timeout=5)
            except Exception:
                pass
            return
        self._socket_initialized = True

        def _connect():
            try:
                sio.connect(SOCKET_URL, auth={"token": self.parent.token}, wait_timeout=10)
            except Exception as e:
                print(f"Socket connect err: {e}")
        threading.Thread(target=_connect, daemon=True).start()

        @sio.on("connect")
        def on_connect():
            self.socket_signals.connected.emit()

        @sio.on("disconnect")
        def on_disconnect():
            self.socket_signals.disconnected.emit()

        @sio.on("online_users")
        def on_online(users):
            self.socket_signals.online_users_received.emit(users)

        @sio.on("receive_message")
        def on_receive(msg):
            self.socket_signals.new_message_received.emit(msg)
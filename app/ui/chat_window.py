from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ChatWindow(QWidget):
    message_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowFlags(
            Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint
        )
        self.setWindowTitle("桌宠对话")
        self.resize(420, 560)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(
            """
            QWidget {
                background: #fff7fb;
                color: #5b4a5a;
                font-family: "Microsoft YaHei UI";
            }
            QLabel#title {
                font-size: 16px;
                font-weight: 700;
                color: #ff5e9c;
            }
            QTextBrowser {
                border: 2px solid #ffd1e4;
                border-radius: 14px;
                background: #ffffff;
                padding: 8px;
                selection-background-color: #ffc2db;
            }
            QLineEdit {
                border: 2px solid #ffd1e4;
                border-radius: 12px;
                padding: 8px 10px;
                background: #fff;
                font-size: 14px;
            }
            QPushButton {
                border: none;
                border-radius: 12px;
                padding: 8px 14px;
                background: #ff79ad;
                color: #fff;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #ff5c9b;
            }
            """
        )

        self.title = QLabel("和桌宠聊天", self)
        self.title.setObjectName("title")
        self.title.setFont(QFont("Microsoft YaHei UI", 11))

        self.chat_view = QTextBrowser(self)
        self.chat_view.setOpenExternalLinks(False)

        self.input_box = QLineEdit(self)
        self.input_box.setPlaceholderText("输入你想说的话...")
        self.send_btn = QPushButton("发送", self)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(self.send_btn, 0)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addWidget(self.title)
        root.addWidget(self.chat_view, 1)
        root.addLayout(input_row)

        self.send_btn.clicked.connect(self._emit_message)
        self.input_box.returnPressed.connect(self._emit_message)

    def _emit_message(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self.message_submitted.emit(text)

    def append_user(self, text: str):
        self._append_bubble("你", text, "#ffe4ef", "#7c4760")

    def append_assistant(self, text: str):
        self._append_bubble("桌宠", text, "#e8f7ff", "#3f6075")

    def append_status(self, text: str):
        self._append_tip(f"状态: {text}", "#b66f8d")

    def append_error(self, text: str):
        self._append_tip(f"错误: {text}", "#cc4b4b")

    def _append_bubble(self, sender: str, text: str, bg: str, fg: str):
        safe = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        html = (
            f"<div style='margin: 8px 0;'>"
            f"<div style='font-size:12px;color:#a06b84;margin-bottom:3px'>{sender}</div>"
            f"<div style='display:inline-block;max-width:95%;"
            f"background:{bg};color:{fg};padding:8px 10px;border-radius:10px;"
            f"line-height:1.4'>{safe}</div></div>"
        )
        self.chat_view.append(html)
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def _append_tip(self, text: str, color: str):
        safe = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self.chat_view.append(
            f"<div style='margin:6px 0;color:{color};font-size:12px'>[{safe}]</div>"
        )
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def prepare_for_shutdown(self):
        self._allow_close = True

    def closeEvent(self, event):
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()

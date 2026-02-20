import sys
import threading

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (QApplication, QLabel, QWidget, QGraphicsDropShadowEffect,
                             QScrollArea, QVBoxLayout, QFrame)

# 定义滚动条的样式表（粉色、细条、圆角）
SCROLLBAR_STYLESHEET = """
    QScrollArea {
        background: transparent;
        border: none;
    }
    QScrollBar:vertical {
        border: none;
        background: rgba(0, 0, 0, 0);
        width: 6px;
        margin: 0px 0px 0px 0px;
    }
    QScrollBar::handle:vertical {
        background: rgba(255, 138, 188, 0.6);
        min-height: 20px;
        border-radius: 3px;
    }
    QScrollBar::handle:vertical:hover {
        background: rgba(255, 138, 188, 1.0);
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
"""


class AnimeSpeechBubble(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)  # 允许鼠标事件以便滚动

        # --- 配置参数 ---
        self._border_radius = 12
        self._tail_width = 24
        self._tail_height = 14

        # 最大尺寸限制
        self._max_width = 400
        self._max_height = 250  # 超过这个高度出现滚动条
        self._min_width = 120

        # 内部边距 (Padding)
        self._padding_x = 16
        self._padding_y = 12

        # 动态坐标
        self._tail_x = 20

        # --- 阴影效果 ---
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        # --- UI 结构初始化 ---
        self._init_ui()
        self.hide()

    def _init_ui(self):
        # 1. 滚动区域
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll_area.setStyleSheet(SCROLLBAR_STYLESHEET)

        # 2. 滚动区域的内容容器
        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")

        # 3. 文本标签
        self._label = QLabel(self._scroll_content)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._label.setStyleSheet(
            "QLabel {"
            "  color: #594d5b;"
            "  font: 11pt 'Microsoft YaHei UI';"
            "  font-weight: bold;"
            "  padding: 0px;"
            "}"
        )

        # 布局管理（确保Label撑满ScrollContent）
        self._layout = QVBoxLayout(self._scroll_content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._label)

        self._scroll_area.setWidget(self._scroll_content)

    def show_text(self, text: str):
        t = (text or "").strip()
        if not t:
            self.hide()
            return

        self._label.setText(t)

        # 1. 计算文本在限定宽度下的理想尺寸
        # 预留给滚动条和左右Padding的空间
        available_text_width = self._max_width - (self._padding_x * 2) - 10

        fm = self._label.fontMetrics()
        # 计算文字矩形
        text_rect = fm.boundingRect(0, 0, available_text_width, 8000, Qt.TextWordWrap, t)

        text_w = text_rect.width()
        text_h = text_rect.height()

        # 2. 计算气泡总尺寸 (文字 + Padding)
        bubble_w = max(self._min_width, text_w + (self._padding_x * 2))
        bubble_h = text_h + (self._padding_y * 2)

        # 3. 限制尺寸 & 判断是否需要滚动
        final_w = min(bubble_w, self._max_width)
        final_h = min(bubble_h, self._max_height)

        # 实际窗口高度 = 气泡高度 + 尾巴高度 + 阴影预留
        total_window_h = final_h + self._tail_height + 20

        self.resize(final_w, total_window_h)

        # 4. 设置 ScrollArea 的位置和大小
        # 注意：ScrollArea 要避开圆角区域，稍微往里缩一点
        area_x = 4
        area_y = 4
        area_w = final_w - 8
        area_h = final_h - 8
        self._scroll_area.setGeometry(area_x, area_y, area_w, area_h)

        # 处理对齐：如果文字很少，不需要滚动，我们让文字居中显示比较好看
        if bubble_h <= self._max_height:
            self._label.setAlignment(Qt.AlignCenter)
        else:
            self._label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.show()
        self.raise_()
        self.update()

    def update_anchor(self, local_head: QPoint):
        if not self.isVisible():
            return

        bubble_body_h = self.height() - self._tail_height - 20

        # 基础定位：气泡底边中心对准目标点
        x = int(local_head.x() - self.width() / 2)
        y = int(local_head.y() - bubble_body_h - self._tail_height - 5)

        # 边界检查 (防止气泡飞出父窗口)
        parent = self.parentWidget()
        if parent:
            if x + self.width() > parent.width() - 5:
                x = parent.width() - self.width() - 5
            if x < 5:
                x = 5
            if y < 5:
                y = 5

        self.move(x, y)

        # 计算尾巴相对于气泡左侧的 X 坐标
        rel_x = local_head.x() - x

        # 限制尾巴不要跑出圆角
        margin = self._border_radius + self._tail_width / 2
        self._tail_x = max(margin, min(self.width() - margin, rel_x))

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        # 气泡主体高度 (减去尾巴和阴影预留)
        h = self.height() - self._tail_height - 20
        r = self._border_radius

        tail_w = self._tail_width
        tail_h = self._tail_height
        tail_x = self._tail_x

        # --- 绘制路径 (和之前一样，保证丝滑连接) ---
        path = QPainterPath()
        path.moveTo(0, r)
        path.arcTo(0, 0, r * 2, r * 2, 180, -90)  # 左上
        path.lineTo(w - r, 0)
        path.arcTo(w - r * 2, 0, r * 2, r * 2, 90, -90)  # 右上
        path.lineTo(w, h - r)
        path.arcTo(w - r * 2, h - r * 2, r * 2, r * 2, 0, -90)  # 右下

        # 底部连接尾巴
        path.lineTo(tail_x + tail_w / 2, h)

        # 贝塞尔曲线画尾巴
        tip_x = tail_x
        tip_y = h + tail_h

        # 向下弯曲
        path.cubicTo(tail_x + tail_w * 0.2, h, tail_x + tail_w * 0.1, tip_y - 2, tip_x, tip_y)
        # 向上弯曲回底边
        path.cubicTo(tail_x - tail_w * 0.1, tip_y - 2, tail_x - tail_w * 0.2, h, tail_x - tail_w / 2, h)

        path.lineTo(r, h)
        path.arcTo(0, h - r * 2, r * 2, r * 2, 270, -90)  # 左下
        path.closeSubpath()

        # 填充 (柔和渐变)
        grad = QLinearGradient(0, 0, 0, h + tail_h)
        grad.setColorAt(0.0, QColor("#ffffff"))
        grad.setColorAt(1.0, QColor("#fff0f5"))
        painter.setBrush(grad)

        # 描边
        pen = QPen(QColor(255, 150, 190, 240), 1.5)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        painter.drawPath(path)
        painter.end()

    # 重写 wheelEvent 确保鼠标在气泡上滚动时能传递给 ScrollArea
    # 通常 QScrollArea 会自己处理，但显式声明有时候更稳健
    def wheelEvent(self, event):
        self._scroll_area.wheelEvent(event)


# --- 测试代码 ---
class _ConsoleBridge(QObject):
    text_submitted = pyqtSignal(str)


class _BubbleTestWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scrollable Anime Bubble Test")
        self.resize(600, 500)
        self.setStyleSheet("background:#333333;")  # 深色背景看阴影
        self._bubble = AnimeSpeechBubble(self)

    def set_bubble_text(self, text: str):
        self._bubble.show_text(text)
        self.update_bubble_pos()

    def update_bubble_pos(self):
        # 模拟人物头部位置
        head_pos = QPoint(self.width() // 2, self.height() - 100)
        self._bubble.update_anchor(head_pos)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_bubble_pos()

    def mousePressEvent(self, event):
        # 点击屏幕测试移动气泡
        self._bubble.update_anchor(event.pos())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = _BubbleTestWindow()
    bridge = _ConsoleBridge()
    bridge.text_submitted.connect(window.set_bubble_text)
    window.show()


    def _read_console():
        print("Type long text to test scrolling!")
        print("Example: Copy paste a paragraph.")
        # 发送一段长文本进行测试
        long_text = (
                "这是一个很长的测试文本。" * 2 +
                "\n\n这里是第二段，用来测试换行和滚动条是否正常工作。"
                "当内容超出设定的高度（250px）时，右侧会出现一个粉色的细滚动条。"
                "你可以使用鼠标滚轮来查看隐藏的内容。"
                "\n\nUI依然保持了圆角和小尾巴的无缝连接设计。"
        )
        bridge.text_submitted.emit(long_text)

        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not line: continue
            if line.strip().lower() in {"/exit", "quit"}:
                app.quit()
                break
            bridge.text_submitted.emit(line.strip())


    threading.Thread(target=_read_console, daemon=True).start()
    sys.exit(app.exec_())
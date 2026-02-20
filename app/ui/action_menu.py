import sys
import math
from PyQt5.QtCore import (
    QPoint, Qt, pyqtSignal, QRectF, QEasingCurve, QVariantAnimation
)
# 【修正1】添加 QRegion 到导入列表
from PyQt5.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QRadialGradient, QBrush, QConicalGradient, QRegion
)
from PyQt5.QtWidgets import QWidget, QApplication


# --- 1. 动画扇形按钮 ---
class ArcButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, label: str, start_angle: float, span_angle: float, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        # 初始属性
        self._label = label
        self._start_angle = start_angle
        self._span_angle = span_angle

        # 尺寸参数 (用于动画)
        self._base_inner = 70.0
        self._base_outer = 110.0
        self._current_outer_offset = 0.0  # 动画增量
        self._hover_opacity = 0.0  # 0.0 ~ 1.0 用于颜色渐变

        # 状态
        self._hover = False
        self._pressed = False
        self._enabled = True

        # 动画引擎
        self._anim = QVariantAnimation()
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutBack)  # 弹性效果
        self._anim.valueChanged.connect(self._update_anim_values)

        # UI 设置
        self.setAttribute(Qt.WA_TranslucentBackground)

    # --- 动画相关属性 ---
    def _update_anim_values(self, value):
        # value 是 0.0 到 1.0
        self._current_outer_offset = value * 15.0  # 悬停时向外扩张 15px
        self._hover_opacity = value
        self.update()
        self._update_mask()  # 形状变了，点击区域也要变

    def enterEvent(self, event):
        if not self._enabled: return
        self._hover = True
        self._anim.stop()
        self._anim.setStartValue(self._hover_opacity)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._pressed = False
        self._anim.stop()
        self._anim.setStartValue(self._hover_opacity)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._enabled and event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._enabled and event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # --- 核心逻辑 ---
    def _get_path(self):
        """根据当前动画状态计算扇形路径"""
        center = QPoint(self.width() // 2, self.height() // 2)

        # 动态半径
        inner = self._base_inner
        outer = self._base_outer + self._current_outer_offset

        # 加上间隙 (Gap)
        gap = 4  # 扇区之间的缝隙角度
        start = self._start_angle + gap / 2
        span = self._span_angle - gap

        path = QPainterPath()
        # 注意：Qt的arcTo角度是逆时针的，0度在3点钟

        # 转换为弧度
        start_rad = math.radians(start)
        end_rad = math.radians(start + span)

        # 1. 外弧 (逆时针)
        rect_outer = QRectF(center.x() - outer, center.y() - outer, outer * 2, outer * 2)
        path.arcMoveTo(rect_outer, start)
        path.arcTo(rect_outer, start, span)

        # 2. 内弧 (顺时针回画)
        rect_inner = QRectF(center.x() - inner, center.y() - inner, inner * 2, inner * 2)
        # arcTo 的 startAngle 是起点，sweepLength 是扫过角度
        path.lineTo(center.x() + inner * math.cos(end_rad), center.y() - inner * math.sin(end_rad))
        path.arcTo(rect_inner, start + span, -span)

        path.closeSubpath()
        return path

    def _update_mask(self):
        """设置遮罩，让鼠标只能点击扇形区域"""
        # 【修正2】显式使用 QRegion 包裹 QPolygon
        # .toFillPolygon() 返回 QPolygonF (浮点)
        # .toPolygon() 返回 QPolygon (整数)
        # QRegion(QPolygon) 是合法的构造函数
        region = QRegion(self._get_path().toFillPolygon().toPolygon())
        self.setMask(region)

    def resizeEvent(self, event):
        self._update_mask()
        super().resizeEvent(event)

    def set_label(self, text):
        self._label = text
        self.update()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = self._get_path()

        # --- 颜色配置 (Cyber Pastel 风格) ---
        base_color = QColor(40, 44, 52, 180)
        border_color = QColor(0, 255, 255, 80)

        target_bg = QColor(0, 200, 255, 100)
        if "Exit" in self._label:
            target_bg = QColor(255, 80, 100, 100)

        if self._pressed:
            target_bg = QColor(255, 255, 255, 150)

        final_bg = self._mix_color(base_color, target_bg, self._hover_opacity)
        final_border = self._mix_color(border_color, QColor(255, 255, 255, 200), self._hover_opacity)

        painter.setPen(Qt.NoPen)
        painter.setBrush(final_bg)
        painter.drawPath(path)

        pen = QPen(final_border, 1.5 if not self._hover else 2.5)
        painter.setPen(pen)
        painter.drawPath(path)

        if self._enabled:
            painter.setPen(Qt.NoPen)
            deco_color = QColor(255, 255, 255, int(40 + 100 * self._hover_opacity))
            painter.setBrush(deco_color)
            center = QPoint(self.width() // 2, self.height() // 2)
            mid_angle = math.radians(self._start_angle + self._span_angle / 2)
            mid_r = self._base_outer + self._current_outer_offset - 4
            p_x = center.x() + mid_r * math.cos(mid_angle)
            p_y = center.y() - mid_r * math.sin(mid_angle)
            painter.drawEllipse(QPoint(int(p_x), int(p_y)), 2, 2)

        center = QPoint(self.width() // 2, self.height() // 2)
        mid_angle_deg = self._start_angle + self._span_angle / 2
        mid_angle_rad = math.radians(mid_angle_deg)
        text_r = (self._base_inner + self._base_outer + self._current_outer_offset) / 2
        tx = center.x() + text_r * math.cos(mid_angle_rad)
        ty = center.y() - text_r * math.sin(mid_angle_rad)

        painter.setPen(QColor(255, 255, 255, 240) if self._enabled else QColor(255, 255, 255, 80))
        font = QFont("Segoe UI", 10, QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        painter.setFont(font)
        text_rect = QRectF(tx - 40, ty - 15, 80, 30)
        painter.drawText(text_rect, Qt.AlignCenter, self._label)

    def _mix_color(self, c1, c2, ratio):
        r = c1.red() * (1 - ratio) + c2.red() * ratio
        g = c1.green() * (1 - ratio) + c2.green() * ratio
        b = c1.blue() * (1 - ratio) + c2.blue() * ratio
        a = c1.alpha() * (1 - ratio) + c2.alpha() * ratio
        return QColor(int(r), int(g), int(b), int(a))


# --- 2. 中心装饰核心 ---
class CoreWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 120)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._angle = 0
        self._timer = QVariantAnimation()
        self._timer.setDuration(10000)
        self._timer.setStartValue(0)
        self._timer.setEndValue(360)
        self._timer.setLoopCount(-1)
        self._timer.valueChanged.connect(self._update_angle)
        self._timer.start()

    def _update_angle(self, val):
        self._angle = val
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = self.rect().center()

        painter.setPen(QPen(QColor(0, 255, 255, 100), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(c, 55, 55)

        painter.translate(c)
        painter.rotate(self._angle)

        grad = QConicalGradient(0, 0, 0)
        grad.setColorAt(0.0, QColor(0, 255, 255, 0))
        grad.setColorAt(0.5, QColor(0, 255, 255, 180))
        grad.setColorAt(1.0, QColor(0, 255, 255, 0))

        pen = QPen(QBrush(grad), 2)
        painter.setPen(pen)
        painter.drawEllipse(QPoint(0, 0), 62, 62)
        painter.end()


# --- 3. 主面板 ---
class ModelActionPanel(QWidget):
    open_chat_clicked = pyqtSignal()
    close_app_clicked = pyqtSignal()
    eye_follow_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(320, 320)

        self._eye_follow_enabled = True
        self._init_ui()

    def _init_ui(self):
        self.core = CoreWidget(self)
        self.core.move(self.width() // 2 - self.core.width() // 2, self.height() // 2 - self.core.height() // 2)

        # 顶部
        self.btn_top = self._add_arc_btn("Eyes ON", 55, 70)
        self.btn_top.clicked.connect(self._toggle_eye_follow)

        # 左侧
        self.btn_chat = self._add_arc_btn("Chat", 145, 70)
        self.btn_chat.clicked.connect(self.open_chat_clicked)

        # 底部
        self.btn_bottom = self._add_arc_btn("Config", 235, 70)
        self.btn_bottom.set_enabled(False)

        # 右侧
        self.btn_exit = self._add_arc_btn("Exit", 325, 70)
        self.btn_exit.clicked.connect(self.close_app_clicked)

    def _add_arc_btn(self, text, start, span):
        btn = ArcButton(text, start, span, self)
        btn.resize(self.width(), self.height())
        return btn

    def _toggle_eye_follow(self):
        self._eye_follow_enabled = not self._eye_follow_enabled
        self.btn_top.set_label("Eyes ON" if self._eye_follow_enabled else "Eyes OFF")
        self.eye_follow_toggled.emit(self._eye_follow_enabled)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = self.rect().center()

        grad = QRadialGradient(c, 80)
        grad.setColorAt(0.0, QColor(0, 0, 0, 180))
        grad.setColorAt(0.8, QColor(0, 20, 40, 100))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(c, 80, 80)

        painter.setPen(QColor(0, 255, 255, 200))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignCenter, "SYSTEM")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    panel = ModelActionPanel()
    panel.show()
    sys.exit(app.exec_())
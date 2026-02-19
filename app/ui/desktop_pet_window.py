import logging
import math
import os
import sys
from pathlib import Path
import time

from PyQt5.QtCore import QPoint, QTimer, Qt, pyqtSignal, QRectF
from PyQt5.QtGui import (
    QCursor,
    QGuiApplication,
    QFont,
    QColor,
    QPainter,
    QPen,
    QPainterPath,
    QLinearGradient,
)
from PyQt5.QtWidgets import (
    QApplication,
    QWidget, QVBoxLayout,
)

from app.core.local_model_server import LocalModelServer
from app.ui.chat_window import ChatWindow
from app.ui.live2d_webview import Live2DWebView
from app.workers.llm_worker import LLMWorker
from app.workers.voice_worker import VoiceWorker


logger = logging.getLogger("live2d.window")

# ============================
# 闂?闂傚倷绀侀幖顐﹀磹閻熼偊鐔嗘慨妞诲亾鐠侯垶鏌涢幇闈涙灍闁哄拋鍓氶幈銊モ攽閸ワ附婢€ndows 闂傚倷鑳剁划顖炲礉濡ゅ懎绠犻柟鎹愵嚙閸氳銇勯弬鍨挃闁活厽宀搁弻娑滎槼闁靛洦鐩畷鎴﹀箻閸︻厾鏉搁梺鍝勫暊閸嬫捇鏌￠崱妯肩煉闁?# ============================
_IS_WINDOWS = sys.platform == "win32"
if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

    _WM_NCHITTEST = 0x0084
    _HTTRANSPARENT = -1
    _HTCLIENT = 1


class ArcButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._path = QPainterPath()
        self._hover = False
        self._enabled = True
        self._center = QPoint(0, 0)
        self._inner_r = 60
        self._outer_r = 90
        self._start_deg = 0
        self._span_deg = 0
        self._mid_deg = 0
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)

    def set_arc(self, center: QPoint, inner_r: int, outer_r: int, start_deg: int, span_deg: int):
        self._center = center
        self._inner_r = inner_r
        self._outer_r = outer_r
        self._start_deg = start_deg
        self._span_deg = span_deg
        self._mid_deg = start_deg + span_deg / 2
        self._rebuild_path()
        self.update()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._hover = False
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.update()

    def _rebuild_path(self):
        path = QPainterPath()
        outer = QRectF(
            self._center.x() - self._outer_r,
            self._center.y() - self._outer_r,
            self._outer_r * 2,
            self._outer_r * 2,
        )
        inner = QRectF(
            self._center.x() - self._inner_r,
            self._center.y() - self._inner_r,
            self._inner_r * 2,
            self._inner_r * 2,
        )
        path.arcMoveTo(outer, self._start_deg)
        path.arcTo(outer, self._start_deg, self._span_deg)
        path.arcTo(inner, self._start_deg + self._span_deg, -self._span_deg)
        path.closeSubpath()
        self._path = path

    def _contains(self, pos):
        return self._path.contains(pos)

    def hit_test(self, pos: QPoint) -> bool:
        return self._path.contains(pos)

    def set_hover(self, hover: bool):
        if self._hover != hover:
            self._hover = hover
            self.update()

    def mouseMoveEvent(self, event):
        if not self._enabled:
            return
        hover = self._contains(event.pos())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._enabled and event.button() == Qt.LeftButton and self._contains(event.pos()):
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        if self._path.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base = QColor(90, 220, 255, 140)
        accent = QColor(200, 120, 255, 160)
        if not self._enabled:
            base = QColor(120, 180, 200, 90)
            accent = QColor(150, 140, 180, 90)
        if self._hover:
            base = QColor(140, 240, 255, 200)
            accent = QColor(230, 160, 255, 220)

        grad = QLinearGradient(self._path.boundingRect().topLeft(), self._path.boundingRect().bottomRight())
        grad.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 60))
        grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 80))
        painter.fillPath(self._path, grad)

        pen = QPen(base, 3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self._path)

        inner_pen = QPen(accent, 1)
        painter.setPen(inner_pen)
        painter.drawPath(self._path)

        painter.setPen(QColor(220, 245, 255, 230))
        painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
        mid_r = (self._inner_r + self._outer_r) / 2
        angle = self._mid_deg % 360
        rad = math.radians(angle)
        x = self._center.x() + math.cos(rad) * mid_r
        y = self._center.y() - math.sin(rad) * mid_r
        text_rect = QRectF(x - 50, y - 12, 100, 24)
        painter.drawText(text_rect, Qt.AlignCenter, self._label)
        painter.end()


class ModelActionPanel(QWidget):
    open_chat_clicked = pyqtSignal()
    close_app_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(320, 320)
        self.setMouseTracking(True)
        self._build_ui()

    def _build_ui(self):
        self.chat_btn = ArcButton("Chat", self)
        self.close_btn = ArcButton("Exit", self)
        self.top_btn = ArcButton("", self)
        self.bottom_btn = ArcButton("", self)
        self.chat_btn.clicked.connect(self.open_chat_clicked.emit)
        self.close_btn.clicked.connect(self.close_app_clicked.emit)
        self.top_btn.set_enabled(False)
        self.bottom_btn.set_enabled(False)
        self._layout_arcs()

    def _layout_arcs(self):
        center = self.rect().center()
        inner_r = 84
        outer_r = 128
        for btn in (self.chat_btn, self.close_btn, self.top_btn, self.bottom_btn):
            btn.setGeometry(0, 0, self.width(), self.height())
        self.close_btn.set_arc(center, inner_r, outer_r, -35, 70)
        self.chat_btn.set_arc(center, inner_r, outer_r, 145, 70)
        self.top_btn.set_arc(center, inner_r, outer_r, 55, 70)
        self.bottom_btn.set_arc(center, inner_r, outer_r, 235, 70)
        self.bottom_btn.raise_()
        self.top_btn.raise_()
        self.chat_btn.raise_()
        self.close_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_arcs()

    def _hit_test_segment(self, pos: QPoint):
        for btn in (self.chat_btn, self.close_btn, self.top_btn, self.bottom_btn):
            if btn._enabled and btn.hit_test(pos):
                return btn
        return None

    def mouseMoveEvent(self, event):
        hit = self._hit_test_segment(event.pos())
        for btn in (self.chat_btn, self.close_btn, self.top_btn, self.bottom_btn):
            btn.set_hover(btn is hit)
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._hit_test_segment(event.pos())
            if hit is self.chat_btn:
                self.open_chat_clicked.emit()
            elif hit is self.close_btn:
                self.close_app_clicked.emit()
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        for btn in (self.chat_btn, self.close_btn, self.top_btn, self.bottom_btn):
            btn.set_hover(False)
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        ring_rect = self.rect().adjusted(24, 24, -24, -24)
        outer_pen = QPen(QColor(120, 220, 255, 180), 3)
        outer_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(outer_pen)
        painter.drawArc(ring_rect, 40 * 16, 100 * 16)
        painter.drawArc(ring_rect, 220 * 16, 100 * 16)

        inner_rect = ring_rect.adjusted(16, 16, -16, -16)
        inner_pen = QPen(QColor(180, 120, 255, 140), 2)
        inner_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(inner_pen)
        painter.drawArc(inner_rect, 50 * 16, 80 * 16)
        painter.drawArc(inner_rect, 230 * 16, 80 * 16)

        core_pen = QPen(QColor(120, 220, 255, 120), 1)
        painter.setPen(core_pen)
        painter.drawEllipse(self.rect().center(), 46, 30)

        painter.setPen(QColor(220, 245, 255, 220))
        painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignVCenter, "CORE MENU")
        painter.end()


class DesktopPetWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live2D Desktop Pet")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # ================================================================
        # 闂?缂傚倸鍊烽悞锕傚礉閺嶎厹鈧啴宕ㄩ懜顑挎睏濠电偞鍨堕惌顕€鎮炴繝鍥ㄧ厱婵犻潧妫楅鎾煕閵堝洤鏋旂紒杈ㄥ笒閳藉鈻庨幋婵冩嫛闂備礁鎼Λ娆撳礉濞嗘挸鏋佺€广儱鎷嬪鈺傘亜閹烘垵鈧湱绱為崟顐嬫棃鎮╅棃娑楁勃濠电偛寮剁划鎾诲箖閿熺媭鏁囬柣鏂挎憸缁夊爼姊洪棃鈺佺槣闁告ü绮欓幃?60%闂傚倷鐒︾€笛呯矙閹寸偟闄勯柡鍐ㄥ€荤粻鏃堟煕瀹€鈧崑鐐哄煕?1200px
        #   濠电姵顔栭崳顖滃緤閹屾綎缁绢厼鎳庨弳鐐烘⒒娴ｈ鍋犻柛鏂跨焸椤㈡牠宕ㄩ弶鎳筹箓鏌涢敐鍌氫壕nough闂傚倷绀侀幖顐﹀磹閸ф鐒垫い鎺嶈兌閵嗘帡鏌嶇憴鍕诞闁哄瞼鍠栭弻銊р偓锝庡墮閳绻濆▓鍨灀闁哄懐濞€瀵偄顓奸崶锔藉媰闂佸憡鎸嗛崟鎴欏劦濮婅櫣绮欑捄銊х杽闂佸憡鏌ㄩ惌鍌炲箚閸儱骞㈡繛鍡楁捣閸撱劑鎮楅悙鐟扳偓妤呫€冩俊宸檊h闂傚倷鐒﹀鎸庣濞嗘挻鍊块柨鏇炲€搁悞鍨亜閹搭厼澧俊顐ｎ殜瀹?
        # ================================================================
        screen = QGuiApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            short_edge = min(sg.width(), sg.height())
            win_size = min(int(short_edge * 0.6), 1200)
        else:
            win_size = 800
        self.resize(648, 720)
        logger.info("Window size: %dx%d", win_size, win_size)

        self._model_server: LocalModelServer | None = None
        self.chat_window: ChatWindow | None = None
        self._last_pan_log_ts = 0.0
        self._drag_accum_x = 0.0
        self._drag_accum_y = 0.0
        self._drag_cursor_last: QPoint | None = None

        # ============================
        # 闂?闂傚倷绀侀幖顐﹀磹閻熼偊鐔嗘慨妞诲亾鐠侯垶鏌涢幇闈涙灍闁哄拋鍓氶幈銊ノ熼崸妤€鎽甸梺鍝勬缁绘劙婀佸┑鐘诧工閸燁垶骞戦敐澶嬬厱閻庯絽澧庣粔顔锯偓瑙勬礃閻熲晠骞婇敓鐘参ч柛灞剧⊕濞呮牠姊婚崒娆戣窗闁稿瀚幑銏ゅ箳閹存梹鐏侀梺鐟邦嚟婵澹曟禒瀣厵闁诡垎鍜冪礊闂佸湱鏅繛鈧柡宀嬬到铻ｉ柛顭戝枤閸橆偊姊?        # ============================
        self._cached_model_bounds: dict = {}
        self._is_model_dragging: bool = False

        self._action_panel = ModelActionPanel()
        self._action_panel.open_chat_clicked.connect(self._open_chat)
        self._action_panel.close_app_clicked.connect(self._quit_app)

        model_url = self._prepare_model_url()
        self.live2d_view = Live2DWebView(self, model_url=model_url, no_motion_mode=True)
        self.live2d_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.live2d_view.customContextMenuRequested.connect(self._show_action_panel)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.live2d_view)
        self.setStyleSheet("background: transparent;")

        self.llm_worker = LLMWorker(self)
        self.voice_worker = VoiceWorker(self)
        self._chat_follow_timer = QTimer(self)
        self._chat_follow_timer.setInterval(220)
        self._chat_follow_timer.timeout.connect(self._position_chat_window)
        self._window_follow_timer = QTimer(self)
        self._window_follow_timer.setInterval(16)
        self._window_follow_timer.timeout.connect(self._follow_window_with_model)

        self._wire_signals()
        self.llm_worker.start()
        self.voice_worker.start()
        self._window_follow_timer.start()

    # ================================================================
    # 闂?闂傚倷绀侀幖顐﹀磹閻熼偊鐔嗘慨妞诲亾鐠侯垶鏌涢幇闈涙灍闁哄拋鍓氶幈銊モ攽閸ワ絽浜炬い銈庢CHITTEST 闂傚倸鍊风欢锟犲磻閸曨厽宕查柟瀛樻儕閿濆绠ｉ柨鏇楀亾缂佲偓瀹€鍕厱婵犻潧妫楅顐︽煕濡吋鏆柡宀嬬秮楠炲洭顢欓挊澶嬬暚闂備焦鎮堕崐銈夊窗濮樺崬鍨濇慨妯挎硾闁裤倖淇婇悙棰濆殭濞?    # ================================================================
    def nativeEvent(self, eventType, message):
        if _IS_WINDOWS and eventType == b"windows_generic_MSG":
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == _WM_NCHITTEST:
                    if self._is_model_dragging:
                        return True, _HTCLIENT
                    lp = msg.lParam
                    screen_x = ctypes.c_short(lp & 0xFFFF).value
                    screen_y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                    local_pos = self.mapFromGlobal(QPoint(screen_x, screen_y))
                    if self._is_point_on_model(local_pos):
                        return True, _HTCLIENT
                    return True, _HTTRANSPARENT
            except Exception:
                logger.debug("nativeEvent WM_NCHITTEST error", exc_info=True)
        return super().nativeEvent(eventType, message)

    def _is_point_on_model(self, pos: QPoint) -> bool:
        """
        闂傚倷绀侀幉锛勬暜閸ヮ剙纾归柡宥庡幖閽冪喖鏌涢妷銏℃珖闁绘粎绮穱濠囧Χ閸屾矮澹曢柣鐔哥矌婢ф绮欓幘瀵割浄闁挎洖鍊搁悞鍨亜閹烘垵顏柣鎺楃畺閺屾洘绻涢崹顔煎缂傚倸绉寸换姗€寮婚垾鎰佸悑闁告侗鍠栭ˇ鈺佲攽閳ュ啿绾ч柟顔煎€搁悾鐑藉Ψ閳哄倹娅囬梺閫炲苯澧柨鏇樺灲椤㈡棃宕熸惔锝呮灈闁搞劍鍎抽悾鐑藉炊閿濆懍澹曢梺缁樺灱濡嫮绮堝畝鍕厱婵炴垵宕獮妤冪磼缂併垹鐏﹂柡宀嬬秮閸╋繝宕掑杈╂噯闂備礁鎽滈崑鐐哄箰閾忣偂绻?        婵犵數鍋犻幓顏嗙礊閳ь剚绻涙径瀣鐎殿噮鍋婃俊鍫曞椽娴ｇ懓鐦滄俊鐐€栭幐楣冨磻閻愮繝绻嗛柛銉ｅ妺缁诲棙銇勯幇顔兼瀻濞存粍鍎抽埞鎴︽倷鐎涙绋囧銈嗗灥濡繈骞嗘笟鈧、姘跺焵椤掑嫬绠氱€光偓閸曨偆鍘告繛杈剧秬濡嫰鎮￠崒鐐粹拺闁告繂瀚婵囥亜閵夛箑濮嶇€规洘鑹鹃埥澶愬閻樼洅鏇㈡⒑閸撴彃浜濈紒顔肩Ч閹洭鎮剧仦鎯ф瀾闂佺厧澹婇崜娆撴倶椤忓牊鍤夐柍鍝勬噺閻撴洟鏌曟繛鍨姶闁稿孩鎹囬弻锝夊Χ閸涱噮妫炲銈嗘礃閻熲晠鐛鈧獮宥嗘媴闂€鎰棜?        """
        bounds = self._cached_model_bounds
        if not bounds:
            return True  # 闂備礁鎼ˇ顐﹀疾濠婂棙鎳岄梻浣圭湽閸庨亶骞婂鈧悰顔跨疀濞戞ê鐎銈嗘閸嬫劙宕?bounds 闂傚倷娴囧銊╂嚄閼稿灚娅犳俊銈傚亾闁伙絽鐏氱粭鐔煎焵椤掆偓椤曪綁顢欓悙顒€顎涢梺闈╁瘜閸橀箖藝椤旇姤鍙忛柟鐑樻尵閳洘銇勯鐐靛ⅵ妞ゃ垺蓱缁绘繂顫濋鍌ゆН闂備線娼ц墝闁哄懏鐩畷婵嬪Ψ閳哄倻鍘卞┑鐐叉閿氶柣蹇ョ秮閺屾稓鈧絽澧庣粔顔锯偓?
        left = float(bounds.get("left", 0))
        right = float(bounds.get("right", self.width()))
        top = float(bounds.get("top", 0))
        bottom = float(bounds.get("bottom", self.height()))

        model_w = right - left
        model_h = bottom - top
        if model_w <= 0 or model_h <= 0:
            return True  # 闂傚倷娴囧銊╂嚄閼稿灚娅犳俊銈傚亾闁伙絽鐏氶幏鍛村礈閹绘帗顓块梻浣侯焾閺堫剙顫濋妸鈺佹槬婵°倕鎳忛悡銉︾箾閹寸儐鐒介柣鎺戞憸缁辨帡顢氶崨顓犱化闂佺懓鍢查澶嬩繆閸洖宸濇い鎾閸炲綊姊绘担铏瑰笡闁告梹顨婇獮濠囧箻鐠囨彃鎯?
        # 濠电姷顣槐鏇㈠磿閹版澘鍌ㄩ柣鎾崇昂閳ь剚甯￠幃娆擃敆閸屾鐏冩俊鐐€栭幐楣冨磻閻旈晲绻嗛柛銉墯閻撴洘绻濇繝鍌氭殺閻庢碍澹嗛埀顒冾潐濞叉﹢宕濋弴銏╂晪?(闂?padding 闂備浇宕垫慨宕囨媼閺屻儱鐤鹃柣鎰暩閸楁岸鏌ｉ弮鍥仩缁炬崘濮ゆ穱濠囶敍濠婂懎绗＄紓浣鸿檸閸ㄥ爼寮婚敓鐘茬妞ゆ帊绀侀弳妤呮⒑缁嬫鍎滅紓宥勭閻?
        padding = max(25, min(model_w, model_h) * 0.12)
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        rx = model_w / 2.0 + padding
        ry = model_h / 2.0 + padding

        # 濠电姷顣槐鏇㈠磿閹版澘鍌ㄩ柣鎾崇昂閳ь剚甯楅妶锝夊礃閵婏富妫熼梻渚€娼荤€靛矂宕㈤幖浣歌摕? (x-cx)闂?rx闂?+ (y-cy)闂?ry闂?<= 1
        dx = (pos.x() - cx) / rx
        dy = (pos.y() - cy) / ry
        return (dx * dx + dy * dy) <= 1.0

    # ================================================================
    def _wire_signals(self):
        # LLM
        self.llm_worker.chunk_ready.connect(self._append_assistant_text)
        self.llm_worker.status_changed.connect(self._append_status)
        self.llm_worker.error_occurred.connect(self._append_error)
        self.llm_worker.voice_payload_ready.connect(self.voice_worker.add_payload)
        self.llm_worker.new_session.connect(self.voice_worker.start_new_session)
        self.llm_worker.pet_command_ready.connect(self._apply_pet_command)

        # Voice
        self.voice_worker.error_occurred.connect(self._append_error)
        self.voice_worker.voice_started.connect(self._on_voice_started)
        self.voice_worker.voice_finished.connect(self._on_voice_finished)
        self.voice_worker.viseme_weights_changed.connect(self._on_viseme_weights)
        self.voice_worker.emphasis_triggered.connect(self._on_emphasis)
        self.voice_worker.emotion_detected.connect(self._on_emotion)

    def _on_voice_started(self):
        self.live2d_view.set_speaking(True)
        # Use immediate viseme-driven mouth control in main app, same as test tool.
        self.live2d_view.set_mouth_immediate(
            0.0, {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        )

    def _on_voice_finished(self):
        self.live2d_view.set_speaking(False)
        self.live2d_view.set_mouth_immediate(
            0.0, {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        )

    def _on_viseme_weights(self, weights: dict):
        if not weights:
            self.live2d_view.set_mouth_immediate(
                0.0, {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
            )
            return
        # Keep level fixed at 0.8 and let viseme weights define current mouth shape.
        self.live2d_view.set_mouth_immediate(0.8, weights)

    def _on_emphasis(self, strength: float):
        self.live2d_view.trigger_emphasis(strength)

    def _on_emotion(self, emotion: str):
        self.live2d_view.set_emotion(emotion)

    def _prepare_model_url(self) -> str:
        env_url = (os.getenv("LIVE2D_MODEL_URL") or "").strip()
        if env_url:
            return env_url

        model_file = self._find_model_file()
        if model_file is None:
            return ""

        root_dir = self._determine_root_dir(model_file)
        self._model_server = LocalModelServer(root_dir=root_dir, port=18080)
        self._model_server.start()
        return self._model_server.build_url(model_file)

    def _find_model_file(self) -> Path | None:
        env_path = (os.getenv("LIVE2D_MODEL_PATH") or "").strip()
        if env_path:
            p = Path(env_path)
            if p.exists() and p.is_file() and p.name.endswith((".model3.json", ".model.json")):
                return p.resolve()

        default_root = (Path(__file__).resolve().parents[2] / "models").resolve()
        if not default_root.exists():
            return None

        files = sorted(default_root.rglob("*.model3.json"))
        if files:
            return files[0].resolve()
        files = sorted(default_root.rglob("*.model.json"))
        if files:
            return files[0].resolve()
        return None

    def _determine_root_dir(self, model_file: Path) -> Path:
        default_root = (Path(__file__).resolve().parents[2] / "models").resolve()
        if default_root.exists():
            try:
                model_file.resolve().relative_to(default_root.resolve())
                return default_root.resolve()
            except Exception:
                pass
        return model_file.parent.resolve()

    def _ensure_chat_window(self) -> ChatWindow:
        if self.chat_window is None:
            self.chat_window = ChatWindow()
            self.chat_window.message_submitted.connect(self._send_message)
        return self.chat_window

    def _open_chat(self):
        self._action_panel.hide()
        chat = self._ensure_chat_window()
        chat.show()
        chat.raise_()
        chat.activateWindow()
        self._chat_follow_timer.start()
        self._position_chat_window()

    def _position_chat_window(self):
        if not self.chat_window or not self.chat_window.isVisible():
            self._chat_follow_timer.stop()
            return
        chat = self.chat_window
        avail = self._active_screen_geometry()
        geo = self.frameGeometry()
        x = geo.right() + 16
        y = geo.top() + 28
        if avail is None:
            chat.move(x, y)
            return

        chat_w = chat.width()
        chat_h = chat.height()
        if x + chat_w > avail.right():
            x = geo.left() - chat_w - 16
        x = max(avail.left(), min(x, avail.right() - chat_w))
        y = max(avail.top(), min(y, avail.bottom() - chat_h))
        chat.move(x, y)

    def _position_chat_window_fallback(self):
        if not self.chat_window:
            return
        chat = self.chat_window
        geo = self.frameGeometry()
        x = geo.right() + 16
        y = geo.top() + 28
        chat.move(x, y)

    def _show_action_panel(self, pos: QPoint):
        global_pos = self.live2d_view.mapToGlobal(pos)
        panel_size = self._action_panel.sizeHint()
        x = global_pos.x() - panel_size.width() // 2
        y = global_pos.y() - panel_size.height() - 14
        self._action_panel.move(x, y)
        self._action_panel.show()
        self._action_panel.raise_()
        self._action_panel.activateWindow()

    # ================================================================
    # 闂?婵犵數鍎戠徊钘壝归崒鐐茬獥婵°倕鎳庨弸浣糕攽閸屾碍鍟為柡鍜佸墯閹便劌顫滈崱妤€顫梻濠庡墻閸撶喎顕ｉ崼鏇熷€婚柛鈩冾殕閻忔洖顪冮妶鍡樼妞ゃ劌锕ら悾鐑藉醇閺囩喎鈧兘鏌涢幘鑼妽妞ゆ柨绉电换娑㈠箣閻愬瓨鍎庨梺鍛娒晶钘壩ｉ幇鐗堟櫆闂佹鍨版禍?bounds 婵犵數鍋為幐鑽ゅ枈瀹ュ洨鐭欓柟鎹愵嚙绾惧鏌ㄥ┑鍡╂Ц缂佲偓閸曨垱鐓犲┑顔藉姇閳ь剚鍔欏顐㈩吋婢跺鎷哄銈嗗姂閸ㄨ崵绮婚敐鍛斀闁绘劕寮堕崰姗€鏌?    # ================================================================
    def _follow_window_with_model(self):
        def _on_bounds(bounds):
            if not isinstance(bounds, dict) or not bounds:
                return

            # 闂?缂傚倸鍊搁崐鎼佸磹瑜版帒绠伴柟闂寸劍閸?bounds闂傚倷鐒︾€笛呯矙閹寸偟闄勯柡鍐ㄥ€归?nativeEvent 婵犵數鍋為崹鍫曞箹閳哄懎鍌ㄩ柟顖嗏偓閺?WM_NCHITTEST 婵犵數鍋犻幓顏嗙礊閳ь剚绻涙径瀣鐎?            self._cached_model_bounds = bounds
            drag_active = bool(bounds.get("dragActive", False))
            self._is_model_dragging = drag_active

            if not drag_active:
                self._drag_accum_x = 0.0
                self._drag_accum_y = 0.0
                self._drag_cursor_last = None
                return
            cursor = QCursor.pos()
            if self._drag_cursor_last is None:
                self._drag_cursor_last = cursor
                return
            dx = cursor.x() - self._drag_cursor_last.x()
            dy = cursor.y() - self._drag_cursor_last.y()
            self._drag_cursor_last = cursor
            self._pan_window_with_delta(bounds, float(dx), float(dy))

        self.live2d_view.get_model_bounds(_on_bounds)

    def _pan_window_with_delta(self, bounds: dict, raw_dx: float, raw_dy: float):
        if abs(raw_dx) < 0.5 and abs(raw_dy) < 0.5:
            return

        raw_dx, raw_dy = self._consume_bounds_overflow(bounds, raw_dx, raw_dy)

        self._drag_accum_x += raw_dx
        self._drag_accum_y += raw_dy
        apply_dx = int(self._drag_accum_x)
        apply_dy = int(self._drag_accum_y)
        if apply_dx == 0 and apply_dy == 0:
            return
        self._drag_accum_x -= apply_dx
        self._drag_accum_y -= apply_dy

        cur = self.frameGeometry()
        wanted_x = cur.x() + apply_dx
        wanted_y = cur.y() + apply_dy

        avail = self._active_screen_geometry()
        clamped_x = False
        clamped_y = False
        if avail is not None:
            min_x = avail.x()
            max_x = avail.x() + avail.width() - cur.width()
            min_y = avail.y()
            max_y = avail.y() + avail.height() - cur.height()
            if wanted_x < min_x:
                wanted_x = min_x
                clamped_x = True
            elif wanted_x > max_x:
                wanted_x = max_x
                clamped_x = True
            if wanted_y < min_y:
                wanted_y = min_y
                clamped_y = True
            elif wanted_y > max_y:
                wanted_y = max_y
                clamped_y = True

        applied_dx = wanted_x - cur.x()
        applied_dy = wanted_y - cur.y()
        self.move(wanted_x, wanted_y)

        residual_dx = apply_dx - applied_dx if clamped_x else 0.0
        residual_dy = apply_dy - applied_dy if clamped_y else 0.0
        if abs(residual_dx) > 0.01 or abs(residual_dy) > 0.01:
            self.live2d_view.nudge_model_offset(residual_dx, residual_dy)

        now = time.monotonic()
        if now - self._last_pan_log_ts > 0.5:
            self._last_pan_log_ts = now
            logger.info(
                "Drag pan raw=(%.3f, %.3f) applied=(%d, %d) clamp=(%s,%s) window=(%d, %d, %d, %d)",
                raw_dx, raw_dy, applied_dx, applied_dy,
                clamped_x, clamped_y, wanted_x, wanted_y, cur.width(), cur.height(),
            )

        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()

    def _consume_bounds_overflow(self, bounds: dict, dx: float, dy: float):
        vw = float(bounds.get("viewWidth", self.live2d_view.width()))
        vh = float(bounds.get("viewHeight", self.live2d_view.height()))
        left = float(bounds.get("left", 0))
        right = float(bounds.get("right", 0))
        top = float(bounds.get("top", 0))
        bottom = float(bounds.get("bottom", 0))

        overflow_x = 0.0
        overflow_y = 0.0
        if right > vw:
            overflow_x = right - vw
        elif left < 0:
            overflow_x = left
        if bottom > vh:
            overflow_y = bottom - vh
        elif top < 0:
            overflow_y = top

        if overflow_x > 0 and dx < 0:
            step = min(-dx, overflow_x)
            self.live2d_view.nudge_model_offset(-step, 0)
            dx += step
        elif overflow_x < 0 and dx > 0:
            step = min(dx, -overflow_x)
            self.live2d_view.nudge_model_offset(step, 0)
            dx -= step

        if overflow_y > 0 and dy < 0:
            step = min(-dy, overflow_y)
            self.live2d_view.nudge_model_offset(0, -step)
            dy += step
        elif overflow_y < 0 and dy > 0:
            step = min(dy, -overflow_y)
            self.live2d_view.nudge_model_offset(0, step)
            dy -= step

        return dx, dy

    def _active_screen_geometry(self):
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return None
        geo = screen.geometry()
        logger.debug(
            "Active screen geometry: x=%s y=%s w=%s h=%s",
            geo.x(), geo.y(), geo.width(), geo.height(),
        )
        return geo

    def _send_message(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        chat = self._ensure_chat_window()
        chat.append_user(text)
        self.llm_worker.send_message(text)

    def _apply_pet_command(self, payload: dict):
        expression = str(payload.get("expression", "neutral")).strip().lower()
        motion = str(payload.get("motion", "idle")).strip().lower()
        reply = str(payload.get("reply", "") or "")
        if expression == "neutral":
            inferred = self._infer_expression_from_text(reply)
            if inferred:
                expression = inferred
        # Do not set expression here, otherwise face changes before voice playback starts.
        # Expression is driven by voice timeline events during speaking.
        self.live2d_view.play_motion(motion)

    def _append_assistant_text(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_assistant(text)

    def _append_status(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_status(text)

    def _append_error(self, text: str):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.append_error(text)
    def _infer_expression_from_text(self, text: str) -> str:
        if not text:
            return ""
        t = text.lower()
        happy = ["开心", "高兴", "太好", "棒", "喜欢", "可爱", "哈哈", "谢谢", "好呀", "好哇", "好耶", "太棒"]
        sad = ["难过", "抱歉", "对不起", "遗憾", "伤心", "不好意思", "失落", "可惜"]
        angry = ["生气", "烦", "讨厌", "别", "闭嘴", "愤怒", "气死", "不爽"]
        surprised = ["哇", "诶", "惊", "真的吗", "竟然", "居然", "原来", "天哪"]
        shy = ["害羞", "脸红", "不好意思", "小声", "嘻嘻"]

        if any(k in t for k in angry):
            return "angry"
        if any(k in t for k in sad):
            return "sad"
        if any(k in t for k in surprised) or "?" in t or "？" in t:
            return "surprised"
        if any(k in t for k in shy):
            return "shy"
        if any(k in t for k in happy):
            return "happy"
        return ""

    def _quit_app(self):
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        self._chat_follow_timer.stop()
        self._window_follow_timer.stop()
        self._action_panel.close()
        if self.chat_window:
            self.chat_window.prepare_for_shutdown()
            self.chat_window.close()
        self.llm_worker.stop()
        self.voice_worker.stop()
        if self._model_server:
            self._model_server.stop()
        super().closeEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.chat_window and self.chat_window.isVisible():
            self._position_chat_window()

import json
import os
import logging
from pathlib import Path

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QColor
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineSettings, QWebEngineView


DEFAULT_MODEL_URL = (
    "https://unpkg.com/pixi-live2d-display@0.4.0/assets/shizuku/shizuku.model.json"
)

logger = logging.getLogger("live2d.webview")


class LoggingWebEnginePage(QWebEnginePage):
    LEVEL_MAP = {
        QWebEnginePage.InfoMessageLevel: logging.INFO,
        QWebEnginePage.WarningMessageLevel: logging.WARNING,
        QWebEnginePage.ErrorMessageLevel: logging.ERROR,
    }

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        py_level = self.LEVEL_MAP.get(level, logging.INFO)
        logger.log(py_level, "JS[%s:%s] %s", source_id, line_number, message)
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class Live2DWebView(QWebEngineView):
    def __init__(self, parent=None, model_url: str | None = None):
        super().__init__(parent)
        self._model_url = model_url
        self.setPage(LoggingWebEnginePage(self))
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        html_path = Path(__file__).resolve().parents[2] / "assets" / "web" / "index.html"
        logger.info("Loading web page: %s", html_path)
        self.load(QUrl.fromLocalFile(str(html_path)))
        self.loadFinished.connect(self._on_load_finished)
        self.loadProgress.connect(lambda p: logger.info("WebView load progress: %s%%", p))

    def _on_load_finished(self, ok: bool):
        logger.info("WebView load finished: ok=%s", ok)
        if not ok:
            logger.error("WebView failed to load HTML page.")
            return
        model_url = (self._model_url or os.getenv("LIVE2D_MODEL_URL", "")).strip() or DEFAULT_MODEL_URL
        logger.info("Live2D model URL: %s", model_url)
        self._run_js("window.Live2DHost && window.Live2DHost.init", [model_url])

    def set_expression(self, expression: str):
        logger.info("Set expression: %s", expression)
        self._run_js("window.Live2DHost && window.Live2DHost.setExpression", [expression])

    def play_motion(self, motion: str):
        logger.info("Play motion: %s", motion)
        self._run_js("window.Live2DHost && window.Live2DHost.playMotion", [motion])

    def set_speaking(self, speaking: bool):
        logger.info("Set speaking: %s", speaking)
        self._run_js("window.Live2DHost && window.Live2DHost.setSpeaking", [speaking])

    def nudge_model_offset(self, dx: float, dy: float):
        self._run_js("window.Live2DHost && window.Live2DHost.nudgeOffset", [dx, dy])

    def set_fit_locked(self, locked: bool, refit: bool = True):
        self._run_js("window.Live2DHost && window.Live2DHost.setFitLocked", [locked, refit])

    def get_model_bounds(self, callback):
        js = (
            "(function(){"
            " const host = window.Live2DHost;"
            " if (!host || typeof host.getModelBounds !== 'function') return null;"
            " return host.getModelBounds();"
            "})();"
        )
        self.page().runJavaScript(js, callback)

    def _run_js(self, fn_expr: str, args):
        args_json = json.dumps(args, ensure_ascii=False)
        js = (
            "(function(){"
            f" const fn = {fn_expr};"
            " const owner = window.Live2DHost || window;"
            f" if (typeof fn === 'function') fn.apply(owner, {args_json});"
            "})();"
        )
        logger.debug("Run JS: %s args=%s", fn_expr, args)
        self.page().runJavaScript(js)

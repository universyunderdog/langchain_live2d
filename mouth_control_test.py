from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.core.local_model_server import LocalModelServer
from app.ui.live2d_webview import Live2DWebView


class MouthControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live2D Mouth Control Test")
        self.resize(1200, 900)

        self._model_server: LocalModelServer | None = None
        model_url = self._prepare_model_url()

        self.view = Live2DWebView(self, model_url=model_url)

        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.view, 1)

        controls = QWidget(self)
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setHorizontalSpacing(12)
        controls_layout.setVerticalSpacing(8)

        self.speaking_chk = QCheckBox("Speaking")
        self.speaking_chk.setChecked(True)
        self.speaking_chk.stateChanged.connect(self._on_speaking_changed)
        controls_layout.addWidget(self.speaking_chk, 0, 0, 1, 1)

        self.level_slider, self.level_label = self._make_slider("Level", 60, self._apply_mouth)
        controls_layout.addWidget(self.level_label, 0, 1)
        controls_layout.addWidget(self.level_slider, 0, 2, 1, 5)

        self.a_slider, self.a_label = self._make_slider("A", 100, self._apply_mouth)
        self.i_slider, self.i_label = self._make_slider("I", 0, self._apply_mouth)
        self.u_slider, self.u_label = self._make_slider("U", 0, self._apply_mouth)
        self.e_slider, self.e_label = self._make_slider("E", 0, self._apply_mouth)
        self.o_slider, self.o_label = self._make_slider("O", 0, self._apply_mouth)

        rows = [
            ("A", self.a_label, self.a_slider),
            ("I", self.i_label, self.i_slider),
            ("U", self.u_label, self.u_slider),
            ("E", self.e_label, self.e_slider),
            ("O", self.o_label, self.o_slider),
        ]
        for idx, (_name, lbl, sld) in enumerate(rows, start=1):
            controls_layout.addWidget(lbl, idx, 1)
            controls_layout.addWidget(sld, idx, 2, 1, 5)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton("Reset")
        btn_reset.clicked.connect(self._reset)
        btn_norm = QPushButton("Normalize")
        btn_norm.clicked.connect(self._normalize)
        btn_row.addWidget(btn_reset)
        btn_row.addWidget(btn_norm)
        btn_row.addStretch(1)

        row_wrap = QWidget(self)
        row_wrap.setLayout(btn_row)
        controls_layout.addWidget(row_wrap, 6, 0, 1, 7)

        layout.addWidget(controls, 0)

        self._on_speaking_changed(Qt.Checked)
        self._apply_mouth()

    def _make_slider(self, name: str, initial: int, on_change):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(initial)
        label = QLabel(f"{name}: {initial / 100.0:.2f}")

        def _changed(v: int):
            label.setText(f"{name}: {v / 100.0:.2f}")
            on_change()

        slider.valueChanged.connect(_changed)
        return slider, label

    def _on_speaking_changed(self, _state):
        self.view.set_speaking(self.speaking_chk.isChecked())

    def _weights(self) -> dict:
        return {
            "A": self.a_slider.value() / 100.0,
            "I": self.i_slider.value() / 100.0,
            "U": self.u_slider.value() / 100.0,
            "E": self.e_slider.value() / 100.0,
            "O": self.o_slider.value() / 100.0,
        }

    def _apply_mouth(self):
        self.view.set_mouth_immediate(self.level_slider.value() / 100.0, self._weights())

    def _normalize(self):
        vals = [self.a_slider.value(), self.i_slider.value(), self.u_slider.value(), self.e_slider.value(), self.o_slider.value()]
        s = sum(vals)
        if s <= 0:
            self.a_slider.setValue(100)
            self.i_slider.setValue(0)
            self.u_slider.setValue(0)
            self.e_slider.setValue(0)
            self.o_slider.setValue(0)
            return
        scales = [int(round(v * 100 / s)) for v in vals]
        self.a_slider.setValue(scales[0])
        self.i_slider.setValue(scales[1])
        self.u_slider.setValue(scales[2])
        self.e_slider.setValue(scales[3])
        self.o_slider.setValue(scales[4])

    def _reset(self):
        self.level_slider.setValue(60)
        self.a_slider.setValue(100)
        self.i_slider.setValue(0)
        self.u_slider.setValue(0)
        self.e_slider.setValue(0)
        self.o_slider.setValue(0)
        self.speaking_chk.setChecked(True)
        self._apply_mouth()

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

        default_root = (Path(__file__).resolve().parent / "models").resolve()
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
        default_root = (Path(__file__).resolve().parent / "models").resolve()
        if default_root.exists():
            try:
                model_file.resolve().relative_to(default_root.resolve())
                return default_root.resolve()
            except Exception:
                pass
        return model_file.parent.resolve()

    def closeEvent(self, event):
        if self._model_server:
            self._model_server.stop()
            self._model_server = None
        super().closeEvent(event)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    load_dotenv()
    app = QApplication(sys.argv)
    app.setApplicationName("Live2D Mouth Control Test")
    win = MouthControlWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())

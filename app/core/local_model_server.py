from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class LocalModelServer:
    def __init__(self, root_dir: Path, host: str = "127.0.0.1", port: int = 18080):
        self.root_dir = Path(root_dir).resolve()
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._server is not None:
            return
        handler_cls = partial(SimpleHTTPRequestHandler, directory=str(self.root_dir))
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
        except OSError:
            self._server = ThreadingHTTPServer((self.host, 0), handler_cls)
            self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def build_url(self, file_path: Path) -> str:
        rel = Path(file_path).resolve().relative_to(self.root_dir).as_posix()
        return f"http://{self.host}:{self.port}/{rel}"

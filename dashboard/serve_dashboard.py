#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Раздача дашборда + POST /api/refresh (запуск refresh.py).
Только stdlib; пишется владельцем стенда для LAN. Статика — как http.server."""
import json
import os
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DIR = os.path.dirname(os.path.abspath(__file__))
IP = os.environ.get("DASH_BIND_IP", "192.168.2.127")
PORT = int(os.environ.get("DASH_PORT", "8090"))
REFRESH_TIMEOUT_S = 180

_lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("directory", DIR)
        super().__init__(*args, **kwargs)

    def do_POST(self):  # noqa: N802 (api http.server)
        if self.path.split("?")[0].rstrip("/") != "/api/refresh":
            self.send_error(404)
            return
        if _lock.locked():
            self._json({"ok": True, "status": "busy"})
            return
        with _lock:
            try:
                p = subprocess.run(
                    ["/usr/bin/python3", "refresh.py"],
                    cwd=DIR, capture_output=True, text=True,
                    timeout=REFRESH_TIMEOUT_S,
                )
                tail = [l for l in (p.stdout or "").strip().splitlines() if l][-3:]
                self._json({"ok": p.returncode == 0,
                            "status": "done" if p.returncode == 0 else "failed",
                            "summary": tail,
                            "stderr": (p.stderr or "")[-300:] if p.returncode else ""})
            except Exception as exc:  # timeout и прочее — тоже сюда
                self._json({"ok": False, "status": "error", "error": repr(exc)[:200]})

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):  # noqa: N802
        self.send_header("Cache-Control", "no-store")  # data.js всегда свежий
        super().end_headers()

    def log_message(self, fmt, *args):
        msg = fmt % args if args else fmt
        if "/api/" in msg:
            super().log_message(fmt, *args)  # тишина для картинок/скриптов


if __name__ == "__main__":
    ThreadingHTTPServer((IP, PORT), Handler).serve_forever()

#!/usr/bin/env python3
"""cache_trace.py — TLS-снифер между агентом и инфером, чтобы найти точку рассинхрона префикса.

Зачем. Radix-кэш SGLang матчит промпт посимвольно с начала; замер на хосте показал, что у
Claude Code стабильно кэшируется ~55K токенов, а хвост (~72K) ре-префиллится каждый ход.
Чтобы понять МЕХАНИЗМ, надо сравнить два подряд отправленных запроса и найти первый элемент,
который отличается. Изнутри клиентского процесса запрос не виден, wire-логов у Desktop-пути нет.

Что делает. Слушает https://0.0.0.0:1235 (тот же серверный сертификат, что и :1234), форвардит
всё на https://127.0.0.1:1234 байт-в-байт, стриминг не буферизует. В лог пишет ОДНУ строку JSON
на запрос: хэши system / tools / каждой message, их длины, путь, usage из ответа и — главное —
поле diverge: чем именно этот запрос отличается от предыдущего ('system' | 'tools' | 'msg[i]' |
'append-only' | 'none'). Содержимое промпта в лог не пишется никогда, только sha1 и размеры.

Запуск на хосте:
  nohup python3 -I ~/agent-tools/cache_trace.py >>~/agent-tools/cache_trace.out 2>&1 &
  echo $! > ~/agent-tools/cache_trace.pid
Остановка: kill $(cat ~/agent-tools/cache_trace.pid)
Лог:       ~/agent-tools/cache_trace.jsonl   (зеркало на Windows: ops/rget.sh)

Клиент надо переключить на https://192.168.2.127:1235 (Developer → Configure Third-Party
Inference → base URL). Обратное переключение = вернуть :1234, прокси тогда просто не нужен.
"""
import http.client
import json
import os
import re
import hashlib
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get("TRACE_LOG", os.path.join(HERE, "cache_trace.jsonl"))
CERT = os.environ.get("TRACE_CERT", "/home/molochko/penny/tls/server.pem")
KEY = os.environ.get("TRACE_KEY", "/home/molochko/penny/tls/server.key")
PORT = int(os.environ.get("TRACE_PORT", "1235"))
UP_HOST, UP_PORT = "127.0.0.1", 1234

UP_CTX = ssl._create_unverified_context()
MAIN_KIND = "/v1/messages"


def kind_of(p):
    return (p or "").split("?")[0]
DROP_REQ = {"host", "content-length", "connection", "accept-encoding", "keep-alive", "proxy-connection"}
DROP_RESP = {"transfer-encoding", "content-length", "connection", "content-encoding", "keep-alive", "date", "server"}

_lock = threading.Lock()
_prev = {"sig": None}
_usage_re = re.compile(rb'"usage"\s*:\s*(\{[^{}]*\})')


def h(x):
    return hashlib.sha1(json.dumps(x, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12] if x is not None else None


def snip(m, n=110):
    """Role + первые n символов сообщения — чтобы назвать вину, а не перекладывать промпт на диск.

    Пишется только для ОДНОГО расходящегося сообщения и только в лог на хосте.
    """
    try:
        role = m.get("role") if isinstance(m, dict) else None
        c = m.get("content") if isinstance(m, dict) else m
        if isinstance(c, list):
            parts = []
            for b in c:
                if isinstance(b, dict):
                    t = b.get("text") or b.get("content") or ""
                    if isinstance(t, list):
                        t = json.dumps(t, ensure_ascii=False)
                    parts.append(f"<{b.get('type','?')}:{str(t)[:70]}>")
            c = " ".join(parts)
        return f"{role}: " + re.sub(r"\s+", " ", str(c))[:n]
    except Exception:
        return "?"


def fingerprint(payload, raw_len, kind=None):
    """Возвращает (строка-в-лог, сигнатуру для следующего сравнения)."""
    if not isinstance(payload, dict):
        return {"kind": "nonjson", "bytes": raw_len}, None
    sysb = payload.get("system")
    tools = payload.get("tools")
    msgs = payload.get("messages") or []
    m_sig = [h(m) for m in msgs]
    m_len = [len(json.dumps(m, ensure_ascii=False)) for m in msgs]
    m_snip = [snip(m) for m in msgs]
    # поля верхнего уровня, кроме system/messages: если из них рождается префикс, оно тут всплывёт
    tops = {k: h(v) for k, v in payload.items() if k not in ("system", "messages")}
    sig = {"sys": h(sysb), "tools": h(tools), "msgs": m_sig, "snips": m_snip, "kind": kind, "tops": tops}
    row = {
        "ts": round(time.time(), 2),
        "path": None,  # заполняет вызывающий
        "bytes": raw_len,
        "n_msgs": len(msgs),
        "sys_h": sig["sys"],
        "tools_h": sig["tools"],
        "msgs_h": m_sig,
        "msg_chars": m_len,
        "model": payload.get("model"),
        "max_tokens": payload.get("max_tokens"),
        "stream": payload.get("stream"),
    }
    p = _prev["sig"]
    same_kind = p is not None and (p.get("kind") == sig.get("kind"))
    if p is None or not same_kind:
        row["diverge"] = "first-seen" if p is None else "new-kind"
    elif p["sys"] != sig["sys"]:
        row["diverge"] = "system"
    elif p["tools"] != sig["tools"]:
        row["diverge"] = "tools"
    elif len(p["msgs"]) != len(sig["msgs"]):
        n = min(len(p["msgs"]), len(sig["msgs"]))
        first = next((i for i in range(n) if p["msgs"][i] != sig["msgs"][i]), None)
        row["diverge"] = f"msg[{first}] +len" if first is not None else f"append(len {len(p['msgs'])}->{len(sig['msgs'])})"
    else:
        first = next((i for i, (a, b) in enumerate(zip(p["msgs"], sig["msgs"])) if a != b), None)
        row["diverge"] = f"msg[{first}]" if first is not None else "none"
    if same_kind and p is not None:
        vary = sorted(k for k in set(tops) | set(p["tops"]) if tops.get(k) != p["tops"].get(k))
        if vary:
            row["vary_top"] = vary
            if row["diverge"] in ("none", "first-seen"):
                # history идентична, но чем-то запрос отличается — вот он, кандидат на рассинхрон
                row["diverge"] = "field:" + ",".join(vary)
    if isinstance(row["diverge"], str) and row["diverge"].startswith("msg["):
        try:
            i = int(row["diverge"].split("[")[1].split("]")[0])
            if p and i < len(p["msgs"]) and i < len(sig["msgs"]):
                row["msg_idx"] = i
                row["div_prev"] = p["snips"][i]
                row["div_cur"] = sig["snips"][i]
        except Exception:
            pass
    return row, sig


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _tunnel(self, body):
        conn = http.client.HTTPSConnection(UP_HOST, UP_PORT, context=UP_CTX, timeout=1800)
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in DROP_REQ}
        hdrs["host"] = f"{UP_HOST}:{UP_PORT}"
        conn.request(self.command, self.path, body=body, headers=hdrs)
        return conn, conn.getresponse()

    def _handle(self, body):
        row, sig = None, None
        if body:
            try:
                row, sig = fingerprint(json.loads(body), len(body), self.path.split("?")[0])
                row["path"] = self.path
            except Exception as e:
                row, sig = {"ts": round(time.time(), 2), "kind": "parse-err", "err": str(e)[:120], "bytes": len(body)}, None
            # TRACE_DUMP=1: сохранить одно боевое тело целиком, чтобы прогнать его повторно
            # и побисекчить (только /v1/messages, перезаписать следующим, потом удалить файл).
            if (
                os.environ.get("TRACE_DUMP")
                and kind_of(self.path) == MAIN_KIND
                and row
                and row.get("n_msgs", 0) >= int(os.environ.get("TRACE_DUMP_MIN_MSGS", "50"))
            ):
                try:
                    with open(os.path.join(HERE, "last_body.json"), "wb") as f:
                        f.write(body)
                    row["dumped"] = len(body)
                except Exception as e:
                    row["dump_err"] = str(e)[:80]
        if row is not None:
            # бета-заголовки могут менять рендер (thinking/tool-streaming и т.п.) — хэшируем как есть
            row["hdr_h"] = h(
                {
                    k.lower(): v
                    for k, v in self.headers.items()
                    if k.lower().startswith("anthropic") or k.lower() in ("x-session-id", "user-agent")
                }
            )
        try:
            conn, resp = self._tunnel(body)
        except Exception as e:
            self.send_error(502, f"upstream: {e}")
            if row:
                row["upstream_err"] = str(e)[:160]
                self._emit(row)
            return
        try:
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in DROP_RESP:
                    continue
                self.send_header(k, v)
            ctype = (resp.getheader("content-type") or "")
            self._nolag()
            if "text/event-stream" in ctype:
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                reader = getattr(resp, "read1", None) or resp.read
                tail = b""
                while True:
                    chunk = reader(65536)
                    if not chunk:
                        break
                    tail = (tail + chunk)[-200000:]
                    self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                if row is not None:
                    m = _usage_re.findall(tail)
                    if m:
                        try:
                            row["usage"] = json.loads(m[-1])
                        except Exception:
                            pass
            else:
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                if row is not None:
                    m = _usage_re.findall(data)
                    if m:
                        try:
                            row["usage"] = json.loads(m[-1])
                        except Exception:
                            pass
            row and row.setdefault("status", resp.status)
        except (BrokenPipeError, ConnectionResetError):
            row and row.setdefault("client_gone", True)
        except Exception as e:
            row and row.setdefault("relay_err", str(e)[:160])
        finally:
            try:
                resp.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        if row is not None:
            with _lock:
                _prev["sig"] = sig
            self._emit(row)

    def _nolag(self):
        try:
            import socket

            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass

    def _emit(self, row):
        line = json.dumps(row, ensure_ascii=False)
        with _lock:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _read_body(self):
        """Тело и по Content-Length, и по chunked — иначе chunked-запрос уйдёт пустым."""
        te = (self.headers.get("transfer-encoding") or "").lower()
        if "chunked" in te:
            buf = bytearray()
            while True:
                line = self.rfile.readline(65536).strip()
                if not line:
                    continue
                try:
                    n = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if n == 0:
                    self.rfile.readline(65536)  # trailing CRLF
                    break
                buf += self.rfile.read(n)
                self.rfile.readline(65536)  # CRLF after chunk
            return bytes(buf)
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        self._handle(self._read_body())

    def do_GET(self):
        self._handle(None)

    def do_HEAD(self):
        self._handle(None)


def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    try:  # TLS1.3 не мешает, но и не обязателен
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"[cache_trace] https://0.0.0.0:{PORT} -> https://{UP_HOST}:{UP_PORT}  log={LOG}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())

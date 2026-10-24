#!/usr/bin/env python3
"""openai_reorder_pump.py — TLS-фронт :8744 с рехоустом system-сообщений.

Зачем: ZCode (Anthropic-привычки) шлёт OpenAI-запросы, где role=system
встречается не только в начале истории; наш sglang отвечает
"System message must be at the beginning." (400). Anthropic-канал тоже не
варит их thinking:{enabled} без budget. Фронт на лету:
  * терминирует TLS своим сертификатом (leaf+CA цепочка — заодно радует
    Schannel-клиентов, коим ZCode и гремит по отзыву на Windows-стеке);
  * для POST /v1/chat/completions поднимает ВСЕ system/dev-сообщения в начало
    (стабильно, относительный порядок внутри групп сохраняется);
  * остальное — raw-дуплекс (SSE прозрачен).
Это мост до фикса на стороне ZCode/Pennyroyal; каждый запрос логируется
одной строкой в stdout.

Env: PUMP_PORT(8744) PUMP_UP_HOST(penny) PUMP_UP_PORT(8001)
     PUMP_CERT PUMP_KEY PUMP_LOG
"""
import json
import os
import socket
import ssl
import sys
import threading
import time

LISTEN = ("0.0.0.0", int(os.environ.get("PUMP_PORT", "8744")))
UPSTREAM = (os.environ.get("PUMP_UP_HOST", "penny"),
            int(os.environ.get("PUMP_UP_PORT", "8001")))
CERT = os.environ.get("PUMP_CERT", "/tls/server.pem")
KEY = os.environ.get("PUMP_KEY", "/tls/server.key")
IDLE_TIMEOUT = float(os.environ.get("PUMP_IDLE", "600"))
SYS_ROLES = ("system", "developer")
MAX_HEAD = 64 * 1024
MAX_BODY = 64 * 1024 * 1024


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stdout, flush=True)


def reorder_body(raw: bytes):
    """(bytes, changed:bool) — hoist system в начало; при любом сюрпризе
    возвращаем оригинал (не портить запрос ради косметики)."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw, False
    if not isinstance(obj, dict):
        return raw, False
    msgs = obj.get("messages")
    if not isinstance(msgs, list) or len(msgs) < 2:
        return raw, False
    def is_sys(m):
        return isinstance(m, dict) and m.get("role") in SYS_ROLES
    # Anthropic-isms, которые наш OpenAI-пайр не знает: маркеры промпт-кэша
    # и anthropic-only поля верхнего уровня, которые ZCode тащит и в OpenAI wire.
    stripped = 0
    for junk_top in ("thinking", "output_config", "context_management"):
        if junk_top in obj:
            del obj[junk_top]
            stripped += 1
    # Anthropic prefill-ассистент без content — OpenAI-пайр требует content у msgs
    for m in msgs:
        if isinstance(m, dict) and ("content" not in m or
                                    (m.get("role") == "assistant" and m.get("content") is None)):
            m["content"] = ""
            stripped += 1
    for m in msgs:
        if isinstance(m, dict):
            for junk in ("cache_control", "cacheControl"):
                if junk in m:
                    del m[junk]
                    stripped += 1
            c = m.get("content")
            if isinstance(c, list):  # Anthropic-блоки внутри content
                for blk in c:
                    if isinstance(blk, dict):
                        for junk in ("cache_control", "cacheControl"):
                            if junk in blk:
                                del blk[junk]
                                stripped += 1
    for t in (obj.get("tools") or []):           # антропик кэширует и тулзы
        if isinstance(t, dict):
            for junk in ("cache_control", "cacheControl"):
                if junk in t:
                    del t[junk]
                    stripped += 1
    if stripped:
        log(f"reorder: stripped {stripped} cache_control marker(s)")
    head = [m for m in msgs if is_sys(m)]
    tail = [m for m in msgs if not is_sys(m)]
    if (not head or msgs[:len(head)] == head) and not stripped:   # уже правильно
        return raw, False
    obj["messages"] = head + tail
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), True


def anthropic_fixup(raw: bytes):
    """POST /v1/messages: ZCode шлёт thinking:{type:enabled} без budget_tokens
    (схема Pennyroyal требует >=1024) и redacted_thinking в истории (405-подобный
    отказ). Бюджет на бэкенде всё равно не исполняется — дописываем минимальный;
    редacted-блоки вырезаем."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw, False
    if not isinstance(obj, dict):
        return raw, False
    changed = False
    th = obj.get("thinking")
    if isinstance(th, dict) and th.get("type") == "enabled" and "budget_tokens" not in th:
        cap = obj.get("max_tokens") or 4096
        th["budget_tokens"] = max(1024, min(cap // 2, 8192))
        changed = True
    msgs = obj.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict):
                for junk in ("cache_control", "cacheControl"):
                    if junk in m:
                        del m[junk]
                        changed = True
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                keep = [b for b in c if not (isinstance(b, dict) and b.get("type") == "redacted_thinking")]
                if len(keep) != len(c):
                    m["content"] = keep if keep else [{"type": "text", "text": ""}]
                    changed = True
                for b in m["content"] if isinstance(m.get("content"), list) else []:
                    if isinstance(b, dict):
                        for junk in ("cache_control", "cacheControl"):
                            if junk in b:
                                del b[junk]
                                changed = True
        for t in (obj.get("tools") or []):
            if isinstance(t, dict):
                for junk in ("cache_control", "cacheControl"):
                    if junk in t:
                        del t[junk]
                        changed = True
    if changed:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8"), True
    return raw, False


def read_head(f):
    recv1 = getattr(f, "read1", None) or (lambda n: f.read(n))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = recv1(4096)
        if not chunk:
            return None, None, buf
        buf += chunk
        if len(buf) > MAX_HEAD:
            return None, None, buf
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        hdrs[k.decode("latin-1").lower().strip()] = v.decode("latin-1").strip()
    return lines[0].decode("latin-1"), hdrs, rest


def handle(tls):
    up = None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        up = ctx.wrap_socket(socket.create_connection(UPSTREAM, timeout=15),
                             server_hostname="qwen-infer")
        f = tls.makefile("rb")
        while True:
            reqline, hdrs, rest = read_head(f)
            if reqline is None:
                return
            method, path = (reqline.split() + ["", ""])[:2]
            clen = int(hdrs.get("content-length", 0) or 0)
            if hdrs.get("transfer-encoding", "").lower() == "chunked":
                # не парсим — прозрачный проброс (reorder для таких запросов
                # недоступен; ZCode шлёт content-length, сюда не попадает)
                head_bytes = (reqline.encode("latin-1") + b"\r\n"
                              + b"\r\n".join(f"{k}: {v}".encode("latin-1") for k, v in hdrs.items())
                              + b"\r\n\r\n")
                up.sendall(head_bytes + rest)
                pump_raw(tls, up)
                return
            body = rest
            while len(body) < clen:
                body += f.read(clen - len(body))
            out_body = body
            if method == "POST" and 0 < clen <= MAX_BODY:
                if path.rstrip("/").endswith("/chat/completions"):
                    out_body, changed = reorder_body(body)
                    if changed:
                        hdrs["content-length"] = str(len(out_body))
                        log(f"reorder: hoisted/cleaned ({path}, {clen}->{len(out_body)}B)")
                elif path.rstrip("/").endswith("/messages"):
                    out_body, changed = anthropic_fixup(body)
                    if changed:
                        hdrs["content-length"] = str(len(out_body))
                        log(f"anthropic-fixup: thinking budget/redacted ({path}, {clen}->{len(out_body)}B)")
            head_bytes = (reqline.encode("latin-1") + b"\r\n"
                          + b"\r\n".join(f"{k}: {v}".encode("latin-1") for k, v in hdrs.items())
                          + b"\r\n\r\n")
            up.sendall(head_bytes + (out_body if body != b"" or clen else b""))
            # ответ: заголовки читаем; content-length точный -> цикл; иначе (chunked/SSE)
            # — доигрываем соединение raw-пайпом и закрываем
            rbuf = b""
            while b"\r\n\r\n" not in rbuf:
                c = up.recv(4096)
                if not c:
                    tls.sendall(rbuf); return
                rbuf += c
            rh, rrest = rbuf.split(b"\r\n\r\n", 1)
            rl = rh.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            rhl = {}
            for ln in rh.split(b"\r\n")[1:]:
                k, _, v = ln.partition(b":")
                rhl[k.decode("latin-1").lower().strip()] = v.decode("latin-1").strip()
            tls.sendall(rh + b"\r\n\r\n" + rrest)
            if " 400 " in rl or " 422 " in rl or " 500 " in rl:
                peek = rrest
                if "content-length" in rhl and len(peek) < int(rhl["content-length"]):
                    try:
                        while len(peek) < int(rhl["content-length"]):
                            c = up.recv(min(1024, int(rhl["content-length"]) - len(peek)))
                            if not c:
                                break
                            peek += c
                            tls.sendall(c)
                    except OSError:
                        pass
                log(f"{rl} resp body[:{min(len(peek),1200)}]: {peek.decode('utf-8','replace')[:900]!r}")
                try:
                    o = json.loads(out_body.decode("utf-8"))
                    summ = {"keys": sorted(o.keys()),
                            "roles": [m.get("role") for m in o.get("messages", [])][:6],
                            "n_msgs": len(o.get("messages", []))}
                except Exception:
                    summ = {"raw_head": out_body[:200].decode("utf-8", "replace")}
                log(f"  req summary: {summ}")
            te = rhl.get("transfer-encoding", "").lower()
            if "chunked" in te or "text/event-stream" in rhl.get("content-type", ""):
                pump_raw(tls, up)
                return
            if "content-length" in rhl:
                need = int(rhl["content-length"]) - len(rrest)
                while need > 0:
                    c = up.recv(min(need, 65536))
                    if not c:
                        break
                    tls.sendall(c); need -= len(c)
                if hdrs.get("connection", "").lower() == "close":
                    return
                continue
            return  #unknown framing — закрываем, клиент переключится
    except (OSError, ssl.SSLError, ValueError) as e:
        log("conn err:", type(e).__name__, str(e)[:120])
    finally:
        for s in (up, tls):
            try:
                if s: s.close()
            except OSError:
                pass


def pump_raw(a, b):
    def one(x, y):
        try:
            x.settimeout(IDLE_TIMEOUT)
            while True:
                d = x.recv(65536)
                if not d:
                    break
                y.sendall(d)
        except OSError:
            pass
        finally:
            try:
                y.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    t = threading.Thread(target=one, args=(b, a), daemon=True)
    t.start()
    one(a, b)
    t.join(timeout=2)


def main():
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(CERT, KEY)
    try:
        sctx.set_alpn_protocols(["http/1.1"])
    except OSError:
        pass
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(LISTEN)
    ls.listen(64)
    log(f"reorder-pump on {LISTEN} -> {UPSTREAM} (cert={CERT})")
    while True:
        c, _ = ls.accept()
        try:
            tls = sctx.wrap_socket(c, server_side=True)
        except (ssl.SSLError, OSError) as e:
            log("hs err:", e); c.close(); continue
        threading.Thread(target=handle, args=(tls,), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())

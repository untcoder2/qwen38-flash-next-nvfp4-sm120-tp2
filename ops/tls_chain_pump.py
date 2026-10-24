#!/usr/bin/env python3
"""tls_chain_pump.py — TLS-фронт для penny без рестарта движка.

Слушает 0.0.0.0:8443 с цепочкой leaf+CA (сервер подаёт full chain — то, чего
Schannel/строгим rustls не хватает от leaf-only инфера), пересобирает TLS к
127.0.0.1:8001 внутри контейнера не касается — ход на 1234 = проброс хост:контейнер)
и работает raw-дуплексом (SSE/стриминг прозрачны). Только stdlib, single-file,
threading per-connection. Это мост до ближайшего планового рестарта penny,
после которого sglang сам начнёт отдавать обновлённый server.pem с цепочкой.

Запуск на хосте: nohup python3 -I ~/agent-tools/tls_chain_pump.py &
Остановка: kill по pid-файку.
"""
import os, socket, ssl, sys, threading, time

LISTEN = ("0.0.0.0", int(os.environ.get("PUMP_PORT", "8443")))
UPSTREAM = (os.environ.get("PUMP_UP_HOST", "127.0.0.1"),
            int(os.environ.get("PUMP_UP_PORT", "1234")))  # -p 1234:8001
CERT = os.environ.get("PUMP_CERT", os.path.expanduser("~/penny/tls/server.pem"))  # leaf+CA (24.10)
KEY = os.environ.get("PUMP_KEY", os.path.expanduser("~/penny/tls/server.key"))
PIDFILE = os.environ.get("PUMP_PID", os.path.expanduser("~/agent-tools/tls_chain_pump.pid"))
IDLE_TIMEOUT = 600.0


def server_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.set_alpn_protocols(["http/1.1"])
    try:
        ctx.options |= ssl.OP_NO_COMPRESSION  # и так TLS1.3, лишнее
    except AttributeError:
        pass
    return ctx


def upstream_sock():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # внутренний hop: петербэк-инфер с самоподписом
    ctx.set_alpn_protocols(["http/1.1"])
    s = socket.create_connection(UPSTREAM, timeout=15)
    return ctx.wrap_socket(s, server_hostname="qwen-infer")


def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
            a.settimeout(IDLE_TIMEOUT); b.settimeout(IDLE_TIMEOUT)
    except (OSError, ssl.SSLError):
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            s.close()


def handle(client, ctx):
    try:
        tls = ctx.wrap_socket(client, server_side=True)
    except (ssl.SSLError, OSError):
        client.close()
        return
    try:
        up = upstream_sock()
    except OSError:
        tls.close()
        return
    t1 = threading.Thread(target=pump, args=(tls, up), daemon=True)
    t2 = threading.Thread(target=pump, args=(up, tls), daemon=True)
    t1.start(); t2.start(); t1.join(); t2.join()


def main():
    try:
        if os.path.exists(PIDFILE):
            old = int(open(PIDFILE).read())
            os.kill(old, 0)
            print("already running pid", old, file=sys.stderr); return 1
    except (OSError, ValueError):
        pass
    try:
        open(PIDFILE, "w").write(str(os.getpid()))
    except OSError:
        pass  # ro-mount в контейнере — без pid-файла тоже живём
    ctx = server_ctx()
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(LISTEN); ls.listen(64)
    print(time.strftime("%F %T"), "pump on", LISTEN, "->", UPSTREAM, flush=True)
    while True:
        c, _ = ls.accept()
        threading.Thread(target=handle, args=(c, ctx), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())

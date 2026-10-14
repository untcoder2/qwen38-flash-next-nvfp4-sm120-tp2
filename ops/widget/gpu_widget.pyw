#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gpu_widget.pyw — мини-виджет поверх всех окон: скорость SGLang-стенда.

Данные каждые 5 с по HTTPS (/metrics, пин QwenHome-CA), плюс контекст и число
активных сессий Kimi Code из локальных wire-логов (best-effort, прочерк без логов).

Управление: ЛКМ-перетаскивание — двигать; ПКМ — меню (прозрачность, свернуть
на 60 с, выход); двойной клик — выход. Позиция и прозрачность сохраняются в
%LOCALAPPDATA%/gpu_widget.json.

Режимы: без аргументов — GUI; --selftest — один опрос в консоль без окна.
"""
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

HOST = os.environ.get("GPU_WIDGET_HOST", "192.168.2.127:1234")
POLL_S = 5.0
# Инфер с 14.10 отвечает только по https, лист самоподписанный. Пиним корень из
# репозитория (рядом с виджетом: ../../certs), env GPU_WIDGET_CA перебивает путь,
# GPU_WIDGET_SCHEME=http возвращает старое поведение для локального стенда.
_HERE = os.path.dirname(os.path.abspath(__file__))
CA_FILE = os.environ.get("GPU_WIDGET_CA") or os.path.normpath(
    os.path.join(_HERE, "..", "..", "certs", "qwen-home-ca.pem"))
_SCHEME = os.environ.get("GPU_WIDGET_SCHEME", "https")
_CTX_WINDOW = int(os.environ.get("GPU_WIDGET_CTX", "786432"))
RE_LINES = {
    "gen": r"^sglang:generation_tokens_total(?:\{[^}]*\})?\s+([\d.eE+\-]+)",
    "prompt": r"^sglang:prompt_tokens_total(?:\{[^}]*\})?\s+([\d.eE+\-]+)",
    "cached": r"^sglang:cached_tokens_total(?:\{[^}]*\})?\s+([\d.eE+\-]+)",
    "running": r"^sglang:num_running_reqs(?:\{[^}]*\})?\s+([\d.eE+\-]+)",
    "thpt": r"^sglang:gen_throughput(?:\{[^}]*\})?\s+([\d.eE+\-]+)",
}
CFG_PATH = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "gpu_widget.json")
WIRE_ROOT = os.path.expanduser(r"~/.kimi-code/sessions")


def _ssl_ctx():
    """None => схема http. Без найденного корня — проверка по системному доверию."""
    if not _SCHEME.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    if os.path.exists(CA_FILE):
        ctx.load_verify_locations(cafile=CA_FILE)
    return ctx


_CTX = _ssl_ctx()


def _http(url, timeout=3.0):
    req = urllib.request.Request(url, headers={"User-Agent": "gpu-widget"})
    kw = {"timeout": timeout}
    if _CTX is not None:
        kw["context"] = _CTX
    return urllib.request.urlopen(req, **kw).read().decode(errors="replace")


def _metrics():
    txt = _http("%s://%s/metrics" % (_SCHEME, HOST))
    out = {}
    for key, pat in RE_LINES.items():
        vals = [float(m) for m in re.findall(pat, txt, re.M)]
        if not vals:
            out[key] = None
        elif key == "running":
            out[key] = max(vals)          # gauge: максимум по лейблам
        else:
            out[key] = sum(vals)          # counter: сумма series (streaming+non-streaming)
    return out


def _session_ctx(max_age_s=1200):
    """tokens самой свежей активной сессии Kimi Code; None если нет свежих."""
    try:
        import glob
        now = time.time()
        best_w, best_t = None, 0
        for w in glob.glob(os.path.join(WIRE_ROOT, "wd_*", "session_*", "agents", "main", "wire.jsonl")):
            t = os.path.getmtime(w)
            if now - t < max_age_s and t > best_t:
                best_w, best_t = w, t
        if not best_w:
            return None
        tokens = None
        with open(best_w, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"token_counting.measured"' in line:
                    try:
                        tokens = json.loads(line).get("tokens")
                    except Exception:
                        pass
        if tokens:
            return int(tokens)
        return None
    except Exception:
        return None


def _fresh_files(pattern, max_age_s):
    """пути под pattern, mtime свежее max_age_s, самые новые первыми."""
    import glob
    now = time.time()
    found = []
    for f in glob.glob(pattern):
        try:
            t = os.path.getmtime(f)
        except OSError:
            continue
        if now - t <= max_age_s:
            found.append((t, f))
    found.sort(reverse=True)
    return [f for _, f in found]


def _kimi_active(max_age_s=1200):
    """сколько активных сессий Kimi Code: agents/*/wire.jsonl (это и main, и subagent'ы)."""
    return len(_fresh_files(os.path.join(WIRE_ROOT, "wd_*", "session_*", "agents", "*", "wire.jsonl"),
                            max_age_s))


GPU_SSH = {"host": "192.168.2.127", "user": "molochko",
           "key": os.path.expanduser(r"~\.ssh\molochko_deploy")}
GPU_CMD = ("nvidia-smi --query-gpu=index,utilization.gpu,memory.used,power.draw,temperature.gpu"
           " --format=csv,noheader,nounits")


class GpuPoller(threading.Thread):
    """nvidia-smi по SSH раз в ~8 с; None при недоступности — GUI это переживёт."""

    def __init__(self):
        super().__init__(daemon=True)
        self.last = None  # [(idx, util, mem_mib, power_w, temp_c), ...]
        self.err = None
        self._cli = None

    def _conn(self):
        import paramiko
        if self._cli is not None and self._cli.get_transport() is not None \
                and self._cli.get_transport().is_active():
            return self._cli
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(GPU_SSH["host"], username=GPU_SSH["user"],
                    key_filename=GPU_SSH["key"], timeout=6, banner_timeout=8, auth_timeout=8)
        self._cli = cli
        return cli

    def run(self):
        while True:
            try:
                cli = self._conn()
                _, o, _ = cli.exec_command(GPU_CMD, timeout=12)
                rows = []
                for line in o.read().decode(errors="replace").splitlines():
                    p = [x.strip() for x in line.split(",")]
                    if len(p) >= 5:
                        rows.append((int(p[0]), int(float(p[1])), float(p[2]), float(p[3]), int(float(p[4]))))
                self.last = rows or None
                self.err = None
            except Exception as e:
                self.err = type(e).__name__
                self._cli = None
            time.sleep(8)

    def sample_once_blocking(self):
        try:
            cli = self._conn()
            _, o, _ = cli.exec_command(GPU_CMD, timeout=12)
            out = o.read().decode(errors="replace")
            return {"ok": True, "raw": out.strip().splitlines()}
        except Exception as e:
            return {"ok": False, "err": repr(e)}


class Sampler(threading.Thread):
    """Фоновый опрос раз в 1.5 с. Сглаживание: rate/hit — по скользящему окну
    ~9 с (нет мигания между запросами), lanes — «липкие»: держат последнее
    ненулевое значение до 6 с, чтобы число не прыгало 4→0→4 на ровном месте."""

    WINDOW_S = 9.0
    LANES_HOLD_S = 6.0

    def __init__(self):
        super().__init__(daemon=True)
        self.last = None
        self.error = None
        self._win = __import__("collections").deque(maxlen=8)  # (t, gen, prompt, cached)
        self._lanes_val, self._lanes_t = 0, 0.0

    def run(self):
        from collections import deque  # noqa: F401 (deque уже в _win)
        while True:
            try:
                m = _metrics()
                now = time.time()
                self._win.append((now, m.get("gen") or 0.0, m.get("prompt") or 0.0, m.get("cached") or 0.0))
                rate = hit = None
                # окно: первые vs последние записи старше WINDOW_S отбрасываем
                pts = [p for p in self._win if now - p[0] <= self.WINDOW_S]
                if len(pts) >= 2:
                    t0, g0, p0, c0 = pts[0]
                    t1, g1, p1, c1 = pts[-1]
                    dt = max(t1 - t0, 1.0)
                    if g1 >= g0:
                        rate = (g1 - g0) / dt
                    dp = p1 - p0
                    if dp > 200:
                        hit = max(0.0, min(1.0, (c1 - c0) / dp))
                # lanes sticky
                raw = int(m.get("running") or 0)
                if raw > 0:
                    self._lanes_val, self._lanes_t = raw, now
                elif now - self._lanes_t > self.LANES_HOLD_S:
                    self._lanes_val = 0
                # живой токен-рейт: пока есть сырые активные запросы — движковский
                # gen_throughput (обновляется в реальном времени), иначе оконная дельта
                raw_now = int(m.get("running") or 0)
                eff_rate = (m.get("thpt") or 0.0) if raw_now > 0 else rate
                self.last = {"t": now, "rate": eff_rate, "lanes": self._lanes_val, "hit": hit}
                self.error = None
            except Exception as e:
                self.error = type(e).__name__
            time.sleep(1.5)

    # selftest helper
    def sample_once_blocking(self):
        try:
            m = _metrics()
            return {"ok": True, "raw": {k: v for k, v in m.items()}, "lanes": int(m.get("running") or 0)}
        except Exception as e:
            return {"ok": False, "err": repr(e)}


def _fmt_rate(r):
    if r is None:
        return "—"
    return ("{:,.0f}" if r >= 100 else "{:.0f}").format(r).replace(",", " ")


def _plur(n, one, few, many):
    n10, n100 = n % 10, n % 100
    if n100 >= 11 and n100 <= 14:
        return many
    if n10 == 1:
        return one
    if 2 <= n10 <= 4:
        return few
    return many


def gui():
    import tkinter as tk

    cfg = {}
    try:
        cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    except Exception:
        pass

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#15151d")
    try:
        root.attributes("-alpha", float(cfg.get("alpha", 0.9)))
    except Exception:
        pass
    w, h = 380, 132
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = cfg.get("x"), cfg.get("y")
    if x is None:
        x, y = sw - w - 24, 60
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))
    root.resizable(False, False)

    l1 = tk.Label(root, text="GPU: старт…", bg="#15151d", fg="#7ee787", font=("Consolas", 12, "bold"), anchor="w", padx=6, pady=1)
    l2 = tk.Label(root, text="", bg="#15151d", fg="#c9d1d9", font=("Consolas", 10), anchor="w", padx=6, pady=0)
    l3 = tk.Label(root, text="", bg="#15151d", fg="#8b949e", font=("Consolas", 9), anchor="w", padx=6, pady=0)
    l4 = tk.Label(root, text="карты: опрос…", bg="#15151d", fg="#8b949e", font=("Consolas", 10), anchor="w", padx=6, pady=0)
    l1.pack(fill="x"); l2.pack(fill="x"); l3.pack(fill="x"); l4.pack(fill="x")

    sampler = Sampler()
    sampler.start()
    gpoll = GpuPoller()
    gpoll.start()
    ctx_cache = {"t": 0, "v": None}

    def tick():
        s, err = sampler.last, sampler.error
        try:
            if err or not s:
                l1.config(text="GPU: host down", fg="#f85149")
                l2.config(text="(%s)" % (err or "нет данных"), fg="#8b949e")
            else:
                rate, lanes, hit = s["rate"], s["lanes"], s["hit"]
                active = rate is not None and rate > 5
                color = "#7ee787" if active else "#8b949e"
                per = (rate / lanes) if (active and lanes > 0) else None
                t1 = "%s tok/s · %d %s" % (_fmt_rate(rate) if rate is not None else "—",
                                            lanes, _plur(lanes, "агент", "агента", "агентов"))
                l1.config(text=t1, fg=color)
                if active:
                    l2.config(text=("~%d на агента" % per) if per else "~— на агента", fg="#79c0ff")
                else:
                    l2.config(text="— сервер в простое —", fg="#4d5566")
                bits = ["кэш %s" % ("%.0f%%" % (hit * 100) if hit is not None else "—")]
                now = time.time()
                if now - ctx_cache["t"] > 20:
                    ctx_cache["v"] = {"ctx": _session_ctx(), "n": _kimi_active()}
                    ctx_cache["t"] = now
                v = ctx_cache["v"] or {}
                nk, ctx = v.get("n", 0), v.get("ctx")
                # Kimi показан всегда, даже в нуле: бездействующая ветка — это
                # прочерк, а не исчезнувшая строка.
                bits.append("kimi %dаг ctx %s" % (
                    nk, "~%.0f%%" % (ctx / _CTX_WINDOW * 100) if ctx else "—"))
                l3.config(text=" · ".join(bits), fg="#8b949e")
            rows = gpoll.last
            if rows:
                def seg(r):
                    u, mem, pw, tc = r[1], r[2], r[3], r[4]
                    return "карта%d %2d%%  %4.1f/96ГБ  %3.0fВт %2d°" % (r[0], u, mem / 1024.0, pw, tc)
                l4.config(text="\n".join(seg(r) for r in rows[:2]), justify="left",
                          fg="#f0883e" if any(r[1] >= 85 for r in rows) else
                             ("#7ee787" if any(r[1] > 2 for r in rows) else "#8b949e"))
            elif gpoll.err:
                l4.config(text="карты: n/a (%s)" % gpoll.err, fg="#4d5566")
        finally:
            root.update_idletasks()
            labels = [l1, l2, l4] + ([l3] if l3.winfo_ismapped() else [])
            need_w = max((l.winfo_reqwidth() for l in labels), default=200) + 4
            need_h = sum(l.winfo_reqheight() for l in (l1, l2, l3, l4) if l.winfo_ismapped()) + 6
            cw, ch = root.winfo_width(), root.winfo_height()
            if need_w != cw or abs(need_h - ch) > 3:
                root.geometry("%dx%d" % (need_w, need_h))
            root.after(1000, tick)

    def save(**kw):
        cfg.update(kw)
        try:
            json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"))
        except Exception:
            pass

    def on_drag(ev):
        root.geometry("+%d+%d" % (ev.x_root - root._dragx, ev.y_root - root._dragy))

    def on_press(ev):
        root._dragx, root._dragy = ev.x, ev.y

    def on_release(_ev):
        save(x=root.winfo_x(), y=root.winfo_y())

    def on_double(_ev):
        root.destroy()

    def set_alpha(a):
        try:
            root.attributes("-alpha", a)
            save(alpha=a)
        except Exception:
            pass

    def hide_60():
        root.withdraw()
        root.after(60_000, root.deiconify)

    menu = tk.Menu(root, tearoff=0, bg="#1e1e28", fg="#c9d1d9", activebackground="#2f3640")
    menu.add_command(label="Свернуть на 60 с", command=hide_60)
    menu.add_command(label="Прозрачность: обычная", command=lambda: set_alpha(0.9))
    menu.add_command(label="Прозрачность: призрачная", command=lambda: set_alpha(0.55))
    menu.add_separator()
    menu.add_command(label="Выход", command=root.destroy)

    def on_right(ev):
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()

    for lbl in (l1, l2, l3, l4):
        lbl.bind("<ButtonPress-1>", on_press)
        lbl.bind("<B1-Motion>", on_drag)
        lbl.bind("<ButtonRelease-1>", on_release)
        lbl.bind("<Double-Button-1>", on_double)
        lbl.bind("<Button-3>", on_right)

    root.bind("<Escape>", lambda e: root.destroy())
    tick()
    root.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        r = Sampler().sample_once_blocking()
        g = GpuPoller().sample_once_blocking()
        print(json.dumps(r, ensure_ascii=False, default=str))
        print(json.dumps(g, ensure_ascii=False, default=str))
        print("agents: kimi=%d | ctx: kimi=%s | ca=%s(%s) scheme=%s" % (
            _kimi_active(), _session_ctx(), CA_FILE,
            "есть" if os.path.exists(CA_FILE) else "НЕТ", _SCHEME))
        sys.exit(0 if (r.get("ok") and g.get("ok")) else 1)
    gui()

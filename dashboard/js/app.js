/* Kimi Token Ops — ui_core (js/app.js)
   Каркас: hero с count-up, live-свежесть данных, статус сервера, селектор
   периода (7/14/30/90/365/всё, выбор в localStorage), graceful-деградация без
   data.js / ECharts, делегирование графиков в window.DASH_CHARTS. */
(function () {
  "use strict";

  var COUNTUP_MS = 1200;
  var REL_TICK_MS = 20000;
  var PERIOD_STORE_KEY = "kimi-dash-period";
  var PERIOD_DEFAULT = 30;

  var nfInt = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
  var nfPct = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var nfDate = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

  function $(id) { return document.getElementById(id); }

  function num(v) {
    var n = Number(v);
    return isFinite(n) ? n : null;
  }
  function fmtInt(v) {
    var n = num(v);
    return n === null ? "—" : nfInt.format(n);
  }
  function fmtPct(v) {
    var n = num(v);
    return n === null ? "—" : nfPct.format(n) + "%";
  }

  function relTime(iso) {
    var t = Date.parse(iso);
    if (!isFinite(t)) return null;
    var diff = Date.now() - t;
    if (diff < 60000) return "только что";
    var mins = Math.floor(diff / 60000);
    if (mins < 60) return mins + " мин назад";
    var hours = Math.floor(mins / 60);
    if (hours < 24) return hours + " ч назад";
    return Math.floor(hours / 24) + " дн назад";
  }

  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function countUp(el, target) {
    if (!el) return;
    var n = num(target);
    if (n === null) { el.textContent = "—"; return; }
    if (prefersReducedMotion()) { el.textContent = nfInt.format(n); return; }
    var t0 = null;
    function frame(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min((ts - t0) / COUNTUP_MS, 1);
      var eased = 1 - Math.pow(1 - p, 3); /* easeOutCubic */
      el.textContent = nfInt.format(Math.round(n * eased));
      if (p < 1) window.requestAnimationFrame(frame);
    }
    window.requestAnimationFrame(frame);
  }

  function setError(text) {
    var el = $("error-banner");
    if (!el) return;
    el.textContent = "Ошибка: " + text;
    el.hidden = false;
  }

  function fillHero(dash) {
    var wire = dash.wire || {};
    var totals = wire.totals || {};
    var prompt = num(totals.prompt);
    var gen = num(totals.gen);
    var total = (prompt === null && gen === null) ? null : (prompt || 0) + (gen || 0);

    countUp($("stat-total"), total);
    countUp($("stat-prompt"), prompt);
    countUp($("stat-gen"), gen);
    countUp($("stat-steps"), totals.steps);

    var chip = $("cache-chip");
    var sInfo = (dash.server && dash.server.online && dash.server.info) || {};
    var pct = num(sInfo.cache_hit_pct);
    if (chip) {
      chip.textContent = "cache hit " + (pct === null ? "—" : "сервер: " + fmtPct(pct));
      if (pct !== null) {
        chip.classList.add(pct >= 80 ? "chip-good" : pct >= 50 ? "chip-mid" : "chip-low");
      }
    }

    var period = $("hero-period");
    var days = num(dash.window_days);
    if (period) {
      period.textContent = (days !== null && days > 0)
        ? "окно: " + nfInt.format(days) + " дн"
        : "окно: всё время";
    }

    var srv = dash.server || {};
    var online = srv.online === true;
    var dot = $("server-dot");
    var label = $("server-label");
    if (dot && label) {
      dot.classList.add(online ? "is-online" : "is-offline");
      label.textContent = online ? "сервер инфера · online" : "сервер инфера · offline";
      var c = srv.counters || {};
      label.title = online
        ? "prompt_total: " + fmtInt(c.prompt_total) + "\ngeneration_total: " + fmtInt(c.generation_total)
        : "Метрики SGLang (192.168.2.127:1234) не отвечают";
    }
  }

  function startLiveFreshness(dash) {
    var el = $("generated-at");
    if (!el) return;
    var iso = dash.generated_at;
    var t = Date.parse(iso);
    function paint() {
      if (!isFinite(t)) { el.textContent = "данные: дата неизвестна"; return; }
      var rel = relTime(iso);
      el.textContent = "сгенерировано " + nfDate.format(new Date(t)) + (rel ? " · " + rel : "");
    }
    paint();
    window.setInterval(paint, REL_TICK_MS);
  }

  function annotateEmptyCharts(text) {
    var boxes = document.querySelectorAll(".chart");
    Array.prototype.forEach.call(boxes, function (box) {
      if (!box.firstChild) {
        var note = document.createElement("div");
        note.className = "chart-note";
        note.textContent = text;
        box.appendChild(note);
      }
    });
  }

  function initCharts(dash) {
    if (window.__DASH_ECHARTS_FAILED || typeof window.echarts === "undefined") {
      annotateEmptyCharts("ECharts недоступна — графики пропущены");
      return;
    }
    var api = window.DASH_CHARTS;
    if (!api || typeof api.init !== "function") {
      annotateEmptyCharts("графики будут после подключения js/charts.js");
      return;
    }
    try {
      api.init(document, dash);
    } catch (e) {
      setError("графики не отрисованы: " + (e && e.message ? e.message : String(e)));
      annotateEmptyCharts("графика остановилась на ошибке");
    }
  }

  /* ================= селектор периода ======================================
     Держим исходный window.DASH без усечений; выбранное окно (в днях, 0 = всё
     время) сохраняется в localStorage и применяется пересчётом дневных рядов
     (DASH_CHARTS.slice) — hero-цифры и все графики обновляются на клиенте. */
  var PERIODS = [7, 14, 30, 90, 365, 0];
  var state = { dash: null, period: PERIOD_DEFAULT };

  function loadPeriod() {
    try {
      var v = parseInt(window.localStorage.getItem(PERIOD_STORE_KEY), 10);
      if (PERIODS.indexOf(v) >= 0) return v;
    } catch (e) { /* приватный режим — дефолт */ }
    return PERIOD_DEFAULT;
  }

  function savePeriod(p) {
    try { window.localStorage.setItem(PERIOD_STORE_KEY, String(p)); } catch (e) { }
  }

  function currentView() {
    var api = window.DASH_CHARTS;
    if (api && typeof api.slice === "function") {
      try {
        return api.slice(state.dash, state.period);
      } catch (e) {
        setError("пересчёт периода: " + (e && e.message ? e.message : String(e)));
      }
    }
    return state.dash; // нет charts.js — период не пересчитывается, данные как есть
  }

  function renderHero() {
    fillHero(currentView());
  }

  function renderCharts() {
    initCharts(currentView());
  }

  function bindPeriodControl() {
    var sel = $("period-select");
    if (!sel) return;
    sel.value = String(state.period);
    sel.addEventListener("change", function () {
      var p = parseInt(sel.value, 10);
      state.period = PERIODS.indexOf(p) >= 0 ? p : PERIOD_DEFAULT;
      sel.value = String(state.period);
      savePeriod(state.period);
      renderHero();
      renderCharts();
    });
  }

  function observeReveal() {
    var els = Array.prototype.slice.call(document.querySelectorAll(".reveal, #hero"));
    function show(el) { el.classList.add("in-view"); }
    if (!("IntersectionObserver" in window)) {
      els.forEach(show);
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        // top < 0 = элемент уже выше вьюпорта (например, после reload с восстановленным скроллом)
        if (en.isIntersecting || en.boundingClientRect.top < 0) {
          show(en.target);
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -4% 0px" });
    els.forEach(function (el) { io.observe(el); });
    // ручная проверка для элементов, видимых или пройденных на момент старта
    var vh = window.innerHeight || document.documentElement.clientHeight;
    els.forEach(function (el) {
      if (el.getBoundingClientRect().top < vh) {
        show(el);
        io.unobserve(el);
      }
    });
  }

  function boot() {
    var refreshBtn = $("refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        // файл:// или умерший API — просто перезагрузка; на сервере — genuine refresh
        if (!/^https?:$/.test(location.protocol)) { window.location.reload(); return; }
        refreshBtn.disabled = true;
        refreshBtn.textContent = "обновляю…";
        fetch("/api/refresh", { method: "POST" })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .catch(function () { return {}; })
          .then(function () { window.location.reload(); });
        setTimeout(function () { window.location.reload(); }, 25000); // страховка
      });
    }
    var phReload = $("placeholder-reload");
    if (phReload) {
      phReload.addEventListener("click", function () { window.location.reload(); });
    }

    var dash = window.DASH;
    var hasData = !!(dash && typeof dash === "object" && (dash.wire || dash.server || dash.costs));

    if (!hasData) {
      document.body.classList.add("no-data");
      var ph = $("placeholder");
      if (ph) ph.hidden = false;
      observeReveal();
      if (!window.echarts) {
        var cb = $("cdn-banner");
        if (cb) cb.hidden = false;
      }
      return;
    }

    state.dash = dash;
    state.period = loadPeriod();
    bindPeriodControl();

    startLiveFreshness(dash);
    renderHero();
    observeReveal();

    // charts.js объявлен после app.js — ждём полной загрузки скриптов
    if (document.readyState === "complete") {
      renderCharts();
    } else {
      window.addEventListener("load", renderCharts);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

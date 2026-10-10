/* ui_charts — ECharts-визуализации для window.DASH.
 * Контракт: D:\llm\dashboard\CONTRACT.md («КОНТРАКТ UI»).
 * Публичный API: window.DASH_CHARTS.init(root, dash)
 * Контейнеры (готовит ui_core): chart-projects, chart-days, chart-cache, chart-price, chart-server.
 * Палитра и стили текста графиков — константы этого файла (ui_core цвета графиков не задаёт). */
(function () {
  'use strict';

  /* ================= палитра / типографика (владеет ui_charts) ============= */
  var C = {
    amber:  '#ffb454',
    cyan:   '#46e0ff',
    purple: '#b57bff',
    mint:   '#5ee6a8',
    rose:   '#ff7a9c',
    blue:   '#6f8bff',
    text:   '#c9d5ea',
    dim:    '#8391ad',
    bright: '#eaf2ff',
    faint:  'rgba(140,160,200,0.16)',
    rest:   '#5b6a86',
    tooltipBg: 'rgba(9,13,23,0.94)'
  };
  var DONUT_COLORS = [C.amber, C.cyan, C.purple, C.mint, C.rose, C.blue, C.rest];
  var FONT_MONO = '"JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, "Courier New", monospace';
  var FONT_SANS = '"Inter", system-ui, "Segoe UI", Roboto, sans-serif';

  /* ================= форматирование (ru: разделители тысяч, % 1 знак) ===== */
  var nfInt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
  var nfPct1 = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var nfUsdS = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var nfUsdB = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

  function num(v, dflt) {
    var n = Number(v);
    return isFinite(n) ? n : dflt;
  }
  function int(v) { return nfInt.format(num(v, 0)); }
  function pct1(v) { return nfPct1.format(num(v, 0)) + '\u00a0%'; }
  function compactTok(v) {
    var n = num(v, 0), a = Math.abs(n);
    if (a >= 1e9) return nfPct1.format(n / 1e9) + '\u00a0млрд';
    if (a >= 1e6) return nfPct1.format(n / 1e6) + '\u00a0млн';
    if (a >= 1e4) return nfInt.format(Math.round(n / 1e3)) + '\u00a0тыс.';
    return nfInt.format(n);
  }
  function moneyUsd(v) {
    var n = num(v, 0);
    if (n > 0 && n < 0.01) return '<$0.01';
    return '$' + (n >= 100 ? nfUsdB.format(n) : nfUsdS.format(n));
  }
  function moneyRub(v) {
    return nfInt.format(Math.round(num(v, 0))) + '\u00a0₽';
  }
  function hhmm(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    }
    var m = String(iso).match(/T(\d{2}):(\d{2})/);
    return m ? m[1] + ':' + m[2] : null;
  }
  function clip(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function wsName(ws) {
    return String(ws || '').trim()
      .replace(/^wd_/, '')
      .replace(/_[0-9a-fA-F]{8,}$/, '') || String(ws || '');
  }

  /* ================= реестр инстансов / жизненный цикл ===================== */
  var charts = {};        // id -> echarts instance
  var resizeTimer = null;
  var resizeBound = false;
  var ro = null;          // ResizeObserver (если доступен)

  function hasEcharts() {
    return !!(window.echarts && typeof window.echarts.init === 'function');
  }
  function grad(x, y, x2, y2, stops) {
    return new window.echarts.graphic.LinearGradient(x, y, x2, y2,
      stops.map(function (s) { return { offset: s[0], color: s[1] }; }));
  }
  function containerEl(root, id) {
    if (root && typeof root.getElementById === 'function') return root.getElementById(id);
    if (root && typeof root.querySelector === 'function') return root.querySelector('#' + id);
    return document.getElementById(id);
  }
  function disposeChart(id) {
    var inst = charts[id];
    if (!inst) return;
    try { inst.dispose(); } catch (e) { /* уже мёртв */ }
    delete charts[id];
  }
  function disposeAll() {
    Object.keys(charts).forEach(disposeChart);
    if (ro) { try { ro.disconnect(); } catch (e) {} ro = null; }
  }
  function mount(el, id) {
    disposeChart(id);
    el.innerHTML = '';
    var inst = window.echarts.init(el, null, { renderer: 'canvas' });
    charts[id] = inst;
    if (window.ResizeObserver) {
      if (!ro) ro = new ResizeObserver(function () { scheduleResize(); });
      ro.observe(el);
    }
    return inst;
  }
  function scheduleResize() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      resizeTimer = null;
      Object.keys(charts).forEach(function (k) {
        try { charts[k].resize(); } catch (e) {}
      });
    }, 120);
  }
  function bindResize() {
    if (!resizeBound) {
      window.addEventListener('resize', scheduleResize, { passive: true });
      // DPR/panel-ловушки: смена видимости таба, возврат фокуса, pageshow, смена scale
      document.addEventListener('visibilitychange', function () {
        if (!document.hidden) scheduleResize();
      });
      window.addEventListener('focus', scheduleResize);
      window.addEventListener('pageshow', scheduleResize);
      var watchDpr = function () {
        var mq = window.matchMedia &&
          window.matchMedia('(resolution: ' + (window.devicePixelRatio || 1) + 'dppx)');
        if (!mq) return;
        var on = function () { scheduleResize(); watchDpr(); }; // переподписаться на новый DPR
        mq.addEventListener ? mq.addEventListener('change', on, { once: true })
                            : mq.addListener(on);
      };
      watchDpr();
      resizeBound = true;
    }
  }

  /* ================= placeholder «нет данных» ============================== */
  function empty(el, id, msg) {
    disposeChart(id);
    el.innerHTML = '';
    var box = document.createElement('div');
    box.className = 'dash-chart-empty';
    var ic = document.createElement('span');
    ic.className = 'dash-chart-empty__icon';
    ic.textContent = '◌';
    var tx = document.createElement('span');
    tx.className = 'dash-chart-empty__text';
    tx.textContent = msg || 'нет данных';
    box.appendChild(ic); box.appendChild(tx);
    el.appendChild(box);
  }
  function echartsMissing(el) {
    empty(el, el.id, 'ECharts не загрузился — проверьте сеть/CDN');
  }

  function baseOption(tooltipExtra) {
    var tip = {
      show: true,
      backgroundColor: C.tooltipBg,
      borderColor: 'rgba(70,224,255,0.28)',
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: '#e8f0ff', fontFamily: FONT_SANS, fontSize: 12 },
      extraCssText: 'border-radius:10px;box-shadow:0 10px 34px rgba(0,0,0,.55);backdrop-filter:blur(6px);'
    };
    if (tooltipExtra) for (var k in tooltipExtra) tip[k] = tooltipExtra[k];
    return {
      backgroundColor: 'transparent',
      textStyle: { fontFamily: FONT_SANS, color: C.text },
      animationDuration: 850,
      animationEasing: 'cubicOut',
      animationDurationUpdate: 500,
      tooltip: tip
    };
  }

  /* ================= 1. chart-projects — donut по проектам ================= */
  function buildProjects(el, dash) {
    var id = 'chart-projects';
    var raw = (dash.wire && Array.isArray(dash.wire.projects)) ? dash.wire.projects : [];
    var rows = raw
      .map(function (p) { return { name: wsName(p && p.ws), value: num(p && p.prompt, 0) + num(p && p.gen, 0) }; })
      .filter(function (r) { return r.name && r.value > 0; })
      .sort(function (a, b) { return b.value - a.value; });
    if (!rows.length) return empty(el, id, 'нет данных по проектам');

    var TOP = 6;
    var data = rows.slice(0, TOP);
    if (rows.length > TOP) {
      var rest = rows.slice(TOP).reduce(function (s, r) { return s + r.value; }, 0);
      if (rest > 0) data.push({
        name: 'остальные · ' + (rows.length - TOP),
        value: rest,
        itemStyle: { color: C.rest }
      });
    }
    var total = data.reduce(function (s, r) { return s + r.value; }, 0) || 1;

    var inst = mount(el, id);
    var narrow = el.clientWidth < 520; // на мобильном внешние подписи тонут в легенде — уводим их в легенду+тултип
    var opt = baseOption({
      trigger: 'item',
      formatter: function (p) {
        return p.marker + ' <b>' + p.name + '</b><br>' +
          int(p.value) + ' токенов · ' + pct1(p.value / total * 100) +
          '<br><span style="color:' + C.dim + '">' + int(p.data.steps || 0) + ' шагов</span>';
      }
    });
    opt.title = {
      text: compactTok(total),
      subtext: 'всего токенов',
      left: narrow ? '50%' : '33%', top: narrow ? '34%' : 'middle',
      textAlign: 'center', verticalAlign: 'middle',
      textStyle: { fontFamily: FONT_MONO, fontSize: narrow ? 17 : 21, fontWeight: 700, color: C.bright },
      subtextStyle: { fontFamily: FONT_SANS, fontSize: 10, color: C.dim }
    };
    opt.legend = narrow
      ? { type: 'scroll', orient: 'horizontal', bottom: 2, left: 'center', itemWidth: 9, itemHeight: 9, itemGap: 8, icon: 'roundRect', textStyle: { color: C.dim, fontSize: 10, fontFamily: FONT_SANS } }
      : {
        type: 'scroll', orient: 'vertical', right: 8, top: 'middle',
        itemWidth: 10, itemHeight: 10, itemGap: 9, icon: 'roundRect',
        textStyle: { color: C.dim, fontSize: 11, fontFamily: FONT_SANS, width: 116, overflow: 'truncate' },
        formatter: function (name) { return clip(name, 17); }
      };
    opt.series = [{
      name: 'проекты',
      type: 'pie',
      radius: narrow ? ['44%', '64%'] : ['54%', '78%'],
      center: narrow ? ['50%', '42%'] : ['33%', '50%'],
      padAngle: 2,
      minShowLabelAngle: narrow ? 999 : 11,
      itemStyle: { borderRadius: 6, borderColor: 'rgba(5,7,13,0.9)', borderWidth: 2 },
      color: DONUT_COLORS,
      label: {
        show: !narrow, position: 'outside', alignTo: 'labelLine',
        formatter: function (p) {
          return '{n|' + clip(p.name, 14) + '}\n{d|' + pct1(p.value / total * 100) + ' · ' + compactTok(p.value) + '}';
        },
        rich: {
          n: { color: C.text, fontSize: 11, fontWeight: 600, lineHeight: 14, align: 'center' },
          d: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO, lineHeight: 13, align: 'center' }
        }
      },
      labelLine: { length: 10, length2: 12, smooth: true, lineStyle: { color: 'rgba(140,160,200,0.45)' } },
      emphasis: {
        scale: true, scaleSize: 7,
        itemStyle: { shadowBlur: 26, shadowColor: 'rgba(70,224,255,0.35)' }
      },
      data: data.map(function (r) {
        var steps = 0;
        var orig = raw.filter(function (p) { return wsName(p && p.ws) === r.name; })[0];
        if (orig) steps = num(orig.steps, 0);
        return { name: r.name, value: r.value, steps: steps, itemStyle: r.itemStyle };
      })
    }];
    inst.setOption(opt);
  }

  /* ================= 2. chart-days — stacked bar prompt/gen ================ */
  function buildDays(el, dash) {
    var id = 'chart-days';
    var days = (dash.wire && Array.isArray(dash.wire.days))
      ? dash.wire.days.filter(function (d) { return d && d.date; }) : [];
    if (!days.length) return empty(el, id, 'нет данных по дням');

    var cats = days.map(function (d) {
      var ds = String(d.date);
      return ds.length >= 10 ? ds.slice(8, 10) + '.' + ds.slice(5, 7) : ds;
    });
    var prompt = days.map(function (d) { return num(d.prompt, 0); });
    var gen = days.map(function (d) { return num(d.gen, 0); });

    var inst = mount(el, id);
    var opt = baseOption({
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(70,224,255,0.06)' } },
      formatter: function (params) {
        var sum = 0, html = '<b>' + params[0].axisValue + '</b>';
        params.forEach(function (p) {
          sum += p.value;
          html += '<br>' + p.marker + ' ' + p.seriesName + ': <span style="font-family:' + FONT_MONO + '">' + int(p.value) + '</span>';
        });
        return html + '<br><span style="color:' + C.dim + '">итог: </span><span style="font-family:' + FONT_MONO + '">' + int(sum) + '</span>';
      }
    });
    opt.grid = { left: 6, right: 8, top: 30, bottom: 2, containLabel: true };
    opt.legend = {
      // мини-легенда
      top: 2, right: 4, itemWidth: 9, itemHeight: 9, itemGap: 10, icon: 'roundRect',
      textStyle: { color: C.dim, fontSize: 10, fontFamily: FONT_SANS },
      data: ['prompt', 'генерация']
    };
    opt.xAxis = {
      type: 'category', data: cats,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: C.faint } },
      axisLabel: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO, hideOverlap: true }
    };
    opt.yAxis = {
      type: 'value',
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO, formatter: function (v) { return compactTok(v); } },
      splitLine: { lineStyle: { color: C.faint, type: 'dashed' } }
    };
    opt.series = [
      {
        name: 'prompt', type: 'bar', stack: 'tok', barMaxWidth: 26,
        itemStyle: {
          opacity: 0.85,
          color: grad(0, 0, 0, 1, [[0, 'rgba(70,224,255,0.28)'], [1, 'rgba(70,224,255,0.85)']])
        },
        emphasis: { focus: 'series', itemStyle: { opacity: 1, shadowBlur: 14, shadowColor: 'rgba(70,224,255,0.4)' } },
        data: prompt
      },
      {
        name: 'генерация', type: 'bar', stack: 'tok', barMaxWidth: 26,
        itemStyle: {
          opacity: 0.85, borderRadius: [4, 4, 0, 0],
          color: grad(0, 0, 0, 1, [[0, 'rgba(255,180,84,0.30)'], [1, 'rgba(255,180,84,0.9)']])
        },
        emphasis: { focus: 'series', itemStyle: { opacity: 1, shadowBlur: 14, shadowColor: 'rgba(255,180,84,0.4)' } },
        data: gen
      }
    ];
    inst.setOption(opt);
  }

  /* ================= 3. chart-cache — gauge cache_hit_pct ================== */
  function buildCache(el, dash) {
    var id = 'chart-cache';
    var t = (dash.wire && dash.wire.totals) || {};
    var svInfo = (dash.server && dash.server.online && dash.server.info) || {};
    var pr = num(t.prompt, 0), cr = num(t.cache_read, 0);
    var v = num(svInfo.cache_hit_pct, NaN), src = 'по данным сервера';
    if (!isFinite(v)) {
      v = num(t.cache_hit_pct, NaN); src = 'по клиентским логам';
      if (!isFinite(v) && pr > 0) v = cr / pr * 100;
    }
    if (!isFinite(v)) return empty(el, id, 'нет данных о кэш-хитах');
    v = Math.max(0, Math.min(100, v));

    var inst = mount(el, id);
    var opt = baseOption({
      trigger: 'item',
      formatter: function () {
        var s = '<b>попадание в кэш префиксов</b> · ' + src + '<br>' + pct1(v);
        if (pr > 0) s += '<br><span style="color:' + C.dim + '">в клиентских логах кэш-чтений ' + int(cr) + ' из ' + int(pr) + ' prompt (Kimi Code не получает cached_tokens от SGLang)</span>';
        return s;
      }
    });
    opt.series = [{
      type: 'gauge',
      startAngle: 210, endAngle: -30,
      min: 0, max: 100, splitNumber: 5,
      radius: '88%', center: ['50%', '56%'],
      progress: {
        show: true, width: 15, roundCap: true,
        itemStyle: {
          color: grad(0, 0, 1, 0, [[0, C.cyan], [0.55, C.mint], [1, C.amber]]),
          shadowBlur: 18, shadowColor: 'rgba(70,224,255,0.45)'
        }
      },
      axisLine: { lineStyle: { width: 15, color: [[1, 'rgba(120,140,180,0.14)']] } },
      axisTick: { distance: -22, length: 4, lineStyle: { color: 'rgba(140,160,200,0.35)', width: 1 } },
      splitLine: { distance: -26, length: 9, lineStyle: { color: 'rgba(140,160,200,0.5)', width: 2 } },
      axisLabel: { distance: -12, color: C.dim, fontSize: 9, fontFamily: FONT_MONO, formatter: function (x) { return x === 0 || x === 100 ? String(x) : ''; } },
      pointer: { show: false },
      anchor: { show: false },
      title: {
        offsetCenter: [0, '42%'], fontSize: 11, color: C.dim, fontFamily: FONT_SANS
      },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, '2%'],
        formatter: function (val) { return '{v|' + nfPct1.format(num(val, 0)) + '}{u|\u00a0%}'; },
        rich: {
          v: { fontFamily: FONT_MONO, fontSize: 40, fontWeight: 800, color: C.bright },
          u: { fontFamily: FONT_MONO, fontSize: 18, fontWeight: 600, color: C.amber }
        }
      },
      data: [{ value: v, name: 'попаданий в кэш' }]
    }];
    inst.setOption(opt);
  }

  /* ================= 4. chart-price — гориз. bar est_usd + плашка ₽ ======== */
  function buildPrice(el, dash) {
    var id = 'chart-price';
    var cloud = (dash.costs && Array.isArray(dash.costs.cloud)) ? dash.costs.cloud : [];
    var rows = cloud
      .map(function (r) {
        return {
          vendor: r && r.vendor, model: (r && (r.model || r.vendor)) || '—',
          v: num(r && r.est_usd, 0),
          in_: num(r && r.in_usd, NaN), cch: num(r && r.cached_in_usd, NaN), out: num(r && r.out_usd, NaN),
          note: (r && r.note) ? String(r.note) : ''
        };
      })
      .filter(function (r) { return r.v > 0; })
      .sort(function (a, b) { return b.v - a.v; });

    var local = (dash.costs && dash.costs.local) || {};
    var localRub = num(local.rub, NaN);
    var hasLocal = isFinite(localRub) && localRub > 0;

    if (!rows.length && !hasLocal) return empty(el, id, 'нет данных о ценах');

    // локальные сбережения: поверх карты — контрастная плашка (DOM, см. charts.css)
    var plate = document.createElement('div');
    plate.className = 'dash-plate';
    var pl = document.createElement('span'); pl.className = 'dash-plate__label'; pl.textContent = 'локально ≈';
    var pv = document.createElement('span'); pv.className = 'dash-plate__value';
    pv.textContent = hasLocal ? moneyRub(localRub) : '—';
    if (local.note) plate.title = String(local.note);
    plate.appendChild(pl); plate.appendChild(pv);

    if (!rows.length) {
      empty(el, id, 'облачные оценки ещё не посчитаны');
      el.appendChild(plate);
      return;
    }

    var vMax = rows[0].v, vMin = rows[rows.length - 1].v;
    var useLog = (vMax / vMin) > 50;

    var inst = mount(el, id);
    var opt = baseOption({
      trigger: 'item',
      formatter: function (p) {
        var r = rows[p.dataIndex];
        var s = '<b>' + r.model + '</b>';
        if (r.vendor && r.vendor !== r.model) s += ' <span style="color:' + C.dim + '">(' + r.vendor + ')</span>';
        s += '<br>оценка за окно: <span style="font-family:' + FONT_MONO + '">' + moneyUsd(r.v) + '</span>';
        if (isFinite(r.in_)) s += '<br><span style="color:' + C.dim + '">тарифы $/1M: вход ' + r.in_ +
          (isFinite(r.cch) ? ', кэш-вход ' + r.cch : '') + ', выход ' + r.out + '</span>';
        if (r.note) s += '<br><span style="color:' + C.faint + '">' + r.note + '</span>';
        return s;
      }
    });
    opt.grid = { left: 6, right: 62, top: 6, bottom: 0, containLabel: true };
    opt.xAxis = {
      type: useLog ? 'log' : 'value',
      logBase: 10, min: useLog ? undefined : 0,
      axisLine: { show: false }, axisTick: { show: false },
      name: useLog ? 'log₁₀ $' : '',
      nameTextStyle: { color: C.dim, fontSize: 9 },
      axisLabel: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO, formatter: function (x) { return moneyUsd(x); } },
      splitLine: { lineStyle: { color: C.faint, type: 'dashed' } }
    };
    opt.yAxis = {
      type: 'category',
      inverse: true, // самый дорогой — сверху
      data: rows.map(function (r) { return r.model; }),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: C.faint } },
      axisLabel: {
        color: C.text, fontSize: 10.5, fontFamily: FONT_SANS, width: 130, overflow: 'truncate',
        formatter: function (name) { return clip(name, 20); }
      }
    };
    opt.series = [{
      name: 'стоимость в облаке',
      type: 'bar',
      barMaxWidth: 18,
      itemStyle: {
        borderRadius: [0, 6, 6, 0],
        color: grad(0, 0, 1, 0, [[0, 'rgba(70,224,255,0.55)'], [0.6, 'rgba(181,123,255,0.75)'], [1, 'rgba(255,180,84,0.95)']])
      },
      emphasis: { focus: 'series', itemStyle: { shadowBlur: 16, shadowColor: 'rgba(181,123,255,0.5)' } },
      label: {
        show: true, position: 'right', distance: 6,
        color: C.bright, fontFamily: FONT_MONO, fontSize: 11,
        formatter: function (p) { return moneyUsd(p.value); }
      },
      data: rows.map(function (r) { return r.v; })
    }];
    inst.setOption(opt);
    el.appendChild(plate);
  }

  /* ================= 5. chart-server — пул-бар счётчиков инфера ============ */
  function buildServer(el, dash) {
    var id = 'chart-server';
    var s = dash.server || {};
    var c = s.counters || {};
    var info = s.info || {};
    var cp = num(c.prompt_total, NaN);
    var cg = num(c.generation_total, NaN);
    var cap = num(info.max_total_num_tokens, NaN);

    var cats = [], vals = [], styles = [];
    if (isFinite(cp)) { cats.push('prompt с запуска'); vals.push(cp); styles.push({ opacity: 0.9, color: grad(0, 0, 1, 0, [[0, 'rgba(70,224,255,0.35)'], [1, 'rgba(70,224,255,0.95)']]) }); }
    if (isFinite(cg)) { cats.push('генерация с запуска'); vals.push(cg); styles.push({ opacity: 0.9, color: grad(0, 0, 1, 0, [[0, 'rgba(255,180,84,0.35)'], [1, 'rgba(255,180,84,0.95)']]) }); }
    if (isFinite(cap)) { cats.push('ёмкость контекста'); vals.push(cap); styles.push({ color: 'rgba(181,123,255,0.22)', borderColor: 'rgba(181,123,255,0.6)', borderWidth: 1, borderType: 'dashed' }); }

    if (!cats.length) return empty(el, id, 'сервер ещё не отчитался');

    var inst = mount(el, id);
    var narrow = el.clientWidth < 520;
    var opt = baseOption({
      trigger: 'item',
      formatter: function (p) {
        return '<b>' + p.name + '</b><br><span style="font-family:' + FONT_MONO + '">' + int(p.value) + '</span> токенов';
      }
    });
    opt.grid = { left: 6, right: 66, top: 6, bottom: 0, containLabel: true };
    opt.xAxis = {
      type: 'value',
      splitNumber: narrow ? 3 : 8,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO, hideOverlap: true, formatter: function (x) { return compactTok(x); } },
      splitLine: { lineStyle: { color: C.faint, type: 'dashed' } }
    };
    opt.yAxis = {
      type: 'category', inverse: true, data: cats,
      axisTick: { show: false }, axisLine: { lineStyle: { color: C.faint } },
      axisLabel: { color: C.text, fontSize: 11, fontFamily: FONT_SANS }
    };
    opt.series = [{
      type: 'bar', barMaxWidth: 16,
      data: vals.map(function (v, i) { return { value: v, itemStyle: Object.assign({ borderRadius: [0, 6, 6, 0] }, styles[i]) }; }),
      label: {
        show: true, position: 'right', distance: 6,
        color: C.bright, fontFamily: FONT_MONO, fontSize: 11,
        formatter: function (p) { return compactTok(p.value); }
      },
      emphasis: { focus: 'series' }
    }];
    inst.setOption(opt);

    if (s.online === false) {
      // сервер лежит — поверх оставляем последние известные цифры, но под дымкой
      var haze = document.createElement('div');
      haze.className = 'dash-haze';
      var pill = document.createElement('span');
      pill.className = 'dash-haze__pill';
      var tm = hhmm(s.updated_at);
      pill.textContent = tm ? 'сервер недоступен · данные на ' + tm : 'сервер недоступен';
      haze.appendChild(pill);
      el.appendChild(haze);
    }
  }

  /* ================= выбор периода — пересчёт из дневных рядов ============ */
  /* Окно считается календарно от самой свежей даты в wire.days
   * (0 = всё время). Тоталы, проекты и цены заново суммируются по строкам days,
   * серверные кумулятивные счётчики (chart-server) периоду не подлежат. */
  function shiftDateStr(ds, deltaDays) {
    var y = +ds.slice(0, 4), m = +ds.slice(5, 7) - 1, d = +ds.slice(8, 10);
    var t = Date.UTC(y, m, d) + deltaDays * 86400000;
    return new Date(t).toISOString().slice(0, 10);
  }
  function within(ds, cutoff) {
    return cutoff === null || String(ds) >= cutoff;
  }
  function sumDays(rows, cutoff) {
    var t = { steps: 0, prompt: 0, gen: 0, cache_read: 0 };
    (rows || []).forEach(function (d) {
      if (!d || !d.date || !within(d.date, cutoff)) return;
      t.steps += num(d.steps, 0);
      t.prompt += num(d.prompt, 0);
      t.gen += num(d.gen, 0);
      t.cache_read += num(d.cache_read, 0);
    });
    return t;
  }
  function sliceDash(dash, days) {
    dash = (dash && typeof dash === 'object') ? dash : {};
    var wire = dash.wire || {};
    var allDays = Array.isArray(wire.days) ? wire.days.filter(function (d) { return d && d.date; }) : [];
    if (!allDays.length) {
      /* нет дневной разбивки (старый data.js) — показываем как есть */
      return dash;
    }
    var cutoff = null;
    if (days > 0) cutoff = shiftDateStr(allDays[allDays.length - 1].date, -(days - 1));

    var win = allDays.filter(function (d) { return within(d.date, cutoff); });
    var t = sumDays(win, null);
    t.cache_hit_pct = t.prompt ? Math.round(t.cache_read / t.prompt * 1000) / 10 : 0;

    /* проекты: суммируем их собственные дневные ряды; у старых форматов без
     * days проект попадает только в окно «всё время» */
    var projects = (Array.isArray(wire.projects) ? wire.projects : [])
      .map(function (p) {
        if (!p || !p.ws) return null;
        if (Array.isArray(p.days)) {
          var s = sumDays(p.days, cutoff);
          return { ws: p.ws, steps: s.steps, prompt: s.prompt, gen: s.gen, cache_read: s.cache_read };
        }
        if (days > 0) return null;
        return { ws: p.ws, steps: num(p.steps, 0), prompt: num(p.prompt, 0), gen: num(p.gen, 0), cache_read: num(p.cache_read, 0) };
      })
      .filter(function (p) { return p && (p.prompt + p.gen) > 0; })
      .sort(function (a, b) { return (b.prompt + b.gen) - (a.prompt + a.gen); });

    /* кэш-доля для биллинга: как на сервере — измеренный серверный hit-rate,
     * с фолбэком на клиентский cache_read по окну */
    var svInfo = (dash.server && dash.server.online && dash.server.info) || {};
    var srvHit = num(svInfo.cache_hit_pct, NaN);
    var effCr = isFinite(srvHit)
      ? Math.round(t.prompt * Math.min(Math.max(srvHit, 0), 100) / 100)
      : Math.min(t.cache_read, t.prompt);

    var cloud = ((dash.costs && dash.costs.cloud) || []).map(function (r) {
      var est = effCr / 1e6 * num(r.cached_in_usd, 0)
        + (t.prompt - effCr) / 1e6 * num(r.in_usd, 0)
        + t.gen / 1e6 * num(r.out_usd, 0);
      return Object.assign({}, r, { est_usd: Math.round(est * 100) / 100 });
    }).sort(function (a, b) { return b.est_usd - a.est_usd; });

    /* электричество: активные дни окна × 24 ч × мощность (константы от сервера) */
    var pw = (dash.costs && dash.costs.power) || {};
    var avgW = num(pw.avg_w, 700), rubKwh = num(pw.rub_per_kwh, 10);
    var active = win.filter(function (d) {
      return d.steps > 0 || (d.prompt + d.gen) > 0;
    }).length;
    var kwh = active * 24 * avgW / 1000;
    var local = Object.assign({}, (dash.costs && dash.costs.local) || {}, {
      kwh: Math.round(kwh * 10) / 10,
      rub: Math.round(kwh * rubKwh),
      note: 'оценка по активным дням выбранного периода: ' + active + ' дн × 24 ч'
    });

    return Object.assign({}, dash, {
      window_days: days > 0 ? days : null,
      window_start: cutoff,
      wire: Object.assign({}, wire, {
        totals: t,
        projects: projects,
        days: win
      }),
      costs: Object.assign({}, dash.costs, {
        totals: { prompt: t.prompt, gen: t.gen, cache_read: effCr },
        cloud: cloud,
        local: local
      })
    });
  }

  /* ================= init =================================================== */
  function init(root, dash) {
    root = root || document;
    if (dash === undefined || dash === null) dash = window.DASH;
    dash = (dash && typeof dash === 'object') ? dash : {};

    disposeAll(); // повторный init — чистый лист

    var noEcharts = !hasEcharts();
    var todo = [
      ['chart-projects', buildProjects],
      ['chart-days', buildDays],
      ['chart-cache', buildCache],
      ['chart-price', buildPrice],
      ['chart-server', buildServer]
    ];
    todo.forEach(function (pair) {
      var el = containerEl(root, pair[0]);
      if (!el) return; // контейнера нет в DOM — тихо пропускаем (карта ещё не отрисована ui_core)
      if (noEcharts) { echartsMissing(el); return; }
      try {
        pair[1](el, dash);
      } catch (e) {
        try { empty(el, pair[0], 'график не отрисовался'); } catch (e2) {}
        if (window.console && console.error) console.error('[DASH_CHARTS]', pair[0], e);
      }
    });

    if (!noEcharts) {
      bindResize();
      scheduleResize(); // страховка: контейнеры могли получить размеры после инициализации
      setTimeout(scheduleResize, 900);   // после reveal-анимаций
      setTimeout(scheduleResize, 2500);  // после устоятия layout/DPR в панельных браузерах
    }
  }

  window.DASH_CHARTS = {
    init: init,
    dispose: disposeAll,
    slice: sliceDash, // пересчёт window.DASH на выбранное окно (пользуется ui_core)
    PALETTE: C // только для отладки; ui_core отсюда цвета не берёт
  };
})();

# DASHBOARD CONTRACT — Kimi/Квен токен-дашборд (локальный, без сборки)

Общий каталог: `D:\llm\dashboard\`. Никто НЕ трогает файлы вне своей зоны ответственности.

## Технические рамки (общие для всех)
- Только чистые HTML/CSS/JS + Python 3. Никаких npm/сборщиков. ES2020.
- Графики: **Apache ECharts 5.x с CDN** (`https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js`). Если CDN недоступен — страница обязана показать вежливый баннер, а не белый экран.
- Открывается как `file:///D:/llm/dashboard/index.html` → **никаких fetch() к json**! Все данные приходят из `data.js` (генерируется refresh.py), который объявляет `window.DASH`.
- Язык интерфейса: русский. Светлая тема, «2026»-эстетика: мягкий сине-серый фон #f2f5fa с пастельными aurora-пятнами, белые glass-карточки с лёгкими тенями, приглушённые акценты (янтарь #d97a06, циан #0e8fb8, пурпур #7a52e0), моноширинные крупные цифры (JetBrains Mono/system mono), плавные анимации появления.
- Все скриптыcollectors'ов обязаны иметь функцию `collect(days: float) -> dict` и работать без аргументов CLI по умолчанию (полная история: `days<=0` = без среза; дневные ряды не обрезаются, период выбирается на фронте).

## Источники данных (реальность, проверена)
1. Wire-логи Kimi Code: `~/.kimi-code/sessions/wd_*/session_*/agents/*/wire.jsonl`.
   События учёта: JSON-строки, где есть `"usage"` и `"step.end"`; поля: `time` (мс),
   `event.usage.{inputOther, inputCacheRead, inputCacheCreation, output}`.
   prompt = inputOther+inputCacheRead+inputCacheCreation; gen = output.
   Проект = имя каталога `wd_*` (5-й сегмент пути из конца).
   Референс парсера: `D:\llm\ops\token_bill_days.py` (не копировать print-часть).
2. Сервер инфера: `http://192.168.2.127:1234/metrics` (Prometheus, доступен с Windows).
   Считать суммой ВСЕХ серий: `sglang:prompt_tokens_total`, `sglang:generation_tokens_total`.
   Дополнительно `http://192.168.2.127:1234/get_server_info` (JSON): `max_total_num_tokens`,
   `memory_usage` и пр. — брать что есть, не выдумывать полей. Сервер может лежать → `online:false`, не падать.
   Дневной gen throughput движка — `docker logs penny` (**только на хосте, где есть docker**):
   строки `[YYYY-MM-DD HH:MM:SS TP0] Decode batch, … gen throughput (token/s): N.NN`,
   только TP0 (TP1 дублирует), только `Decode batch`. Метки в логах UTC — ключ дня =
   MSK (+03:00 фикс.), та же дневная ось, что у wire.days. История логов теряется при
   рестарте контейнера → бакеты персистентны в `data/server_gen_buckets.json`
   (`{date: {"<t/s целое>": count}}`), добор инкрементальный (`docker logs --since`,
   дедуп по последнему ts), state — `data/server_gen_state.json`. Нет docker — ряд
   строится из прошлых бакетов (не падать). Чтение — `stderr=STDOUT` (SGLang пишет
   Decode-лог в stderr контейнера; `capture_output` с явными stdout/stderr несовместим
   — ValueError). Деплой-smoke после замены коллектора: снести state/bakеты, вызвать
   `_update_gen_days()`, потребовать `raw-hits > 0` — одного Windows-selftest мало.
3. Цены — захардкоженная таблица в collect_costs.py (usd за 1M токенов, вход/кэш-вход/выход), по модели
   из wire-билла: DeepSeek, Moonshot Kimi, OpenAI GPT, Anthropic Claude, xAI, Google Gemini + локальная
   оценка (электричество 10 ₽/кВт·ч, 2×RTX PRO 6000 ≈ 600–750 W под нагрузкой). Формула облака:
   cache_read-доля по кэш-цене, остальной prompt по входной, gen по выходной.

## ВЛАДЕНИЕ ФАЙЛАМИ
| Агент | Пишет | Только чтение |
|---|---|---|
| collector_wire | collect_wire.py | CONTRACT, ops/token_bill_days.py |
| collector_server | collect_server.py, data/server_gen_buckets.json, data/server_gen_state.json | CONTRACT |
| collector_costs | collect_costs.py | CONTRACT |
| ui_core | index.html, css/style.css, js/app.js | CONTRACT |
| ui_charts | js/charts.js, css/charts.css | CONTRACT |
| glue | refresh.py, Dashboard.bat, README.md | всё |

## СХЕМА ДАННЫХ (контракт window.DASH, генерит refresh.py в data.js)
```js
window.DASH = {
  generated_at: "2026-10-09T12:00:00+03:00",
  window_days: 30 | null,             // null = полная история (DEFAULT_DAYS=0)
  wire: {                             // из collect_wire.collect()
    updated_at: iso,
    totals: { steps, prompt, gen, cache_read, cache_creation, cache_hit_pct },
    projects: [ { ws, steps, prompt, gen, cache_read,
                  days: [ { date, steps, prompt, gen, cache_read } ] } ],  // сортировка по убыв. prompt+gen
    days:     [ { date:"YYYY-MM-DD", steps, prompt, gen, cache_read } ], // по возрастанию даты, ПОЛНАЯ история
    models:   { "<model_id>": { steps, prompt, gen } },            // опционально, если модель видна в логах
    speed: { steps_measured, med_tps, p25_tps, ttft_med_s,         // tps = output/llmStreamDurationMs по step.end
             buckets: [ {label, n, med_tps, ttft_med_s} ],         // корзины контекста <32K / 32–128K / >128K
             days: [ {date, n, med_tps, ttft_med_s} ] }            // дневные медианы (фронт фильтрует по периоду)
  },
  server: {                           // из collect_server.collect()
    updated_at: iso, online: bool,
    counters: { prompt_total, generation_total },                  // int, «с запуска» — периоду не подлежит
    info: { max_total_num_tokens, /* что удалось вытянуть */ },
    gen_tps_days: [ { date:"YYYY-MM-DD", n:int, p50:float, p95:float } ]
      // дневные медианы серверного gen throughput (tok/s): docker logs penny,
      // только TP0 "Decode batch"; UTC-метки порезаны по MSK(+03:00) — та же
      // дневная ось, что у wire.days; ПОЛНАЯ история (бакеты — data/server_gen_buckets.json)
  },
  costs: {                            // из collect_costs.collect()
    updated_at: iso,
    totals: { prompt, gen, cache_read },                           // те же, что в wire (для расчёта)
    cloud: [ { vendor, model, in_usd, cached_in_usd, out_usd, est_usd } ],
    local: { kwh, rub, note },
    power: { avg_w, rub_per_kwh }     // константы железа: фронт пересчитывает ₽ на выбранный период
  }
}
```

## КОНТРАКТ UI
- `js/app.js` (ui_core): грузит `data.js`, проверяет `window.DASH`, рисует hero-блок с
  big-number-анимацией (count-up), статус-бар «обновлено N мин назад», секцию ошибок, селектор
  периода `#period-select` (7/14/30/90/365/0=всё, выбор в localStorage `kimi-dash-period`),
  вызывает `window.DASH_CHARTS.init(document, view)`, где `view = DASH_CHARTS.slice(DASH, periodDays)`
  — пересчёт тоталов/проектов/цен из дневных рядов на клиенте.
- `js/charts.js` (ui_charts): объявляет `window.DASH_CHARTS = { init(root, dash){...} }` и строит ECharts-карты
  в контейнеры, которые готовит ui_core строго с id:
  `chart-projects` (donut по проектам), `chart-days` (stacked bar prompt/gen по дням),
  `chart-cache` (gauge cache hit %), `chart-price` (гориз. bar облачных оценок usd + строка «локально ~X ₽»),
  `chart-speed` (линия tok/s по дням + пунктир TTFT + линия «движок, t/s (медиана)» из
  `server.gen_tps_days.p50` — то же окно-дата, что у wire.days; день без бакета = разрыв
  линии, не ноль; под графиком строка breakdown по корзинам контекста),
  `chart-server` (спарклайн суммарных счётчиков / пул-бар, что дано).
  Каждый контейнер-карта оборачивается ui_core в glass-карточку `<section class="card" data-chart="chart-...">`.
- Адаптив: grid 12 колонок, на мобильном — в столбик. Resize charts через window resize listener.
- ui_core НЕ определяет цвета графиков; палитра-chartов — константа внутри charts.js; общие CSS-переменные — в style.css, charts.css может их использовать (цвета: --accent-amber #ffb454, --accent-cyan #46e0ff, --accent-purple #b57bff, --bg #05070d).

## Тесты, обязательные перед сдачей
- collect_*: `python D:\llm\dashboard\collect_X.py` → пишет data/X.json, валиден json, печатает summary на 1 строчку, парсится без ошибок на реальных данных (30 дней).
- ui_core/ui_charts: открыть file:///D:/llm/dashboard/index.html (mcp desktop browser), скриншот, ни одного сломанного блока; при пустом/отсутствующем data.js — graceful placeholder.
- glue: `refresh.py` без аргументов: запускает collect'ы (import, не subprocess), пишет data.js; `Dashboard.bat` = python refresh.py && start index.html.
Все числа форматировать с разделителями тысяч; проценты — один знак после запятой.

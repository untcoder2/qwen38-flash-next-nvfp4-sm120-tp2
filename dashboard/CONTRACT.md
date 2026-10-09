# DASHBOARD CONTRACT — Kimi/Квен токен-дашборд (локальный, без сборки)

Общий каталог: `D:\llm\dashboard\`. Никто НЕ трогает файлы вне своей зоны ответственности.

## Технические рамки (общие для всех)
- Только чистые HTML/CSS/JS + Python 3. Никаких npm/сборщиков. ES2020.
- Графики: **Apache ECharts 5.x с CDN** (`https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js`). Если CDN недоступен — страница обязана показать вежливый баннер, а не белый экран.
- Открывается как `file:///D:/llm/dashboard/index.html` → **никаких fetch() к json**! Все данные приходят из `data.js` (генерируется refresh.py), который объявляет `window.DASH`.
- Язык интерфейса: русский. Тёмная тема, «2026»-эстетика: глубина чёрно-синего, aurora-градиенты, glass-карточки, неоновые акценты (янтарь #ffb454, циан #46e0ff, пурпур #b57bff), моноширинные крупные цифры (JetBrains Mono/system mono), плавные анимации появления.
- Все скриптыcollectors'ов обязаны иметь функцию `collect(days: float) -> dict` и работать без аргументов CLI по умолчанию (30 дней).

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
3. Цены — захардкоженная таблица в collect_costs.py (usd за 1M токенов, вход/кэш-вход/выход), по модели
   из wire-билла: DeepSeek, Moonshot Kimi, OpenAI GPT, Anthropic Claude, xAI, Google Gemini + локальная
   оценка (электричество 10 ₽/кВт·ч, 2×RTX PRO 6000 ≈ 600–750 W под нагрузкой). Формула облака:
   cache_read-доля по кэш-цене, остальной prompt по входной, gen по выходной.

## ВЛАДЕНИЕ ФАЙЛАМИ
| Агент | Пишет | Только чтение |
|---|---|---|
| collector_wire | collect_wire.py | CONTRACT, ops/token_bill_days.py |
| collector_server | collect_server.py | CONTRACT |
| collector_costs | collect_costs.py | CONTRACT |
| ui_core | index.html, css/style.css, js/app.js | CONTRACT |
| ui_charts | js/charts.js, css/charts.css | CONTRACT |
| glue | refresh.py, Dashboard.bat, README.md | всё |

## СХЕМА ДАННЫХ (контракт window.DASH, генерит refresh.py в data.js)
```js
window.DASH = {
  generated_at: "2026-10-09T12:00:00+03:00",
  window_days: 30,
  wire: {                       // из collect_wire.collect()
    updated_at: iso,
    totals: { steps, prompt, gen, cache_read, cache_creation, cache_hit_pct },
    projects: [ { ws, steps, prompt, gen, cache_read } ],          // сортировка по убыв. prompt+gen
    days:     [ { date:"YYYY-MM-DD", steps, prompt, gen, cache_read } ], // по возрастанию даты
    models:   { "<model_id>": { steps, prompt, gen } }             // опционально, если модель видна в логах
  },
  server: {                     // из collect_server.collect()
    updated_at: iso, online: bool,
    counters: { prompt_total, generation_total },                  // int
    info: { max_total_num_tokens, /* что удалось вытянуть */ }
  },
  costs: {                      // из collect_costs.collect()
    updated_at: iso,
    totals: { prompt, gen, cache_read },                           // те же, что в wire (для расчёта)
    cloud: [ { vendor, model, in_usd, cached_in_usd, out_usd, est_usd } ],
    local: { kwh, rub, note }
  }
}
```

## КОНТРАКТ UI
- `js/app.js` (ui_core): грузит `data.js`, проверяет `window.DASH`, рисует hero-блок с
  big-number-анимацией (count-up), статус-бар «обновлено N мин назад», секцию ошибок, вызывает
  `window.DASH_CHARTS.init(document, DASH)` если таковой зарегистрирован.
- `js/charts.js` (ui_charts): объявляет `window.DASH_CHARTS = { init(root, dash){...} }` и строит ECharts-карты
  в контейнеры, которые готовит ui_core строго с id:
  `chart-projects` (donut по проектам), `chart-days` (stacked bar prompt/gen по дням),
  `chart-cache` (gauge cache hit %), `chart-price` (гориз. bar облачных оценок usd + строка «локально ~X ₽»),
  `chart-server` (спарклайн суммарных счётчиков / пул-бар, что дано).
  Каждый контейнер-карта оборачивается ui_core в glass-карточку `<section class="card" data-chart="chart-...">`.
- Адаптив: grid 12 колонок, на мобильном — в столбик. Resize charts через window resize listener.
- ui_core НЕ определяет цвета графиков; палитра-chartов — константа внутри charts.js; общие CSS-переменные — в style.css, charts.css может их использовать (цвета: --accent-amber #ffb454, --accent-cyan #46e0ff, --accent-purple #b57bff, --bg #05070d).

## Тесты, обязательные перед сдачей
- collect_*: `python D:\llm\dashboard\collect_X.py` → пишет data/X.json, валиден json, печатает summary на 1 строчку, парсится без ошибок на реальных данных (30 дней).
- ui_core/ui_charts: открыть file:///D:/llm/dashboard/index.html (mcp desktop browser), скриншот, ни одного сломанного блока; при пустом/отсутствующем data.js — graceful placeholder.
- glue: `refresh.py` без аргументов: запускает collect'ы (import, не subprocess), пишет data.js; `Dashboard.bat` = python refresh.py && start index.html.
Все числа форматировать с разделителями тысяч; проценты — один знак после запятой.

# Kimi Code: три правки, которые ускоряют агента

Ставятся за пять минут, всё клиентское (`~/.kimi-code/`).

## 1. Effort по задаче без ручного селектора — в config.toml

Субагенты по умолчанию идут на medium, xhigh — только когда главный агент сам
явно зовёт его на сложную подзадачу. Подставь свою модель/provider вместо
плейсхолдеров; запись `-fast` копирует поля оригинала полностью — они не
наследуются:

```toml
[models."local/<твоя-модель>-fast"]
provider = "<твой provider>"
model = "<та же model>"
max_context_size = <тот же>
capabilities = [ "tool_use", "thinking" ]
support_efforts = [ "low", "medium", "xhigh" ]

[models."local/<твоя-модель>-fast".overrides]
default_effort = "medium"

[secondary_model]
default_model = "local/<твоя-модель>-fast"

[secondary_model.models]
"local/<твоя-модель>" = "xhigh: hard debugging, audits, root-cause — explicitly."
"local/<твоя-модель>-fast" = "medium: routine ops. Default for subagents."
```

Если в `[secondary_model]` есть секционная строка `default_effort` — удали её,
она перебивает уровень всех записей пула. Main-сессию не понижай — экономия на
делегатах, качество на главном. После правки — полный рестарт приложения.

## 2. Маленький вывод по умолчанию — в AGENTS.md (глобальный или проектный)

```md
Локальные Grep/Glob всегда с head_limit (по умолчанию 20, не 100) и фильтром
`glob: "!{<датасеты>,tmp}/**"`. Длинные/удалённые команды — сразу `| tail -N` /
`grep -m N`; полный вывод нужен целиком — писать в файл, не в ленту.
```

## 3. Серии удалённых команд — одним скриптом

`rsh.sh` (одна команда) и `runsh.sh` (локальный скрипт хосту за один
round-trip, с аргументами и пробросом exit-кода). Цель через окружение:

```bash
export RSH_HOST=user@host RSH_KEY=~/.ssh/ваш_ключ
```

Плюс то же на клиенте: независимые вызовы — параллельно в одном ходе, всё
дольше минуты — фоном.

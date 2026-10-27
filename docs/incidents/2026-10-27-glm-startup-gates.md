# 27.10.2026 — запуск GLM/vLLM после ложных startup-гейтов

В 20:26 runner остановил и удалил уже поднявшийся glm-shadow, поскольку ожидал 401 на публичном /metrics. Health и authenticated model identity прошли; штатная остановка контейнера exit0, свежих OOM/Xid не было.

Исправление ops/_serve_glm53_shadow.sh:
- Проверка отсутствующего API-ключа перенесена на /v1/models с bounded curl. Положительная authenticated identity проверка сохранена; ключ и auth middleware не менялись.
- READY_OK выставляется после обязательных sanity и lossless проверок, чтобы ошибка ответа не оставляла ложный успех.
- По прямому выбору владельца Hashview оставлен. Удалён отказ только по ActiveState агента; actual проверка двух GPU с free >=92000 MiB сохранена. Модель/image/CLI/ресурсы не менялись.

Проверки: actual AuthenticationMiddleware из pinned image через offline ASGI, before/after actual runner-tail и actual preflight fixtures без GPU/сети. Исходные отказы воспроизведены; неправильный/отсутствующий ключ отвергается, правильный допускается; нехватка реальной VRAM по-прежнему блокирует старт. Пара code/security review: PASS0; 2 раунда, pending закрыты; арбитр GO, D:* и discipline-audit с доказательствами.

После выбора coexistence GLM запущен в 21:13:16, к21:19:56 runner exit0. Финальный срез 21:20:25: health200, anonymous /v1/models401, authorized200; aliases qwen3.8-flash-next и glm-5.3-flash; sanity/lossless passed, restart_count0. memory.peak77.30GiB при limit80GiB, oom/oom_kill0. Полного quality/batch/performance benchmark не было.

Live runner SHA256: c92c08e6e8addec0530855c9d75e8deb6a9720d0d0f3e58ee54490d21bb1140d. Изменение будущего startup-файла сохранено атомарно без рестарта текущего контейнера. Hashview, старый sglang и хост не перезапускались. .maintenance сохранён: он удерживает осиротевший старый watchdog от запуска Penny поверх GLM.

Remote backups: ~/agent-tools/incidents/20261027-210953-glm-auth-repair и 20261027-211316-glm-resume. Локальные исходники, verdicts и результаты проверок: C:/Users/abcd/.kimi-code/backups/glm-repair-20261027-211316/.

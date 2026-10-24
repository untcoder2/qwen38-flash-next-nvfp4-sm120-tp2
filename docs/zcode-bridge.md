### Мост ZCode→penny (`ops/openai_reorder_pump.py`, контейнер `zcode-pump`)

ZCode в OpenAI-канале шлёт `system`-сообщения не только в начале истории
(Anthropic-привычка), а sglang требует «System message must be at the
beginning»; в Anthropic-канале он же шлёт `thinking:{type:"enabled"}` без
`budget_tokens`, что схема Pennyroyal v3.1.1 отвергает как 400. Фронт:
TLS :8744 (цепочка leaf+CA из `~/penny/tls/server.pem`) + hoist всех
system/dev-сообщений в начало `messages` для `POST */chat/completions`;
SSE — raw-дуплексом. Обход ufw — docker `-p 8744:8744` (DOCKER DNAT раньше
юферовских цепочек); upstream — по пользовательской сети `penny-pump` прямо
на `penny:8001` (хостовый INPUT с docker0 файрвол режет — проверено).

Подключение в ZCode: тип OpenAI-compatible, base `https://192.168.2.127:8744/v1`,
модель `qwen3.8-flash-next`, ключ любой. Фронт так же вырезает маркеры
`cache_control`/`cacheControl` (ZCode мажет ими system-блоки и tools по-антропиковски;
наш OpenAI-пайр их не знает → 400) и отдаёт полную TLS-цепочку — Windows-curl требует
`--ssl-no-revoke` (у нашего CA нет CRL), узловой клиент Node/ZCode — нет.
При любом 400/422/500 фронт пишет тело ошибки в `docker logs zcode-pump`.

Жизненный цикл: стартует сам (`--restart unless-stopped`). После любого
рестарта sglang переподключить penny к сети: `docker network connect penny-pump penny`
(иначе фронт отдаёт 502). После следующего рестарта sglang сам начнёт отдавать
full-chain (серверный cert-файл уже обновлён) — фронт останется только ради reorder.
Сносится: `docker rm -f zcode-pump; docker network rm penny-pump`.
Чистовик — апстрим-фиксы: релакс thinking-budget на фронте Pennyroyal + мягкая
расстановка system в OpenAI-сервинге sglang; до них мост живёт осознанно.

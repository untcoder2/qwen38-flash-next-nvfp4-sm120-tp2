# ТЕКУЩАЯ БОЕВАЯ КОНФИГУРАЦИЯ — с 16.09.2026

Прод переведён с нашего форка SGLang на контейнер **Pennyroyal v2.5**
(`jpezzulli/sglang-rtxpro6000`), TP=2, обе карты. Прежнее устройство стека описано
в [architecture.md](architecture.md) — оно остаётся рабочим запасным вариантом.
История замеров — [TONIGHT-2026-09-16.md](TONIGHT-2026-09-16.md).

## Что изменилось и зачем

| | было (наш форк) | стало (Pennyroyal TP=2) |
|---|---|---|
| tok/s медиана, temp 0 | 214.8 | **308.3** |
| tok/s медиана, боевое сэмплирование | не мерено | **275.9** |
| p90 / максимум (temp 0) | 227.8 / 241.9 | **339.1 / 384.7** |
| accept медиана | 2.51 | **3.01** (2.84 при temp 1.0) |
| KV-пул | 2 044 032 | **2 411 648** |
| контекст | 786 432 | 786 432 |
| качество (20 задач) | 20/20 | 20/20 |

Главный источник прироста — **online MXFP8** (`SGLANG_SM120_ONLINE_MXFP8=true`):
неквантованные BF16-линейные слои переводятся в MXFP8 **на загрузке**, веса NVIDIA
на диске не трогаются. Это ровно тот пункт, что годами висел в notes как
«Не пройдено: FP8 на проекциях внимания».

## Модель

Чекпоинт не менялся: `/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp` —
официальные веса NVIDIA (296 444 тензора, не тронуты) плюс **пересаженная
MTP-голова от RadixArk**, 31 тензор BF16.

Отказаться от головы RadixArk **нельзя**, проверено трижды: родная fp8-голова NVIDIA
даёт accept 1.50 и на нашем движке, и на Pennyroyal, и не чинится патчем 0010
(он падает с `Triton illegal memory access`). Подробности — в notes за 16.09.

## Как запущено

```
systemd sglang.service
  └─ serve-active.sh → active.cfg → serve-penny.sh
       └─ docker run --rm --name penny  (foreground, flock как у прочих leaf)
            ghcr.io/jpezzulli/sglang-rtxpro6000:v2.5.0  next-plain
```

Порт **1234**, имя модели **`qwen3.8-flash-next`** — клиент opencode не менялся.
Контейнер слушает 8001, проброшен в 1234.

**GPU в контейнер проброшены вручную**, `nvidia-container-toolkit` не установлен и
не нужен: `--device` на все `/dev/nvidia*`, бинд-маунт четырёх драйверных библиотек
в `/nvdrv` и `LD_LIBRARY_PATH=/nvdrv`. Последнее критично — без него torch не
находит `libcuda.so.1`, даже если файл лежит в системном каталоге.

Подменены два файла профиля внутри образа (лежат в `/home/molochko/penny/`):
`tp2-serve-flash-next.sh` и `tp2-ple-backend.sh`.

## Наши правки к их профилю

| правка | зачем |
|---|---|
| `TP_SIZE=1` → `2` | обе карты |
| `--ple-offload-embedding` → `--no-ple-offload-embedding` | таблица PLE (47.7 ГиБ) в VRAM, как у нас всегда было; освобождает хостовую RAM. **С 14.10 обратно: `--ple-offload-embedding` (pinned RAM) — KV-пул вырос до 6.42M токенов, см. notes 14.10** |
| `--mem-fraction-static 0.981` → `0.88` | 0.981 — одноплатное значение; 0.88 наше проверенное |
| добавлен `--disable-custom-all-reduce` | **без него TP=2 падает**: `RuntimeError: invalid argument` в `get_graph_buffer_ipc_meta` |
| убран `--hicache-storage-backend nixl` и его конфиг | NIXL не инициализируется, `Failed to create NIXL backend` |
| `CONTEXT_LENGTH` 524288 → 786432 → **1048576**, YaRN `factor` 2.0 → 3.0 → **4.0**, `max_position_embeddings` синхронно | вернуть наш контекст; 27.09 поднят до 1M: сервер пропускает запрос только если `input + max_new_tokens + draft-слоты ≤ контекст` (notes 27.09 про 400 на 641K) |
| `--served-model-name pennyroyal` → `qwen3.8-flash-next` | чтобы opencode не трогать |
| `--max-mamba-cache-size` 24 → 64 | снять потолок в 4 одновременных запроса; 64 подобрано 17.09 |
| подменён `chat-template.sh` | вернуть родной шаблон чекпоинта вместо Froggeric |

## Боевая команда (как разрешилась)

> **Сверено живьём 10.10.2026** (`/proc/<pid>/cmdline` контейнера `penny`): образ
> `v2.5.3`, `--mem-fraction-static 0.95`, `--context-length 786432`,
> `--max-running-requests 16`, `--max-mamba-cache-size 128` — ниже исторический
> блок эпохи v2.5.1, кто и когда поднимал mrr/mf и возвращал контекст с 1M —
> в notes не записано.

> **Сверено живьём 14.10.2026**: лист `serve-penny-301.sh`, образ **v3.0.1**, TLS на
> :1234 (https + `penny/tls/ca.pem`), adaptive NEXTN (steps 3/7, draft 8),
> `--hicache-size 4` (**в профиле уже лежит 10 — вступит при следующем рестарте**,
> notes 14.10 вечер), **PLE в host pinned RAM** (`--ple-offload-embedding`),
> KV-пул **6 425 920** токенов, VRAM 93.7/97.9 GiB на карту, short_bench медиана
> **405.6** — новая база. Подробности и откат: notes 14.10.

```
--tp 2 --disable-custom-all-reduce --dtype bfloat16 --quantization modelopt_fp4
--kv-cache-dtype fp8_e4m3 --mem-fraction-static 0.88 --context-length 1048576
--page-size 64 --max-running-requests 8 --sleep-on-idle --chunked-prefill-size 4096
--mamba-radix-cache-strategy extra_buffer --mamba-ssm-dtype bfloat16
--max-mamba-cache-size 64 --gdn-mtp-cache-mode none --mamba-track-interval 256
--linear-attn-decode-backend flashinfer --linear-attn-prefill-backend flashinfer
--enable-hierarchical-cache --hicache-size 32 --hicache-host-memory-mode cache
--hicache-write-policy write_through --hicache-io-backend kernel
--hicache-mem-layout page_first --no-ple-offload-embedding
--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1
--speculative-num-draft-tokens 4 --speculative-draft-model-quantization unquant
--reasoning-parser qwen3 --tool-call-parser qwen3_coder
--chat-template .../froggeric-v22.5.jinja --watchdog-timeout 1800
```

Занято: 85.6 ГБ на каждой карте, хостовой памяти свободно 10 ГБ (HiCache держит 32).

**Замер на боевых параметрах сэмплирования** (temp 1.0 / top_p 0.95 / top_k 20,
после обеих правок): медиана **274.7**, p90 313.4, максимум 353.3, accept 2.91,
качество 20/20. При temp 0 те же настройки дают 308.3 — разницу создаёт
сэмплирование, а не конфигурация.

## ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ПРИВЫЧНОГО — читать перед работой

1. ~~Другой шаблон чата.~~ **ИСПРАВЛЕНО 16.09.** Их `froggeric-v22.5.jinja`
   (28 234 байта) заменён на родной шаблон чекпоинта (8 952 байта) — тот самый,
   на котором работал прежний прод. Подменяется файл `chat-template.sh` внутри
   образа: наша версия в `/home/molochko/penny/tp2-chat-template.sh`, жёсткая
   сверка контрольной суммы их эталона снята. На скорость смена не повлияла:
   274.7 против 275.9 медианы.
2. ~~Риск распада accept по аптайму.~~ **СНЯТО 17.09.** В обсуждении #8 у RadixArk
   триггером назван `--mamba-track-interval` **64** вместе с короткими запросами:
   accept сползает почти к нулю за сутки. При переезде мы унаследовали от их профиля
   именно 64, и это было записано как риск. Сейчас стоит **256** — то же значение,
   что у прежнего форка, на котором за 27-часовое окно распада не было (час 0 →
   2.15, час 27 → 2.25). Монитор `_uptime.py` оставлен на всякий случай.
3. **`--sleep-on-idle`.** Сервер засыпает в простое; первый запрос после паузы
   будет медленнее. Не пугаться.
4. ~~`max_running_requests` фактически 4.~~ **ИСПРАВЛЕНО.** Причина была в логе
   дословно: «capped to 4 by the mamba state cache (max_mamba_cache_size=24,
   5 state slots per request)». Подняли `--max-mamba-cache-size`; после подбора
   17.09 остановились на **64** — см. раздел про расход памяти в двух местах.
   Фактически стало 8 одновременных, KV-пул 2 459 712.
6. **Апстримный баг парсера рассуждений** (`--reasoning-parser qwen3`), найден в
   jpezzulli/sglang-rtxpro6000 issue #6. Детектор ищет `"<tool_call>" in buffer`
   голым подстрочным сравнением. Если модель **упоминает** этот тег, рассуждая в
   прозе — а агентские клиенты инструктируют её про формат вызовов, и она это
   пересказывает — срабатывает ложно: `_in_reasoning` уходит в `False` навсегда, а
   настоящий `</think>` вываливается в видимый `content`. Автор issue сверил файл с
   `sgl-project/sglang` main — байт в байт, то есть баг **не их**, а апстрима, и
   **был у нас и на старом форке тоже**. Открыт, не исправлен. Симптом: обрывки
   `</think>` в ответе и обрезанное рассуждение. Патч предложен в той же issue.

7. **`SGLANG_HICACHE_TORCH_PINNED_ALLOC=1` не включать.** Из issue #8 там же: чинит
   illegal memory access в HiCache **под WSL2** (у нас голый Linux, не наш случай),
   но роняет пропускную способность примерно в 20 раз. PR возвращён в черновик.

5. **NCCL вместо custom all-reduce.** На PCIe без NVLink это стоит задержки —
   то есть резерв ещё есть, если починить custom all-reduce.

## Память: слоты Mamba расходуются в ДВУХ местах

Неочевидное, из-за чего 17.09 был трёхчасовой крашлуп. `--max-mamba-cache-size`
выделяет не только состояния в VRAM, но и **зеркало в хостовой RAM** —
иерархический кеш Mamba:

| слотов | ssm_state в VRAM | зеркало в хосте | KV-пул |
|---|---|---|---|
| 48 | 1.29 ГБ | 1.30 ГБ | 2 522 560 |
| **64 (сейчас)** | ~1.7 ГБ | ~1.7 ГБ | **2 459 712** |
| 128 | 3.40 ГБ | ~3.5 ГБ | 2 208 192 |
| 256 | 6.78 ГБ | ~7 ГБ | 1 705 280 |

При 128 вместе с HiCache на 32 ГиБ хостовой памяти не хватало **120 мегабайт**, и
загрузка падала на самом последнем шаге:

```
ValueError: Not enough host memory for QSA compressed HiCache.
Requesting 2.88 GB but only have 2.76 GB free.
```

Веса, KV и все CUDA-графы к этому моменту уже загружены — отсюда стабильные три
минуты до `Received sigquit from a child process` и рестарт по кругу, причём
`Result=success`, потому что процесс выходит чисто.

**И проверка смотрит на `MemFree`, а не на `MemAvailable`.** В момент отказа было
16.6 ГБ free при 74.5 ГБ available — разница это страничный кеш от чтения
124-гигабайтного чекпоинта. Значит один и тот же размер может подняться на холодной
машине и перестать после нескольких перезагрузок модели. Та же природа была у отказа
NIXL при переезде. Если после правки размеров сервер не встаёт — смотреть **хостовую**
память, а не VRAM.

**Сколько слотов нужно на самом деле.** Замер занятости под нагрузкой: при 4 запросах
`mamba usage` 0.12 от пула, то есть ~31 слот — около **7.75 на запрос**, а не 5.
Пятёрка из стартового сообщения движка это учёт при допуске, а не фактическая
занятость: в неё входят снимки веток диалога. На восьмерых получается ~62, поэтому
**64** и выбрано.

## Не проверено

- многоагентная нагрузка (у старого прода было 422–451 суммарно на четырёх);
- вырождение в длинных рассуждениях (обсуждение #3) — проба `_degen.py` готова;
- HiCache-персистентность через рестарт (NIXL выключен);
- поведение на длинном контексте под 786K.

## Откат

```bash
echo serve-fn-bf16mtp.sh > /home/molochko/vllm/active.cfg && sudo systemctl restart sglang
```

Старый leaf, дерево движка `sglang-qwen4` и все бэкапы на месте, ничего не удалено.

## Доступ друга по WireGuard (с 10.10.2026; работает с 13.10)

Отдельный интерфейс `wg1` (сервер: `10.9.0.1/24`, `ListenPort 443/UDP` — порт выбран
«под HTTPS/QUIC» против шейперов; старый `wg0` от 26.09 снесён 10.10, архив
`/etc/wireguard/disabled-20261010-224731`). Онбординг друга — скриптом (идём через
sudoers-пин `wg-manage.sh`, идемпотентно, следующим друзьям — следующие /32):

```bash
sudo /usr/bin/bash /home/molochko/agent-tools/wg-manage.sh add <имя> [пубключ]
# зеркала: ops/wg-manage.sh, ops/wg1-add-friend.sh (дефолты зеркала подровнены
# 13.10 под бой: ENDPOINT=94.102.125.42, PORT=443, как в /etc/wireguard/wg1.conf)
```

Клиентский конфиг: `AllowedIPs = 192.168.2.127/32` — только инфер-хост, LAN не светится.
Endpoint `94.102.125.42:443` (белый IP Искрателекома, выдан 12.10; прежний
`81.200.10.220` был общим NAT-шлюзом LSN — входящие не доходили). На роутере — проброс
UDP 443 → 192.168.2.127.

Три неочевидных условия, без которых wg1 «не работает» (все проверены кровью 12–13.10):
1. **ufw хоста активен, INPUT DROP** — нужно `sudo ufw allow 443/udp`, иначе пакеты
   доходят до интерфейса и молча дrops'ятся (tcpdump видит вход, ответа нет);
2. **AppArmor профиль `wg-quick`** блокирует бинд привилегированного порта дочерним `ip`
   (`net_bind_service` EPERM) — лечится `apparmor_parser -C -r /etc/apparmor.d/wg-quick`
   (complain); на непривилегированных портах (51820/8443) проблемы нет;
3. в конфиге стоит `Table = off` и `/etc/NetworkManager/conf.d/99-unmanaged-wg1.conf` —
   чтобы systemd/NM не устраивали гонку за интерфейс при рестартах.

Удалить друга: убрать его `[Peer]`-блок из `/etc/wireguard/wg1.conf` и
`sudo systemctl restart wg-quick@wg1`. Проверка живости: `sudo wg show wg1 latest-handshakes`.
Хендшейк подтверждён 13.10 01:00 (peer `friend`, источник 178.71.4.111).

Kimi Code у друга (`~/.kimi-code/config.toml`, шаблон `wireguard/friend-kimi-config.toml`):
провайдер `type = "openai"`, `base_url = https://192.168.2.127:1234/v1` (с 14.10 — TLS,
нужен CA `certs/qwen-home-ca.pem` в доверенных корневых на его машине),
модель `qwen3.8-flash-next`, `max_context_size = 786432`, `adaptive_thinking = true`,
`support_efforts = [low, medium, xhigh]`, `default_effort = xhigh`.

ВАЖНО про рабочее окружение (проверено на друге 13.10): если GUI на Windows открывает
папку в WSL (`\\wsl.localhost\...`) — индексация проекта по 9P не влезает в
30-секундный лимит отправки промпта, GUI показывает «server response timed out»,
сеть при этом исправна. Правила: GUI на Windows → проект на Windows FS; проект в WSL
→ Kimi CLI внутри WSL (конфиг `/home/<user>/.kimi-code/config.toml`) и в WSL
`sudo ip link set dev eth0 mtu 1400` — иначе крупное тело запроса гибнет на форварде
Windows в wg1 (WSL-eth0 MSS 1460 > MTU туннеля 1420).

## Claude Code на этом ПК — Desktop через нативный /v1/messages (14.10)

Начиная с Some-Version SGLang эндпоинт Anthropic `POST /v1/messages` зарегистрирован на
боевом листе автоматически (проверено живым запросом; сервер **не валидирует поле
`model`** — любой id в запросе, всегда отвечает загруженная модель; `--tool-call-parser`
в листе уже стоит, tool-вызовы возвращаются блоками `tool_use`). Прокси/LiteLLM не нужны.

**CLI** (`@anthropic-ai/claude-code` v2.1.296) ставился 14.10 для теста и по решению
владельца **удалён в тот же день** (пакет, `~/.claude/`, `~/.claude.json`). Рецепт на
возврат, если понадобится: env-блок user-settings — `ANTHROPIC_BASE_URL=
https://192.168.2.127:1234`, `ANTHROPIC_AUTH_TOKEN` непустой, `NODE_EXTRA_CA_CERTS=
D:\llm\certs\qwen-home-ca.pem`, `ANTHROPIC_MODEL=claude-sonnet-5`,
`CLAUDE_CODE_ATTRIBUTION_HEADER=0` (иначе per-request hash в system-промпте ломает
radix-кэш — полный ре-префилл истории каждый ход; официально рекомендовано доками SGLang),
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=700000` (модель 786k, суффикс `[1m]` НЕ использовать);
первый запуск — интерактивно (wizard), на девственном профиле 2.1.296 креды из env-блока
до него не читаются (проверено).

**Desktop (GUI, аналог Kimi Code Desktop)** — не читает ни env, ни settings.json; только
Developer → Configure Third-Party Inference (после Help → Troubleshooting → Enable
Developer Mode). Подключено на этом ПК 14.10, работает. Особенности, все проверены:
1. **TLS на самом SGLang (с 14.10, v3.0.1-лист)**: `--ssl-keyfile /tls/server.key
   --ssl-certfile /tls/server.pem` в `tp2-serve-flash-next.sh` (launch_args, после
   watchdog-timeout), ро-маунт `~/penny/tls → /tls` в leaf `serve-penny-301.sh`.
   Порт прежний **:1234**, но теперь только https. CA/ключи: `/home/molochko/penny/tls/`
   (генерация `ops/tls/gen-certs.sh`; server cert 825 дней, SAN IP:192.168.2.127,
   IP:127.0.0.1, DNS:localhost — ключи не покидают хост). Публичный CA для клиентов:
   `certs/qwen-home-ca.pem` (этот репо). Раньше был SSH-туннель 127.0.0.1:1234 ради
   политики Desktop «http только на loopback» — **снят 14.10**, не нужен с серверным TLS.
2. **Watchdog** (`watch-vllm.sh`): `h()` переведён на https + `--cacert ~/penny/tls/ca.pem`;
   бэкап `watch-vllm.sh.bak-tls-1410`. Скрипты на хосте, дёргающие инфер через urllib,
   требуют `SSL_CERT_FILE=/home/molochko/penny/tls/ca.pem` в окружении; хостовые
   curl-провы — `--cacert`. Хозяин-ПК (Windows): CA в пользовательском Root-хранилище
   (`certutil -addstore -user Root`), Node-клиенты — `NODE_EXTRA_CA_CERTS`.
   Друзья по WG: `base_url` → `https://…/v1` + поставить `qwen-home-ca.pem` в доверенные
   корневые (шаблон `wireguard/friend-kimi-config.toml` обновлён).
   ПРАВИЛА, обожжённые 14.10: (а) добавили новый корневой CA — ВСЕ Electron/Chromium
   клиенты перезапустить полностью, на лету корень не подхватывается (на этом встал
   живущий Kimi Code сразу после переключения); (б) Windows curl(Schannel) ругается
   `CRYPT_E_NO_REVOCATION_CHECK` — у CA нет CRL, это норма, нужны `--ssl-no-revoke`
   (браузеры/Electron не душатся); (в) server cert до ~янв 2029 — перевыпуск:
   `ops/tls/gen-certs.sh` без `--force` пересоберёт только server-пару, CA и доверие
   клиентов не меняются; с `--force` — новый CA, тогда переустановка CA всем клиентам;
   (г) HEALTHCHECK образа v3.0.1 лупит http по :8001 и под TLS вечно показывает
   unhealthy — leaf теперь стартует с `--no-healthcheck`, здоровье меряет вотчдог;
   (д) sha256-пин `adaptive-next.json` в tp2 satisfies файл ВНУТРИ образа
   (/opt/pennyroyal/configs/pennyroyal/, не ~/penny — 14.10 клауд-агент дал фейк-алапт
   «следующий рестарт упадёт», проверено: sha в контейнере = пину). Правило: при СМЕНЕ
   ОБРАЗА pennyroyal сверять sha256sum этого файла с пином, не совпадёт — обновить пин
   осознанно (это и есть назначение пина — drift политики ловить до старта).
   Имена маршрутов (model id в клиентском профиле) промерены 14.10 CLI + Desktop на бою:
   серверу поле model безразлично, но по имени клиент выбирает профиль запросов.
   КАНОН для десктопа: `claude-opus-5-5` + effort любой (после алиас-патча и Max
   безопасен — гасится в xhigh); каталог v2225 знает это имя, effort-контрол есть. ВНИМАНИЕ про xhigh: Anthropic-слой sglang (serving.py:623-634)
   штатно роняет output_config.effort xhigh→max ("OpenAI Literal без xhigh"), а родной
   шаблон Qwen знал только xhigh/medium/low ⇒ Extra давал 500, а без effort шёл medium.
   14.10 накатано: (1) в chat_template.jinja (модельный каталог, бэкап .bak-effort-1410)
   алиасы max/high→xhigh, minimal→low — Extra теперь настоящий xhigh, любые имена
   маршрутов безопасны; (2) в leaf -e PENNY_REASONING_EFFORT=xhigh (хелпер tier'ов PR#18
   есть в 3.0.1) — дефолт effort по всему серверу xhigh вместо medium (шаг из давнего
   «staged right now» закрыт). SHABLON sha входит в NIXL-идентичность: пространство
   сменилось, первый прогон холодный. Старые сессии с запомненным «Max» — не реюзать.
3. **Model discovery показывает только «claude-*»-id** — не-claude модели фильтруются,
   пикер пустеет. Лечится фиксированным Model list (`inferenceModels`): route-id
   `claude-sonnet-4-5` (валидатор формы требует Anthropic-подобное имя; сервер field model
   игнорит), label `Qwen 3.8 Flash Next (local)`, supports1m=false, tier=opus/sonnet,
   maxEffort=xhigh, defaultModelEffort=xhigh.
4. Сохранённый конфиг: `%LOCALAPPDATA%\Claude-3p\configLibrary\*.json` (+ `host-creds-*.json`).
   Правка на живую приложением переписывается — менять при закрытом приложении, затем рестарт.
   Состояние видно в `%LOCALAPPDATA%\Claude-3p\logs\main.log`: `ConfigHealth … healthy`,
   `inference apiHost=http://127.0.0.1:1234`.
5. ОБОЛОЧКА приложения требует логин claude.ai (на этом ПК: alex Free) — inference при этом
   весь идёт на гейтвей, подписка на запросы не влияет; Remote Control/voice недоступны
   в 3P-режиме (это ожидаемо).
После ребута «Gateway unreachable» = сначала проверить туннель (`curl http://127.0.0.1:1234/v1/models`),
потом уже приложение. Приложение обновлено автоапдейтером до 2.37337.1 в тот же вечер.

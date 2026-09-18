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
| `--ple-offload-embedding` → `--no-ple-offload-embedding` | таблица PLE (47.7 ГиБ) в VRAM, как у нас всегда было; освобождает хостовую RAM |
| `--mem-fraction-static 0.981` → `0.88` | 0.981 — одноплатное значение; 0.88 наше проверенное |
| добавлен `--disable-custom-all-reduce` | **без него TP=2 падает**: `RuntimeError: invalid argument` в `get_graph_buffer_ipc_meta` |
| убран `--hicache-storage-backend nixl` и его конфиг | NIXL не инициализируется, `Failed to create NIXL backend` |
| `CONTEXT_LENGTH` 524288 → 786432, YaRN `factor` 2.0 → 3.0, добавлен `max_position_embeddings` | вернуть наш контекст |
| `--served-model-name pennyroyal` → `qwen3.8-flash-next` | чтобы opencode не трогать |
| `--max-mamba-cache-size` 24 → 64 | снять потолок в 4 одновременных запроса; 64 подобрано 17.09 |
| подменён `chat-template.sh` | вернуть родной шаблон чекпоинта вместо Froggeric |

## Боевая команда (как разрешилась)

```
--tp 2 --disable-custom-all-reduce --dtype bfloat16 --quantization modelopt_fp4
--kv-cache-dtype fp8_e4m3 --mem-fraction-static 0.88 --context-length 786432
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

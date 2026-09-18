# ARCHITECTURE — устройство стека

> **УСТАРЕЛО С 16.09.2026.** Прод переведён на контейнер Pennyroyal v2.5,
> TP=2 — см. [current-config.md](current-config.md). Этот документ описывает
> наш форк SGLang, который остаётся рабочим запасным вариантом и поднимается
> откатом `echo serve-fn-bf16mtp.sh > active.cfg && sudo systemctl restart sglang`.

## Железо и хост

`192.168.2.127`, пользователь `molochko`, вход по ключу `~/.ssh/molochko_deploy`.
Две **RTX PRO 6000 Blackwell по 96 ГБ** (sm120, GDDR7 ~1.8 ТБ/с каждая).
**NVLink между картами НЕТ** — обмен через PCIe, топология `SYS`. Это ключевой
факт: любой all-reduce между картами дорог по задержке.

Хостовой памяти 89 ГБ, из них кеши занимают ~48, свободно ~25.
Диск: NVMe, под моделями ~2.3 ТБ свободно.
IOMMU выключен (`amd_iommu=off` в GRUB) — без этого P2P между картами висел
(NCCL issue #1999). Проверка: `nvidia-smi topo -p2p r` должна давать OK.

## Модель

`/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp` — **собранная нами**
из весов NVIDIA и канонического BF16-MTP. Каталог состоит из символьных ссылок на
файлы `Qwen3.8-Flash-Next-NVFP4-official` плюс три своих файла: новый MTP-шард,
индекс и конфиг. Веса NVIDIA не изменены ни на байт (сверено по inode).

Архитектура `Qwen4ExpForConditionalGeneration`: 125B всего / **6B активных**,
MoE на 512 экспертов (10+1 на токен), 48 слоёв — 36 линейных GDN + 12 полного
внимания QSA. Плюс таблица PLE n-gram на 20 млн строк (51B параметров, 47.7 ГиБ
в FP8), которая лежит в VRAM.

Квантование смешанное (`quant_algo: MIXED_PRECISION`): эксперты NVFP4 group-16,
PLE в FP8, проекции внимания — **BF16, 294 тензора на 5.23 ГиБ** (запас на будущее,
см. notes).

## Движок

SGLang из форка `/home/molochko/sglang-qwen4` — git worktree ветки
`qwen4-main-squashed-rebased`, коммит `eb7725ef78` от 04.09.2026. Upstream эту
архитектуру ещё не поддерживает.
Версии: flashinfer **0.6.17**, sgl_kernel 0.4.6.post1, torch 2.13.0+cu130.

### Наши правки в движке

| файл | что и зачем |
|---|---|
| `qsa/sparse_attn.py` | 4 строки: `tl.load(...).to(q_values.dtype)` в обоих ядрах префила. Без этого fp8 KV роняет сервер. Бэкап `.bak.fp8qsa` |
| `qwen3_5_mtp.py` | патч `_mtp_quant_config` (эффекта не дал, оставлен) |
| разное | `exist_ok=True` в регистрации конфигов (обязательно с transformers 5.16.1) |

## Запуск и супервизор

```
systemd sglang.service
  └─ ExecStart: /home/molochko/vllm/serve-active.sh   (простой диспетчер)
       └─ читает active.cfg → serve-fn-bf16mtp.sh       (боевой скрипт)
```

**Замок от двоевластия.** В каждом из 12 `serve-*.sh` стоит flock на
`/home/molochko/vllm/.launch.lock`: кто первый взял — работает, второй выходит с
exit 0, никого не убивая. Замок лежит именно в leaf-скриптах, а **не** в
диспетчере, потому что `watch-vllm.sh` зовёт их напрямую и диспетчер обходит.

`vllm-watchdog.service` — `Restart=always`, `disabled`. Если поднять его руками,
убитый процесс воскреснет через 10 секунд. Его логика: раз в 20 секунд проверяет
порт и при молчании поднимает инстанс — а модель грузится минутами, отсюда и
завалы в прошлом.

## Боевые параметры

```
--tp 2                              две карты
--mem-fraction-static 0.86          НЕ поднимать, см. notes про пик префила
--kv-cache-dtype fp8_e4m3           только вместе с патчем sparse_attn.py
--context-length 786432             + YaRN factor 3.0 через json-model-override-args
--speculative-algorithm NEXTN       (алиас к EAGLE в этой сборке)
--speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4
--moe-runner-backend flashinfer_cutlass    единственный работающий для NVFP4
--disable-custom-all-reduce         обязательно, custom падает
--expert-parallel-size 2            +2%
--no-ple-offload-embedding          таблица PLE в VRAM
--enable-hierarchical-cache --hicache-size 24
--max-running-requests 8
--chunked-prefill-size 16384 --max-prefill-tokens 32768
--reasoning-parser qwen3 --tool-call-parser qwen3_coder
```

## Рабочие показатели

| | |
|---|---|
| скорость, один поток | ~243 tok/s |
| accept спекулятива | 2.9–3.1 (потолок 4) |
| KV-пул | 1 655 296 токенов |
| hicache в хосте | 1 464 960 токенов, 19.5 ГБ на ранг |
| VRAM под нагрузкой | 89 178 MiB (87.1 ГиБ) из 97 887 |
| 4 агента | 465 tok/s суммарно, 125 на поток |

## Клиент

opencode на десктопе, `C:\Users\abcd\.config\opencode\opencode.jsonc`.
Провайдер `local` → `http://192.168.2.127:1234/v1`, модель `qwen3.8-flash-next`.
Параметры: `temperature 1.0, top_p 0.95, top_k 20`, `reasoning_effort: xhigh`.

## Соседние каталоги на хосте

| путь | что |
|---|---|
| `/home/molochko/vllm/` | скрипты запуска, active.cfg, watchdog, наши тесты `_*.py` |
| `/home/molochko/vllm-models/` | чекпоинты |
| `/home/molochko/sglang-qwen4/` | форк движка (worktree) |
| `/home/molochko/sglang/env/` | venv с python 3.12 |
| `/home/molochko/specforge/` | обучение EAGLE-головы (заброшено, см. notes) |
| `/home/molochko/train-data/FINDINGS-2026-09-04.md` | разбор по обучению головы |

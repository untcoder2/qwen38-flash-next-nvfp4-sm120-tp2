# specforge-ext — офлайн-тренинг MTP-головы Qwen3.8-Flash-Next под SpecForge

Расширение SpecForge (`sgl-project/SpecForge`, main) для дообучения нативной
MTP-головы целевой модели **Qwen3.8-Flash-Next** (arch `qwen4_exp` /
`Qwen4ExpForConditionalGeneration`, чекпойнт
`/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp`, NVFP4 + bf16-MTP).
Ничего в SpecForge и в форке sglang не редактируется: всё новое — в этом
каталоге, подключение через `install()`.

Отличия от встроенного `training.strategy=mtp` (он заточен под dense
`Qwen3_5MTPDraftModel`):

| | встроенный mtp | здесь (`mtp_hc`) |
|---|---|---|
| фича цели | `target_last_hidden_states` (post-norm, 2560) | `target_hc_hidden_states` (сырой HC-поток, `hc_count*hidden`=10240) |
| фузия входа | `fc = Linear(2H→H)` по `cat(emb, hidden)` | `fc_embedding` + `fc_hidden`: эмбеддинг аддитивно инжектится в каждую из 4 веток HC-потока (зеркало serving `_fuse_residual_linear_shared`) |
| слой | dense Qwen3 MLP | 1× full_attention + HC-mixers (GatedResidual) + MoE 512 экспертов top-10 + shared expert + QSA-indexer (веса, в трейне не участвует) |
| lm_head | общий, заморожен | общий `shared_head` цели, заморожен (`share_lm_head: true`) |

Структура:

```
specforge-ext/
├── qwen4_exp_mtp_draft.py        # класс Qwen4ExpMTPDraft, @register_draft
├── configs/qwen4-exp-flash-next-mtp.json
├── shim/
│   ├── __init__.py               # install(): draft-реестр + стратегия mtp_hc
│   ├── mtp_hc_algorithm.py       # AlgorithmSpec/providers для mtp_hc
│   ├── capture_hc.py             # серверный HC capture-hук (in-process SGLangRunner)
│   ├── normalizers.py            # офлайн-схема target_hc_hidden_states
│   ├── prepare_hc.py             # обёртка scripts/prepare_hidden_states.py
│   ├── train_hc.py               # обёртка specforge.cli (train)
│   └── merge_hc.py               # merge в базу (принимает strategy=mtp_hc)
├── selftest.py                   # CPU-самопроверка (18 check'ов)
└── .upstream/                    # read-only клон SpecForge для selftest (на сервере не нужен)
```

## 1. Установка на сервере (хост 192.168.2.127)

Работаем в venv форка sglang (том, который обслуживает serving), чтобы
`import sglang` разрешался в pennyroyal-форк, а не в апстрим:

```bash
cd ~
git clone https://github.com/sgl-project/SpecForge specforge
cd ~/specforge
# в активированном venv форка:
pip install -e . --no-deps            # не трогаем pinned torch/sglang вилки
pip install accelerate click datasets safetensors tqdm \
    "transformers==<версии, совместимой с вилкой SpecForge: см. pyproject>"
# (полный diff зависимостей: `python -c "import specforge.cli"` по шагам; 
#  --no-deps сознательно, докидываем только отсутствующее)
```

Копирование этого каталога (с Windows: `git clone` репозитория D:\llm или
scp; имя каталога — с подчёркиванием, это Python-пакет):

```bash
cp -r /path/to/specforge-ext ~/specforge/specforge_ext
# .upstream на сервере не нужен — remove его, если копировался:
rm -rf ~/specforge/specforge_ext/.upstream
```

Проверка (CPU, без GPU) — на сервере selftest пройдёт по «настоящему»
imпорту SpecForge (seam=`installed`):

```bash
cd ~/specforge && python specforge_ext/selftest.py   # ждём SELFTEST: OK
```

## 2. Фичи для офлайна (capture)

**Готовые датасеты** — как в стандартном пайплайне SpecForge:

```bash
python scripts/prepare_data.py --dataset sharegpt   # -> ./cache/datasets/...
```

**Capture HC-потока** (наш backend вместо post-norm last_hidden):

```bash
cd ~/specforge
python -m specforge_ext.shim.prepare_hc \
    --target-model-path /home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp \
    --data-path ./cache/datasets/sharegpt/... \
    --output-path ./cache/hidden_states/qwen38-flash-next-mtp \
    --draft-model-config ./specforge_ext/configs/qwen4-exp-flash-next-mtp.json \
    --strategy mtp_hc \
    --chat-template qwen --max-length 2048 --batch-size 8 --tp-size 1 \
    --sglang-mem-fraction-static 0.4
```

Механика (`shim/capture_hc.py`): таргет поднимается in-process через
замороженный `SGLangRunner` SpecForge; каждый `LogitsProcessor` подменяется
`OfflineHCLogitsProcessor`, который после стандартного offline-вызова
(`_get_pruned_states` в DECODE-режиме = все токены, без lm-head) кладёт в
поле `last_hidden_states` буфер `Qwen4ExpVLModel.last_hc_hidden_states` —
тот же самый HC-поток шириной 10240, который serving подаёт голове в
`forward_batch.spec_info.hidden_states`. `set_dflash_layers_to_capture` для
этой цели сознательно no-op: форк-цепочка `Qwen4ExpModel.forward` на
non-idle ветке возвращает `(hidden, hc)` и aux-тензоры не отдаёт (включение
aux-capture сломало бы анпакинг в Qwen3VL).

После прогона выбрать 1 файл и проверить:

```python
import torch
d = torch.load("cache/hidden_states/qwen38-flash-next-mtp/...000000.ckpt")
assert set(d) == {"input_ids", "loss_mask", "target_hc_hidden_states"}
assert d["target_hc_hidden_states"].shape[-1] == 10240
assert not torch.isnan(d["target_hc_hidden_states"]).any()
```

## 3. Тренинг

Run-config (yaml), по образцу `examples/configs/offline/colocated/*-offline.yaml`:

```yaml
model:
  target_model_path: /home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp
  draft_model_config: ./specforge_ext/configs/qwen4-exp-flash-next-mtp.json
  target_backend: sglang
  torch_dtype: bfloat16
  # ключи подтверждены по model.safetensors.index.json живого чекпойнта:
  embedding_key: model.language_model.embed_tokens.weight
  lm_head_key: lm_head.weight

data:
  hidden_states_path: ./cache/hidden_states/qwen38-flash-next-mtp
  max_length: 2048
  chat_template: qwen
  cache_dir: ./cache

training:
  strategy: mtp_hc
  num_epochs: 10
  batch_size: 1
  learning_rate: 6e-4
  max_grad_norm: 0.5
  attention_backend: sdpa      # capabilities: eager|sdpa
  mtp_objective_chunk_size: 4096
  save_interval: 1000
  dist_timeout: 20
  seed: 0

run_id: qwen38-flash-next-mtp-hc
output_dir: ./outputs/qwen38-flash-next-mtp-hc

deployment:
  mode: local_colocated
  trainer:
    nnodes: 1
    nproc_per_node: 1
```

Запуск (одна GPU — напрямую; несколько — torchrun'ом по схеме SpecForge):

```bash
cd ~/specforge
python -m specforge_ext.shim.train_hc train -c run-qwen4-exp-mtp-hc.yaml
# или: torchrun --nproc_per_node=2 -m specforge_ext.shim.train_hc train -c ...
# (offline-потребители требуют training.tp_size=1 — валидатор это проверяет)
```

Что должно быть в логе первого старта:

1. `[mtp] initialized 31 native mtp.* weights from ... (native-MTP fine-tune).`
   — строгая загрузка 31 ключа из нативной головы чекпойнта (не рандом);
2. `[mtp] shared target embed_tokens/lm_head with the draft (frozen).`;
3. стартовый `loss ≈ ln(248320) ≈ 12.4` только при ЛОЖНОМ старте с нуля —
   при корректном fine-tune стартовый loss заметно ниже, accuracy nonzero.

## 4. Merge обратно в базу

```bash
python -m specforge_ext.shim.merge_hc \
    --base-model-path /home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp \
    --mtp-checkpoint-path ./outputs/qwen38-flash-next-mtp-hc/<RUN>-latest \
    --draft-config ./specforge_ext/configs/qwen4-exp-flash-next-mtp.json \
    --output-path ~/models/Qwen3.8-Flash-Next-NVFP4-bf16mtp-ft \
    --key-format sglang
```

Пишется нативный плоский layout `mtp.*` (ровно те же 31 имя/dtype, что в
исходном чекпойнте) + backfill `mtp.embed_tokens.weight` / `mtp.lm_head.weight`
(общие веса, которые serving instantiate сам). Форк-загрузчик
`Qwen4ExpForCausalLMMTP.load_weights` маппит `mtp.* → model.*`, стрипит
`.self_attn`, складывает q/k/v в `qkv_proj` и `experts.gate_up_proj/down_proj`
в `w13_weight/w2_weight` — всё это имена из нашего контракта.

## 5. Допущения (подтвердить при первом боевом прогоне)

- **Структура/dtype головы** взяты из живого индекса чекпойнта (31 ключ,
  все BF16) и из кода форка ветки `pennyroyal-main-sm120-final`
  (`qwen4_exp_mtp.py`, `qwen4_exp.py`, `hyperconnection.py`, `qsa_indexer.py`).
  `fc_hidden.weight [2560,2560]` — НЕ половина `Linear(2H,H)`: при hc_count=4
  фузия резидентная (`emb.unsqueeze(-2) + fc_hidden(hc.view(...,4,H))`), а
  одинарная `fc` бывает только при hc_count≤1. Проверено selftest'ом.
- **Router MoE**: softmax по всем 512 → top-10 → ренормировка
  (`norm_topk_prob=true`), как `RoutingMethodType.RenormalizeNaive` в serving.
  Численная точность: serving гоняет topk-маршрутизацию в своих ядрах; при
  расхождении accept-rate свериться с dense-референсом.
- **QSA-indexер** загружается (3 ключа из 31) но в тренировочном forward не
  вызывается: обучение — dense causal attention по префиксу. Serving при этом
  на draft-extend переиспользует target-aligned выборку индексов
  (`should_reuse_mtp_sparse_indices`), т.е. обучаемая плотная голова остаётся
  совместимой, но это главное семантическое расхождение — смотреть
  accept-length в serving после merge.
- **Гипер-связи**: `params_dtype=torch.bfloat16` у GatedResidual хардкодится,
  как в форке; HC-нормы — grouped GemmaRMSNorm по 2560 на ветку; все GEMM
  смешанной точности идут через внутреннее fp32 в нормах.
- **HC-фича в capture** — `last_hc_hidden_states` ДО финального миксера цели
  (ровно то, что serving кладёт в `logits_output.hidden_states`). Если форк
  изменит место съёма — assert по ширине 10240 в capture_hc поймает это.
- **mtp_hc — своя стратегия**, поэтому `scripts/merge_mtp_to_base.py` (принимает
  только `strategy=="mtp"`) заменён тонкой обёрткой `shim/merge_hc.py`.
- **online/streaming** (`mode=disaggregated`, server-capture SGLang) не
  проверялся — контракт зарегистрирован симметрии ради, путь не тестировался.
- Чекпойнт таргета — multimodal (`language_model_only: false`); capture
  прогоняет ТОЛЬКО текстовые пачки (как весь offline-путь SpecForge). Промпты
  с image-токенами в датасете давать нельзя.

## 6. Риски первого боевого прогона — чеклист

1. `pip install -e --no-deps` vs вилки SpecForge: если `import specforge.cli`
   падает по отсутствующей зависимости — докинуть явно (список в pyproject,
   torch/sglang не переустанавливать).
2. Первый capture — на 2–3 сэмплах: убедиться, что HC-буфер не None
   (`Qwen4ExpHCUnavailable` ловит), `.ckpt` имеет ширину 10240, NaN-фильтр
   (`_save_tensor_sync` скипает NaN-записи) не повыкидывал всё.
3. Проверить, что SGLang-сервер на хосте в это время не грузится генерацией:
   capture — полноценный prefill всей модели на GPU (mem_fraction 0.4).
4. Accept-rate после merge мерить на реальном трафике: dense-vs-QSA
   расхождение (см. допущения) проявится только там.
5. `target_hc_hidden_states` — [seq, 10240] bf16 ≈ 20 КБ/токен: датасет на
   100k×2k токенов ≈ 4 ТБ без `--compress`; на диске ~/ это может не поместиться,
   считать объём до запуска (`--compress` экономит ~2× на таких тензорах).

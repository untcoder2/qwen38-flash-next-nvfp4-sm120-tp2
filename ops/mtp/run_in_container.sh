#!/bin/bash
# ВНУТРИ контейнера pennyroyal v3.0.1: capture -> train -> merge.
# Пишет этапы в /work/steps.log; любой сбой = ненулевой выход (хост поднимет прод).
set -uo pipefail
export PATH=/opt/pennyroyal/.venv/bin:$PATH
export PYTHONPATH=/work/pylibs:/work/SpecForge:${PYTHONPATH:-}
export PYTHONUNBUFFERED=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export TRANSFORMERS_VERBOSITY=error HF_HUB_OFFLINE=1
# численная консистентность с севом: GDN prefill FP16-accum + bf16 conv-state
export FLASHINFER_GDN_FP16_ACCUM_MMA=1 SGLANG_MAMBA_CONV_DTYPE=bfloat16 SGLANG_SM120_ONLINE_MXFP8=true
MODEL=/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp
DRAFTCFG=/work/SpecForge/specforge_ext/configs/qwen4-exp-flash-next-mtp.json
cd /work/SpecForge
S(){ echo "[steps] $(date -Is) $*" | tee -a /work/steps.log; }

S "specforge через PYTHONPATH (venv образа root-owned, pip -e невозможен)"
mkdir -p /work/pylibs
MISSING=$(python -c "
import importlib
mods=['click','datasets','accelerate','tqdm','tensorboard','pyarrow','dill','multiprocess','xxhash']
need=[]
for m in mods:
    try: importlib.import_module(m)
    except Exception: need.append(m)
print(' '.join(need))")
S "отсутствующие деps: ${MISSING:-none}"
if [ -n "$MISSING" ]; then
  pip install -q --no-deps --target /work/pylibs $MISSING >>/work/steps.log 2>&1
  MISSING2=$(python -c "
import importlib
bad=[]
for m in '$MISSING'.split():
    try: importlib.import_module(m)
    except Exception: bad.append(m)
print(' '.join(bad))")
  [ -n "$MISSING2" ] && { S "FATAL deps не подошли: $MISSING2"; exit 11; }
fi
python -c "import specforge.cli" || { S "FATAL import specforge.cli"; python -c "import specforge.cli" 2>&1 | tail -6 | tee -a /work/steps.log; exit 12; }

S "selftest (CPU)"
python specforge_ext/selftest.py 2>&1 | tail -3 | tee -a /work/steps.log || { S "FATAL selftest"; exit 13; }

S "chat-template probe"
TPL=$(python - <<'PY' 2>/dev/null | tail -1
try:
    from specforge.data.template import TEMPLATE_REGISTRY as R
    ks=list(R.registry.keys()) if hasattr(R,'registry') else []
except Exception:
    try:
        import specforge.data.template as t
        ks=[k for k in dir(t) if 'qwen' in k.lower()]
    except Exception:
        ks=[]
cand=[k for k in ks if 'qwen3.5' in str(k)] or [k for k in ks if 'qwen3' in str(k)] or [k for k in ks if 'qwen' in str(k)] or ['qwen']
print(cand[0])
PY
)
TPL=${TPL:-qwen}; S "template=$TPL"

S "prepare_data sharegpt (capture-safe: режем до первого human)"
python /work/filt_obs.py /work/corpus.jsonl /work/corpus-f.jsonl 2>>/work/steps.log
NF=$(wc -l < /work/corpus-f.jsonl); S "episodes после human-first-фильтра: $NF"
[ "$NF" -ge 500 ] || { S "FATAL мало human-first эпизодов"; exit 14; }
OUTJSON=""
if python scripts/prepare_data.py --dataset sharegpt --data-path /work/corpus-f.jsonl --output-path ./cache/dataset >>/work/steps.log 2>&1; then
  OUTJSON=$(find ./cache/dataset -name '*.jsonl' | head -1)
else
  S "prepare c tool-ролями упал — фолбэк чисто human/gpt"
  grep -v '"from":"tool"' /work/corpus-f.jsonl > /work/corpus-hg.jsonl || python - <<'PY'
import json
for line in open('/work/corpus-f.jsonl',encoding='utf-8'):
    r=json.loads(line)
    r['conversations']=[c for c in r['conversations'] if c['from'] in ('human','gpt','system')]
    print(json.dumps(r,ensure_ascii=False))
PY
  python scripts/prepare_data.py --dataset sharegpt --data-path /work/corpus-hg.jsonl --output-path ./cache/dataset-hg >>/work/steps.log 2>&1 || { S "FATAL prepare_data"; exit 14; }
  OUTJSON=$(find ./cache/dataset-hg -name '*.jsonl' | head -1)
fi
[ -n "$OUTJSON" ] || { S "FATAL пустой OUTJSON"; exit 14; }
S "dataset json: $OUTJSON"

CAP() { # $1=mem-frac $2=num-samples $3=outdir $4=batch
  torchrun --standalone --nproc_per_node=2 -m specforge_ext.shim.prepare_hc \
    --target-model-path "$MODEL" --data-path "$OUTJSON" \
    --output-path "$3" --draft-model-config "$DRAFTCFG" \
    --strategy mtp_hc --chat-template "$TPL" --trust-remote-code \
    --sglang-attention-backend flashinfer \
    --max-length 4096 --num-samples "$2" --tp-size 2 --batch-size "$4" \
    --sglang-mem-fraction-static "$1" --compress
}
S "capture smoke (3, mem .70)"
if ! CAP 0.70 3 ./cache/hs-smoke 1 >>/work/steps.log 2>&1; then
  S "smoke@.70 упал — пробую .78"; CAP 0.78 3 ./cache/hs-smoke 1 >>/work/steps.log 2>&1 || {
    S "smoke@.78 упал — .85"; CAP 0.85 3 ./cache/hs-smoke 1 >>/work/steps.log 2>&1 || { S "FATAL capture-smoke"; tail -12 /work/steps.log; exit 15; }; }
fi
python - <<'PY' || { S "FATAL smoke-features"; exit 16; }
import torch,glob
fs=sorted(glob.glob("cache/hs-smoke/**/*.ckpt",recursive=True))
assert fs, "нет .ckpt"
d=torch.load(fs[0],map_location="cpu")
assert set(d)>= {"input_ids","loss_mask","target_hc_hidden_states"}, d.keys()
h=d["target_hc_hidden_states"]
assert h.shape[-1]==10240, h.shape
assert not torch.isnan(h).any(), "NaN в фичах"
print(f"smoke ok: {len(fs)} файл(ов), форма {tuple(h.shape)}")
PY

S "capture полный прогон (2024 эпизода, batch 8)"
N=$(wc -l < /work/corpus-f.jsonl)
CAP 0.78 "$N" ./cache/hs 8 >>/work/steps.log 2>&1 || { S "FATAL capture-full"; tail -12 /work/steps.log; exit 17; }
S "features на диске: $(du -sh ./cache/hs | cut -f1)"

S "training (yaml run-mtp-hc.yaml, DP2)"
if torchrun --standalone --nproc_per_node=2 -m specforge_ext.shim.train_hc train -c run-mtp-hc.yaml >> /work/train.log 2>&1; then
  S "train DP2 ok"
else
  rc=$?; tail -8 /work/train.log | tee -a /work/steps.log
  S "DP2 упал (rc=$rc) — retries DP1"
  sed -i 's/nproc_per_node: 2/nproc_per_node: 1/' run-mtp-hc.yaml
  torchrun --standalone --nproc_per_node=1 -m specforge_ext.shim.train_hc train -c run-mtp-hc.yaml >> /work/train.log 2>&1 || { S "FATAL train"; tail -15 /work/train.log | tee -a /work/steps.log; exit 18; }
  S "train DP1 ok"
fi
grep -m3 -E "loss" /work/train.log | tail -3 | tee -a /work/steps.log

S "merge -> /work/out/flash-next-mtp-r1"
LATEST=$(ls -d outputs/qwen38-flash-next-mtp-hc/*-latest 2>/dev/null | head -1)
[ -n "$LATEST" ] || LATEST=$(ls -d outputs/*/*-latest 2>/dev/null | head -1)
[ -n "$LATEST" ] || { S "FATAL merge: нет чекпойнта"; ls -R outputs | tail -10 | tee -a /work/steps.log; exit 19; }
S "чекпойнт: $LATEST"
python -m specforge_ext.shim.merge_hc \
  --base-model-path "$MODEL" \
  --mtp-checkpoint-path "$LATEST" \
  --draft-config "$DRAFTCFG" \
  --output-path /work/out/flash-next-mtp-r1 \
  --key-format sglang >>/work/steps.log 2>&1 || { S "FATAL merge"; tail -10 /work/steps.log; exit 20; }
[ -f /work/out/flash-next-mtp-r1/config.json ] || { S "merge не записал config.json — копирую базовый"; cp "$MODEL/config.json" /work/out/flash-next-mtp-r1/config.json; }
S "merged: $(du -sh /work/out/flash-next-mtp-r1 | cut -f1)"
S CONTAINER-ALL-OK

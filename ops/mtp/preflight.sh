#!/bin/bash
# Префлайт БЕ GPU при живом проде: все CPU-шаги pipeline + dry-аргументы capture.
set -uo pipefail
export PATH=/opt/pennyroyal/.venv/bin:$PATH
export PYTHONPATH=/work/pylibs:/work/SpecForge:${PYTHONPATH:-}
export PYTHONUNBUFFERED=1 TRANSFORMERS_VERBOSITY=error HF_HUB_OFFLINE=1
cd /work/SpecForge
S(){ echo "[pre] $*"; }
mkdir -p /work/pylibs
MISSING=$(python -c "
import importlib.util as u
mods=['click','datasets','accelerate','tqdm','tensorboard','pyarrow','dill','multiprocess','xxhash']
print(' '.join(m for m in mods if u.find_spec(m) is None))")
S "missing: ${MISSING:-none}"
[ -n "$MISSING" ] && pip install -q --no-deps --target /work/pylibs $MISSING
python -c "import specforge.cli; print('specforge.cli OK')" || exit 12
[ -f specforge_ext/selftest.py ] || { S "FATAL specforge_ext/selftest.py отсутствует"; exit 13; }
python specforge_ext/selftest.py >/work/pre-selftest.log 2>&1 && S "selftest OK" || { S "FATAL selftest"; tail -8 /work/pre-selftest.log; exit 14; }
TPL=$(python - <<'PY' 2>/dev/null | tail -1
try:
    from specforge.data.template import TEMPLATE_REGISTRY as R
    ks=list(R.registry.keys()) if hasattr(R,'registry') else list(getattr(R,'_registry',{}).keys()) if hasattr(R,'_registry') else []
except Exception:
    ks=[]
cand=[k for k in ks if 'qwen3.5' in str(k)] or [k for k in ks if 'qwen' in str(k)] or ['qwen']
print(cand[0])
PY
)
S "template=$TPL"
python scripts/prepare_data.py --help >/work/pre-prepare-help.log 2>&1 || { S "prepare_data --help не вывел"; }
grep -oE "\-\-[a-z-]+" /work/pre-prepare-help.log | sort -u | head -20 > /work/pre-prepare-flags.txt
if python scripts/prepare_data.py --dataset sharegpt --data-path /work/corpus.jsonl --output-path ./cache/pre-dataset >>/work/steps.log 2>&1; then
  OUTJSON=$(find ./cache/pre-dataset -name '*.jsonl' | head -1); S "prepare_data OK: $OUTJSON"
else
  S "prepare_data(полный) упал — фолбэк без obs"
  python /work/filt_obs.py /work/corpus.jsonl /work/corpus-hg.jsonl
  python scripts/prepare_data.py --dataset sharegpt --data-path /work/corpus-hg.jsonl --output-path ./cache/pre-dataset2 >>/work/steps.log 2>&1 && OUTJSON=$(find ./cache/pre-dataset2 -name '*.jsonl' | head -1) && S "fallback OK: $OUTJSON" || { S "FATAL prepare_data оба варианта"; tail -10 /work/steps.log; exit 15; }
fi
[ -n "${OUTJSON:-}" ] && python - "$OUTJSON" <<'PY'
import json,sys
f=open(sys.argv[1],encoding='utf-8')
r=json.loads(f.readline())
c=r.get('conversations',r if isinstance(r,list) else [])
print('sample roles:', [x.get('from') or x.get('role') for x in c][:6])
print('PRE-FLIGHT-ALL-OK')
PY

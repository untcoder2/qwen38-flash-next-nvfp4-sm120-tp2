#!/bin/bash
# Префлайт-2: CPU-проверка тех шагов, что в прошлом прогоне не дошли.
set -uo pipefail
export PATH=/opt/pennyroyal/.venv/bin:$PATH
export PYTHONPATH=/work/pylibs:/work/SpecForge:${PYTHONPATH:-}
export HF_HUB_OFFLINE=1 TRANSFORMERS_VERBOSITY=error
cd /work/SpecForge
mkdir -p /work/pylibs
[ -d /work/pylibs/accelerate ] || pip install -q --no-deps --target /work/pylibs accelerate tensorboard
S(){ echo "[p2] $*"; }

S "1) импорты шима"
python - <<'PY'
import importlib
for m in ("specforge_ext.shim","specforge_ext.shim.mtp_hc_algorithm","specforge_ext.shim.capture_hc",
          "specforge_ext.shim.normalizers","specforge_ext.qwen4_exp_mtp_draft"):
    try:
        importlib.import_module(m); print("  ok",m)
    except Exception as e:
        print("  FAIL",m,repr(e)[:200]); raise SystemExit(31)
# install() must register strategy mtp_hc
import specforge_ext.shim as sh
sh.install()
from specforge.algorithms.builtin import builtin_algorithm_registry as B
reg = B() if callable(B) else B
n = getattr(reg, "names", None)
names = list(n() if callable(n) else (n or []))
print("  registry names:", names)
assert "mtp_hc" in names, "mtp_hc НЕ в реестре"
print("  mtp_hc registered: True")
PY
[ $? -eq 0 ] || exit 31

S "2) prepare_hc --help"
python -m specforge_ext.shim.prepare_hc --help 2>&1 | grep -oE "\-\-[a-z0-9-]+" | sort -u | head -40 > /work/p2-prepare-flags.txt
for f in target-model-path data-path output-path draft-model-config strategy chat-template max-length num-samples tp-size batch-size sglang-mem-fraction-static compress; do
  grep -qx -- "--$f" /work/p2-prepare-flags.txt || { S "  MISSING flag --$f"; cat /work/p2-prepare-flags.txt; exit 33; }
done
S "  all capture flags present"

S "3) merge_hc --help"
python -m specforge_ext.shim.merge_hc --help 2>&1 | grep -oE "\-\-[a-z0-9-]+" | sort -u | head -20 > /work/p2-merge-flags.txt
for f in base-model-path mtp-checkpoint-path draft-config output-path key-format; do
  grep -qx -- "--$f" /work/p2-merge-flags.txt || { S "  MISSING merge flag --$f"; cat /work/p2-merge-flags.txt; exit 34; }
done
S "  all merge flags present"

S "4) YAML против схемы SpecForge"
python - <<'PY'
import yaml,sys
try:
    from specforge.config import load_config  # вероятная точка входа
except Exception:
    try:
        from specforge.config.schema import RunConfig
        load_config = lambda p: RunConfig.model_validate(yaml.safe_load(open(p)))
    except Exception as e:
        print("  no typed loader:",e); raise SystemExit(0)
try:
    cfg = load_config("run-mtp-hc.yaml")
    print("  config validated; strategy:", getattr(cfg.training,'strategy',None))
except Exception as e:
    print("  VALIDATION FAIL:", repr(e)[:400]); raise SystemExit(35)
PY
[ $? -eq 0 ] || exit 35

S "5) датасет уже приготовлен? переиспользуем"
ls cache/pre-dataset/sharegpt_train.jsonl >/dev/null 2>&1 && S "  есть (префлайт-1)" || { python scripts/prepare_data.py --dataset sharegpt --data-path /work/corpus.jsonl --output-path ./cache/dataset >/work/steps.log 2>&1 || { S "FATAL prepare"; tail -8 /work/steps.log; exit 36; }; }
S "PREFLIGHT-2-ALL-OK"

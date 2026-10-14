#!/bin/bash
set -uo pipefail
export PATH=/opt/pennyroyal/.venv/bin:$PATH
export PYTHONPATH=/work/pylibs:/work/SpecForge:${PYTHONPATH:-}
mkdir -p /work/pylibs
MISSING=$(python -c "
import importlib.util as u
mods=['click','datasets','accelerate','tqdm','tensorboard','pyarrow','dill','multiprocess','xxhash']
print(' '.join(m for m in mods if u.find_spec(m) is None))")
[ -n "$MISSING" ] && pip install -q --no-deps --target /work/pylibs $MISSING
cd /work/SpecForge
python - <<'PY'
from specforge_ext.shim import install
install()
from transformers import AutoConfig
c = AutoConfig.from_pretrained("/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp")
assert c.model_type == "qwen4_exp"
print("FIX-VERIFIED model_type=", c.model_type)
PY

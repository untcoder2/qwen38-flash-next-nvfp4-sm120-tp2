#!/usr/bin/env python3
import subprocess, sys
P = "/home/molochko/vllm/serve-penny-252.sh"
BAK = P + ".bak-trv"
import shutil; shutil.copy(BAK, P)
src = open(P).read()
assert "TRANSFORMERS_VERBOSITY" not in src
needle = "  -e SGLANG_SM120_ONLINE_MXFP8=true \\\n"
assert src.count(needle) == 1
src = src.replace(needle, needle + "  -e TRANSFORMERS_VERBOSITY=error \\\n")
open(P, "w", newline="").write(src)
r = subprocess.run(["bash", "-n", P], capture_output=True, text=True)
print("bash -n rc:", r.returncode, r.stderr[:200])
sys.exit(r.returncode)

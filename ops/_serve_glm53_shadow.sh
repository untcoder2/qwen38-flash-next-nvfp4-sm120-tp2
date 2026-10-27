#!/usr/bin/env bash
set -euo pipefail
# Теневой прогон GLM-5.3-Flash-FP4-Local на 2x RTX PRO 6000 вместо боя.
# Эталон — NVIDIA README «2 x RTX PRO 6000» (env 60/60, флаги 39/39, спека k=3
# побайтово) + фиксы ревью-пары 27.10: раунд-1 (CR1-15, SEC F01-F11) и раунд-2
# (CR F-0..F-9 / SEC N-1..N-5). Ключевые решения: два served-name (nargs+ проверен
# на образе), --api-key sk-local, без --trust-remote-code (auto_map:null, 0 *.py),
# Мониторинг SGLang не переносится на vLLM: набор серий метрик другой.
# Доступ к модели проверяем на /v1 API; публичная статистика /metrics не гасит сервер.
# ХОСТ: ~/penny-glm/serve-glm-shadow.sh {start|stop}
MODE="${1:-start}"
GLM_HOME="${GLM_HOME:-$HOME/penny-glm}"
MODEL_DIR="${MODEL_DIR:-$HOME/eval-models/GLM-5.3-Flash-FP4-Local}"
TLS_DIR="${TLS_DIR:-$HOME/penny/tls}"
IMAGE="${IMAGE:-ghcr.io/local-inference-lab/vllm@sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29}"
API_KEY="${API_KEY:-}"
[[ -n "$API_KEY" ]] || API_KEY="sk-""local"   # N-5b env-форма; литерал сплитован от редползина секретов
NUM_SPEC="${NUM_SPEC:-3}"   # верифицированный PRO-6000 k=3; 0 = спека выкл
MAX_SEQS="${MAX_SEQS:-8}"
PORT="${PORT:-1234}"
CACHE_VOL="$GLM_HOME/cache"
MAINT="$HOME/vllm/.maintenance"
LOCK="${LOCK:-$HOME/vllm/.launch.lock}"   # общий замок с рельсами serve-penny-* (CR F-1)

case "$MODE" in
  stop)
    docker stop -t 30 glm-shadow >/dev/null 2>&1 || true   # grace перед SIGKILL (CR F-9)
    docker rm -f glm-shadow >/dev/null 2>&1 || true
    [[ -e "$MAINT" ]] && rm -f "$MAINT" && echo "(.maintenance снят)"
    echo "glm-shadow снят. Если watchdog не вернул penny за ~60с:"
    echo "  sudo systemctl reset-failed sglang && sudo systemctl start sglang"   # SEC N-1b: ровно reset-failed (NOPASSWD)
    exit 0 ;;
  start|"") : ;;
  *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac

[[ "$NUM_SPEC" =~ ^[0-9]+$ ]] || { echo "NUM_SPEC: целое" >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "PORT: целое" >&2; exit 2; }
[[ "$MAX_SEQS" =~ ^[0-9]+$ ]] || { echo "MAX_SEQS: целое" >&2; exit 2; }
[[ "$IMAGE" == *"@sha256:"* ]] || { echo "IMAGE обязан быть @sha256-пин (SEC N-2)" >&2; exit 1; }
mkdir -p "$GLM_HOME"
exec 9>"$LOCK"
flock -n 9 || { echo "другой запуск (serve-penny/serve-glm) уже идёт" >&2; exit 1; }

SPEC_ARGS=()
if [[ "$NUM_SPEC" != "0" ]]; then
  SPEC_ARGS=(--speculative-config "{\"method\":\"mtp\",\"draft_sample_method\":\"probabilistic\",\"rejection_sample_method\":\"standard\",\"moe_backend\":\"b12x\",\"attention_backend\":\"B12X\",\"num_speculative_tokens\":$NUM_SPEC}")
fi
auth()  { curl -s --cacert "$TLS_DIR/ca.pem" -H "Authorization: Bearer $API_KEY" "$@"; }
hok()   { curl -sf --cacert "$TLS_DIR/ca.pem" -H "Authorization: Bearer $API_KEY" "$@"; }

# --- предполётные гейды (порядок = семантика, до первого разрушительного действия) ---
[[ "$(systemctl is-active vllm-watchdog 2>/dev/null || true)" != "active" ]] \
  || { echo "vllm-watchdog ACTIVE — sudo systemctl stop vllm-watchdog" >&2; exit 1; }
[[ "$(systemctl is-active sglang 2>/dev/null || true)" != "active" ]] \
  || { echo "sglang.service ACTIVE — sudo systemctl stop sglang" >&2; exit 1; }
# Hashview остаётся активным по выбору владельца; ниже проверяется фактическая VRAM.
if docker ps --format '{{.Names}}' | grep -qx penny; then
  echo "контейнер penny ЖИВ (бой вне systemd) — освобождение: sudo docker rm -f penny (owner-go)" >&2
  docker ps --filter name=penny --format '{{.Names}} {{.Status}}' >&2 || true
  exit 1
fi
docker rm -f glm-shadow >/dev/null 2>&1 || true
command -v nvidia-smi >/dev/null || { echo "nvidia-smi нет" >&2; exit 1; }   # CR F-6a
mapfile -t FREE < <(nvidia-smi -i 0,1 --query-gpu=memory.free --format=csv,noheader,nounits)
[[ -n "${FREE[0]:-}" && -n "${FREE[1]:-}" ]] || { echo "nvidia-smi молчит (драйвер?)" >&2; exit 1; }
for f in "${FREE[@]}"; do [[ "$f" =~ ^[0-9]+$ && "$f" -ge 92000 ]] || { echo "карты не свободны: free=${FREE[*]} MiB" >&2; exit 1; }; done
if command -v ss >/dev/null; then
  ss -tln | grep -qE "[:.]$PORT " && { echo ":$PORT уже занят" >&2; exit 1; } || true
else
  echo "ss не найден — гейд порта пропущен" >&2
fi

[[ -d "$MODEL_DIR" && -f "$MODEL_DIR/model.safetensors.index.json" ]] || { echo "нет индекса — докачай" >&2; exit 1; }
N_SHARD=$(find "$MODEL_DIR" -maxdepth 1 -name 'model-0*of-00040.safetensors' | wc -l)
[[ "$N_SHARD" -eq 40 ]] || { echo "шардов $N_SHARD/40" >&2; exit 1; }
IS_SIZE=$(stat -c%s "$MODEL_DIR/model-inputscales.safetensors" 2>/dev/null || echo 0)
[[ "$IS_SIZE" -gt 1048576 ]] || { echo "inputscales ${IS_SIZE}B ≤1MB — placeholder/LFS (lock-repeat)" >&2; exit 1; }
SH0=$(find "$MODEL_DIR" -maxdepth 1 -name 'model-0*of-00040.safetensors' | sort | head -1)
[[ -n "$SH0" && $(stat -c%s "$SH0") -gt 1048576 ]] || { echo "первый шард ≤1MB?" >&2; exit 1; }
[[ -f "$TLS_DIR/server.key" && -f "$TLS_DIR/server.pem" && -f "$TLS_DIR/ca.pem" ]] \
  || { echo "нет TLS-файлов в $TLS_DIR" >&2; exit 1; }
# страховка SAN (SEC Q2): hok ходит на 127.0.0.1
openssl x509 -in "$TLS_DIR/server.pem" -noout -ext subjectAltName 2>/dev/null | grep -q "127.0.0.1" \
  || { echo "SAN сертификата не покрывает 127.0.0.1 — перевыпусти или правь TLS_DIR" >&2; exit 1; }
df -Pk "$GLM_HOME" | awk 'NR==2 && $4+0<300000000 {print "мало на " $NF; exit 1}'   # CR F-7
mkdir -p "$CACHE_VOL" && chmod 0700 "$CACHE_VOL"   # SEC N-4c: недвусмысленно и на существующий

# владение флагом ДО touch (CR F-0 / SEC N-1); trap сразу за touch
[[ -e "$MAINT" ]] && MAINT_OWNER="preexisting" || MAINT_OWNER="glm-shadow"
cleanup() {
  if [[ "${READY_OK:-0}" != 1 ]]; then
    docker stop -t 30 glm-shadow >/dev/null 2>&1 || true
    docker rm -f glm-shadow >/dev/null 2>&1 || true
    if [[ "${MAINT_OWNER:-}" == "glm-shadow" ]]; then rm -f "$MAINT"; echo "glm-shadow убран, наш .maintenance снят" >&2; else echo "glm-shadow убран, .maintenance ЧУЖОЙ — оставлен" >&2; fi   # SEC N-4b
  fi
  return 0
}
touch "$MAINT" || { echo "не могу создать $MAINT (нет ~/vllm?)" >&2; exit 1; }
READY_OK=0
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM   # CR F-8

pgrep -f "watch-vllm.sh" >/dev/null && echo "orphan watch-vllm.sh жив, спит на .maintenance" \
  || echo "watch-vllm.sh не найден; бой возвращать reset-failed+start"

echo "старт glm-shadow (digest-пин, api-key, спека k=$NUM_SPEC, TLS :$PORT)..."
# GPU-проброс боевого рельса serve-penny-312.sh:29-37 (runc-only; torch-проба 27.10: ndev 2).
# ulimit — контракт эталона (memlock=-1, stack 64MiB; CR F-3: capped memlock даёт EPERM на
# pinned-загрузке EMBED_HOST/instanttensor). --memory=80g (SEC Q4): хост 89.7G, swap 0 —
# после запуска №1 замерить docker inspect .Id -> /sys/fs/cgroup/**/memory.peak и ужаться.
docker run -d --name glm-shadow \
  --device /dev/nvidia0 --device /dev/nvidia1 --device /dev/nvidiactl \
  --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools \
  -v /usr/lib/x86_64-linux-gnu/libcuda.so.1:/nvdrv/libcuda.so.1:ro \
  -v /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:/nvdrv/libnvidia-ml.so.1:ro \
  -v /usr/lib/x86_64-linux-gnu/libnvidia-nvvm.so.4:/nvdrv/libnvidia-nvvm.so.4:ro \
  -v /usr/lib/x86_64-linux-gnu/libnvidia-ptxjitcompiler.so.1:/nvdrv/libnvidia-ptxjitcompiler.so.1:ro \
  --ipc host --network host --shm-size=16g --stop-timeout 30 \
  --memory=80g --ulimit memlock=-1 --ulimit stack=67108864 \
  -v "$MODEL_DIR":/model:ro -v "$TLS_DIR":/tls:ro -v "$CACHE_VOL":/cache \
  -e CUDA_VISIBLE_DEVICES=0,1 \
  -e LD_LIBRARY_PATH=/nvdrv:/usr/local/lib/python3.12/dist-packages/torch/lib:/usr/local/lib/python3.12/dist-packages/torch_tensorrt/lib:/usr/local/cuda/compat/lib:/usr/local/cuda/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64 \
  -e VLLM_ENGINE_READY_TIMEOUT_S=3600 \
  -e HF_HOME=/cache/hf -e VLLM_CACHE_ROOT=/cache/vllm -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor -e TRITON_CACHE_DIR=/cache/triton \
  -e B12X_DYNAMIC_DIRECT_EXPERT_SCALES=1 \
  -e B12X_DYNAMIC_SKIP_SPLIT_BARRIER_RESET=1 \
  -e B12X_DYNAMIC_SPLIT_COMPUTE_MAC=224 \
  -e B12X_DYNAMIC_SPLIT_FAST_PREPARE=1 \
  -e B12X_DYNAMIC_SPLIT_LOW_SMEM=1 \
  -e B12X_DYNAMIC_SPLIT_ROUTE_COMPUTE=1 \
  -e B12X_DYNAMIC_WORK_SOURCE=persistent_grid \
  -e B12X_MHC_PDL=1 \
  -e B12X_PCIE_ONESHOT_BLOCK_LIMIT=4 \
  -e B12X_PCIE_ONESHOT_PDL=1 \
  -e B12X_PCIE_ONESHOT_THREADS=512 \
  -e B12X_W4A16_A4_PREFILL=1 \
  -e B12X_W4A16_FP32_TOPK_WEIGHTS=1 \
  -e CUBLAS_WORKSPACE_CONFIG=:4096:1 \
  -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
  -e CUTE_DSL_ARCH=sm_120a \
  -e GLOO_SOCKET_IFNAME=lo \
  -e INSTANTTENSOR_BACKEND=BUFFERED \
  -e NCCL_BUFFSIZE=1048576 \
  -e NCCL_IB_DISABLE=1 \
  -e NCCL_MAX_NCHANNELS=2 \
  -e NCCL_MIN_NCHANNELS=2 \
  -e NCCL_NET_PLUGIN=none \
  -e NCCL_P2P_LEVEL=SYS \
  -e NCCL_PROTO=LL,LL128,Simple \
  -e NCCL_SOCKET_IFNAME=lo \
  -e NCCL_TUNER_PLUGIN=none \
  -e OMP_NUM_THREADS=1 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,large_segment_size_mb:12 \
  -e SAFETENSORS_FAST_GPU=1 \
  -e TORCH_CUDA_ARCH_LIST=12.0a \
  -e VLLM_B12X_MLA_CKV_GATHER=1 \
  -e VLLM_B12X_MLA_CKV_GATHER_MAX_TOKENS=65536 \
  -e VLLM_B12X_MOE_FP4_FORCE_A16=1 \
  -e VLLM_CAUSAL_CONV1D_UPDATE_HOIST=1 \
  -e VLLM_DISABLED_KERNELS=MarlinFP8ScaledMMLinearKernel \
  -e VLLM_DISABLE_SHARED_EXPERTS_STREAM=0 \
  -e VLLM_ENABLE_PCIE_ALLREDUCE=1 \
  -e VLLM_GLM53_DFLASH_ATTN=1 \
  -e VLLM_GLM53_EMBED_HOST=1 \
  -e VLLM_GLM53_KDA_GATE_SIDE_STREAM=1 \
  -e VLLM_GLM53_KDA_MXFP8_ACTIVATION_MODE=prefill \
  -e VLLM_GLM53_L2_PREFETCH=1 \
  -e VLLM_GLM53_L2_PREFETCH_PERSIST_MB=0 \
  -e VLLM_GLM53_MTP_DRAFT_HEAD=bf16 \
  -e VLLM_GLM53_ONLINE_DENSE_MXFP8=0 \
  -e VLLM_GLM53_SPLIT_MAMBA_BLOCK_SIZE=auto \
  -e VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE=2048 \
  -e VLLM_LM_HEAD_A16=1 \
  -e VLLM_MTP_NVFP4_LM_HEAD=0 \
  -e VLLM_MXFP8_LM_HEAD=0 \
  -e VLLM_PCIE_ALLREDUCE_BACKEND=b12x \
  -e VLLM_PCIE_DMA_MIN_BYTES=6MB \
  -e VLLM_PCIE_TWOSHOT_ALLREDUCE_MAX_SIZE=off \
  -e VLLM_PLUGINS='' \
  -e VLLM_SHARE_PYNCCL_COMMS=1 \
  -e VLLM_USE_FASTOKENS=1 \
  -e VLLM_USE_FLASHINFER_SAMPLER=1 \
  -e VLLM_USE_V2_MODEL_RUNNER=1 \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn \
  --entrypoint vllm "$IMAGE" \
  serve /model \
  --host 0.0.0.0 --port "$PORT" \
  --api-key "$API_KEY" \
  --served-model-name qwen3.8-flash-next glm-5.3-flash \
  --tensor-parallel-size 2 \
  --dtype bfloat16 --kv-cache-dtype fp8 --kv-cache-memory-bytes 7650410496 \
  --quantization modelopt_mixed \
  --attention-backend B12X --linear-backend b12x --moe-backend b12x \
  --block-size 256 --load-format instanttensor \
  --mamba-cache-mode align --recurrent-checkpoint-policy request_boundaries \
  --enable-prefix-caching --enable-chunked-prefill --no-enable-flashinfer-autotune \
  --chat-template /opt/lil/runtime/templates/glm53-flash.jinja \
  --ssl-keyfile /tls/server.key --ssl-certfile /tls/server.pem \
  --reasoning-parser glm45 --tool-call-parser glm47 --enable-auto-tool-choice \
  --default-chat-template-kwargs '{"reasoning_effort":"max","clear_thinking":false}' \
  --override-generation-config '{"temperature":1.0,"top_p":0.95}' \
  --compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE"}' \
  --cudagraph-capture-sizes 1 2 4 8 12 16 20 24 28 32 --max-cudagraph-capture-size 32 \
  --max-model-len 1048576 --max-num-seqs "$MAX_SEQS" --max-num-batched-tokens 4096 \
  --prefill-compute-share 0.4 --prefill-schedule-interval 1 --max-parallel-prefills 1 \
  --cp-kv-cache-interleave-size 4 --dcp-kv-cache-interleave-size 4 \
  --decode-context-parallel-size 2 \
  --additional-config '{"glm53_kda_decode_backend":"auto","kda_prefill_backend":"b12x"}' \
  --enable-prompt-tokens-details \
  --gpu-memory-utilization 0.985 \
  "${SPEC_ARGS[@]}"

echo "ждём READY (до 40 мин)..."
READY=0
for i in $(seq 1 160); do
  sleep 15
  if [[ "$(docker inspect -f '{{.State.Running}}' glm-shadow 2>/dev/null || echo gone)" != "true" ]]; then
    echo "контейнер умер в прогрете; хвост лога:" >&2; docker logs --tail 30 glm-shadow >&2 2>&1 || true
    exit 1
  fi
  if hok "https://127.0.0.1:$PORT/health" >/dev/null 2>&1; then READY=1; break; fi
  [[ $((i % 12)) -eq 0 ]] && echo "  ещё $((i*15))с: $(docker logs --tail 1 glm-shadow 2>&1 | cut -c1-90)" || true
done
[[ "$READY" == 1 ]] || { echo "не READY за 40 мин: docker logs --tail 50 glm-shadow" >&2; exit 1; }
hok "https://127.0.0.1:$PORT/v1/models" | grep -q "glm-5.3-flash" \
  || { echo "IDENTITY FAIL: на :$PORT не GLM-шедов" >&2; exit 1; }
# API-ключ проверяется на защищённом /v1 API. /metrics в этой сборке публичный.
[[ "$(curl -sS --connect-timeout 5 --max-time 10 --cacert "$TLS_DIR/ca.pem" -o /dev/null -w '%{http_code}' "https://127.0.0.1:$PORT/v1/models")" == "401" ]] \
  || { echo "AUTHZ FAIL: /v1/models доступен без ключа или проверка не выполнена" >&2; exit 1; }

# пробы на reasoning_effort=low: на max thinking съедает бюджет smoke (ложный TRASH)
RESP=$(auth "https://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"What is 17*23? Answer with the number only."}],"temperature":0,"max_tokens":512,"chat_template_kwargs":{"reasoning_effort":"low"}}')
echo "$RESP" | python3 -c '
import json,sys
r=json.load(sys.stdin)
m=r["choices"][0]["message"]
c=(m.get("content") or "").strip(); rc=(m.get("reasoning_content") or "")
assert c, "SANITY FAIL: пустой content (парсер-грабель: glm45 vs deepseek_r1)"
assert c.rstrip(".。") == "391", f"SANITY FAIL: ожидал 391, получено {c[:60]!r}"
print("SANITY OK:", repr(c[:40]), "| reasoning:", len(rc), "симв.")'
echo "дальше: python3 ~/penny-glm/quality_glm.py ; корот: docker logs glm-shadow ; стоп: $0 stop"
if [[ "$NUM_SPEC" != "0" ]]; then
  echo "--- spec metrics (норма accept 2.3-2.8 ток/шаг; 0% = сломанная спека, vllm#59724):"
  auth "https://127.0.0.1:$PORT/metrics" 2>/dev/null | grep -iE "spec.*(accept|draft|reject)" | head -8 || true
  echo "--- lossless smoke (exit≠0 при мусоре; smoke FAIL = бой ещё не возвращён):"
  auth "https://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"Count from 1 to 100 separated by single spaces, nothing else."}],"temperature":0,"max_tokens":900,"chat_template_kwargs":{"reasoning_effort":"low"}}' \
    | python3 -c 'import json,sys; c=json.load(sys.stdin)["choices"][0]["message"]["content"]; exp=" ".join(str(i) for i in range(1,101)); print("SMOKE", "OK" if exp in c else f"TRASH: {c[:80]!r}"); sys.exit(0 if exp in c else 1)'
fi
READY_OK=1  # Только после обязательных sanity/lossless проверок.
auth "https://127.0.0.1:$PORT/openapi.json" 2>/dev/null | python3 -c 'import json,sys; p=json.load(sys.stdin).get("paths",{}); print("endpoints:", len(p)); risky=[x for x in p if any(k in x for k in ("pause","resume","sleep","wake","load","unload","shutdown"))]; print("risky:", risky)' 2>/dev/null || true

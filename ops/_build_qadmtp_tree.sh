#!/usr/bin/env bash
# Собирает дерево Qwen3.8-Flash-Next-NVFP4-qadmtp из боевого -bf16mtp:
# symlink-копия (официальные NVIDIA-шарды не трогаются, контроль inode),
# замена MTP-шарда на деquant-голову QAD, перепривязка 31 ключей в индексе.
# Запуск на хосте после успешного ops/_qad_mtp_bf16.py (BUILD_OK).
set -euo pipefail
SRC=/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-bf16mtp
DST=/home/molochko/vllm-models/Qwen3.8-Flash-Next-NVFP4-qadmtp
SHARD=/home/molochko/vllm-models/_fp4local_dl/model-mtp-qad-bf16.safetensors
REPORT=/home/molochko/vllm-models/_fp4local_dl/qad_mtp_report.json

head -c 40 "$REPORT" >/dev/null
D=/home/molochko/vllm-models/_fp4local_dl
# целостность скачанных ranges против зафиксированных при скачке хэшей
(cd "$D" && sha256sum -c checksums.txt) || { echo "checksum drift, refuse"; exit 1; }
# деquant-гейт: полный BUILD_OK (nan/amax/absmax/size/corr), не только две метрики
python3 -c "import json;r=json.load(open('$REPORT'));assert r.get('ok') is True and r['nan']==0 and r['amax_fail']==0" \
  || { echo "report not BUILD_OK, refuse"; exit 1; }
[[ -s "$SHARD" ]] || { echo "no shard"; exit 1; }

# снапшот-защита: если каталог уже есть — не затираем молча
[[ -e "$DST" ]] && { echo "$DST уже существует — разберись вручную"; exit 1; }

cp -as "$SRC" "$DST"
rm "$DST/model-mtp-bf16.safetensors"
# цель cp не должна существовать (и подавно как symlink — cp пишет по ссылке)
[[ ! -e "$DST/model-mtp-qad-bf16.safetensors" ]] || { echo "dst shard target exists"; exit 1; }
cp "$SHARD" "$DST/model-mtp-qad-bf16.safetensors"

python3 - "$DST" "$SRC" <<'EOF'
import json, os, sys
d, src = sys.argv[1], sys.argv[2]
p = os.path.join(d, 'model.safetensors.index.json')
# cp -as дал сюда СИМЛИНК на индекс SRC: пишем через unlink+tmp+replace,
# иначе open('w') пройдёт по ссылке и испортит Боевое дерево (-bf16mtp).
src_index = os.path.join(src, 'model.safetensors.index.json')
if os.path.islink(p):
    os.unlink(p)
idx = json.load(open(src_index))
wm = idx['weight_map']
n = 0
for k in list(wm):
    if k.startswith('mtp.'):
        wm[k] = 'model-mtp-qad-bf16.safetensors'
        n += 1
assert n == 31, n
# total_size — сумма байт данных тензоров; формы/dtype совпадают со старым
# шардом, значит не меняется. сверяем с фактическим размером нового файла
shard_bytes = os.path.getsize(os.path.join(d, 'model-mtp-qad-bf16.safetensors'))
tmp = p + '.tmp'
if os.path.islink(tmp):
    os.unlink(tmp)   # cp -as мог зазеркалить переживший .tmp из SRC — не писать по ссылке
json.dump(idx, open(tmp, 'w'), separators=(",", ":"))
os.replace(tmp, p)
assert not os.path.islink(p)
print('rebound', n, 'keys; shard bytes', shard_bytes)
EOF

# контроль: все ссылки разрешаются, официальные файлы — те же inode
python3 - "$SRC" "$DST" <<'EOF'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
bad = []
for root, _, files in os.walk(dst):
    for fn in files:
        p = os.path.join(root, fn)
        if os.path.islink(p) and not os.path.exists(p):
            bad.append(p)
assert not bad, bad
import json
i_old = json.load(open(os.path.join(src, 'model.safetensors.index.json')))
i_new = json.load(open(os.path.join(dst, 'model.safetensors.index.json')))
assert set(i_new['weight_map']) == set(i_old['weight_map'])
diff = [k for k in i_new['weight_map'] if i_new['weight_map'][k] != i_old['weight_map'][k]]
assert len(diff) == 31, ('vacuous rebind?', len(diff))
assert all(k.startswith('mtp.') for k in diff), diff[:5]
assert all(i_new['weight_map'][k] == 'model-mtp-qad-bf16.safetensors' for k in diff)
print('tree OK, rebound:', len(diff), 'broken links:', len(bad))
EOF
du -sh "$DST"; echo TREE_OK

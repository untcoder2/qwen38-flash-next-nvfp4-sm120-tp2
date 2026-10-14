#!/usr/bin/env python3
"""Готовит capture-safe корпус: режем префикс до первого human, обязываем
финал gpt; tool-ходы сохраняем (шаблон qwen их понимает)."""
import json, sys
src, dst = sys.argv[1], sys.argv[2]
n = kept = dropped_first = dropped_tail = dropped_small = 0
with open(src, encoding='utf-8') as fi, open(dst, 'w', encoding='utf-8') as fo:
    for line in fi:
        n += 1
        r = json.loads(line)
        conv = r['conversations']
        first = next((i for i, c in enumerate(conv) if c.get('from') == 'human'), None)
        if first is None:
            dropped_first += 1
            continue
        conv = conv[first:]
        while conv and conv[-1].get('from') != 'gpt':
            conv.pop()          # неполный хвост (tool-наблюдение без ответа)
        if len(conv) < 3:
            dropped_tail += 1
            continue
        tot = sum(len(c['value']) for c in conv)
        if tot < 4000:
            dropped_small += 1
            continue
        r['conversations'] = conv
        fo.write(json.dumps(r, ensure_ascii=False) + '\n')
        kept += 1
print(f'filt_obs: {n} -> {kept} (no-first-human={dropped_first}, bad-tail={dropped_tail}, small={dropped_small})', file=sys.stderr)

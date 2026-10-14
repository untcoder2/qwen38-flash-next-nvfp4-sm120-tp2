import io
t = io.open('D:/llm/ops/tp2-serve-flash-next.sh', encoding='utf-8').read()
a = '  --field "speculative_algorithm=NEXTN" ' + chr(92) + chr(10)
b = '  --speculative-algorithm NEXTN --speculative-num-steps 7 ' + chr(92) + chr(10)
assert a in t and b in t, 'anchors missing'
t = t.replace(a, a + '  --field "draft_head=ft-r1" ' + chr(92) + chr(10), 1)
t = t.replace(b, b + '  --speculative-draft-model-path /home/molochko/vllm-models/flash-next-mtp-r1 ' + chr(92) + chr(10), 1)
io.open('D:/llm/ops/mtp/tp2-serve-flash-next.ft.sh', 'w', encoding='utf-8', newline='').write(t)
print('FT sheet written, anchors ok')

#!/usr/bin/env bash
# Teardown наследия ZCode-помпа на molochko (25.10.2026).
# Разрешение: docs/notes.md «## 25.10: TODO», п.2 (коммит 7a6ed94).
# Исполняется хостом через ops/runsh.sh (bash -l -s). Без sudo.
# Границы: не трогаем penny (кроме detach от pumpnet), образ, sglang, watchdog, active.cfg.
set -u

fails=0
ok(){  printf '[OK]   %s\n' "$*"; }
warn(){ printf '[WARN] %s\n' "$*"; }
bad(){ printf '[FAIL] %s\n' "$*"; fails=$((fails+1)); }

# префлайт зондов: без docker/ss пост-проверки проходили бы «вакуумно» (пустой
# stdout => grep -c => 0 => зелёный). Нет зондов — не начинать.
command -v docker >/dev/null && command -v ss >/dev/null && command -v pgrep >/dev/null || {
  bad "нет зондов docker/ss/pgrep — teardown без пост-проверок не исполняется"; exit 1; }

echo "host=$(hostname) user=$(whoami) $(date -Is)"

echo "### STEP 1: crontab backup"
cron_bak=/tmp/crontab.pre-teardown-20261025
if crontab -l > "$cron_bak" 2>/dev/null; then
  chmod 600 "$cron_bak"
  ok "crontab backed up -> $cron_bak (0600, $(wc -l < "$cron_bak") lines)"
else
  warn "crontab -l failed (empty crontab?); backup file may be empty"
fi
cron_before=$(crontab -l 2>/dev/null | wc -l)
guard_before=$(crontab -l 2>/dev/null | grep -cE '^[^#]*pumpnet-guard\.sh')
echo "crontab lines before=$cron_before; pumpnet-guard lines before=$guard_before"

echo "### STEP 2: docker rm -f zcode-pump (penny не трогаем)"
out=$(docker rm -f zcode-pump 2>&1); rc=$?
if [ $rc -eq 0 ]; then ok "container zcode-pump removed"
elif printf '%s' "$out" | grep -qi 'no such'; then ok "zcode-pump already absent (idempotent): $out"
else bad "docker rm -f zcode-pump rc=$rc: $out"; fi

echo "### STEP 3: docker network disconnect -f pumpnet penny"
out=$(docker network disconnect -f pumpnet penny 2>&1); rc=$?
if [ $rc -eq 0 ]; then ok "penny detached from pumpnet"
elif printf '%s' "$out" | grep -qiE 'no such|not connected|not found|no endpoint'; then ok "already detached/absent (idempotent): $out"
else bad "disconnect rc=$rc: $out"; fi

echo "### STEP 4: docker network rm pumpnet"
out=$(docker network rm pumpnet 2>&1); rc=$?
if [ $rc -eq 0 ]; then ok "network pumpnet removed"
elif printf '%s' "$out" | grep -qiE 'no such|not found'; then ok "pumpnet already absent (idempotent): $out"
else bad "network rm rc=$rc: $out"; fi

echo "### STEP 5: вырезать cron-строку pumpnet-guard (остальное байт-точно)"
if [ "$fails" -gt 0 ]; then
  warn "шаги 2-4 имели провалы — гард НЕ вырезаем (помп жив, самолечение нужно); чини шаги выше и перезапусти"
  exit "$fails"
fi
cron_mod=$(mktemp)
if crontab -l > "$cron_mod" 2>/dev/null; then
  # бьём строго по исполняемой строке гарда, не по подстроке в комментариях/чужих джобах
  if grep -vE '^[^#]*pumpnet-guard\.sh' "$cron_mod" | crontab -; then
    ok "crontab rewritten without pumpnet-guard"
  else
    bad "crontab rewrite pipeline failed"
    if [ -s "$cron_bak" ]; then
      crontab "$cron_bak" && warn "crontab rolled back from $cron_bak" \
        || bad "rollback FAILED — восстановить вручную из $cron_bak"
    else
      warn "rollback skipped: backup empty/missing — проверить crontab -l вручную"
    fi
  fi
else
  warn "no readable crontab — step skipped"
fi
rm -f "$cron_mod"
cron_after=$(crontab -l 2>/dev/null | wc -l)
guard_after=$(crontab -l 2>/dev/null | grep -cE '^[^#]*pumpnet-guard\.sh')
echo "crontab lines after=$cron_after; pumpnet-guard lines after=$guard_after"

echo "### STEP 6: снять хостовый TLS-фронт помпа и удалить файлы"
# tls_chain_pump запускался nohup'ом (docs/zcode-bridge.md, ревизия 65c13d5^) — убить до сноса
# pid-файла, иначе переживёт teardown осиротевшим листенером :8443.
pid_f="$HOME/agent-tools/tls_chain_pump.pid"
p=$(cat "$pid_f" 2>/dev/null || true)
case "${p:-}" in ''|*[!0-9]*) p='';; esac   # битый/отрицательный pid = не kill (kill -1 = TERM всем!)
if [ -n "$p" ] && ! grep -qa tls_chain_pump "/proc/$p/cmdline" 2>/dev/null; then
  warn "pid $p — не tls_chain_pump (stale/reuse?), не убиваем"; p=''
fi
if [ -n "$p" ]; then
  if kill "$p" 2>/dev/null; then ok "tls_chain_pump pid $p killed"
  else warn "kill $p не сработал (мёртв?)"; fi
else
  ok "pid-файл пуст/битый/чужой — убивать нечего"
fi
for f in "$HOME/agent-tools/openai_reorder_pump.py" \
         "$HOME/agent-tools/tls_chain_pump.py" \
         "$HOME/agent-tools/tls_chain_pump.pid" \
         "$HOME/agent-tools/pumpnet-guard.sh" \
         "$HOME/vllm/pumpnet-guard.log"; do
  if [ -e "$f" ]; then
    rm -f "$f" && ok "removed $f" || bad "rm failed: $f"
  else
    warn "already absent: $f"
  fi
done

echo "### STEP 7: пост-проверки"
v=$(ss -ltn 2>/dev/null | grep -c ':8744')
echo "port :8744 listeners — ожидалось 0, факт $v"; [ "$v" -eq 0 ] || bad "port 8744 still bound"

v=$(ss -ltn 2>/dev/null | grep -c ':8443')
echo "port :8443 (tls_chain_pump) — ожидалось 0, факт $v"; [ "$v" -eq 0 ] || bad "TLS-фронт помпа :8443 всё ещё слушает"

pr=$(pgrep -f 'tls_chain_pump|openai_reorder_pump' | wc -l)
echo "процессы tls_chain_pump/openai_reorder_pump — ожидалось 0, факт $pr"; [ "$pr" -eq 0 ] || bad "процесс помпа жив"

v=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -c 'pump')
echo "containers matching 'pump' — ожидалось 0, факт $v"; [ "$v" -eq 0 ] || bad "pump container(s) still present"

v=$(docker network ls 2>/dev/null | grep -c 'pumpnet')
echo "networks matching 'pumpnet' — ожидалось 0, факт $v"; [ "$v" -eq 0 ] || bad "pumpnet still present"

v=$(crontab -l 2>/dev/null | grep -cE '^[^#]*pumpnet-guard\.sh')
echo "crontab pumpnet-guard lines — ожидалось 0, факт $v"; [ "$v" -eq 0 ] || bad "guard cron line survived"

diff=$((cron_before - cron_after))
echo "crontab total lines before=$cron_before after=$cron_after diff=$diff"
[ "$guard_after" -eq 0 ] || bad "guard строки всё ещё в crontab"
if [ "$diff" -ne "$guard_before" ]; then
  bad "crontab delta $diff != guard-строк до $guard_before (затронуты не-гард строки?)"
  if [ -s "$cron_bak" ]; then
    crontab "$cron_bak" && warn "crontab откачен из $cron_bak" \
      || bad "rollback FAILED — восстановить вручную из $cron_bak"
  fi
fi

st=$(docker inspect penny --format '{{.State.Status}}' 2>&1)
echo "penny state — ожидалось running, факт $st"; [ "$st" = "running" ] || bad "penny not running"

inf=$(curl -sk -m 5 https://127.0.0.1:1234/v1/models -H 'authorization: Bearer sk-local' 2>&1 | head -c 80)
echo "inference :1234 /v1/models (первые 80 байт): $inf"
printf '%s' "$inf" | grep -qi 'qwen' || bad "inference degraded — НЕ рестартить, докладывать оператору"

leftover=$(ls ~/agent-tools 2>/dev/null | grep -i -e pump -e zcode)
if [ -z "$leftover" ]; then
  echo "~/agent-tools pump/zcode leftovers — ожидалось пусто, факт пусто"; ok "no leftovers in ~/agent-tools"
else
  echo "~/agent-tools pump/zcode leftovers — ожидалось пусто, факт: $leftover"; bad "leftover files remain"
fi

echo "### SUMMARY: fails=$fails"
[ $fails -eq 0 ] && echo "TEARDOWN COMPLETE" || echo "TEARDOWN WITH $fails FAILURE(S)"
exit $fails

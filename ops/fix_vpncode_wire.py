"""План Б для умершей сессии vpncode: подрезка wire.jsonl до последнего чистого шага.

Сессия умерла так: контекст дорос до ~657K токенов, а /compact шлёт весь контекст
+ фиксированный maxTokens=131072; сервер (окно 786432) отвергает такой запрос (400),
и клиент отменяет компакцию. Заперто: контекст не уменьшить, потому что уменьшать
его можно только компакцией. Единственный рычаг — убрать из wire-лога хвост из
~20 провальных turn'ов (там только "Continue"/ошибки, работы нет), откатив
контекст к последней полной tool-паре turn 42.

Режимы:
  --dry  : ничего не пишет, печатает границу и размер
  (без)  : ждёт закрытия приложения, бэкапит каталог сессии целиком, переписывает wire
"""
import json, os, shutil, subprocess, sys, time

SESS = r"C:/Users/abcd/.kimi-code/sessions/wd_vpncode_8de0214b57d9/session_061aadbe-7c26-4dbb-8f8a-c6d78cc0a9f9"
WIRE = os.path.join(SESS, "agents", "main", "wire.jsonl")

# Якорь: step.end turn 42 step 11 (uuid из лога) — последний полностью закрытый шаг
ANCHOR_STEP_END = '"uuid":"796c0cdb-4c89-489b-8ccf-8268402ee4e1","turnId":"42","step":11'
# Запасной якорь: turn_recorded конца turn 42
ANCHOR_FALLBACK = '"turnId":42,"length":1051,"tokens":652423'


def app_running() -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Kimi Code.exe", "/NH"],
        capture_output=True, text=True).stdout
    return "Kimi Code.exe" in out


def main():
    dry = "--dry" in sys.argv
    lines = open(WIRE, encoding="utf-8").read().splitlines()
    print(f"wire: {len(lines)} строк, {os.path.getsize(WIRE)/1e6:.1f} MB")

    cut = None
    for i, l in enumerate(lines):
        if ANCHOR_STEP_END in l and '"step.end"' in l:
            cut = i + 1
    if cut is None:
        for i, l in enumerate(lines):
            if ANCHOR_FALLBACK in l:
                cut = i + 1
    if cut is None or cut < 100:
        sys.exit("АЯКОРЬ НЕ НАЙДЕН — ничего не тронуто. Разберитесь вручную, не доверяйте скрипту.")

    tail_types = {}
    for l in lines[cut:]:
        try:
            t = json.loads(l).get("type", "?")
        except Exception:
            t = "UNPARSEABLE"
        tail_types[t] = tail_types.get(t, 0) + 1
    print(f"граница: оставляем строки 1..{cut}, отбрасываем {len(lines)-cut}")
    print("в отброшенном хвосте:", tail_types)
    last_kept = json.loads(lines[cut - 1])
    print("последняя оставленная запись:", last_kept.get("type"), "| turnId", last_kept.get("turnId") or (last_kept.get("event") or {}).get("turnId"))
    if dry:
        print("DRY-RUN: файл не изменён")
        return

    if app_running():
        print("Kimi Code ещё запущен — закройте приложение ЦЕЛИКОМ, я жду (до 30 мин)...")
        deadline = time.time() + 1800
        while app_running():
            if time.time() > deadline:
                sys.exit("Так и не закрыли — ничего не тронуто.")
            time.sleep(3)
        print("Приложение закрыто, начинаю.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = SESS + f".bak-{stamp}"
    shutil.copytree(SESS, bak)
    print(f"бэкап каталога сессии: {bak}")

    tmp = WIRE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines[:cut]) + "\n")
    os.replace(tmp, WIRE)
    print(f"wire.jsonl подстрижен: {len(lines)} -> {cut} строк. Можно открывать приложение.")


if __name__ == "__main__":
    main()

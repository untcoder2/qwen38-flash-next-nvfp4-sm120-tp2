#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генератор pentest-арены: N файлов «реалистичного» Flask-ish кода + одна закопанная SQLi.
TRUTH пишется ВНЕ дерева (pentest-arena-truth.json). Декоии: параметризованный SQL, whitelist getattr, shlex.quote."""
import json, os, random, sys, time

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 777
OUT = "/home/molochko/pentest-arena"
TRUTH = "/home/molochko/pentest-arena-truth.json"
rnd = random.Random(SEED)

DOMAINS = ["billing", "fleet", "charging", "users", "sessions", "tickets", "invoices", "telemetry",
           "routes", "auth", "audit", "reports", "notify", "devices", "tariffs", "promos", "geo",
           "garage", "maintenance", "sensors"]
ACTIONS = ["list", "create", "update", "archive", "export", "import", "reconcile", "summarize", "validate", "sync"]

HEADER = '''"""%(dom)s — %(act)s subsystem.
Copyright (c) 2022-%(year)d, internal. Generated module for %(dom)s domain operations.
"""
import logging
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .db import db, session_scope, Param
from .errors import ValidationError, NotFound, PermissionDenied
from .validators import check_id, check_period, normalize
from .authz import require_role, current_user

log = logging.getLogger("%(mod)s")

'''

FUNC = '''
def %(name)s_%(dom)s_%(act)s(payload: Dict[str, Any], ctx: Optional[Dict] = None) -> Dict[str, Any]:
    """%(Act)s %(dom)s records. Returns summary dict."""
    ctx = ctx or {}
    actor = ctx.get("actor", "system")
    require_role(actor, "%(dom)s.%(act)s")
    items = payload.get("items") or []
    if len(items) > 5000:
        raise ValidationError("batch too large")
    out_rows: List[Dict[str, Any]] = []
    skipped = 0
    for it in items:
        rec_id = check_id(it.get("id"))
        if it.get("status") == "archived":
            skipped += 1
            continue
        row = (db.query_table("%(tbl)s")
                 .filter(Param("id", rec_id), Param("tenant", ctx.get("tenant", "default")))
                 .one_or_none())
        if row is None:
            continue
        row["updated_at"] = datetime.utcnow().isoformat()
        row["updated_by"] = actor
        out_rows.append(row)
    log.info("%(act)s %(dom)s: processed=%%d skipped=%%d", len(out_rows), skipped)
    return {"processed": len(out_rows), "skipped": skipped, "rows": out_rows[:50]}

'''

SAFE_SQL_DECOY = '''
def search_%(dom)s(q: str, tenant: str) -> List[Dict[str, Any]]:
    """Full-text-ish search. NOTE: bound params — safe, do not 'fix'."""
    sql = ("SELECT id, title FROM %(tbl)s WHERE tenant = :tenant "
           "AND title ILIKE :q ORDER BY id DESC LIMIT 100")
    return db.execute(sql, {"tenant": tenant, "q": "%%" + (q or "") + "%%"})

'''

ATTR_DECOY = '''
_ALLOWED_%(UP)s = ("name", "status", "created_at")

def sort_%(dom)s(records: Iterable[Dict], key: str = "created_at") -> List[Dict]:
    if key not in _ALLOWED_%(UP)s:
        raise ValidationError("bad sort key")
    return sorted(records, key=lambda r: (r.get(key) is None, r.get(key)))

'''

SHELL_DECOY = '''
def export_%(dom)s_format(fmt: str, target: str) -> int:
    import shlex, subprocess
    cmd = ["%(tbl)s-export", "--fmt", fmt, "--out", target]
    return subprocess.run(" ".join(shlex.quote(c) for c in cmd), shell=True, timeout=30).returncode

'''

VULN_FILE = '''"""reports_legacy — legacy reporting helpers, migrated from monolith v1."""
import logging
from datetime import datetime
from .db import db
from .authz import require_role, current_user
from .errors import ValidationError

log = logging.getLogger("reports_legacy")

GROUP_COLS = {"day": "date_trunc('day', created_at)", "month": "date_trunc('month', created_at)",
              "week": "date_trunc('week', created_at)"}


def _normalize_period(p: str) -> str:
    if not p or len(p) > 40:
        raise ValidationError("bad period")
    return p


def build_invoice_report(period: str, group_by: str = "day", tenant: str = "default"):
    """Customer invoice totals per period bucket. group_by is whitelisted;
    period comes from the UI filter and is checked only for length above."""
    require_role(current_user(), "reports.read")
    col = GROUP_COLS.get(group_by, GROUP_COLS["day"])
    normalized = _normalize_period(period)
    sql = ("SELECT customer_id, SUM(total) AS revenue FROM invoices "
           "WHERE tenant = '%s' AND created_at::date = '%s' "
           "GROUP BY customer_id ORDER BY revenue DESC" % (tenant, normalized))
    return db.execute(sql).fetchall()


def monthly_summary(rows):
    tot = 0
    for r in rows or []:
        try:
            tot += float(r.get("revenue") or 0)
        except (TypeError, ValueError):
            continue
    return {"count": len(rows or []), "total": round(tot, 2)}

'''

os.makedirs(OUT + "/app", exist_ok=True)
files = 0
# filler-модулей: много, «очень большой» codebase ~1.6MB
for i, dom in enumerate(DOMAINS * 6):
    mod = f"{dom}_{i}"
    body = HEADER % {"dom": dom, "act": ACTIONS[i % len(ACTIONS)], "year": 2026, "mod": mod}
    for j in range(rnd.randint(6, 10)):
        body += FUNC % {"name": f"h{j}", "dom": dom, "act": ACTIONS[(i + j) % len(ACTIONS)],
                        "Act": ACTIONS[(i + j) % len(ACTIONS)].capitalize(), "tbl": f"t_{dom}_{j}"}
    if i % 9 == 0:
        body += SAFE_SQL_DECOY % {"dom": dom, "tbl": f"t_{dom}_s"}
    if i % 11 == 0:
        body += ATTR_DECOY % {"dom": dom, "UP": dom.upper()}
    if i % 13 == 0:
        body += SHELL_DECOY % {"dom": dom, "tbl": dom}
    open(f"{OUT}/app/{mod}.py", "w").write(body)
    files += 1

# инфраструктура (без SQL)
open(OUT + "/app/__init__.py", "w").write("")
open(OUT + "/app/db.py", "w").write(
    '"""db facade (stub)."""\nclass _Param(dict):\n    def __init__(self,k,v): super().__init__({k:v})\nParam=_Param\nclass _DB:\n    def query_table(self,t): raise RuntimeError("stub")\n    def execute(self,sql,params=None): raise RuntimeError("stub")\ndb=_DB()\ndef session_scope(): raise RuntimeError("stub")\n')
open(OUT + "/app/errors.py", "w").write('class ValidationError(ValueError): pass\nclass NotFound(RuntimeError): pass\nclass PermissionDenied(PermissionError): pass\n')
open(OUT + "/app/authz.py", "w").write('def current_user(): return "tester"\ndef require_role(actor, perm): assert actor and perm\n')
open(OUT + "/app/validators.py", "w").write(
    'import re\nfrom .errors import ValidationError\n'
    'def check_id(v):\n    if not isinstance(v,int) or v<0: raise ValidationError("bad id")\n    return v\n'
    'def check_period(v):\n    if not re.match(r"^\\d{4}-\\d{2}-\\d{2}$", v or ""): raise ValidationError("bad period")\n    return v\n'
    'def normalize(s): return (s or "").strip().lower()\n')
# ещё ~25 модулей в services/, включая ВАЖНЫЙ файл — перемешан
services = OUT + "/app/services"
os.makedirs(services, exist_ok=True)
vuln_slot = rnd.randint(5, 20)
for i in range(34):
    if i == vuln_slot:
        name = "reports_legacy.py"
        open(f"{services}/{name}", "w").write(VULN_FILE)
    else:
        dom = DOMAINS[i % len(DOMAINS)]
        body = HEADER % {"dom": dom, "act": "svc", "year": 2025, "mod": f"svc_{dom}_{i}"}
        for j in range(rnd.randint(3, 6)):
            body += FUNC % {"name": f"s{j}", "dom": dom, "act": ACTIONS[(i+j) % len(ACTIONS)],
                            "Act": ACTIONS[(i+j) % len(ACTIONS)].capitalize(), "tbl": f"sv_{dom}"}
        open(f"{services}/svc_{dom}_{i}.py", "w").write(body)
    files += 1

# каталог tests/ — тоже шум
tests = OUT + "/tests"
os.makedirs(tests, exist_ok=True)
for i in range(24):
    dom = DOMAINS[i % len(DOMAINS)]
    open(f"{tests}/test_{dom}.py", "w").write(
        f'from app.services.svc_{dom}_0 import *\n\ndef test_{dom}_smoke():\n    assert True\ndef test_{dom}_empty_batch():\n    assert (lambda p: p)(dict(items=[]))\n')
    files += 1

truth = {"file": f"app/services/reports_legacy.py", "function": "build_invoice_report",
         "param": "period", "seed": SEED, "vuln_slot": vuln_slot}
json.dump(truth, open(TRUTH, "w"), indent=1)
total_bytes = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(OUT) for f in fs)
print(f"arena ready: {files} files, {total_bytes/1024:.0f} KiB -> {OUT}; truth at {TRUTH}")

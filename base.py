"""
IPL Fantasy 2026 — Shared Base Module                        base v1.0.0
===========================================================================
Bugfix: breaks the circular import between server.py and routes.py.

Previously routes.py did `import server as _srv` to reach CURRENT_PUBLIC_URL,
which forced Python to re-execute server.py while still mid-import, deadlocking
on `from routes import bp` (bp not yet defined at that point).

Fix — clean linear dependency graph:
    config.py → base.py → routes.py → server.py

base.py owns ALL shared state:
  • Flask app singleton
  • DatabaseManager singleton (db)
  • Logging, rate limiter, _db_con, player resolver
  • Path / game constants
  • CURRENT_PUBLIC_URL (mutable — updated by server.py via `base.CURRENT_PUBLIC_URL = url`)

Neither routes.py nor server.py imports from the other.
"""

import collections
import hashlib as _hashlib
import json as _json
import logging as _logging
import re
import secrets as _secrets
import sqlite3
import threading
import time
import unicodedata

from flask import Flask, request, jsonify, render_template
from db_manager import DatabaseManager
from config import DB_PATH, BASE_DIR, DATA_DIR  # paths — single source of truth
from logic.fuzzy_match import _SEMANTIC_MAP  # single source — was duplicated here

# ── Paths / Game constants ──────────────────────────────────────────────────────────

STATIC_DIR = BASE_DIR / "Static"   # capital S — actual on-disk folder; Linux is case-sensitive
DATA_DIR.mkdir(exist_ok=True)

BUDGET_TOTAL = 100.0
XI_SIZE      = 11
# Matches the IPL 2026 schedule in data/schedule.json — 10 fantasy weeks
# spanning the season (Weeks 1–10, totalling 74 matches; see schedule's
# week-breakdown log line on workflow runs). Originally 8 (placeholder
# from before the full schedule landed). Bumped 2026-05-18 after the
# Monday rollover at the end of W8 returned "season_complete" instead
# of producing a `ui:rollover:w9` commit — the api_rollover endpoint
# at routes.py:1138 short-circuits when current_week >= MAX_WEEKS.
MAX_WEEKS    = 10

_ID_RE = re.compile(r'^[a-z]{1,3}\d{1,2}$')


# ── Player resolver ───────────────────────────────────────────────────────────────────
# Uses _SEMANTIC_MAP imported from logic/fuzzy_match — single source of truth.

def _normalise(s):
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _token_set_ratio(a, b):
    ta = set(_normalise(a).split()); tb = set(_normalise(b).split())
    if not ta or not tb: return 0.0
    exp = set()
    for t in ta:
        if len(t)==1:
            for x in tb:
                if x.startswith(t): exp.add(x)
        else: exp.add(t)
    inter = exp & tb; union = exp | tb
    return len(inter)/len(union) if union else 0.0

def _load_all_players(con):
    return [dict(r) for r in con.execute("SELECT id,name,team,role,price FROM players").fetchall()]

def resolve_player_id(con, input_str, team_hint=None, fuzzy_threshold=0.40):
    if not input_str: return None
    raw=str(input_str).strip(); norm=_normalise(raw)
    th=(team_hint or "").strip().upper() if team_hint else None
    players=_load_all_players(con)
    if not players: return None
    for p in players:
        if p["id"]==raw: return {**p,"_match_tier":1}
    if th:
        for p in players:
            if _normalise(p["name"])==norm and p["team"].upper()==th: return {**p,"_match_tier":2}
    for p in players:
        if _normalise(p["name"])==norm: return {**p,"_match_tier":3}
    st=_SEMANTIC_MAP.get(norm) or _SEMANTIC_MAP.get(raw.lower())
    if st:
        sn=_normalise(st)
        if th:
            for p in players:
                if _normalise(p["name"])==sn and p["team"].upper()==th: return {**p,"_match_tier":4}
        for p in players:
            if _normalise(p["name"])==sn: return {**p,"_match_tier":4}
    best=fuzzy_threshold; bp=None
    for p in players:
        sc=_token_set_ratio(norm,_normalise(p["name"]))
        if th and p["team"].upper()==th: sc=min(1.0,sc+0.12)
        if sc>best: best=sc; bp=p
    if bp: return {**bp,"_match_tier":5}
    words=norm.split(); last=words[-1] if words else norm
    if len(last)>=3:
        hits=[p for p in players if (_normalise(p["name"]).split() or [""])[-1]==last]
        if hits:
            if th:
                th_hits=[p for p in hits if p["team"].upper()==th]
                if th_hits: return {**th_hits[0],"_match_tier":6}
            return {**hits[0],"_match_tier":6}
    return None

def resolve_id_list(con, id_or_name_list, display_name=None, week_no=None):
    resolved=[]; log=[]; needs_patch=False
    for item in id_or_name_list:
        s=str(item).strip() if item else ""
        if _ID_RE.match(s):
            resolved.append(s); log.append({"input":s,"output":s,"tier":0,"action":"passthrough"}); continue
        match=resolve_player_id(con,s)
        if match:
            canonical=match["id"]; action="corrected" if canonical!=s else "resolved"
            if action=="corrected": needs_patch=True
            resolved.append(canonical)
            log.append({"input":s,"output":canonical,"name":match["name"],"team":match["team"],"tier":match["_match_tier"],"action":action})
        else:
            resolved.append(s); log.append({"input":s,"output":s,"tier":-1,"action":"unresolved"})
            _log(f"[resolver] UNRESOLVED: '{s}'","warning")
    if needs_patch and display_name and week_no is not None:
        try:
            con.execute("UPDATE user_selections SET nw_team_json=? WHERE display_name=? AND week_no=?",
                        (_json.dumps(resolved),display_name,week_no))
        except Exception as e:
            _log(f"[resolver] Write-back failed: {e}","warning")
    return resolved, log


# ── Safe JSON loader ──────────────────────────────────────────────────────────────────

def _jloads(s, default=None):
    if not s: return default
    try: return _json.loads(s)
    except: return default


# ── Logging ─────────────────────────────────────────────────────────────────────

def _setup_logging():
    fmt=_logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",datefmt="%Y-%m-%d %H:%M:%S")
    logger=_logging.getLogger("ipl"); logger.setLevel(_logging.DEBUG)
    ch=_logging.StreamHandler(); ch.setFormatter(fmt); ch.setLevel(_logging.INFO); logger.addHandler(ch)
    try:
        from logging.handlers import RotatingFileHandler
        fh=RotatingFileHandler(BASE_DIR/"server.log",maxBytes=1_000_000,backupCount=3,encoding="utf-8")
        fh.setFormatter(fmt); fh.setLevel(_logging.DEBUG); logger.addHandler(fh)
    except Exception as e:
        print(f"  warning: log file error: {e}")
    return logger

_logger=_setup_logging()
def _log(msg,level="info"): getattr(_logger,level,_logger.info)(msg)


# ── DB connection factory ────────────────────────────────────────────────────────────────

def _db_con():
    con=sqlite3.connect(str(DB_PATH),timeout=30)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


# ── Rate limiter ────────────────────────────────────────────────────────────────────

class _RateLimiter:
    def __init__(self,max_calls=30,window_seconds=60):
        self._max=max_calls; self._win=window_seconds
        self._calls=collections.defaultdict(list); self._lock=threading.Lock()
    def is_allowed(self,ip):
        now=time.time()
        with self._lock:
            self._calls[ip]=[t for t in self._calls[ip] if now-t<self._win]
            if len(self._calls[ip])>=self._max: return False
            self._calls[ip].append(now); return True

_write_limiter=_RateLimiter(30,60)

def _check_rate(lim):
    ip=request.remote_addr or "unknown"
    if not lim.is_allowed(ip):
        return jsonify({"error":"Too many requests","code":429}),429
    return None


# ── Singletons ───────────────────────────────────────────────────────────────────────

db  = DatabaseManager(DB_PATH)
app = Flask(
    __name__,
    template_folder=str(BASE_DIR/"templates"),
    # Capital "Static" — the on-disk folder name. Windows is case-insensitive
    # so "static" worked locally, but Render / any Linux host treats this as
    # case-sensitive and 404s every /static/* asset (including ipl_glue.js,
    # which then leaves the UI stuck on "Loading your league...").
    static_folder=str(BASE_DIR/"Static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024


# ── Flask error handlers + middleware ────────────────────────────────────────────────

@app.errorhandler(sqlite3.IntegrityError)
def _handle_integrity(e):
    msg=str(e)
    if "UNIQUE" in msg.upper(): return jsonify({"error":f"Duplicate record: {msg}","code":400}),400
    if "CHECK" in msg.upper() or "FOREIGN KEY" in msg.upper(): return jsonify({"error":f"Constraint: {msg}","code":400}),400
    return jsonify({"error":msg,"code":400}),400

@app.errorhandler(sqlite3.OperationalError)
def _handle_operational(e):
    _log(f"SQLite error: {e}","error")
    return jsonify({"error":"Database error","detail":str(e),"code":500}),500

@app.errorhandler(500)
def _handle_500(e):
    _log(f"500: {e}","error"); return jsonify({"error":"Internal error","code":500}),500

@app.errorhandler(404)
def _handle_404(e):
    try: return render_template("index.html"),200
    except: return jsonify({"error":"Not found","code":404}),404

@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="same-origin"
    if request.path.startswith("/api/"): response.headers["Cache-Control"]="no-store"
    return response


# ── Mutable global — updated by server.py via `import base; base.CURRENT_PUBLIC_URL = url` ──

CURRENT_PUBLIC_URL = ""


# ── Passcode + session helpers (Phase: Passcodes) ────────────────────────────
# Threat model: 4-digit passcode is friction, not real auth. Salting with the
# username prevents identical-passcode users from sharing the same hash; the
# 4-digit space (10k combos) is offline-brute-forceable by anyone with the DB.
# Treat passcodes as "stops the casual sibling," not as a secret credential.

PASSCODE_RE = re.compile(r"^\d{4}$")

def hash_passcode(passcode: str, username: str) -> str:
    return _hashlib.sha256(f"{username}:{passcode}".encode("utf-8")).hexdigest()

def verify_passcode(passcode: str, username: str, stored_hash: str) -> bool:
    if not stored_hash: return False
    return _secrets.compare_digest(hash_passcode(passcode, username), stored_hash)

def new_session_token() -> str:
    return _secrets.token_hex(32)

def get_bearer_token():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        t = h[7:].strip()
        return t or None
    return None

"""
IPL Fantasy 2026 — Flask Server                             Golden File v10
===========================================================================
All persistence delegated to DatabaseManager / GoldenDB (db_manager.py).
This file owns: routing, rate-limiting, tunnel management, startup,
season-history seeding, rolling-week rollover, and the intelligent
player-matching / fuzzy-resolution engine.

v10 changes (Phase-2 polish):
  • /api/rollover passes _resolver closure into db.rollover_season()
    so carry-forward IDs are canonicalised by the full fuzzy engine.
"""

import collections
import json as _json
import logging as _logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests as _req
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from flask import Flask, request, jsonify, render_template, send_from_directory
from db_manager import DatabaseManager

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR / "data"
DB_PATH    = DATA_DIR / "fantasy.db"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR.mkdir(exist_ok=True)

# ── Season config ──────────────────────────────────────────────────────────────
BUDGET_TOTAL  = 100.0
XI_SIZE       = 11
MAX_WEEKS     = 8

# ── Rollover deadline ──────────────────────────────────────────────────────────
DEADLINE_HOUR = 14
DEADLINE_MIN  = 0

# ── ID pattern (r01, x23, rr15, d01, g08 …) ───────────────────────────────────
_ID_RE = re.compile(r'^[a-z]{1,3}\d{1,2}$')

# ── Semantic shorthand map ─────────────────────────────────────────────────────
_SEMANTIC_MAP = {
    "vk":          "virat kohli",
    "rohit":       "rohit sharma",
    "ms":          "ms dhoni",
    "msd":         "ms dhoni",
    "bumrah":      "jasprit bumrah",
    "bumpy":       "jasprit bumrah",
    "jadeja":      "ravindra jadeja",
    "sky":         "suryakumar yadav",
    "kl":          "kl rahul",
    "klr":         "kl rahul",
    "hp":          "hardik pandya",
    "h pandya":    "hardik pandya",
    "pandya":      "hardik pandya",
    "shami":       "mohammed shami",
    "siraj":       "mohammed siraj",
    "chahal":      "yuzvendra chahal",
    "sam":         "sanju samson",
    "ishan":       "ishan kishan",
    "ik":          "ishan kishan",
    "salt":        "phil salt",
    "klaasen":     "heinrich klaasen",
    "david":       "tim david",
    "shepherd":    "romario shepherd",
    "rutherford":  "shimron rutherford",
    "patidar":     "rajat patidar",
    "chakravarthy":"varun chakravarthy",
    "chakra":      "varun chakravarthy",
    "chakar":      "varun chakravarthy",
    "vc":          "varun chakravarthy",
    "chahar":      "deepak chahar",
    "duffy":       "jacob duffy",
    "patel":       "axar patel",
    "varma":       "tilak varma",
    "rahane":      "ajinkya rahane",
    "ravindra":    "rachin ravindra",
    "suryavanshi": "vaibhav suryavanshi",
    "jansen":      "marco jansen",
    "brevis":      "dewald brevis",
    "rickelton":   "ryan rickelton",
    "ngidi":       "lungi ngidi",
    "hetmyer":     "shimron hetmyer",
    "rana":        "nitish rana",
    "pant":        "rishabh pant",
    "noor":        "noor ahmad",
    "dube":        "shivam dube",
    "samson":      "sanju samson",
    "tharva":      "atharva taide",
}


# ═══════════════════════════════════════════════════════════════════════════════
# SEASON HISTORY SEED DATA
# ═══════════════════════════════════════════════════════════════════════════════

_SAI_W0_TEAM = ["k16","m12","r20","m09","d04","rr11","r01","k01","m03","s03","r04"]
_SAI_W0_CAP  = "r01"
_SAI_W0_VC   = "k16"

_MOE_W0_TEAM = ["k16","m09","k11","r08","r09","m07","r02","m03","s03","r04","s04"]
_MOE_W0_CAP  = "r04"
_MOE_W0_VC   = "k16"

_SAI_W1_TEAM = ["d12","rr08","g11","c05","g08","rr15","rr05","s22","rr03","p01","s03"]
_SAI_W1_CAP  = "rr03"
_SAI_W1_VC   = "rr15"

_MOE_W1_TEAM = ["m10","r15","k10","r09","m11","m20","rr05","rr11","m04","s03","d01"]
_MOE_W1_CAP  = "d01"
_MOE_W1_VC   = "s03"

_HISTORY_SEED = [
    ("Sai", 0, _SAI_W0_TEAM, _SAI_W0_CAP, _SAI_W0_VC),
    ("Moe", 0, _MOE_W0_TEAM, _MOE_W0_CAP, _MOE_W0_VC),
    ("Sai", 1, _SAI_W1_TEAM, _SAI_W1_CAP, _SAI_W1_VC),
    ("Moe", 1, _MOE_W1_TEAM, _MOE_W1_CAP, _MOE_W1_VC),
]


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENT PLAYER-MATCHING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise(s: str) -> str:
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_set_ratio(a: str, b: str) -> float:
    ta = set(_normalise(a).split())
    tb = set(_normalise(b).split())
    if not ta or not tb:
        return 0.0
    expanded_a = set()
    for t in ta:
        if len(t) == 1:
            for tb_tok in tb:
                if tb_tok.startswith(t):
                    expanded_a.add(tb_tok)
        else:
            expanded_a.add(t)
    intersection = expanded_a & tb
    union = expanded_a | tb
    return len(intersection) / len(union) if union else 0.0


def _load_all_players(con) -> list:
    rows = con.execute("SELECT id, name, team, role, price FROM players").fetchall()
    return [dict(r) for r in rows]


def resolve_player_id(
    con,
    input_str: str,
    team_hint: str = None,
    fuzzy_threshold: float = 0.40,
) -> dict | None:
    if not input_str:
        return None

    raw  = str(input_str).strip()
    norm = _normalise(raw)
    th   = (team_hint or "").strip().upper() if team_hint else None

    players = _load_all_players(con)
    if not players:
        return None

    for p in players:
        if p["id"] == raw:
            return {**p, "_match_tier": 1}

    if th:
        for p in players:
            if _normalise(p["name"]) == norm and p["team"].upper() == th:
                return {**p, "_match_tier": 2}

    for p in players:
        if _normalise(p["name"]) == norm:
            return {**p, "_match_tier": 3}

    semantic_target = _SEMANTIC_MAP.get(norm) or _SEMANTIC_MAP.get(raw.lower())
    if semantic_target:
        st_norm = _normalise(semantic_target)
        if th:
            for p in players:
                if _normalise(p["name"]) == st_norm and p["team"].upper() == th:
                    return {**p, "_match_tier": 4}
        for p in players:
            if _normalise(p["name"]) == st_norm:
                return {**p, "_match_tier": 4}

    best_score  = fuzzy_threshold
    best_player = None
    for p in players:
        score = _token_set_ratio(norm, _normalise(p["name"]))
        if th and p["team"].upper() == th:
            score = min(1.0, score + 0.12)
        if score > best_score:
            best_score  = score
            best_player = p
    if best_player:
        return {**best_player, "_match_tier": 5}

    words_in = norm.split()
    last_in  = words_in[-1] if words_in else norm
    if len(last_in) >= 3:
        suffix_hits = []
        for p in players:
            p_words = _normalise(p["name"]).split()
            p_last  = p_words[-1] if p_words else ""
            if p_last == last_in:
                suffix_hits.append(p)
        if suffix_hits:
            if th:
                th_hits = [p for p in suffix_hits if p["team"].upper() == th]
                if th_hits:
                    return {**th_hits[0], "_match_tier": 6}
            return {**suffix_hits[0], "_match_tier": 6}

    return None


def resolve_id_list(
    con,
    id_or_name_list: list,
    display_name: str = None,
    week_no: int = None,
) -> tuple:
    resolved    = []
    log         = []
    needs_patch = False

    for item in id_or_name_list:
        s = str(item).strip() if item else ""

        if _ID_RE.match(s):
            resolved.append(s)
            log.append({"input": s, "output": s, "tier": 0, "action": "passthrough"})
            continue

        match = resolve_player_id(con, s)
        if match:
            canonical = match["id"]
            action    = "corrected" if canonical != s else "resolved"
            if action == "corrected":
                needs_patch = True
            resolved.append(canonical)
            log.append({
                "input":  s,
                "output": canonical,
                "name":   match["name"],
                "team":   match["team"],
                "tier":   match["_match_tier"],
                "action": action,
            })
        else:
            resolved.append(s)
            log.append({"input": s, "output": s, "tier": -1, "action": "unresolved"})
            _log(f"[resolver] UNRESOLVED: '{s}'", "warning")

    if needs_patch and display_name and week_no is not None:
        try:
            con.execute(
                """UPDATE user_selections
                   SET nw_team_json = ?
                   WHERE display_name = ? AND week_no = ?""",
                (_json.dumps(resolved), display_name, week_no),
            )
            _log(f"[resolver] Write-back patched {display_name}/W{week_no}")
        except Exception as e:
            _log(f"[resolver] Write-back failed: {e}", "warning")

    return resolved, log


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════════

class _RateLimiter:
    def __init__(self, max_calls: int = 30, window_seconds: int = 60):
        self._max   = max_calls
        self._win   = window_seconds
        self._calls: dict = collections.defaultdict(list)
        self._lock  = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            self._calls[ip] = [t for t in self._calls[ip] if now - t < self._win]
            if len(self._calls[ip]) >= self._max:
                return False
            self._calls[ip].append(now)
            return True


_write_limiter = _RateLimiter(max_calls=30, window_seconds=60)

def _check_rate(limiter: _RateLimiter):
    ip = request.remote_addr or "unknown"
    if not limiter.is_allowed(ip):
        return jsonify({"error": "Too many requests — slow down", "code": 429}), 429
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_logging():
    fmt = _logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logger = _logging.getLogger("ipl")
    logger.setLevel(_logging.DEBUG)
    ch = _logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(_logging.INFO)
    logger.addHandler(ch)
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(
            BASE_DIR / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(_logging.DEBUG)
        logger.addHandler(fh)
    except Exception as e:
        print(f"  warning: could not set up log file: {e}")
    return logger


_logger = _setup_logging()

def _log(msg: str, level: str = "info"):
    getattr(_logger, level, _logger.info)(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# DB HELPERS  (resolver-only — all route SQL delegated to DatabaseManager)
# ═══════════════════════════════════════════════════════════════════════════════

def _db_con():
    """Raw sqlite3 connection used exclusively by resolver functions."""
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-SEED MATCH DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _auto_seed_if_needed():
    seed_script = BASE_DIR / "Seed_ipl2026.py"
    if not seed_script.exists():
        seed_script = BASE_DIR / "seed_ipl2026.py"
    if not seed_script.exists():
        return
    try:
        con = sqlite3.connect(str(DB_PATH), timeout=10)
        match_count = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        con.close()
    except Exception:
        return
    if match_count > 0:
        return
    print("\n  [startup] No match data — running seed script ...")
    try:
        subprocess.run([sys.executable, str(seed_script)], cwd=str(BASE_DIR), timeout=60)
        print("  [startup] Seed complete.\n")
    except Exception as e:
        print(f"  [startup] Could not run seed script: {e}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-SEED SEASON HISTORY  (W0 + W1 for Sai & Moe)
# ═══════════════════════════════════════════════════════════════════════════════

def _auto_seed_history_if_needed():
    def _looks_like_ids(team_json: str) -> bool:
        try:
            arr = _json.loads(team_json or "[]")
        except Exception:
            return False
        if not arr:
            return True
        return all(_ID_RE.match(str(v)) for v in arr)

    try:
        con = _db_con()
        seeded   = []
        replaced = []

        for name, week_no, team, cap, vc in _HISTORY_SEED:
            existing = con.execute(
                "SELECT tw_team_json FROM user_selections WHERE display_name=? AND week_no=?",
                (name, week_no),
            ).fetchone()

            if existing:
                if _looks_like_ids(existing["tw_team_json"]):
                    continue
                else:
                    resolved_team, rlog = resolve_id_list(con, team)
                    unresolved = [e for e in rlog if e["action"] == "unresolved"]
                    if unresolved:
                        print(f"  [startup] Warn: unresolved in {name}/W{week_no}: {unresolved}")
                    con.execute(
                        "DELETE FROM user_selections WHERE display_name=? AND week_no=?",
                        (name, week_no),
                    )
                    team = resolved_team
                    if cap and not _ID_RE.match(str(cap)):
                        m = resolve_player_id(con, cap)
                        if m:
                            cap = m["id"]
                    if vc and not _ID_RE.match(str(vc)):
                        m = resolve_player_id(con, vc)
                        if m:
                            vc = m["id"]
                    replaced.append(f"{name}/W{week_no}")

            con.execute("""
                INSERT INTO user_selections
                    (display_name, week_no,
                     tw_team_json, tw_cap_id, tw_vc_id,
                     nw_team_json, nw_cap_id, nw_vc_id)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(display_name, week_no) DO UPDATE SET
                    tw_team_json = excluded.tw_team_json,
                    tw_cap_id    = excluded.tw_cap_id,
                    tw_vc_id     = excluded.tw_vc_id
            """, (
                name, week_no,
                _json.dumps(team), cap, vc,
                _json.dumps(team), cap, vc,
            ))
            seeded.append(f"{name}/W{week_no}")

        if replaced:
            print(f"  [startup] Replaced stale seeds: {', '.join(replaced)}")
        if seeded:
            now_iso = datetime.now(timezone.utc).isoformat()
            con.execute(
                "INSERT OR REPLACE INTO meta (key,value) VALUES ('_saved',?)",
                (now_iso,),
            )
            con.commit()
            print(f"  [startup] History seeded: {', '.join(seeded)}")
        else:
            print("  [startup] Season history up-to-date — no reseed needed.")
        con.close()
    except Exception as e:
        print(f"  [startup] Could not seed history: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DB SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

db = DatabaseManager(DB_PATH)


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK APP
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024


@app.errorhandler(sqlite3.IntegrityError)
def _handle_integrity(e):
    msg = str(e)
    if "UNIQUE" in msg.upper():
        return jsonify({"error": f"Duplicate record: {msg}", "code": 400}), 400
    if "CHECK" in msg.upper() or "FOREIGN KEY" in msg.upper():
        return jsonify({"error": f"Constraint violation: {msg}", "code": 400}), 400
    return jsonify({"error": msg, "code": 400}), 400

@app.errorhandler(sqlite3.OperationalError)
def _handle_operational(e):
    _log(f"SQLite operational error: {e}", "error")
    return jsonify({"error": "Database error", "detail": str(e), "code": 500}), 500

@app.errorhandler(500)
def _handle_500(e):
    _log(f"Unhandled 500: {e}", "error")
    return jsonify({"error": "Internal server error", "code": 500}), 500

@app.errorhandler(404)
def _handle_404(e):
    try:
        return render_template("index.html"), 200
    except Exception:
        return jsonify({"error": "Not found", "code": 404}), 404


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["Referrer-Policy"]        = "same-origin"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        _log(f"GET / failed: {e}", "error")
        return f"<h1>Server error</h1><p>{e}</p>", 500


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/state", methods=["GET"])
def api_get_state():
    try:
        state = db.get_state()
        etag  = state.get("_saved", "")
        if request.headers.get("If-None-Match") == etag:
            return "", 304
        resp = jsonify(state)
        resp.headers["ETag"] = etag
        return resp
    except Exception as e:
        _log(f"GET /api/state failed: {e}", "error")
        return jsonify({"error": str(e), "code": 500}), 500


@app.route("/api/state", methods=["POST"])
def api_save_state():
    rate_err = _check_rate(_write_limiter)
    if rate_err:
        return rate_err
    try:
        d = request.get_json(force=True, silent=True)
        if not isinstance(d, dict):
            return jsonify({"error": "bad payload", "code": 400}), 400
        db.save_state(d)
        return jsonify({"ok": True})
    except Exception as e:
        _log(f"POST /api/state failed: {e}", "error")
        return jsonify({"error": str(e), "code": 500}), 500


@app.route("/api/current-week", methods=["GET"])
def api_current_week():
    try:
        return jsonify({"week_no": db.get_current_week(), "max_weeks": MAX_WEEKS, "ok": True})
    except Exception as e:
        _log(f"GET /api/current-week failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/resolve-player", methods=["POST"])
def api_resolve_player():
    rate_err = _check_rate(_write_limiter)
    if rate_err:
        return rate_err
    try:
        d     = request.get_json(force=True, silent=True) or {}
        query = (d.get("query") or "").strip()
        team  = (d.get("team")  or "").strip() or None
        if not query:
            return jsonify({"error": "query is required", "code": 400}), 400
        con   = _db_con()
        match = resolve_player_id(con, query, team_hint=team)
        con.close()
        if not match:
            return jsonify({"ok": False, "error": "No match found", "input": query}), 404
        tier = match.pop("_match_tier", None)
        return jsonify({
            "ok":         True,
            "input":      query,
            "match_tier": tier,
            "resolved":   {k: match[k] for k in ("id", "name", "team", "role", "price")},
        })
    except Exception as e:
        _log(f"POST /api/resolve-player failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/history/<n>", methods=["GET"])
def api_history(n):
    try:
        if not n or len(n) > 30:
            return jsonify({"error": "invalid name", "code": 400}), 400
        return jsonify(db.get_history(n))
    except Exception as e:
        _log(f"GET /api/history/{n} failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/save-next-week/<n>", methods=["POST"])
def api_save_next_week(n):
    rate_err = _check_rate(_write_limiter)
    if rate_err:
        return rate_err
    try:
        if not n or len(n) > 30:
            return jsonify({"error": "invalid name", "code": 400}), 400
        d = request.get_json(force=True, silent=True)
        if not isinstance(d, dict):
            return jsonify({"error": "expected JSON object", "code": 400}), 400
        team = d.get("team", [])
        cap  = d.get("cap")
        vc   = d.get("vc")
        if not isinstance(team, list):
            return jsonify({"error": "team must be a list", "code": 400}), 400

        resolution_log = []
        if team:
            con = _db_con()
            team, resolution_log = resolve_id_list(
                con, team, display_name=n, week_no=db.get_current_week()
            )
            if cap and not _ID_RE.match(str(cap)):
                cap_m = resolve_player_id(con, cap)
                if cap_m:
                    cap = cap_m["id"]
            if vc and not _ID_RE.match(str(vc)):
                vc_m = resolve_player_id(con, vc)
                if vc_m:
                    vc = vc_m["id"]
            con.close()

        if team and len(team) != XI_SIZE:
            return jsonify({
                "error": f"Squad must have exactly {XI_SIZE} players (got {len(team)})",
                "code": 422,
            }), 422

        if team:
            is_valid, total_cost = db.validate_budget(team, BUDGET_TOTAL)
            if not is_valid:
                return jsonify({
                    "error":      f"Budget exceeded: {total_cost:.1f} CR > {BUDGET_TOTAL:.1f} CR limit",
                    "total_cost": total_cost,
                    "budget":     BUDGET_TOTAL,
                    "code":       422,
                }), 422
        else:
            total_cost = 0.0

        result = db.save_next_week(n, team, cap, vc)
        return jsonify({
            "ok":             True,
            "week_no":        result["week_no"],
            "total_cost":     total_cost,
            "resolution_log": resolution_log,
        })
    except sqlite3.IntegrityError as e:
        return jsonify({"error": str(e), "code": 400}), 400
    except Exception as e:
        _log(f"POST /api/save-next-week/{n} failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/member/<n>", methods=["PUT"])
def api_member(n):
    rate_err = _check_rate(_write_limiter)
    if rate_err:
        return rate_err
    try:
        if not n or len(n) > 30:
            return jsonify({"error": "Team name must be 1-30 characters", "code": 400}), 400
        d = request.get_json(force=True, silent=True)
        if not isinstance(d, dict):
            return jsonify({"error": "Invalid JSON payload", "code": 400}), 400
        db.upsert_member(n, d)
        return jsonify({"ok": True})
    except Exception as e:
        _log(f"PUT /api/member/{n} failed: {e}", "error")
        return jsonify({"error": str(e), "code": 500}), 500


@app.route("/api/match", methods=["POST"])
def api_match():
    rate_err = _check_rate(_write_limiter)
    if rate_err:
        return rate_err
    try:
        m = request.get_json(force=True, silent=True)
        if not isinstance(m, dict) or "id" not in m:
            return jsonify({"error": "missing id", "code": 400}), 400
        db.upsert_match(m)
        return jsonify({"ok": True})
    except Exception as e:
        _log(f"POST /api/match failed: {e}", "error")
        return jsonify({"error": str(e), "code": 500}), 500


@app.route("/api/rollover", methods=["POST"])
def api_rollover():
    """
    POST /api/rollover[?force=1]
    Delegates to db.rollover_season() with a resolver_callback that wraps
    the full fuzzy-resolution engine so carry-forward IDs are canonical.
    """
    force = request.args.get("force", "").strip() in ("1", "true", "yes")
    try:
        def _resolver(ids: list) -> list:
            con = _db_con()
            try:
                resolved, _ = resolve_id_list(con, ids)
            finally:
                con.close()
            return resolved

        result = db.rollover_season(
            force=force,
            max_weeks=MAX_WEEKS,
            deadline_hour=DEADLINE_HOUR,
            deadline_min=DEADLINE_MIN,
            resolver_callback=_resolver,
        )
        return jsonify(result)
    except Exception as e:
        _log(f"POST /api/rollover failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/seed-history", methods=["POST"])
def api_seed_history():
    rate_err = _check_rate(_write_limiter)
    if rate_err:
        return rate_err
    try:
        _auto_seed_history_if_needed()
        return jsonify({"ok": True})
    except Exception as e:
        _log(f"POST /api/seed-history failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/players", methods=["GET"])
def api_players():
    try:
        players = db.get_players()
        by_id   = {p["id"]:           p for p in players}
        by_name = {p["name"].lower(): p for p in players}
        return jsonify({"players": players, "by_id": by_id, "by_name": by_name, "ok": True})
    except Exception as e:
        _log(f"GET /api/players failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/api/ping")
def api_ping():
    try:
        stats = db.ping_stats()
        stats["ok"]         = True
        stats["public_url"] = CURRENT_PUBLIC_URL
        stats["budget"]     = BUDGET_TOTAL
        stats["xi_size"]    = XI_SIZE
        stats["max_weeks"]  = MAX_WEEKS
        return jsonify(stats)
    except Exception as e:
        _log(f"GET /api/ping failed: {e}", "error")
        return jsonify({"ok": False, "error": str(e), "code": 500}), 500


@app.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    try:
        week_param = request.args.get("week", "").strip()
        week_no    = int(week_param) if week_param.isdigit() else None
        result     = db.get_leaderboard(week_no=week_no)
        return jsonify(result)
    except Exception as e:
        _log(f"GET /api/leaderboard failed: {e}", "error")
        return jsonify({"error": str(e), "code": 500}), 500


@app.route("/api/poll", methods=["GET"])
def api_poll():
    try:
        etags = db.get_etags()
        return jsonify({"state_etag": etags["state"], "ok": True})
    except Exception as e:
        _log(f"GET /api/poll failed: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "IPL Fantasy 2026", "short_name": "IPL Fantasy",
        "description": "Private IPL fantasy cricket league",
        "start_url": "/", "display": "standalone",
        "background_color": "#07111F", "theme_color": "#07111F",
        "icons": [{"src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
                           "viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F3CF;</text></svg>",
                   "sizes": "any", "type": "image/svg+xml"}],
    })


@app.route("/offline")
def offline_page():
    return (
        "<html><body style='background:#07111F;color:#D8E8F5;font-family:sans-serif;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        "<div style='text-align:center'>"
        "<div style='font-size:64px'>&#x1F3CF;</div>"
        "<h1 style='color:#F5C518;margin:16px 0 8px'>You're offline</h1>"
        "<p style='color:#5F7A9B'>Check your connection and try again.</p>"
        "</div></body></html>"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC URL TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

CURRENT_PUBLIC_URL = ""


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════════════════════
# TUNNEL SUPPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TunnelResult:
    def __init__(self, provider: str, url: str, proc):
        self.provider = provider
        self.url      = url
        self.proc     = proc

    def stop(self):
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass


def _run_bg(cmd: list) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )


def try_cloudflare(port):
    exe = shutil.which("cloudflared")
    if not exe:
        return None
    print("  -> Trying Cloudflare Tunnel...")
    try:
        cmd  = [exe, "tunnel", "--url", f"http://localhost:{port}"]
        proc = _run_bg(cmd)
        url  = None
        deadline = time.time() + 30
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.3)
                continue
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                break
            if proc.poll() is not None:
                break
        if url:
            return TunnelResult("Cloudflare", url, proc)
        proc.terminate()
    except Exception as e:
        print(f"    cloudflare error: {e}")
    return None


def try_ngrok(port):
    exe = shutil.which("ngrok")
    if not exe:
        return None
    print("  -> Trying ngrok...")
    try:
        cmd  = [exe, "http", str(port), "--log", "stdout"]
        proc = _run_bg(cmd)
        url  = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.3)
                continue
            m = re.search(r"https://[a-z0-9-]+\.ngrok(-free)?\.app", line)
            if m:
                url = m.group(0)
                break
            if proc.poll() is not None:
                break
        if url:
            return TunnelResult("ngrok", url, proc)
        proc.terminate()
    except Exception as e:
        print(f"    ngrok error: {e}")
    return None


def try_pinggy(port):
    exe = shutil.which("ssh")
    if not exe:
        return None
    print("  -> Trying Pinggy...")
    try:
        cmd = [exe, "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
               "-p", "443", "-R", f"0:localhost:{port}", "a.pinggy.io"]
        proc = _run_bg(cmd)
        url  = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline() or proc.stderr.readline()
            if not line:
                time.sleep(0.3)
                continue
            m = re.search(r"https://[a-z0-9-]+\.a\.free\.pinggy\.link", line)
            if m:
                url = m.group(0)
                break
            if proc.poll() is not None:
                break
        if url:
            return TunnelResult("Pinggy", url, proc)
        proc.terminate()
    except Exception as e:
        print(f"    pinggy error: {e}")
    return None


def try_localhost_run(port):
    exe = shutil.which("ssh")
    if not exe:
        return None
    print("  -> Trying localhost.run...")
    try:
        cmd = [exe, "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
               "-R", f"80:localhost:{port}", "nokey@localhost.run"]
        proc = _run_bg(cmd)
        url  = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline() or proc.stderr.readline()
            if not line:
                time.sleep(0.3)
                continue
            m = re.search(r"https://[a-z0-9-]+\.lhr\.life", line)
            if m:
                url = m.group(0)
                break
            if proc.poll() is not None:
                break
        if url:
            return TunnelResult("localhost.run", url, proc)
        proc.terminate()
    except Exception as e:
        print(f"    localhost.run error: {e}")
    return None


def start_tunnel(port, provider="auto"):
    if provider == "cloudflare":   return try_cloudflare(port)
    if provider == "ngrok":        return try_ngrok(port)
    if provider == "pinggy":       return try_pinggy(port)
    if provider == "localhostrun": return try_localhost_run(port)
    for fn in [try_cloudflare, try_ngrok, try_pinggy, try_localhost_run]:
        result = fn(port)
        if result:
            return result
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP BANNER
# ═══════════════════════════════════════════════════════════════════════════════

WIDE = 58

def banner_line(text="", fill=" "):
    pad = WIDE - len(text)
    return f"||  {text}{fill * max(0, pad - 2)}||"

def print_banner(port, tunnel, lan_ip):
    bar = "=" * WIDE
    print(f"\n+{bar}+")
    print(f"|{'  IPL FANTASY 2026':^{WIDE}}|")
    print(f"+{bar}+")
    print(banner_line(f"Local:    http://localhost:{port}"))
    print(banner_line(f"Network:  http://{lan_ip}:{port}  (same Wi-Fi)"))
    print(f"+{bar}+")
    if tunnel:
        print(banner_line(f"PUBLIC URL ({tunnel.provider}):"))
        url = tunnel.url
        sys.modules[__name__].CURRENT_PUBLIC_URL = url
        for i in range(0, len(url), WIDE - 4):
            print(banner_line(f"   {url[i:i+WIDE-4]}"))
        print(banner_line("SHARE THIS LINK with friends anywhere!"))
    else:
        print(banner_line("No public tunnel running"))
        print(banner_line("Run with --tunnel for remote access"))
    print(f"+{bar}+")
    print(banner_line(f"Data:  {DB_PATH}"))
    print(banner_line(f"Budget: {BUDGET_TOTAL:.0f} CR  |  XI: {XI_SIZE}  |  Season: {MAX_WEEKS} wks"))
    print(banner_line("Stop:  Ctrl+C"))
    print(f"+{bar}+\n")
    if tunnel:
        print(f"Share this with your friends: {tunnel.url}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# WINDOWS SLEEP PREVENTION
# ═══════════════════════════════════════════════════════════════════════════════

def _prevent_windows_sleep():
    try:
        import ctypes
        if ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
            print("  Sleep prevention active")
            return True
    except Exception:
        pass
    return False

def _restore_windows_sleep():
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import atexit
    os.chdir(BASE_DIR)
    _prevent_windows_sleep()
    atexit.register(_restore_windows_sleep)

    parser = argparse.ArgumentParser(
        description="IPL Fantasy 2026 - Self-hosted server with optional public tunnel"
    )
    parser.add_argument("--port",   type=int, default=5000)
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--tunnel", nargs="?", const="auto", metavar="PROVIDER",
                        help="auto|cloudflare|ngrok|pinggy|localhostrun")
    parser.add_argument("--debug",  action="store_true")
    args = parser.parse_args()

    lan_ip = get_lan_ip()
    tunnel = None

    _log(f"Database: {DB_PATH}")
    _log(f"Season: {MAX_WEEKS} weeks | Budget: {BUDGET_TOTAL:.0f} CR | XI: {XI_SIZE}")

    _auto_seed_if_needed()
    _auto_seed_history_if_needed()

    if args.tunnel:
        print(f"\nStarting public tunnel (provider: {args.tunnel})...")
        flask_thread = threading.Thread(
            target=lambda: app.run(
                host=args.host, port=args.port,
                debug=False, use_reloader=False, threaded=True
            ),
            daemon=True,
        )
        flask_thread.start()
        time.sleep(1.5)
        tunnel = start_tunnel(args.port, args.tunnel)
        if not tunnel:
            print("\n  Could not start a public tunnel automatically.")
            print("  Download cloudflared from:")
            print("  https://github.com/cloudflare/cloudflared/releases/latest\n")
        print_banner(args.port, tunnel, lan_ip)
        tunnel_failures     = 0
        MAX_TUNNEL_FAILURES = 5

        try:
            while True:
                time.sleep(5)
                if not flask_thread.is_alive():
                    print("\nFlask thread died - restarting...")
                    flask_thread = threading.Thread(
                        target=lambda: app.run(
                            host=args.host, port=args.port,
                            debug=False, use_reloader=False, threaded=True
                        ),
                        daemon=True,
                    )
                    flask_thread.start()
                if tunnel and tunnel.proc and tunnel.proc.poll() is not None:
                    tunnel_failures += 1
                    if tunnel_failures > MAX_TUNNEL_FAILURES:
                        print(f"\nTunnel failed {tunnel_failures} times - pausing restarts.")
                        tunnel = None
                        continue
                    backoff = min(5 * tunnel_failures, 30)
                    print(f"\nTunnel exited ({tunnel_failures}/{MAX_TUNNEL_FAILURES}). "
                          f"Retrying in {backoff}s...")
                    time.sleep(backoff)
                    tunnel = start_tunnel(args.port, args.tunnel)
                    if tunnel:
                        print(f"Tunnel restarted: {tunnel.url}")
                        sys.modules[__name__].CURRENT_PUBLIC_URL = tunnel.url
                        tunnel_failures = 0
                    else:
                        print("  Restart failed - will retry next cycle")
                elif tunnel:
                    tunnel_failures = 0
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            if tunnel:
                tunnel.stop()
            sys.exit(0)
    else:
        print_banner(args.port, None, lan_ip)
        try:
            app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
        except KeyboardInterrupt:
            print("\nShutting down...")

"""
IPL Fantasy 2026 — Flask Server                             Golden File v12.7
===========================================================================
v12.7 (this release):
  /api/player-points/<n>: SELECT now includes season_pts + points from players
    table. Each player object in the response gains:
      season_pts  — base pts (no cap/vc multiplier), mirrors players.season_pts
      points      — cap/vc-weighted season total, mirrors players.points
    Frontend (_buildPointsTab, picker) can now read both fields directly from
    this endpoint without a separate /api/players call.
  /api/leaderboard: standings rows now include mvp.points and mvp.season_pts
    so the leaderboard card can show a player's weighted form next to the MVP.
v12.6: /api/user-match-points, update_player_season_pts on recalculate.
v12.5: Ghost audit, v8 seed version.
v12.4: sooryavanshi/suryavanshi semantic aliases.
v12.3: On restart wipe match_scores + JSON cache.
Phase 3: import tasks; /api/update-match-url uses tasks.start_bg_scrape()
  instead of an inline subprocess.run() thread closure.
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
import init_db
import tasks
from config import DB_PATH, DEADLINE_HOUR, DEADLINE_MIN, SERVER_VER

BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR.mkdir(exist_ok=True)

BUDGET_TOTAL  = 100.0
XI_SIZE       = 11
MAX_WEEKS     = 8

_ID_RE = re.compile(r'^[a-z]{1,3}\d{1,2}$')

_SEMANTIC_MAP = {
    "vk":"virat kohli","rohit":"rohit sharma","ms":"ms dhoni","msd":"ms dhoni",
    "bumrah":"jasprit bumrah","bumpy":"jasprit bumrah","jadeja":"ravindra jadeja",
    "sky":"suryakumar yadav","kl":"kl rahul","klr":"kl rahul",
    "hp":"hardik pandya","h pandya":"hardik pandya","pandya":"hardik pandya",
    "shami":"mohammed shami","siraj":"mohammed siraj","chahal":"yuzvendra chahal",
    "sam":"sanju samson","ishan":"ishan kishan","ik":"ishan kishan",
    "salt":"phil salt","klaasen":"heinrich klaasen","david":"tim david",
    "shepherd":"romario shepherd","rutherford":"shimron rutherford",
    "patidar":"rajat patidar","chakravarthy":"varun chakravarthy",
    "chakra":"varun chakravarthy","chakar":"varun chakravarthy",
    "vc":"varun chakravarthy","chahar":"deepak chahar","duffy":"jacob duffy",
    "patel":"axar patel","varma":"tilak varma","rahane":"ajinkya rahane",
    "ravindra":"rachin ravindra",
    "sooryavanshi":"vaibhav sooryavanshi",
    "suryavanshi":"vaibhav sooryavanshi",
    "vaibhav":"vaibhav sooryavanshi",
    "jansen":"marco jansen","brevis":"dewald brevis","rickelton":"ryan rickelton",
    "ngidi":"lungi ngidi","hetmyer":"shimron hetmyer","rana":"harshit rana",
    "pant":"rishabh pant","noor":"noor ahmad","dube":"shivam dube",
    "samson":"sanju samson","tharva":"atharva taide","markram":"aiden markram",
    "rashid":"rashid khan","prabhsimran":"prabhsimran singh",
}


# ════ PLAYER RESOLVER

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


# ════ RATE LIMITER

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


# ════ LOGGING

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


# ════ DB HELPERS

def _db_con():
    con=sqlite3.connect(str(DB_PATH),timeout=30)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


# ════ STARTUP: CLEAR SCORE TABLES + JSON CACHE

def _rebuild_scores_and_points():
    """
    v12.6 — Wipe all score tables + JSON cache on every restart.
    After restart run: python scraper.py
    """
    try:
        print("  [startup] Clearing score tables (match_scores, pmp, user_match_points, season_pts)...")
        with db._write() as con:
            con.execute("DELETE FROM match_scores")
            con.execute("DELETE FROM player_match_points")
            try: con.execute("DELETE FROM user_match_points")
            except Exception: pass
            con.execute("UPDATE user_selections SET week_pts = 0")
            try: con.execute("UPDATE players SET season_pts = 0, points = 0")
            except Exception:
                try: con.execute("UPDATE players SET season_pts = 0")
                except Exception: pass

        matches_dir = DATA_DIR / "matches"
        deleted = 0
        if matches_dir.exists():
            for f in matches_dir.glob("*.json"):
                try: f.unlink(); deleted += 1
                except Exception as e2: print(f"  [startup] Could not delete {f.name}: {e2}")

        print(f"  [startup] \u2713 Cleared all score data. Deleted {deleted} cached JSON files.")
        print("  [startup] \u25ba Run: python scraper.py   to repopulate with fresh data.")
    except Exception as e:
        print(f"  [startup] _rebuild_scores_and_points failed: {e}")


def _audit_player_id_coverage():
    """v12.5: Only flags IDs NOT in players table (true ghosts)."""
    try:
        with db._read() as con:
            con.row_factory = sqlite3.Row
            pmp_ids = {r[0] for r in con.execute("SELECT DISTINCT player_id FROM player_match_points").fetchall()}
            all_player_ids = {r["id"] for r in con.execute("SELECT id FROM players").fetchall()}
            all_players = {r["id"]: r["name"] for r in con.execute("SELECT id,name FROM players").fetchall()}
            sels = con.execute(
                "SELECT display_name, week_no, tw_team_json FROM user_selections ORDER BY display_name, week_no"
            ).fetchall()
            totals = {r[0]: r[1] for r in con.execute(
                "SELECT us.display_name, COALESCE(SUM(us.week_pts),0) AS pts "
                "FROM user_selections us GROUP BY us.display_name"
            ).fetchall()}

        print("  [startup] === Player ID Coverage Audit ===")
        true_ghosts = set()
        for sel in sels:
            name = sel["display_name"]; wk = sel["week_no"]
            try: ids = _json.loads(sel["tw_team_json"] or "[]")
            except: continue
            for pid in ids:
                if pid not in all_player_ids and pid not in true_ghosts:
                    true_ghosts.add(pid)
                    prefix = re.match(r'^[a-z]+', pid)
                    suggestions = [f"{p_id}={p_nm}" for p_id,p_nm in all_players.items()
                                   if prefix and p_id.startswith(prefix.group()) and p_id != pid][:4]
                    print(f"  [startup] \u26a0  TRUE GHOST '{pid}' ({name}/W{wk}): "
                          f"NOT in players table! Alternatives: {', '.join(suggestions) or 'none'}")

        if not true_ghosts:
            print("  [startup] \u2713 All selected player IDs exist in players table.")
            if not pmp_ids:
                print("  [startup]   player_match_points is empty (normal after restart) \u2014 run: python scraper.py")

        print("  [startup] === Per-user cumulative totals (from week_pts) ===")
        for uname, pts in sorted(totals.items()):
            print(f"  [startup]   {uname}: {pts} pts")
        print("  [startup] =========================================")
    except Exception as e:
        print(f"  [startup] ID coverage audit failed: {e}")


# ════ DB SINGLETON + COLD-START

db=DatabaseManager(DB_PATH)

def _cold_start_hydrate():
    try:
        with db._read() as con:
            n=con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        if n==0:
            jd=DATA_DIR/"matches"
            if jd.exists() and any(jd.glob("*.json")):
                print("\n  [startup] Cold DB \u2014 hydrating from JSON archives...")
                ingested=db.hydrate_from_json(jd)
                print(f"  [startup] Hydrated: {ingested} matches.\n")
    except Exception as e:
        print(f"  [startup] Hydration check failed: {e}")

_cold_start_hydrate()


# ════ FLASK APP

app=Flask(__name__,template_folder=str(BASE_DIR/"templates"),static_folder=str(BASE_DIR/"static"),static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"]=1*1024*1024

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


# ════ ROUTES

@app.route("/")
def index():
    try: return render_template("index.html")
    except Exception as e: return f"<h1>Error</h1><p>{e}</p>",500

@app.route("/static/<path:filename>")
def serve_static(filename): return send_from_directory(STATIC_DIR,filename)

@app.route("/api/state",methods=["GET"])
def api_get_state():
    try:
        state=db.get_state(); etag=state.get("_saved","")
        if request.headers.get("If-None-Match")==etag: return "",304
        resp=jsonify(state); resp.headers["ETag"]=etag; return resp
    except Exception as e:
        _log(f"GET /api/state: {e}","error"); return jsonify({"error":str(e),"code":500}),500

@app.route("/api/state",methods=["POST"])
def api_save_state():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        d=request.get_json(force=True,silent=True)
        if not isinstance(d,dict): return jsonify({"error":"bad payload","code":400}),400
        db.save_state(d); return jsonify({"ok":True})
    except Exception as e:
        _log(f"POST /api/state: {e}","error"); return jsonify({"error":str(e),"code":500}),500

@app.route("/api/current-week",methods=["GET"])
def api_current_week():
    try: return jsonify({"week_no":db.get_current_week(),"max_weeks":MAX_WEEKS,"ok":True})
    except Exception as e: return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/resolve-player",methods=["POST"])
def api_resolve_player():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        d=request.get_json(force=True,silent=True) or {}
        query=(d.get("query") or "").strip(); team=(d.get("team") or "").strip() or None
        if not query: return jsonify({"error":"query required","code":400}),400
        con=_db_con(); match=resolve_player_id(con,query,team_hint=team); con.close()
        if not match: return jsonify({"ok":False,"error":"No match","input":query}),404
        tier=match.pop("_match_tier",None)
        return jsonify({"ok":True,"input":query,"match_tier":tier,"resolved":{k:match[k] for k in ("id","name","team","role","price")}})
    except Exception as e:
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/history/<n>",methods=["GET"])
def api_history(n):
    try:
        if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
        return jsonify(db.get_history(n))
    except Exception as e: return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/save-next-week/<n>",methods=["POST"])
def api_save_next_week(n):
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
        d=request.get_json(force=True,silent=True)
        if not isinstance(d,dict): return jsonify({"error":"expected JSON object","code":400}),400
        team=d.get("team",[]); cap=d.get("cap"); vc=d.get("vc")
        if not isinstance(team,list): return jsonify({"error":"team must be list","code":400}),400
        rlog=[]
        if team:
            con=_db_con()
            team,rlog=resolve_id_list(con,team,display_name=n,week_no=db.get_current_week())
            if cap and not _ID_RE.match(str(cap)):
                m=resolve_player_id(con,cap)
                if m: cap=m["id"]
            if vc and not _ID_RE.match(str(vc)):
                m=resolve_player_id(con,vc)
                if m: vc=m["id"]
            con.close()
        if team and len(team)!=XI_SIZE:
            return jsonify({"error":f"Need exactly {XI_SIZE} players (got {len(team)})","code":422}),422
        total_cost=0.0
        if team:
            valid,total_cost=db.validate_budget(team,BUDGET_TOTAL)
            if not valid:
                return jsonify({"error":f"Budget exceeded: {total_cost:.1f} CR","total_cost":total_cost,"budget":BUDGET_TOTAL,"code":422}),422
        result=db.save_next_week(n,team,cap,vc)
        return jsonify({"ok":True,"week_no":result["week_no"],"total_cost":total_cost,"resolution_log":rlog})
    except sqlite3.IntegrityError as e: return jsonify({"error":str(e),"code":400}),400
    except Exception as e:
        _log(f"POST /api/save-next-week/{n}: {e}","error"); return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/member/<n>",methods=["PUT"])
def api_member(n):
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        if not n or len(n)>30: return jsonify({"error":"name 1-30 chars","code":400}),400
        d=request.get_json(force=True,silent=True)
        if not isinstance(d,dict): return jsonify({"error":"Invalid JSON","code":400}),400
        db.upsert_member(n,d); return jsonify({"ok":True})
    except Exception as e:
        _log(f"PUT /api/member/{n}: {e}","error"); return jsonify({"error":str(e),"code":500}),500

@app.route("/api/match",methods=["POST"])
def api_match():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        m=request.get_json(force=True,silent=True)
        if not isinstance(m,dict) or "id" not in m: return jsonify({"error":"missing id","code":400}),400
        db.upsert_match(m); return jsonify({"ok":True})
    except Exception as e:
        _log(f"POST /api/match: {e}","error"); return jsonify({"error":str(e),"code":500}),500

@app.route("/api/rollover",methods=["POST"])
def api_rollover():
    force=request.args.get("force","").strip() in ("1","true","yes")
    try:
        def _resolver(ids):
            con=_db_con()
            try: resolved,_=resolve_id_list(con,ids)
            finally: con.close()
            return resolved
        result=db.rollover_season(force=force,max_weeks=MAX_WEEKS,deadline_hour=DEADLINE_HOUR,
                                   deadline_min=DEADLINE_MIN,resolver_callback=_resolver)
        return jsonify(result)
    except Exception as e:
        _log(f"POST /api/rollover: {e}","error"); return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/seed-history",methods=["POST"])
def api_seed_history():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try: init_db._auto_seed_history_if_needed(); return jsonify({"ok":True})
    except Exception as e: return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/players",methods=["GET"])
def api_players():
    try:
        players=db.get_players()
        return jsonify({"players":players,"by_id":{p["id"]:p for p in players},"by_name":{p["name"].lower():p for p in players},"ok":True})
    except Exception as e: return jsonify({"error":str(e),"ok":False,"code":500}),500

@app.route("/api/ping")
def api_ping():
    try:
        stats=db.ping_stats(); stats.update({"ok":True,"public_url":CURRENT_PUBLIC_URL,"budget":BUDGET_TOTAL,"xi_size":XI_SIZE,"max_weeks":MAX_WEEKS})
        return jsonify(stats)
    except Exception as e: return jsonify({"ok":False,"error":str(e),"code":500}),500

@app.route("/api/leaderboard",methods=["GET"])
def api_leaderboard():
    try:
        wp=request.args.get("week","").strip()
        wn=int(wp) if wp.isdigit() else None
        return jsonify(db.get_leaderboard(week_no=wn))
    except Exception as e: return jsonify({"error":str(e),"code":500}),500

@app.route("/api/poll",methods=["GET"])
def api_poll():
    try: return jsonify({"state_etag":db.get_etags()["state"],"ok":True})
    except Exception as e: return jsonify({"error":str(e),"ok":False,"code":500}),500


# ════ v11.x / v12.x ENDPOINTS

@app.route("/api/player-points/<n>",methods=["GET"])
def api_player_points(n):
    """
    v12.7: player rows now include season_pts (base, no multiplier) and
    points (cap/vc-weighted season total) from the players table, so the
    frontend Points tab and picker can use both fields without an extra call.
    """
    name = n
    try:
        if not name or len(name)>30:
            return jsonify({"error":"invalid name","code":400}),400
        with db._read() as con:
            con.row_factory=sqlite3.Row
            sel_rows=con.execute("""
                SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,week_pts,points_per_match
                FROM user_selections WHERE display_name=? ORDER BY week_no
            """,(name,)).fetchall()
            if not sel_rows:
                return jsonify({"ok":True,"name":name,"total_pts":0,"players":[],"weeks":[]})
            import json as _j
            latest=sel_rows[-1]
            team_ids=_j.loads(latest["tw_team_json"] or "[]")
            latest_cap=latest["tw_cap_id"]; latest_vc=latest["tw_vc_id"]
            if not team_ids:
                return jsonify({"ok":True,"name":name,"total_pts":0,"players":[],"weeks":[]})
            ph=",".join("?"*len(team_ids))
            player_rows={r["id"]:dict(r) for r in
                con.execute(
                    f"SELECT id,name,team,role,price,season_pts,points FROM players WHERE id IN ({ph})",
                    team_ids
                ).fetchall()}
            by_player=collections.defaultdict(list)
            for sel in sel_rows:
                wn=sel["week_no"]
                ids=_j.loads(sel["tw_team_json"] or "[]")
                cap=sel["tw_cap_id"]; vc=sel["tw_vc_id"]
                if not ids: continue
                iph=",".join("?"*len(ids))
                pts=con.execute(f"""
                    SELECT pmp.player_id,pmp.match_id,pmp.week_no,pmp.base_pts,
                           m.title,m.date_label
                    FROM player_match_points pmp
                    JOIN matches m ON m.id=pmp.match_id
                    WHERE pmp.player_id IN ({iph}) AND pmp.week_no=?
                    ORDER BY m.week_no,m.id
                """,ids+[wn]).fetchall()
                for r in pts:
                    mult=2.0 if r["player_id"]==cap else (1.5 if r["player_id"]==vc else 1.0)
                    by_player[r["player_id"]].append({
                        "match_id":r["match_id"],
                        "title":r["title"] or r["match_id"],
                        "week_no":r["week_no"],
                        "base_pts":r["base_pts"],
                        "multiplier":mult,
                        "final_pts":round(r["base_pts"]*mult),
                    })
            weeks_out=[]
            for sel in sel_rows:
                ppm=sel["points_per_match"] if "points_per_match" in sel.keys() else "{}"
                weeks_out.append({
                    "week_no":sel["week_no"],
                    "week_pts":sel["week_pts"],
                    "points_per_match":_j.loads(ppm or "{}"),
                })

        grand_total = sum(w["week_pts"] for w in weeks_out)
        players_out=[]
        for pid in team_ids:
            info=player_rows.get(pid,{"id":pid,"name":pid,"team":"","role":"","price":0,"season_pts":0,"points":0})
            matches=by_player.get(pid,[])
            p_total=sum(m["final_pts"] for m in matches)
            players_out.append({
                "id":pid,
                "name":info.get("name",pid),
                "team":info.get("team",""),
                "role":info.get("role",""),
                "price":info.get("price",0),
                "is_cap":pid==latest_cap,
                "is_vc":pid==latest_vc,
                "season_pts":info.get("season_pts",0),
                "points":info.get("points",0),
                "total_pts":p_total,
                "matches":matches,
            })
        players_out.sort(key=lambda x:-x["total_pts"])
        return jsonify({
            "ok":True,"name":name,
            "total_pts":grand_total,
            "players":players_out,
            "weeks":weeks_out,
        })
    except Exception as e:
        _log(f"GET /api/player-points/{name}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/user-match-points/<n>",methods=["GET"])
def api_user_match_points(n):
    """
    v12.6 \u2014 Per-match user points from user_match_points (cap/vc applied).
    Returns [{week_no, match_id, title, status, teams, pts}].
    """
    try:
        if not n or len(n)>30:
            return jsonify({"error":"invalid name","code":400}),400
        matches = db.get_user_match_points(n)
        total = sum(m["pts"] for m in matches)
        return jsonify({"ok":True,"name":n,"total_pts":total,"matches":matches})
    except Exception as e:
        _log(f"GET /api/user-match-points/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/debug-points/<n>",methods=["GET"])
def api_debug_points(n):
    try:
        if not n or len(n)>30:
            return jsonify({"error":"invalid name","code":400}),400
        with db._read() as con:
            con.row_factory=sqlite3.Row
            sels=con.execute(
                "SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,week_pts FROM user_selections WHERE display_name=? ORDER BY week_no",
                (n,)).fetchall()
            if not sels:
                return jsonify({"ok":True,"name":n,"message":"No selections found","selections":[]})
            import json as _j
            out=[]; total_pts=0
            for sel in sels:
                wn=sel["week_no"]
                ids=_j.loads(sel["tw_team_json"] or "[]")
                cap=sel["tw_cap_id"]; vc=sel["tw_vc_id"]
                matches=con.execute(
                    "SELECT id,title,status FROM matches WHERE week_no=? ORDER BY id",(wn,)).fetchall()
                pmp_rows=[]
                if ids:
                    iph=",".join("?"*len(ids))
                    pmp_rows=con.execute(
                        f"SELECT player_id,match_id,base_pts FROM player_match_points WHERE player_id IN ({iph}) AND week_no=?",
                        ids+[wn]).fetchall()
                scored_pids={r["player_id"] for r in pmp_rows}
                missing=[pid for pid in ids if pid not in scored_pids]
                wk_pts=sel["week_pts"]
                total_pts+=wk_pts
                out.append({
                    "week_no":wn,"cap":cap,"vc":vc,"team":ids,
                    "week_pts":wk_pts,
                    "matches_in_week":[{"id":m["id"],"title":m["title"],"status":m["status"]} for m in matches],
                    "scored_entries":len(pmp_rows),
                    "players_with_no_points":missing,
                })
        return jsonify({"ok":True,"name":n,"total_pts":total_pts,"weeks":out})
    except Exception as e:
        _log(f"GET /api/debug-points/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/matches-status",methods=["GET"])
def api_matches_status():
    try:
        with db._read() as con:
            rows=con.execute(
                "SELECT id,week_no,title,status,scorecard_url FROM matches ORDER BY week_no,id"
            ).fetchall()
        return jsonify({"ok":True,"matches":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/update-match-url",methods=["POST"])
def api_update_match_url():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        d=request.get_json(force=True,silent=True) or {}
        match_id=(d.get("match_id") or "").strip(); url=(d.get("url") or "").strip()
        if not match_id or not url:
            return jsonify({"error":"match_id and url required","code":400}),400
        m=re.search(r'(\d{5,})',url)
        if not m: return jsonify({"error":"URL must contain a 5+ digit Cricbuzz match ID","code":400}),400
        cb_id=m.group(1)
        clean_url=f"https://www.cricbuzz.com/live-cricket-scorecard/{cb_id}"
        con=sqlite3.connect(str(DB_PATH),timeout=30); con.execute("PRAGMA busy_timeout=30000")
        row=con.execute("SELECT id FROM matches WHERE id=?",(match_id,)).fetchone()
        if not row:
            con.close(); return jsonify({"error":f"match '{match_id}' not found","code":404}),404
        con.execute("UPDATE matches SET scorecard_url=? WHERE id=?",(clean_url,match_id))
        con.commit(); con.close()
        # Phase 3: background scrape via tasks.py (replaces inline subprocess.run closure)
        tasks.start_bg_scrape(match_id, BASE_DIR)
        return jsonify({"ok":True,"match_id":match_id,"cb_id":cb_id,"url":clean_url,
                        "message":"URL saved. Scraping started in background \u2014 refresh in ~30 seconds."})
    except Exception as e:
        _log(f"POST /api/update-match-url: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/recalculate-points",methods=["POST"])
def api_recalculate_points():
    """v12.6+: recalculate + update season_pts + points."""
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        n=db.recalculate_points()
        wp=db.update_week_points()
        pp=db.update_player_season_pts()
        return jsonify({"ok":True,"rows_updated":n,"week_pts_rows":wp,"player_pts_updated":pp,
                        "message":f"Recalculated {n} player-match rows, {wp} week_pts rows, {pp} player season_pts."})
    except Exception as e:
        _log(f"POST /api/recalculate-points: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/audit-scores/<n>",methods=["GET"])
def api_audit_scores(n):
    try:
        if not n or len(n)>30:
            return jsonify({"error":"invalid name","code":400}),400
        from db_manager import calc_pts as _calc_pts
        with db._read() as con:
            con.row_factory=sqlite3.Row
            sels=con.execute(
                "SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,week_pts "
                "FROM user_selections WHERE display_name=? ORDER BY week_no",(n,)
            ).fetchall()
            if not sels:
                return jsonify({"ok":True,"name":n,"weeks":[],"total_stored":0,"total_computed":0})
            weeks_out=[]
            for sel in sels:
                wn=sel["week_no"]; ids=_json.loads(sel["tw_team_json"] or "[]")
                cap=sel["tw_cap_id"]; vc=sel["tw_vc_id"]; stored=sel["week_pts"]
                week_matches=con.execute(
                    "SELECT id,title,status FROM matches WHERE week_no=? ORDER BY id",(wn,)
                ).fetchall()
                player_details=[]; computed_total=0
                for pid in ids:
                    mult=2.0 if pid==cap else(1.5 if pid==vc else 1.0)
                    pr=con.execute("SELECT name,team,role FROM players WHERE id=?",(pid,)).fetchone()
                    p_name=pr["name"] if pr else pid
                    match_entries=[]
                    for wm in week_matches:
                        ms=con.execute(
                            "SELECT runs,balls,fours,sixes,got_out,duck,overs,runs_conceded,"
                            "wickets,maidens,lbw_bowled,catches,stumpings,run_out_direct,"
                            "run_out_assist,played,raw_score_json "
                            "FROM match_scores WHERE match_id=? AND player_id=?",(wm["id"],pid)
                        ).fetchone()
                        if ms:
                            sc=_json.loads(ms["raw_score_json"] or "{}")
                            bp=_calc_pts(sc); fp=round(bp*mult); computed_total+=fp
                            match_entries.append({
                                "match_id":wm["id"],"match_title":wm["title"],
                                "suspicious":bp>200,
                                "raw":{"runs":ms["runs"],"balls":ms["balls"],"fours":ms["fours"],
                                       "sixes":ms["sixes"],"overs":ms["overs"],"wickets":ms["wickets"],
                                       "runs_conceded":ms["runs_conceded"],"catches":ms["catches"],
                                       "duck":ms["duck"],"maidens":ms["maidens"],
                                       "lbw_bowled":ms["lbw_bowled"],"stumpings":ms["stumpings"]},
                                "base_pts":bp,"multiplier":mult,"final_pts":fp,
                            })
                    player_details.append({
                        "id":pid,"name":p_name,"is_cap":pid==cap,"is_vc":pid==vc,
                        "multiplier":mult,"matches":match_entries,
                        "player_total":sum(m["final_pts"] for m in match_entries),
                    })
                weeks_out.append({
                    "week_no":wn,"cap":cap,"vc":vc,
                    "stored_week_pts":stored,"computed_week_pts":computed_total,
                    "pts_match":stored==computed_total,
                    "matches_in_week":[{"id":m["id"],"title":m["title"],"status":m["status"]} for m in week_matches],
                    "players":player_details,
                })
        return jsonify({
            "ok":True,"name":n,"weeks":weeks_out,
            "total_stored":sum(w["stored_week_pts"] for w in weeks_out),
            "total_computed":sum(w["computed_week_pts"] for w in weeks_out),
        })
    except Exception as e:
        _log(f"GET /api/audit-scores/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@app.route("/api/clean-scores",methods=["POST"])
def api_clean_scores():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        delete_json=request.args.get("delete_json","").strip().lower() in ("1","true","yes")
        with db._write() as con:
            con.execute("DELETE FROM player_match_points")
            con.execute("DELETE FROM match_scores")
            try: con.execute("DELETE FROM user_match_points")
            except Exception: pass
            con.execute("UPDATE user_selections SET week_pts=0")
            try: con.execute("UPDATE players SET season_pts=0, points=0")
            except Exception:
                try: con.execute("UPDATE players SET season_pts=0")
                except Exception: pass
        deleted_files=0
        if delete_json:
            matches_dir=DATA_DIR/"matches"
            if matches_dir.exists():
                for f in matches_dir.glob("*.json"):
                    try: f.unlink(); deleted_files+=1
                    except Exception as e2: _log(f"[clean] {f}: {e2}","warning")
        _log(f"[clean-scores] Cleared match_scores+pmp+ump+week_pts+season_pts+points. JSON deleted: {deleted_files}")
        return jsonify({
            "ok":True,
            "cleared":["match_scores","player_match_points","user_match_points","week_pts","season_pts","points"],
            "json_files_deleted":deleted_files,
            "next_steps":["Run scraper.py to re-scrape match data",
                          "Restart server or POST /api/recalculate-points to rebuild"]
        })
    except Exception as e:
        _log(f"POST /api/clean-scores: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


# ════ STATIC PAGES

@app.route("/manifest.json")
def manifest():
    return jsonify({"name":"IPL Fantasy 2026","short_name":"IPL Fantasy",
        "description":"Private IPL fantasy cricket league","start_url":"/","display":"standalone",
        "background_color":"#07111F","theme_color":"#07111F",
        "icons":[{"src":"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F3CF;</text></svg>",
                  "sizes":"any","type":"image/svg+xml"}]})

@app.route("/offline")
def offline_page():
    return ("<html><body style='background:#07111F;color:#D8E8F5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'><div style='font-size:64px'>&#x1F3CF;</div>"
            "<h1 style='color:#F5C518;margin:16px 0 8px'>You're offline</h1>"
            "<p style='color:#5F7A9B'>Check your connection and try again.</p>"
            "</div></body></html>")

CURRENT_PUBLIC_URL=""


# ════ NETWORK + TUNNEL

def get_lan_ip():
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(("8.8.8.8",80))
        ip=s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

class TunnelResult:
    def __init__(self,provider,url,proc,ephemeral=False):
        self.provider=provider; self.url=url; self.proc=proc; self.ephemeral=ephemeral
    def stop(self):
        try:
            if self.proc: self.proc.terminate()
        except: pass

def _run_bg(cmd):
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding='utf-8', errors='replace'
    )

def try_cloudflare(port):
    exe=shutil.which("cloudflared")
    if not exe:
        _local=BASE_DIR/"cloudflared.exe"
        if _local.exists(): exe=str(_local)
    if not exe: return None
    print("  -> Trying Cloudflare Tunnel...")
    try:
        proc=_run_bg([exe,"tunnel","--url",f"http://localhost:{port}"])
        url=None; dl=time.time()+30
        while time.time()<dl:
            line=proc.stdout.readline()
            if not line: time.sleep(0.3); continue
            m=re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",line)
            if m: url=m.group(0); break
            if proc.poll() is not None: break
        if url: return TunnelResult("Cloudflare",url,proc,ephemeral=False)
        proc.terminate()
    except Exception as e: print(f"    cloudflare error: {e}")
    return None

def try_ngrok(port):
    exe=shutil.which("ngrok")
    if not exe: return None
    print("  -> Trying ngrok...")
    try:
        proc=_run_bg([exe,"http",str(port),"--log","stdout"])
        url=None; dl=time.time()+20
        while time.time()<dl:
            line=proc.stdout.readline()
            if not line: time.sleep(0.3); continue
            m=re.search(r"https://[a-z0-9-]+\.ngrok(-free)?\.app",line)
            if m: url=m.group(0); break
            if proc.poll() is not None: break
        if url: return TunnelResult("ngrok",url,proc,ephemeral=False)
        proc.terminate()
    except Exception as e: print(f"    ngrok error: {e}")
    return None

def try_pinggy(port):
    exe=shutil.which("ssh")
    if not exe: return None
    print("  -> Trying Pinggy (ephemeral SSH tunnel)...")
    try:
        proc=_run_bg([exe,"-o","StrictHostKeyChecking=no","-o","BatchMode=yes",
                     "-o","PasswordAuthentication=no","-o","ServerAliveInterval=30",
                     "-p","443","-R",f"0:localhost:{port}","a.pinggy.io"])
        url=None; dl=time.time()+20
        while time.time()<dl:
            line=proc.stdout.readline() or ""
            if not line: time.sleep(0.3); continue
            m=re.search(r"https://[a-z0-9-]+\.a\.free\.pinggy\.link",line)
            if m: url=m.group(0); break
            if proc.poll() is not None: break
        if url: return TunnelResult("Pinggy",url,proc,ephemeral=True)
        proc.terminate()
    except Exception as e: print(f"    pinggy error: {e}")
    return None

def try_localhost_run(port):
    exe=shutil.which("ssh")
    if not exe: return None
    print("  -> Trying localhost.run (ephemeral SSH tunnel)...")
    try:
        proc=_run_bg([exe,"-o","StrictHostKeyChecking=no","-o","BatchMode=yes",
                     "-o","PasswordAuthentication=no","-o","ServerAliveInterval=30",
                     "-R",f"80:localhost:{port}","nokey@localhost.run"])
        url=None; dl=time.time()+20
        while time.time()<dl:
            line=proc.stdout.readline() or ""
            if not line: time.sleep(0.3); continue
            m=re.search(r"https://[a-z0-9-]+\.lhr\.life",line)
            if m: url=m.group(0); break
            if proc.poll() is not None: break
        if url: return TunnelResult("localhost.run",url,proc,ephemeral=True)
        proc.terminate()
    except Exception as e: print(f"    localhost.run error: {e}")
    return None

def start_tunnel(port,provider="auto"):
    if provider=="cloudflare": return try_cloudflare(port)
    if provider=="ngrok": return try_ngrok(port)
    if provider=="pinggy": return try_pinggy(port)
    if provider=="localhostrun": return try_localhost_run(port)
    for fn in [try_cloudflare,try_ngrok,try_pinggy,try_localhost_run]:
        result=fn(port)
        if result: return result
    return None


# ════ BANNER

WIDE=64
def banner_line(text="",fill=" "): pad=WIDE-len(text); return f"||  {text}{fill*max(0,pad-2)}||"

def print_banner(port,tunnel,lan_ip):
    bar="="*WIDE
    print(f"\n+{bar}+"); print(f"|{'  IPL FANTASY 2026':^{WIDE}}|"); print(f"+{bar}+")
    print(banner_line(f"Local:    http://localhost:{port}"))
    print(banner_line(f"Network:  http://{lan_ip}:{port}  (same Wi-Fi)"))
    print(f"+{bar}+")
    if tunnel:
        print(banner_line(f"PUBLIC URL ({tunnel.provider}):"))
        url=tunnel.url; sys.modules[__name__].CURRENT_PUBLIC_URL=url
        for i in range(0,len(url),WIDE-4): print(banner_line(f"   {url[i:i+WIDE-4]}"))
        if tunnel.ephemeral:
            print(banner_line("\u26a0  EPHEMERAL TUNNEL \u2014 URL may die after ~30 min!"))
            print(banner_line("   Run setup_cloudflare.ps1 for a persistent tunnel"))
        else:
            print(banner_line("SHARE THIS LINK with friends anywhere!"))
    else:
        print(banner_line("No public tunnel running."))
        print(banner_line("Run setup_cloudflare.ps1 once, then:"))
        print(banner_line("  python server.py --tunnel cloudflare"))
    print(f"+{bar}+")
    print(banner_line(f"Data:  {DB_PATH}"))
    print(banner_line(f"Budget: {BUDGET_TOTAL:.0f} CR  |  XI: {XI_SIZE}  |  Season: {MAX_WEEKS} wks"))
    print(banner_line("Stop:  Ctrl+C")); print(f"+{bar}+\n")
    if tunnel: print(f"Share this with your friends: {tunnel.url}\n")


def _prevent_windows_sleep():
    try:
        import ctypes
        if ctypes.windll.kernel32.SetThreadExecutionState(0x80000001): print("  Sleep prevention active"); return True
    except: pass
    return False

def _restore_windows_sleep():
    try:
        import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except: pass


# ════ ENTRY POINT

if __name__=="__main__":
    import atexit
    os.chdir(BASE_DIR); _prevent_windows_sleep(); atexit.register(_restore_windows_sleep)

    parser=argparse.ArgumentParser(description="IPL Fantasy 2026")
    parser.add_argument("--port",type=int,default=5000)
    parser.add_argument("--host",default="0.0.0.0")
    parser.add_argument("--tunnel",nargs="?",const="auto",metavar="PROVIDER")
    parser.add_argument("--debug",action="store_true")
    args=parser.parse_args()

    lan_ip=get_lan_ip(); tunnel=None
    _log(f"Database: {DB_PATH}")
    _log(f"Season: {MAX_WEEKS} weeks | Budget: {BUDGET_TOTAL:.0f} CR | XI: {XI_SIZE}")

    init_db.run_all_sync(db)
    _rebuild_scores_and_points()
    _audit_player_id_coverage()

    if args.tunnel:
        print(f"\nStarting public tunnel ({args.tunnel})...")
        flask_thread=threading.Thread(
            target=lambda: app.run(host=args.host,port=args.port,debug=False,use_reloader=False,threaded=True),
            daemon=True)
        flask_thread.start(); time.sleep(1.5)
        tunnel=start_tunnel(args.port,args.tunnel)
        if not tunnel:
            print("\n  Could not start any tunnel.")
            print("  Run setup_cloudflare.ps1 to install cloudflared (free, persistent):")
            print("  https://github.com/cloudflare/cloudflared/releases/latest")
            print("  Then run: python server.py --tunnel cloudflare\n")
        print_banner(args.port,tunnel,lan_ip)
        tunnel_failures=0; MAX_TF=5
        try:
            while True:
                time.sleep(5)
                if not flask_thread.is_alive():
                    print("\nFlask thread died - restarting...")
                    flask_thread=threading.Thread(
                        target=lambda: app.run(host=args.host,port=args.port,debug=False,use_reloader=False,threaded=True),
                        daemon=True); flask_thread.start()
                if tunnel and tunnel.proc and tunnel.proc.poll() is not None:
                    tunnel_failures+=1
                    if tunnel_failures>MAX_TF:
                        print(f"\nTunnel failed {tunnel_failures}x - pausing.")
                        if tunnel and tunnel.ephemeral:
                            print("  Ephemeral SSH tunnel died. Run setup_cloudflare.ps1 for reliability.")
                        tunnel=None; continue
                    backoff=min(5*tunnel_failures,30)
                    print(f"\nTunnel exited ({tunnel_failures}/{MAX_TF}). Retry in {backoff}s...")
                    time.sleep(backoff)
                    tunnel=start_tunnel(args.port,args.tunnel)
                    if tunnel: print(f"Tunnel restarted: {tunnel.url}"); sys.modules[__name__].CURRENT_PUBLIC_URL=tunnel.url; tunnel_failures=0
                    else: print("  Restart failed")
                elif tunnel: tunnel_failures=0
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            if tunnel: tunnel.stop()
            sys.exit(0)
    else:
        print_banner(args.port,None,lan_ip)
        try: app.run(host=args.host,port=args.port,debug=args.debug,threaded=True)
        except KeyboardInterrupt: print("\nShutting down...")

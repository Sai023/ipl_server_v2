"""
IPL Fantasy 2026 — Flask Server                             Golden File v13.0
===========================================================================
v13.0 (Phase 7 — Cleanup):
  All 24 API route handlers moved to routes.py (Blueprint).
  server.py is now a thin initialiser:
    • Imports + shared state + helpers
    • DatabaseManager singleton
    • Flask app + error handlers + middleware
    • Startup functions (_rebuild_scores_and_points, _audit_player_id_coverage)
    • Blueprint registration (from routes import bp)
    • Tunnel / banner / __main__
  Zero logic changes. Strict-scope cleanup only.
v12.8 (Phase 5 — API Architect):
  Rollover orchestrated in-controller via logic.rollover_engine.
  /api/version endpoint added.
v12.7: /api/player-points self-contained with season_pts + points columns.
v12.6: /api/user-match-points, update_player_season_pts on recalculate.
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
from logic.rollover_engine import last_monday_deadline, already_rolled, pick_active_team
from logic.scoring_engine import calc_pts as _calc_pts, CAP_MULT, VC_MULT

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


# ════ HELPERS: _jloads (used by api_rollover in routes)

def _jloads(s, default=None):
    """Safe JSON loader — mirrors db_manager._jloads for route handlers."""
    if not s: return default
    try: return _json.loads(s)
    except: return default


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


# ════ STARTUP FUNCTIONS

def _rebuild_scores_and_points():
    """v12.6 — Wipe all score tables + JSON cache on every restart."""
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


# ════ FLASK APP + MIDDLEWARE

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


# ════ MUTABLE GLOBALS (read by routes.py via `import server as _srv`)

CURRENT_PUBLIC_URL = ""


# ════ BLUEPRINT REGISTRATION
# Imported AFTER db, app, and all helpers are defined above so that
# routes.py can safely do `from server import db, _db_con, ...`

from routes import bp          # noqa: E402
app.register_blueprint(bp)


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

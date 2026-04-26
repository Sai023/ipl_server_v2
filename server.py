"""
IPL Fantasy 2026 — Flask Server                             Golden File v13.1
===========================================================================
v13.1 (bugfix):
  Circular import between server.py and routes.py resolved via base.py.
  server.py no longer defines shared state — all singletons (app, db,
  logging, rate limiter, resolver) now live in base.py.
  server.py responsibility: startup functions, blueprint registration,
  tunnel / banner, __main__.
v13.0 (Phase 7): routes extracted to routes.py Blueprint.
v12.8 (Phase 5): rollover in-controller, /api/version.
"""

import json as _json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import argparse
from pathlib import Path

from flask import render_template

# ── All shared state comes from base.py ────────────────────────────────────────────────
import base as _base
from base import app, db, _log, BASE_DIR, DATA_DIR, BUDGET_TOTAL, XI_SIZE, MAX_WEEKS
from config import DB_PATH
import init_db

# ── Blueprint registration (no circular import — routes imports base, not server) ──────
from routes import bp
app.register_blueprint(bp)


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
        matches_dir=DATA_DIR/"matches"; deleted=0
        if matches_dir.exists():
            for f in matches_dir.glob("*.json"):
                try: f.unlink(); deleted+=1
                except Exception as e2: print(f"  [startup] Could not delete {f.name}: {e2}")
        print(f"  [startup] \u2713 Cleared all score data. Deleted {deleted} cached JSON files.")
        print("  [startup] \u25ba Run: python scraper.py   to repopulate with fresh data.")
    except Exception as e:
        print(f"  [startup] _rebuild_scores_and_points failed: {e}")


def _audit_player_id_coverage():
    """v12.5: Only flags IDs NOT in players table (true ghosts)."""
    try:
        with db._read() as con:
            pmp_ids={r[0] for r in con.execute("SELECT DISTINCT player_id FROM player_match_points").fetchall()}
            all_player_ids={r["id"] for r in con.execute("SELECT id FROM players").fetchall()}
            all_players={r["id"]:r["name"] for r in con.execute("SELECT id,name FROM players").fetchall()}
            sels=con.execute("SELECT display_name,week_no,tw_team_json FROM user_selections ORDER BY display_name,week_no").fetchall()
            totals={r[0]:r[1] for r in con.execute(
                "SELECT us.display_name,COALESCE(SUM(us.week_pts),0) AS pts FROM user_selections us GROUP BY us.display_name").fetchall()}
        print("  [startup] === Player ID Coverage Audit ===")
        true_ghosts=set()
        for sel in sels:
            name=sel["display_name"]; wk=sel["week_no"]
            try: ids=_json.loads(sel["tw_team_json"] or "[]")
            except: continue
            for pid in ids:
                if pid not in all_player_ids and pid not in true_ghosts:
                    true_ghosts.add(pid)
                    prefix=re.match(r'^[a-z]+',pid)
                    suggestions=[f"{p_id}={p_nm}" for p_id,p_nm in all_players.items()
                                 if prefix and p_id.startswith(prefix.group()) and p_id!=pid][:4]
                    print(f"  [startup] \u26a0  TRUE GHOST '{pid}' ({name}/W{wk}): "
                          f"NOT in players table! Alternatives: {', '.join(suggestions) or 'none'}")
        if not true_ghosts:
            print("  [startup] \u2713 All selected player IDs exist in players table.")
            if not pmp_ids:
                print("  [startup]   player_match_points is empty (normal after restart) \u2014 run: python scraper.py")
        print("  [startup] === Per-user cumulative totals (from week_pts) ===")
        for uname,pts in sorted(totals.items()):
            print(f"  [startup]   {uname}: {pts} pts")
        print("  [startup] =========================================")
    except Exception as e:
        print(f"  [startup] ID coverage audit failed: {e}")


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
    return subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                            text=True,bufsize=1,encoding='utf-8',errors='replace')

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
        _base.CURRENT_PUBLIC_URL = tunnel.url   # update shared mutable via base module
        url=tunnel.url
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
                    if tunnel:
                        _base.CURRENT_PUBLIC_URL=tunnel.url  # update shared mutable
                        print(f"Tunnel restarted: {tunnel.url}")
                        tunnel_failures=0
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

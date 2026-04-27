"""
IPL Fantasy 2026 — API Route Handlers                       routes v1.2.0
===========================================================================
v1.2.0 (Backend Audit & Snapshot):
  /api/audit-player-ids  — Ghost ID sweep (same logic as startup audit,
    callable on demand). Returns JSON: ghosts list, user totals, validity flag.
  /api/audit-blobs       — Pure DB read: SUM(points_per_match values) vs
    week_pts for every user_selections row. Does NOT require match_scores.
    This is the blob integrity check before any frontend merge work.
  /api/snapshot          — Captures leaderboard + member summary + both audit
    results to data/snapshot_*.json. These are the "Receipts" we compare
    against after the Match Centre is built to prove no bugs were introduced.

v1.1.0 (Phase 8 — Scouting & UX):
  /api/version now includes ROUTES_VER in the modules dict.
  /api/players returns season_pts (base, no cap/vc) sorted DESC.
  /api/state now returns player_pts {id: season_pts} via db.get_state().

v1.0.1 (bugfix): circular import resolved via base.py.
v1.0.0 (Phase 7): 24 @bp.route handlers in 8 groups.

Dependency: base.py → routes.py  (routes never imports from server)
"""

import collections
import json as _json
import re
import sqlite3
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, render_template, send_from_directory

import base as _base  # access CURRENT_PUBLIC_URL as _base.CURRENT_PUBLIC_URL (mutable)
from base import (
    db, _db_con, _log, _write_limiter, _check_rate,
    resolve_player_id, resolve_id_list, _ID_RE, _jloads,
    BASE_DIR, DATA_DIR, STATIC_DIR,
    BUDGET_TOTAL, XI_SIZE, MAX_WEEKS,
)
from config import (
    DB_PATH, DEADLINE_HOUR, DEADLINE_MIN,
    APP_VERSION, VERSION_MAP,
    SERVER_VER, ROUTES_VER, DB_VER, SCRAPER_VER, INIT_DB_VER, TASKS_VER,
    SCORING_ENGINE_VER, ROLLOVER_ENGINE_VER, FUZZY_MATCH_VER,
)
import init_db
import tasks
from logic.rollover_engine import last_monday_deadline, already_rolled, pick_active_team
from logic.scoring_engine import calc_pts as _calc_pts, CAP_MULT, VC_MULT

bp = Blueprint("api", __name__)


# ════════════════════════════════════════════════════════════════════════════
# 1. SYSTEM
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/version", methods=["GET"])
def api_version():
    return jsonify({
        "ok": True, "app_version": APP_VERSION,
        "modules": {
            "server":          SERVER_VER,
            "routes":          ROUTES_VER,
            "db_manager":      DB_VER,
            "scraper":         SCRAPER_VER,
            "init_db":         INIT_DB_VER,
            "tasks":           TASKS_VER,
            "scoring_engine":  SCORING_ENGINE_VER,
            "rollover_engine": ROLLOVER_ENGINE_VER,
            "fuzzy_match":     FUZZY_MATCH_VER,
        },
        "version_map": VERSION_MAP,
    })

@bp.route("/api/ping")
def api_ping():
    try:
        stats = db.ping_stats()
        stats.update({"ok": True, "public_url": _base.CURRENT_PUBLIC_URL,
                      "budget": BUDGET_TOTAL, "xi_size": XI_SIZE, "max_weeks": MAX_WEEKS})
        return jsonify(stats)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "code": 500}), 500

@bp.route("/api/poll", methods=["GET"])
def api_poll():
    try:
        return jsonify({"state_etag": db.get_etags()["state"], "ok": True})
    except Exception as e:
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500

@bp.route("/api/current-week", methods=["GET"])
def api_current_week():
    try:
        return jsonify({"week_no": db.get_current_week(), "max_weeks": MAX_WEEKS, "ok": True})
    except Exception as e:
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


# ════════════════════════════════════════════════════════════════════════════
# 2. STATE
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/state", methods=["GET"])
def api_get_state():
    try:
        state = db.get_state(); etag = state.get("_saved", "")
        if request.headers.get("If-None-Match") == etag: return "", 304
        resp = jsonify(state); resp.headers["ETag"] = etag; return resp
    except Exception as e:
        _log(f"GET /api/state: {e}", "error"); return jsonify({"error": str(e), "code": 500}), 500

@bp.route("/api/state", methods=["POST"])
def api_save_state():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    try:
        d = request.get_json(force=True, silent=True)
        if not isinstance(d, dict): return jsonify({"error": "bad payload", "code": 400}), 400
        db.save_state(d); return jsonify({"ok": True})
    except Exception as e:
        _log(f"POST /api/state: {e}", "error"); return jsonify({"error": str(e), "code": 500}), 500


# ════════════════════════════════════════════════════════════════════════════
# 3. PLAYERS
# /api/players returns id, name, team, role, price, season_pts, points
# sorted by season_pts DESC — correct for scouting (base score, no cap/vc).
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/players", methods=["GET"])
def api_players():
    try:
        players = db.get_players()
        return jsonify({"players": players, "by_id": {p["id"]: p for p in players},
                        "by_name": {p["name"].lower(): p for p in players}, "ok": True})
    except Exception as e:
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500

@bp.route("/api/resolve-player", methods=["POST"])
def api_resolve_player():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    try:
        d = request.get_json(force=True, silent=True) or {}
        query = (d.get("query") or "").strip(); team = (d.get("team") or "").strip() or None
        if not query: return jsonify({"error": "query required", "code": 400}), 400
        con = _db_con(); match = resolve_player_id(con, query, team_hint=team); con.close()
        if not match: return jsonify({"ok": False, "error": "No match", "input": query}), 404
        tier = match.pop("_match_tier", None)
        return jsonify({"ok": True, "input": query, "match_tier": tier,
                        "resolved": {k: match[k] for k in ("id","name","team","role","price")}})
    except Exception as e:
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500

@bp.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    try:
        wp = request.args.get("week", "").strip()
        wn = int(wp) if wp.isdigit() else None
        return jsonify(db.get_leaderboard(week_no=wn))
    except Exception as e:
        return jsonify({"error": str(e), "code": 500}), 500


# ════════════════════════════════════════════════════════════════════════════
# 4. HISTORY / POINTS
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/history/<n>", methods=["GET"])
def api_history(n):
    try:
        if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
        return jsonify(db.get_history(n))
    except Exception as e:
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/player-points/<n>", methods=["GET"])
def api_player_points(n):
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
            player_rows={r["id"]:dict(r) for r in con.execute(
                f"SELECT id,name,team,role,price,season_pts,points FROM players WHERE id IN ({ph})",
                team_ids).fetchall()}
            by_player=collections.defaultdict(list)
            for sel in sel_rows:
                wn=sel["week_no"]; ids=_j.loads(sel["tw_team_json"] or "[]")
                cap=sel["tw_cap_id"]; vc=sel["tw_vc_id"]
                if not ids: continue
                iph=",".join("?"*len(ids))
                pts=con.execute(f"""
                    SELECT pmp.player_id,pmp.match_id,pmp.week_no,pmp.base_pts,m.title,m.date_label
                    FROM player_match_points pmp JOIN matches m ON m.id=pmp.match_id
                    WHERE pmp.player_id IN ({iph}) AND pmp.week_no=? ORDER BY m.week_no,m.id
                """,ids+[wn]).fetchall()
                for r in pts:
                    mult=CAP_MULT if r["player_id"]==cap else (VC_MULT if r["player_id"]==vc else 1.0)
                    by_player[r["player_id"]].append({
                        "match_id":r["match_id"],"title":r["title"] or r["match_id"],
                        "week_no":r["week_no"],"base_pts":r["base_pts"],
                        "multiplier":mult,"final_pts":round(r["base_pts"]*mult)})
            weeks_out=[]
            for sel in sel_rows:
                ppm=sel["points_per_match"] if "points_per_match" in sel.keys() else "{}"
                weeks_out.append({"week_no":sel["week_no"],"week_pts":sel["week_pts"],
                                   "points_per_match":_j.loads(ppm or "{}")})
        grand_total=sum(w["week_pts"] for w in weeks_out)
        players_out=[]
        for pid in team_ids:
            info=player_rows.get(pid,{"id":pid,"name":pid,"team":"","role":"","price":0,"season_pts":0,"points":0})
            matches=by_player.get(pid,[]); p_total=sum(m["final_pts"] for m in matches)
            players_out.append({"id":pid,"name":info.get("name",pid),"team":info.get("team",""),
                                 "role":info.get("role",""),"price":info.get("price",0),
                                 "is_cap":pid==latest_cap,"is_vc":pid==latest_vc,
                                 "season_pts":info.get("season_pts",0),"points":info.get("points",0),
                                 "total_pts":p_total,"matches":matches})
        players_out.sort(key=lambda x:-x["total_pts"])
        return jsonify({"ok":True,"name":name,"total_pts":grand_total,
                        "players":players_out,"weeks":weeks_out})
    except Exception as e:
        _log(f"GET /api/player-points/{name}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/user-match-points/<n>", methods=["GET"])
def api_user_match_points(n):
    try:
        if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
        matches=db.get_user_match_points(n); total=sum(m["pts"] for m in matches)
        return jsonify({"ok":True,"name":n,"total_pts":total,"matches":matches})
    except Exception as e:
        _log(f"GET /api/user-match-points/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/debug-points/<n>", methods=["GET"])
def api_debug_points(n):
    try:
        if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
        with db._read() as con:
            con.row_factory=sqlite3.Row
            sels=con.execute(
                "SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,week_pts "
                "FROM user_selections WHERE display_name=? ORDER BY week_no",(n,)).fetchall()
            if not sels: return jsonify({"ok":True,"name":n,"message":"No selections found","selections":[]})
            import json as _j
            out=[]; total_pts=0
            for sel in sels:
                wn=sel["week_no"]; ids=_j.loads(sel["tw_team_json"] or "[]")
                cap=sel["tw_cap_id"]; vc=sel["tw_vc_id"]
                matches=con.execute("SELECT id,title,status FROM matches WHERE week_no=? ORDER BY id",(wn,)).fetchall()
                pmp_rows=[]
                if ids:
                    iph=",".join("?"*len(ids))
                    pmp_rows=con.execute(
                        f"SELECT player_id,match_id,base_pts FROM player_match_points "
                        f"WHERE player_id IN ({iph}) AND week_no=?",ids+[wn]).fetchall()
                scored_pids={r["player_id"] for r in pmp_rows}
                missing=[pid for pid in ids if pid not in scored_pids]
                wk_pts=sel["week_pts"]; total_pts+=wk_pts
                out.append({"week_no":wn,"cap":cap,"vc":vc,"team":ids,"week_pts":wk_pts,
                            "matches_in_week":[{"id":m["id"],"title":m["title"],"status":m["status"]} for m in matches],
                            "scored_entries":len(pmp_rows),"players_with_no_points":missing})
        return jsonify({"ok":True,"name":n,"total_pts":total_pts,"weeks":out})
    except Exception as e:
        _log(f"GET /api/debug-points/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


# ════════════════════════════════════════════════════════════════════════════
# 5. SAVE
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/save-next-week/<n>", methods=["POST"])
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
                return jsonify({"error":f"Budget exceeded: {total_cost:.1f} CR",
                                "total_cost":total_cost,"budget":BUDGET_TOTAL,"code":422}),422
        result=db.save_next_week(n,team,cap,vc)
        return jsonify({"ok":True,"week_no":result["week_no"],"total_cost":total_cost,"resolution_log":rlog})
    except sqlite3.IntegrityError as e:
        return jsonify({"error":str(e),"code":400}),400
    except Exception as e:
        _log(f"POST /api/save-next-week/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/member/<n>", methods=["PUT"])
def api_member(n):
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        if not n or len(n)>30: return jsonify({"error":"name 1-30 chars","code":400}),400
        d=request.get_json(force=True,silent=True)
        if not isinstance(d,dict): return jsonify({"error":"Invalid JSON","code":400}),400
        db.upsert_member(n,d); return jsonify({"ok":True})
    except Exception as e:
        _log(f"PUT /api/member/{n}: {e}","error")
        return jsonify({"error":str(e),"code":500}),500

@bp.route("/api/match", methods=["POST"])
def api_match():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        m=request.get_json(force=True,silent=True)
        if not isinstance(m,dict) or "id" not in m: return jsonify({"error":"missing id","code":400}),400
        db.upsert_match(m); return jsonify({"ok":True})
    except Exception as e:
        _log(f"POST /api/match: {e}","error")
        return jsonify({"error":str(e),"code":500}),500


# ════════════════════════════════════════════════════════════════════════════
# 6. SCORING
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/recalculate-points", methods=["POST"])
def api_recalculate_points():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        n=db.recalculate_points(); wp=db.update_week_points(); pp=db.update_player_season_pts()
        return jsonify({"ok":True,"rows_updated":n,"week_pts_rows":wp,"player_pts_updated":pp,
                        "message":f"Recalculated {n} player-match rows, {wp} week_pts rows, {pp} player season_pts."})
    except Exception as e:
        _log(f"POST /api/recalculate-points: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/audit-scores/<n>", methods=["GET"])
def api_audit_scores(n):
    try:
        if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
        with db._read() as con:
            con.row_factory=sqlite3.Row
            sels=con.execute(
                "SELECT week_no,tw_team_json,tw_cap_id,tw_vc_id,week_pts "
                "FROM user_selections WHERE display_name=? ORDER BY week_no",(n,)).fetchall()
            if not sels:
                return jsonify({"ok":True,"name":n,"weeks":[],"total_stored":0,"total_computed":0})
            weeks_out=[]
            for sel in sels:
                wn=sel["week_no"]; ids=_json.loads(sel["tw_team_json"] or "[]")
                cap=sel["tw_cap_id"]; vc=sel["tw_vc_id"]; stored=sel["week_pts"]
                week_matches=con.execute("SELECT id,title,status FROM matches WHERE week_no=? ORDER BY id",(wn,)).fetchall()
                player_details=[]; computed_total=0
                for pid in ids:
                    mult=CAP_MULT if pid==cap else (VC_MULT if pid==vc else 1.0)
                    pr=con.execute("SELECT name,team,role FROM players WHERE id=?",(pid,)).fetchone()
                    p_name=pr["name"] if pr else pid; match_entries=[]
                    for wm in week_matches:
                        ms=con.execute(
                            "SELECT runs,balls,fours,sixes,got_out,duck,overs,runs_conceded,"
                            "wickets,maidens,lbw_bowled,catches,stumpings,run_out_direct,"
                            "run_out_assist,played,raw_score_json "
                            "FROM match_scores WHERE match_id=? AND player_id=?",(wm["id"],pid)).fetchone()
                        if ms:
                            sc=_json.loads(ms["raw_score_json"] or "{}")
                            bp_=_calc_pts(sc); fp=round(bp_*mult); computed_total+=fp
                            match_entries.append({
                                "match_id":wm["id"],"match_title":wm["title"],"suspicious":bp_>200,
                                "raw":{"runs":ms["runs"],"balls":ms["balls"],"fours":ms["fours"],
                                       "sixes":ms["sixes"],"overs":ms["overs"],"wickets":ms["wickets"],
                                       "runs_conceded":ms["runs_conceded"],"catches":ms["catches"],
                                       "duck":ms["duck"],"maidens":ms["maidens"],
                                       "lbw_bowled":ms["lbw_bowled"],"stumpings":ms["stumpings"]},
                                "base_pts":bp_,"multiplier":mult,"final_pts":fp})
                    player_details.append({"id":pid,"name":p_name,"is_cap":pid==cap,"is_vc":pid==vc,
                                           "multiplier":mult,"matches":match_entries,
                                           "player_total":sum(m["final_pts"] for m in match_entries)})
                weeks_out.append({"week_no":wn,"cap":cap,"vc":vc,
                                  "stored_week_pts":stored,"computed_week_pts":computed_total,
                                  "pts_match":stored==computed_total,
                                  "matches_in_week":[{"id":m["id"],"title":m["title"],"status":m["status"]} for m in week_matches],
                                  "players":player_details})
        return jsonify({"ok":True,"name":n,"weeks":weeks_out,
                        "total_stored":sum(w["stored_week_pts"] for w in weeks_out),
                        "total_computed":sum(w["computed_week_pts"] for w in weeks_out)})
    except Exception as e:
        _log(f"GET /api/audit-scores/{n}: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/clean-scores", methods=["POST"])
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
        _log(f"[clean-scores] Cleared. JSON deleted: {deleted_files}")
        return jsonify({"ok":True,
                        "cleared":["match_scores","player_match_points","user_match_points",
                                   "week_pts","season_pts","points"],
                        "json_files_deleted":deleted_files,
                        "next_steps":["Run scraper.py to re-scrape match data",
                                      "Restart server or POST /api/recalculate-points to rebuild"]})
    except Exception as e:
        _log(f"POST /api/clean-scores: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


# ════════════════════════════════════════════════════════════════════════════
# 6b. AUDIT & SNAPSHOT  (v1.2.0 — backend receipt system)
#
# Run these THREE endpoints in sequence before any frontend work:
#   1. GET  /api/audit-player-ids  → must return all_ids_valid: true
#   2. GET  /api/audit-blobs       → must return all_blobs_valid: true
#   3. POST /api/snapshot          → saves receipts to data/snapshot_*.json
#
# After the Match Centre is built, re-run the snapshot and diff the numbers.
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/audit-player-ids", methods=["GET"])
def api_audit_player_ids():
    """
    Ghost ID Sweep — callable on demand (same logic as startup audit).

    A "ghost" is a player_id that appears in any user's tw_team_json
    but does NOT exist in the players table.  Ghosts will appear as
    "Unknown Player" in any UI that does a player lookup.

    Does NOT require match_scores or player_match_points to be populated.
    Safe to run immediately after a server restart.
    """
    try:
        with db._read() as con:
            all_players = {r["id"]: r["name"] for r in con.execute(
                "SELECT id,name FROM players").fetchall()}
            all_player_ids = set(all_players.keys())
            sels = con.execute(
                "SELECT display_name, week_no, tw_team_json "
                "FROM user_selections ORDER BY display_name, week_no"
            ).fetchall()
            totals_rows = con.execute(
                "SELECT display_name, COALESCE(SUM(week_pts),0) AS pts "
                "FROM user_selections GROUP BY display_name"
            ).fetchall()

        totals = {r["display_name"]: r["pts"] for r in totals_rows}
        ghosts = []
        seen_ghosts = set()
        for sel in sels:
            name = sel["display_name"]; wk = sel["week_no"]
            try: ids = _json.loads(sel["tw_team_json"] or "[]")
            except: continue
            for pid in ids:
                if pid not in all_player_ids and pid not in seen_ghosts:
                    seen_ghosts.add(pid)
                    prefix_m = re.match(r'^[a-z]+', pid)
                    suggestions = [
                        f"{p_id}={p_nm}" for p_id, p_nm in all_players.items()
                        if prefix_m and p_id.startswith(prefix_m.group()) and p_id != pid
                    ][:4]
                    ghosts.append({
                        "ghost_id": pid,
                        "first_seen_user": name,
                        "first_seen_week": wk,
                        "suggestions": suggestions,
                    })

        return jsonify({
            "ok": True,
            "all_ids_valid": len(ghosts) == 0,
            "ghost_count": len(ghosts),
            "ghosts": ghosts,
            "user_totals": [
                {"name": k, "total_pts": v} for k, v in sorted(totals.items())
            ],
            "players_in_db": len(all_player_ids),
            "note": (
                "PASS: All selected player IDs resolve to real players."
                if len(ghosts) == 0
                else f"FAIL: {len(ghosts)} ghost ID(s) found. "
                     "Fix by updating Seed_Players.py and running it."
            ),
        })
    except Exception as e:
        _log(f"GET /api/audit-player-ids: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@bp.route("/api/audit-blobs", methods=["GET"])
def api_audit_blobs():
    """
    Blob Verification — pure DB read, no match_scores required.

    For every user_selections row:
      SUM(points_per_match.values())  must equal  week_pts

    This is the authoritative check that the stored totals (which the
    leaderboard reads) are internally consistent with the per-match
    breakdown (which the History tab reads).

    A mismatch means the two tabs will show conflicting totals — this
    MUST be resolved before any frontend merge work begins.
    """
    try:
        with db._read() as con:
            rows = con.execute(
                "SELECT display_name, week_no, week_pts, points_per_match "
                "FROM user_selections ORDER BY display_name, week_no"
            ).fetchall()

        results = []; mismatches = []
        for row in rows:
            name = row["display_name"]; wk = row["week_no"]
            stored = row["week_pts"]
            try: blob = _json.loads(row["points_per_match"] or "{}")
            except: blob = {}
            blob_sum = sum(int(v) for v in blob.values())
            is_match = stored == blob_sum
            entry = {
                "user": name,
                "week_no": wk,
                "stored_week_pts": stored,
                "blob_sum": blob_sum,
                "diff": stored - blob_sum,
                "match": is_match,
                "matches_in_blob": len(blob),
            }
            results.append(entry)
            if not is_match:
                mismatches.append(entry)

        return jsonify({
            "ok": True,
            "all_blobs_valid": len(mismatches) == 0,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
            "rows_checked": len(results),
            "details": results,
            "note": (
                "PASS: All points_per_match blobs sum correctly to week_pts."
                if len(mismatches) == 0
                else f"FAIL: {len(mismatches)} row(s) where blob sum != week_pts. "
                     "Run POST /api/recalculate-points then re-check."
            ),
        })
    except Exception as e:
        _log(f"GET /api/audit-blobs: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@bp.route("/api/snapshot", methods=["POST"])
def api_snapshot():
    """
    Capture and save a receipt: leaderboard + member totals + both audit results.

    Saved to data/snapshot_<iso>.json.  Run this BEFORE building the Match
    Centre.  After the Match Centre is live, run it again and diff the two
    files — numbers must be identical to prove no bugs were introduced.

    Usage:
      POST /api/snapshot
      Response includes the full snapshot JSON + the filename it was saved to.
    """
    try:
        # 1. Leaderboard (source of truth for totals)
        leaderboard = db.get_leaderboard()

        # 2. Ghost sweep
        with db._read() as con:
            all_players = {r["id"]: r["name"] for r in con.execute(
                "SELECT id,name FROM players").fetchall()}
            all_player_ids = set(all_players.keys())
            sels = con.execute(
                "SELECT display_name, week_no, tw_team_json "
                "FROM user_selections ORDER BY display_name, week_no"
            ).fetchall()
            blob_rows = con.execute(
                "SELECT display_name, week_no, week_pts, points_per_match "
                "FROM user_selections ORDER BY display_name, week_no"
            ).fetchall()

        ghosts = []
        seen_ghosts = set()
        for sel in sels:
            try: ids = _json.loads(sel["tw_team_json"] or "[]")
            except: continue
            for pid in ids:
                if pid not in all_player_ids and pid not in seen_ghosts:
                    seen_ghosts.add(pid)
                    ghosts.append({"ghost_id": pid, "first_seen_user": sel["display_name"]})

        # 3. Blob verification
        blob_mismatches = []
        for row in blob_rows:
            stored = row["week_pts"]
            try: blob = _json.loads(row["points_per_match"] or "{}")
            except: blob = {}
            blob_sum = sum(int(v) for v in blob.values())
            if stored != blob_sum:
                blob_mismatches.append({
                    "user": row["display_name"], "week_no": row["week_no"],
                    "stored_week_pts": stored, "blob_sum": blob_sum, "diff": stored - blob_sum,
                })

        now_iso = datetime.now(timezone.utc).isoformat()
        snapshot = {
            "captured_at": now_iso,
            "backend_clean": len(ghosts) == 0 and len(blob_mismatches) == 0,
            "audit_player_ids": {
                "all_ids_valid": len(ghosts) == 0,
                "ghost_count": len(ghosts),
                "ghosts": ghosts,
            },
            "audit_blobs": {
                "all_blobs_valid": len(blob_mismatches) == 0,
                "mismatch_count": len(blob_mismatches),
                "mismatches": blob_mismatches,
            },
            "leaderboard": leaderboard,
        }

        # Save to disk
        safe_ts = now_iso.replace(":", "-").replace(".", "-")
        snap_path = DATA_DIR / f"snapshot_{safe_ts}.json"
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snap_path, "w") as fh:
            _json.dump(snapshot, fh, indent=2)
        _log(f"[snapshot] Saved receipt → {snap_path.name}")

        return jsonify({
            "ok": True,
            "captured_at": now_iso,
            "saved_to": snap_path.name,
            "backend_clean": snapshot["backend_clean"],
            "audit_summary": {
                "ghost_count": len(ghosts),
                "blob_mismatch_count": len(blob_mismatches),
            },
            "snapshot": snapshot,
        })
    except Exception as e:
        _log(f"POST /api/snapshot: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


# ════════════════════════════════════════════════════════════════════════════
# 7. ADMIN / ROLLOVER
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/rollover", methods=["POST"])
def api_rollover():
    force=request.args.get("force","").strip() in ("1","true","yes")
    try:
        now=datetime.now(timezone.utc)
        if not force:
            lmd=last_monday_deadline(now,DEADLINE_HOUR,DEADLINE_MIN)
            last_raw=db.get_meta("_last_rollover","")
            if already_rolled(last_raw,lmd):
                return jsonify({"ok":True,"rolled":False,"new_week_no":None,
                                "season_complete":False,"reason":"Already rolled for this deadline"})
        current_week=db.get_current_week()
        if current_week>=MAX_WEEKS:
            return jsonify({"ok":True,"rolled":False,"new_week_no":None,
                            "season_complete":True,"reason":f"Season complete \u2014 {MAX_WEEKS} weeks reached"})
        users=db.get_users_and_max_weeks()
        if not users:
            return jsonify({"ok":True,"rolled":False,"new_week_no":None,
                            "season_complete":False,"reason":"No members found"})
        new_week_no=int(users[0]["cur_wk"])+1; now_iso=now.isoformat()
        for u in users:
            uname=u["display_name"]; cur_wk=int(u["cur_wk"])
            cur_row=db.get_selection_row(uname,cur_wk)
            if not cur_row: continue
            nw_team,nw_cap,nw_vc=pick_active_team(
                cur_row["nw_team_json"],cur_row["nw_cap_id"],cur_row["nw_vc_id"],
                cur_row["tw_team_json"],cur_row["tw_cap_id"],cur_row["tw_vc_id"],_jloads)
            con=_db_con()
            try:
                resolved,_=resolve_id_list(con,_jloads(nw_team,[])); nw_team=_json.dumps(resolved)
            except Exception: pass
            finally: con.close()
            db.insert_rollover_week(uname,cur_wk+1,nw_team,nw_cap,nw_vc)
        if not force: db.set_last_rollover(now_iso)
        db.set_meta("_saved",now_iso)
        return jsonify({"ok":True,"rolled":True,"new_week_no":new_week_no,
                        "season_complete":new_week_no>=MAX_WEEKS})
    except Exception as e:
        _log(f"POST /api/rollover: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/seed-history", methods=["POST"])
def api_seed_history():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try: init_db._auto_seed_history_if_needed(); return jsonify({"ok":True})
    except Exception as e: return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/matches-status", methods=["GET"])
def api_matches_status():
    try:
        with db._read() as con:
            rows=con.execute("SELECT id,week_no,title,status,scorecard_url FROM matches ORDER BY week_no,id").fetchall()
        return jsonify({"ok":True,"matches":[dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/update-match-url", methods=["POST"])
def api_update_match_url():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    try:
        d=request.get_json(force=True,silent=True) or {}
        match_id=(d.get("match_id") or "").strip(); url=(d.get("url") or "").strip()
        if not match_id or not url: return jsonify({"error":"match_id and url required","code":400}),400
        m=re.search(r'(\d{5,})',url)
        if not m: return jsonify({"error":"URL must contain a 5+ digit Cricbuzz match ID","code":400}),400
        cb_id=m.group(1); clean_url=f"https://www.cricbuzz.com/live-cricket-scorecard/{cb_id}"
        con=sqlite3.connect(str(DB_PATH),timeout=30); con.execute("PRAGMA busy_timeout=30000")
        row=con.execute("SELECT id FROM matches WHERE id=?",(match_id,)).fetchone()
        if not row: con.close(); return jsonify({"error":f"match '{match_id}' not found","code":404}),404
        con.execute("UPDATE matches SET scorecard_url=? WHERE id=?",(clean_url,match_id))
        con.commit(); con.close()
        tasks.start_bg_scrape(match_id,BASE_DIR)
        return jsonify({"ok":True,"match_id":match_id,"cb_id":cb_id,"url":clean_url,
                        "message":"URL saved. Scraping started in background \u2014 refresh in ~30 seconds."})
    except Exception as e:
        _log(f"POST /api/update-match-url: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


# ════════════════════════════════════════════════════════════════════════════
# 8. STATIC
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/")
def index():
    try: return render_template("index.html")
    except Exception as e: return f"<h1>Error</h1><p>{e}</p>",500

@bp.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_DIR,filename)

@bp.route("/manifest.json")
def manifest():
    return jsonify({"name":"IPL Fantasy 2026","short_name":"IPL Fantasy",
        "description":"Private IPL fantasy cricket league","start_url":"/","display":"standalone",
        "background_color":"#07111F","theme_color":"#07111F",
        "icons":[{"src":"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F3CF;</text></svg>",
                  "sizes":"any","type":"image/svg+xml"}]})

@bp.route("/offline")
def offline_page():
    return ("<html><body style='background:#07111F;color:#D8E8F5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'><div style='font-size:64px'>&#x1F3CF;</div>"
            "<h1 style='color:#F5C518;margin:16px 0 8px'>You're offline</h1>"
            "<p style='color:#5F7A9B'>Check your connection and try again.</p>"
            "</div></body></html>")

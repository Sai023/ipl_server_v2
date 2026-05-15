"""
IPL Fantasy 2026 — API Route Handlers                       routes v1.4.0
===========================================================================
v1.4.0 (Phase 9 — Daily auto-sync):
  /api/sync-now (POST) — manual trigger for the discovery+scrape pipeline.
    Spawns tasks.start_bg_sync() in a daemon thread; returns immediately
    with the thread name. Same code path as the APScheduler 23:55 IST job.
    Accepts {"debug": true} in body or ?debug=1 query string.
  /api/version — modules dict extended with two new pins:
    seed_matches      (SEED_MATCHES_VER       — JSON-aware shim version)
    cricbuzz_discovery (CRICBUZZ_DISCOVERY_VER — shared discovery module)

v1.3.0 (Phase 9.1 — Match Centre Backend):
  /api/match-centre          — Hub endpoint (requires ?user=<name>).
    Returns all matches grouped by week. Each match entry includes:
    match_id, teams, venue, date_label, result, status, user_match_pts.
    Season summary (total_pts, matches_played, avg_per_match, best_pts,
    best_match) included at the top level. Single fetch — no live tracking.
  /api/match-details/<match_id> — Box Score endpoint (requires ?user=<name>).
    Returns the user's historical XI snapshot for that specific match with
    per-player pts and C/VC multipliers applied. Reads tw_team_json from
    the week the match belongs to — historically accurate even if the user
    changed their squad in later weeks. No writes, no live logic.

v1.2.0 (Backend Audit & Snapshot):
  /api/audit-player-ids, /api/audit-blobs, /api/snapshot

v1.1.0 (Phase 8 — Scouting & UX): player_pts badge support.
v1.0.0 (Phase 7): 24 @bp.route handlers in 8 groups.

Dependency: base.py → routes.py  (routes never imports from server)
"""

import json as _json
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, render_template, send_from_directory

import cloud_sync   # Phase 2: git pull / push helpers (HOSTED mode)

# HOSTED mode flag — same source of truth as server.py.
# Routes only branch when behaviour MUST differ in the cloud (Cricbuzz scrape
# replaced with git pull). Everything else stays single-codepath.
_IS_HOSTED = os.environ.get("HOSTED", "").lower() in ("true", "1", "yes")


def _push_if_hosted(reason: str) -> None:
    """
    Phase 4: After a successful write in HOSTED mode, commit fantasy.db and
    push so other clients (and the local box / Actions) see the change on
    their next git pull.

    Sync, not async — durability matters more than the 2-5s latency. If the
    container dies between the local DB write and the git push, the change
    is lost forever (Render redeploys wipe the container's filesystem).
    Sync push means the write is durable as soon as the user sees "OK".

    Failures are logged but do not surface to the caller as a 500. The
    local DB has the change; the next successful push will catch it up.
    """
    if not _IS_HOSTED:
        return
    try:
        ok, msg = cloud_sync.commit_and_push(
            paths=["data/fantasy.db"],
            message=f"ui: {reason}",
        )
        if not ok:
            _log(f"git push after '{reason}' failed: {msg}. "
                 f"Local DB has the change; will catch up on next write.",
                 "warn")
    except Exception as e:
        _log(f"_push_if_hosted({reason}) crashed: {e}", "error")

import base as _base
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
    SEED_MATCHES_VER,
    SCORING_ENGINE_VER, ROLLOVER_ENGINE_VER, FUZZY_MATCH_VER,
    CRICBUZZ_DISCOVERY_VER,
)
import tasks
from logic.rollover_engine import last_monday_deadline, already_rolled, pick_active_team
from logic.scoring_engine import debug_calc_pts as _debug_calc_pts

bp = Blueprint("api", __name__)


# ════════════════════════════════════════════════════════════════════════════
# 1. SYSTEM
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/version", methods=["GET"])
def api_version():
    return jsonify({
        "ok": True, "app_version": APP_VERSION,
        "modules": {
            "server":             SERVER_VER,
            "routes":             ROUTES_VER,
            "db_manager":         DB_VER,
            "scraper":            SCRAPER_VER,
            "init_db":            INIT_DB_VER,
            "tasks":              TASKS_VER,
            "seed_matches":       SEED_MATCHES_VER,         # Phase 9
            "scoring_engine":     SCORING_ENGINE_VER,
            "rollover_engine":    ROLLOVER_ENGINE_VER,
            "fuzzy_match":        FUZZY_MATCH_VER,
            "cricbuzz_discovery": CRICBUZZ_DISCOVERY_VER,   # Phase 9
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



# ════════════════════════════════════════════════════════════════════════════
# 3. PLAYERS
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
        _push_if_hosted(f"save-next-week:{n}:w{result['week_no']}")
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
        db.upsert_member(n,d)
        _push_if_hosted(f"member:{n}")
        return jsonify({"ok":True})
    except Exception as e:
        _log(f"PUT /api/member/{n}: {e}","error")
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
        _push_if_hosted(f"recalc:rows={n}")
        return jsonify({"ok":True,"rows_updated":n,"week_pts_rows":wp,"player_pts_updated":pp,
                        "message":f"Recalculated {n} player-match rows, {wp} week_pts rows, {pp} player season_pts."})
    except Exception as e:
        _log(f"POST /api/recalculate-points: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/audit-scores/<n>", methods=["GET"])
def api_audit_scores(n):
    """
    Step-by-step scoring trace for one user, all weeks.

    Re-derives every player's per-match points from raw stats via
    `logic.scoring_engine.debug_calc_pts`, which is the single source of
    truth for the scoring rules. Each match entry includes:
      - `raw`:   normalised input stats (runs/balls/fours/sixes/...)
      - `steps`: per-component point contributions (participation +4,
                 runs, milestones, SR bonus, wickets, economy, etc.)
      - `base_pts`, `multiplier`, `final_pts`: the authoritative totals.
    """
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
                    pr=con.execute("SELECT name,team,role FROM players WHERE id=?",(pid,)).fetchone()
                    p_name=pr["name"] if pr else pid; match_entries=[]
                    mult_for_player=1.0
                    for wm in week_matches:
                        ms=con.execute(
                            "SELECT raw_score_json FROM match_scores "
                            "WHERE match_id=? AND player_id=?",(wm["id"],pid)).fetchone()
                        if ms:
                            sc=_json.loads(ms["raw_score_json"] or "{}")
                            trace=_debug_calc_pts(sc, player_id=pid, cap_id=cap, vc_id=vc)
                            mult_for_player=trace["multiplier"]
                            computed_total+=trace["final_pts"]
                            match_entries.append({
                                "match_id":wm["id"],"match_title":wm["title"],
                                "suspicious":trace["base_pts"]>200,
                                "raw":trace["inputs"],
                                "steps":trace["steps"],
                                "base_pts":trace["base_pts"],
                                "multiplier":trace["multiplier"],
                                "final_pts":trace["final_pts"]})
                    player_details.append({"id":pid,"name":p_name,"is_cap":pid==cap,"is_vc":pid==vc,
                                           "multiplier":mult_for_player,"matches":match_entries,
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
# 6b. AUDIT & SNAPSHOT
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/audit-player-ids", methods=["GET"])
def api_audit_player_ids():
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
        ghosts = []; seen_ghosts = set()
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
                    ghosts.append({"ghost_id": pid, "first_seen_user": name,
                                   "first_seen_week": wk, "suggestions": suggestions})
        return jsonify({
            "ok": True, "all_ids_valid": len(ghosts) == 0,
            "ghost_count": len(ghosts), "ghosts": ghosts,
            "user_totals": [{"name": k, "total_pts": v} for k, v in sorted(totals.items())],
            "players_in_db": len(all_player_ids),
            "note": ("PASS: All selected player IDs resolve to real players." if len(ghosts) == 0
                     else f"FAIL: {len(ghosts)} ghost ID(s) found."),
        })
    except Exception as e:
        _log(f"GET /api/audit-player-ids: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@bp.route("/api/audit-blobs", methods=["GET"])
def api_audit_blobs():
    try:
        with db._read() as con:
            rows = con.execute(
                "SELECT display_name, week_no, week_pts, points_per_match "
                "FROM user_selections ORDER BY display_name, week_no"
            ).fetchall()
        results = []; mismatches = []
        for row in rows:
            stored = row["week_pts"]
            try: blob = _json.loads(row["points_per_match"] or "{}")
            except: blob = {}
            blob_sum = sum(int(v) for v in blob.values())
            is_match = stored == blob_sum
            entry = {"user": row["display_name"], "week_no": row["week_no"],
                     "stored_week_pts": stored, "blob_sum": blob_sum,
                     "diff": stored - blob_sum, "match": is_match,
                     "matches_in_blob": len(blob)}
            results.append(entry)
            if not is_match: mismatches.append(entry)
        return jsonify({
            "ok": True, "all_blobs_valid": len(mismatches) == 0,
            "mismatch_count": len(mismatches), "mismatches": mismatches,
            "rows_checked": len(results), "details": results,
            "note": ("PASS: All points_per_match blobs sum correctly to week_pts." if len(mismatches) == 0
                     else f"FAIL: {len(mismatches)} row(s) where blob sum != week_pts."),
        })
    except Exception as e:
        _log(f"GET /api/audit-blobs: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@bp.route("/api/snapshot", methods=["POST"])
def api_snapshot():
    try:
        leaderboard = db.get_leaderboard()
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
        ghosts = []; seen_ghosts = set()
        for sel in sels:
            try: ids = _json.loads(sel["tw_team_json"] or "[]")
            except: continue
            for pid in ids:
                if pid not in all_player_ids and pid not in seen_ghosts:
                    seen_ghosts.add(pid)
                    ghosts.append({"ghost_id": pid, "first_seen_user": sel["display_name"]})
        blob_mismatches = []
        for row in blob_rows:
            stored = row["week_pts"]
            try: blob = _json.loads(row["points_per_match"] or "{}")
            except: blob = {}
            blob_sum = sum(int(v) for v in blob.values())
            if stored != blob_sum:
                blob_mismatches.append({"user": row["display_name"], "week_no": row["week_no"],
                                        "stored_week_pts": stored, "blob_sum": blob_sum,
                                        "diff": stored - blob_sum})
        now_iso = datetime.now(timezone.utc).isoformat()
        snapshot = {
            "captured_at": now_iso,
            "backend_clean": len(ghosts) == 0 and len(blob_mismatches) == 0,
            "audit_player_ids": {"all_ids_valid": len(ghosts) == 0,
                                 "ghost_count": len(ghosts), "ghosts": ghosts},
            "audit_blobs": {"all_blobs_valid": len(blob_mismatches) == 0,
                            "mismatch_count": len(blob_mismatches), "mismatches": blob_mismatches},
            "leaderboard": leaderboard,
        }
        safe_ts = now_iso.replace(":", "-").replace(".", "-")
        snap_path = DATA_DIR / f"snapshot_{safe_ts}.json"
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snap_path, "w") as fh:
            _json.dump(snapshot, fh, indent=2)
        _log(f"[snapshot] Saved receipt → {snap_path.name}")
        return jsonify({"ok": True, "captured_at": now_iso, "saved_to": snap_path.name,
                        "backend_clean": snapshot["backend_clean"],
                        "audit_summary": {"ghost_count": len(ghosts),
                                          "blob_mismatch_count": len(blob_mismatches)},
                        "snapshot": snapshot})
    except Exception as e:
        _log(f"POST /api/snapshot: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


# ════════════════════════════════════════════════════════════════════════════
# 6c. MATCH CENTRE  (Phase 9.1)
#
# GET /api/match-centre          ?user=<display_name>
#   Hub view — all matches grouped by week with the user's per-match points.
#   Includes season summary (total, avg, best). Single network request.
#   Read-only, no live tracking, no writes.
#
# GET /api/match-details/<match_id>  ?user=<display_name>
#   Box Score — user's historical XI snapshot for ONE match. Reads
#   tw_team_json from the week that match belongs to, so later team
#   changes do not corrupt the historical record.
#   Read-only, no writes.
# ════════════════════════════════════════════════════════════════════════════

def _match_ordinal(match_id: str) -> str:
    """'ipl26_m12' → 'M12';  fallback strips non-digits."""
    m = re.search(r'_m(\d+)$', match_id, re.IGNORECASE)
    if m: return "M" + m.group(1)
    digits = re.sub(r'[^0-9]', '', match_id)
    return ("M" + digits) if digits else match_id


_IST = timezone(timedelta(hours=5, minutes=30))

def _fmt_date_label(raw: str) -> str:
    """Convert Unix ms timestamp to 'Apr 22' IST; pass through anything else."""
    if not raw:
        return ""
    try:
        ms = int(raw)
        dt = datetime.fromtimestamp(ms / 1000, tz=_IST)
        return dt.strftime("%b") + " " + str(dt.day)
    except (ValueError, TypeError):
        return raw


@bp.route("/api/match-centre", methods=["GET"])
def api_match_centre():
    """
    Match Centre Hub.

    Query param: ?user=<display_name>  (required)

    Response contract:
    {
      "ok": true,
      "name": "Rohan",
      "season": {
        "total_pts": 1363, "matches_played": 9,
        "avg_per_match": 151, "best_pts": 284, "best_match": "RCB vs CSK"
      },
      "weeks": [
        {
          "week_no": 1, "week_pts": 661,
          "matches_played": 3, "total_matches": 3,
          "matches": [
            {
              "match_id": "ipl26_m1", "match_no": "M1",
              "title": "RCB vs CSK",
              "teams": ["RCB", "CSK"],
              "venue": "Chinnaswamy",
              "date_label": "Mar 22",
              "result": "RCB won by 7 wkts",
              "status": "completed",
              "user_match_pts": 284
            }, ...
          ]
        }, ...
      ]
    }
    """
    n = (request.args.get("user") or "").strip()
    if not n or len(n) > 30:
        return jsonify({"error": "?user=<name> required (1-30 chars)", "code": 400}), 400
    try:
        with db._read() as con:
            match_rows = con.execute(
                "SELECT id, week_no, title, teams_json, date_label, status, raw_json "
                "FROM matches ORDER BY week_no, id"
            ).fetchall()
            ump_rows = con.execute(
                "SELECT match_id, pts FROM user_match_points WHERE display_name=?", (n,)
            ).fetchall()
            wk_rows = con.execute(
                "SELECT week_no, week_pts, points_per_match FROM user_selections "
                "WHERE display_name=? ORDER BY week_no", (n,)
            ).fetchall()

        ump_map = {r["match_id"]: r["pts"] for r in ump_rows}
        wk_totals = {r["week_no"]: r["week_pts"] for r in wk_rows}

        # Fill gaps: for any match missing from user_match_points, use points_per_match blob.
        # This handles mixed state where some weeks are scraped and others are history-seeded.
        for r in wk_rows:
            try:
                blob = _jloads(r["points_per_match"], {})
                for mid, pts in blob.items():
                    if mid not in ump_map:
                        ump_map[mid] = pts
            except Exception:
                pass

        weeks_map: dict = {}
        best_pts = 0
        best_match = ""

        for mr in match_rows:
            wk      = mr["week_no"]
            raw     = _jloads(mr["raw_json"], {})
            teams   = _jloads(mr["teams_json"], [])
            user_pts = ump_map.get(mr["id"], 0)

            if user_pts > best_pts:
                best_pts  = user_pts
                best_match = mr["title"] or mr["id"]

            # Pull venue / result from raw_json; keys vary by scraper version
            venue  = (raw.get("venue") or raw.get("location") or
                      raw.get("ground") or raw.get("stadium") or "")
            result = (raw.get("result") or raw.get("match_result") or
                      raw.get("matchResult") or "")

            entry = {
                "match_id":   mr["id"],
                "match_no":   _match_ordinal(mr["id"]),
                "title":      mr["title"] or mr["id"],
                "teams":      teams,
                "venue":      venue,
                "date_label": _fmt_date_label(mr["date_label"]),
                "result":     result,
                "status":     (mr["status"] or "upcoming").lower(),
                "user_match_pts": user_pts,
            }
            if wk not in weeks_map:
                weeks_map[wk] = {
                    "week_no":  wk,
                    "week_pts": wk_totals.get(wk, 0),
                    "matches":  [],
                }
            weeks_map[wk]["matches"].append(entry)

        weeks = sorted(weeks_map.values(), key=lambda w: w["week_no"])
        for w in weeks:
            completed = sum(1 for m in w["matches"]
                            if m["status"] == "completed")
            w["matches_played"] = completed
            w["total_matches"]  = len(w["matches"])

        total_pts      = sum(ump_map.values())
        matches_played = sum(
            1 for mr in match_rows
            if (mr["status"] or "").lower() == "completed" and mr["id"] in ump_map
        )
        avg_per_match = round(total_pts / matches_played) if matches_played else 0

        return jsonify({
            "ok": True,
            "name": n,
            "season": {
                "total_pts":      total_pts,
                "matches_played": matches_played,
                "avg_per_match":  avg_per_match,
                "best_pts":       best_pts,
                "best_match":     best_match,
            },
            "weeks": weeks,
        })
    except Exception as e:
        _log(f"GET /api/match-centre?user={n}: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


@bp.route("/api/match-details/<match_id>", methods=["GET"])
def api_match_details(match_id):
    """
    Box Score for one match.

    Query param: ?user=<display_name>  (required)

    Returns the user's historical XI snapshot for that match, with per-player
    pts and C/VC multipliers applied.  Reads tw_team_json from the week this
    match belongs to — historically accurate regardless of later team changes.

    Response contract:
    {
      "ok": true,
      "match_id": "ipl26_m1", "match_no": "M1",
      "title": "RCB vs CSK", "week_no": 1,
      "venue": "Chinnaswamy", "date_label": "Mar 22",
      "result": "RCB won by 7 wkts", "status": "completed",
      "user_pts": 284,
      "cap_id": "vk18rcb", "vc_id": "jb93mi",
      "top_scorer": {"player_id": "vk18rcb", "name": "Virat Kohli", "pts": 176},
      "players": [
        {
          "player_id": "vk18rcb", "name": "Virat Kohli",
          "role": "BAT", "team": "RCB",
          "is_cap": true, "is_vc": false,
          "base_pts": 88, "multiplier": 2.0,
          "final_pts": 176, "multiplier_str": "88×2"
        }, ...
      ]
    }
    """
    n = (request.args.get("user") or "").strip()
    if not n or len(n) > 30:
        return jsonify({"error": "?user=<name> required (1-30 chars)", "code": 400}), 400
    try:
        with db._read() as con:
            mr = con.execute(
                "SELECT id, week_no, title, teams_json, date_label, status, raw_json "
                "FROM matches WHERE id=?", (match_id,)
            ).fetchone()
            if not mr:
                return jsonify({"error": f"match '{match_id}' not found", "code": 404}), 404

            wk  = mr["week_no"]
            raw = _jloads(mr["raw_json"], {})

            # Historical XI: read from the week this match belongs to
            sel = con.execute(
                "SELECT tw_team_json, tw_cap_id, tw_vc_id "
                "FROM user_selections WHERE display_name=? AND week_no=?",
                (n, wk)
            ).fetchone()

            if not sel:
                return jsonify({
                    "ok": True, "name": n,
                    "match_id": match_id, "match_no": _match_ordinal(match_id),
                    "title": mr["title"] or match_id, "week_no": wk,
                    "venue": "", "date_label": _fmt_date_label(mr["date_label"]),
                    "result": "", "status": (mr["status"] or "upcoming").lower(),
                    "user_pts": 0, "cap_id": None, "vc_id": None,
                    "top_scorer": None, "players": [],
                    "note": f"No selection found for '{n}' in week {wk}",
                })

            team_ids = _jloads(sel["tw_team_json"], [])
            cap_id   = sel["tw_cap_id"]
            vc_id    = sel["tw_vc_id"]

            # Per-player base pts for this match
            pmp_map: dict = {}
            player_info: dict = {}
            if team_ids:
                ph = ",".join("?" * len(team_ids))
                for r in con.execute(
                    f"SELECT player_id, base_pts FROM player_match_points "
                    f"WHERE match_id=? AND player_id IN ({ph})",
                    [match_id] + team_ids
                ).fetchall():
                    pmp_map[r["player_id"]] = r["base_pts"]
                for r in con.execute(
                    f"SELECT id, name, role, team FROM players WHERE id IN ({ph})",
                    team_ids
                ).fetchall():
                    player_info[r["id"]] = dict(r)

            # Authoritative per-match total from user_match_points
            ump_row = con.execute(
                "SELECT pts FROM user_match_points "
                "WHERE display_name=? AND match_id=?", (n, match_id)
            ).fetchone()
            user_total = ump_row["pts"] if ump_row else 0

            # Fallback: read from points_per_match blob in user_selections
            if not user_total:
                ppm_sel = con.execute(
                    "SELECT points_per_match FROM user_selections "
                    "WHERE display_name=? AND week_no=?", (n, wk)
                ).fetchone()
                if ppm_sel:
                    blob = _jloads(ppm_sel["points_per_match"], {})
                    user_total = blob.get(match_id, 0)

        venue  = (raw.get("venue") or raw.get("location") or
                  raw.get("ground") or raw.get("stadium") or "")
        result = (raw.get("result") or raw.get("match_result") or
                  raw.get("matchResult") or "")

        players_out = []
        for pid in team_ids:
            base_pts  = pmp_map.get(pid, 0)
            is_cap    = pid == cap_id
            is_vc     = pid == vc_id
            mult      = 2.0 if is_cap else (1.5 if is_vc else 1.0)
            final_pts = round(base_pts * mult)
            info      = player_info.get(pid, {"id": pid, "name": pid,
                                               "role": "", "team": ""})
            mult_str  = ""
            if is_cap and base_pts > 0:
                mult_str = f"{base_pts}\u00d72"
            elif is_vc and base_pts > 0:
                mult_str = f"{base_pts}\u00d71.5"

            players_out.append({
                "player_id":      pid,
                "name":           info.get("name", pid),
                "role":           info.get("role", ""),
                "team":           info.get("team", ""),
                "is_cap":         is_cap,
                "is_vc":          is_vc,
                "base_pts":       base_pts,
                "multiplier":     mult,
                "final_pts":      final_pts,
                "multiplier_str": mult_str,
            })

        players_out.sort(key=lambda x: -x["final_pts"])
        top = (players_out[0] if players_out and players_out[0]["final_pts"] > 0
               else None)

        return jsonify({
            "ok":         True,
            "name":       n,
            "match_id":   match_id,
            "match_no":   _match_ordinal(match_id),
            "title":      mr["title"] or match_id,
            "week_no":    wk,
            "teams":      _jloads(mr["teams_json"], []),
            "venue":      venue,
            "date_label": _fmt_date_label(mr["date_label"]),
            "result":     result,
            "status":     (mr["status"] or "upcoming").lower(),
            "user_pts":   user_total,
            "cap_id":     cap_id,
            "vc_id":      vc_id,
            "top_scorer": {
                "player_id": top["player_id"],
                "name":      top["name"],
                "pts":       top["final_pts"],
            } if top else None,
            "players": players_out,
        })
    except Exception as e:
        _log(f"GET /api/match-details/{match_id}?user={n}: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


# ════════════════════════════════════════════════════════════════════════════
# 7. ADMIN / ROLLOVER
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/rollover", methods=["POST"])
def api_rollover():
    # Phase 3: optional bearer-token auth. If ROLLOVER_TOKEN is set in env
    # (we set it on the host so the GitHub Actions monday_rollover workflow
    # can trigger), require it. Local dev (no env var) stays open so the
    # "Simulate Monday 2:00 PM Rollover" button in This Week → Dev Tools
    # keeps working without setup. The in-browser auto-rollover from
    # Static/ipl_glue.js also stays unauthenticated; the host's HOSTED+
    # ROLLOVER_TOKEN combo means cloud only — see workflow design.
    expected = os.environ.get("ROLLOVER_TOKEN", "").strip()
    if expected:
        auth = request.headers.get("Authorization", "")
        sent = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        # Fall through if the request comes from the same-origin browser
        # (no Authorization header) — that's the dev-tools button. The
        # workflow always sends the header, so cron triggers must match.
        # If a header is sent but wrong, reject hard.
        if auth and sent != expected:
            return jsonify({"error": "invalid rollover token",
                            "ok": False, "code": 401}), 401
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
        db.update_week_points()
        _push_if_hosted(f"rollover:w{new_week_no}")
        return jsonify({"ok":True,"rolled":True,"new_week_no":new_week_no,
                        "season_complete":new_week_no>=MAX_WEEKS})
    except Exception as e:
        _log(f"POST /api/rollover: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500

@bp.route("/api/matches-status", methods=["GET"])
def api_matches_status():
    try:
        with db._read() as con:
            rows=con.execute(
                "SELECT id,week_no,title,status,scorecard_url,teams_json,date_label "
                "FROM matches ORDER BY id"
            ).fetchall()
        matches=[dict(r) for r in rows]
        # Flag duplicate Cricbuzz IDs so Admin Tab can warn the user
        url_to_ids: dict = {}
        for m in matches:
            url=m.get("scorecard_url") or ""
            cb=url.rstrip("/").split("/")[-1]
            if cb and cb!="00000" and cb.isdigit() and int(cb)>0:
                url_to_ids.setdefault(cb,[]).append(m["id"])
        for m in matches:
            url=m.get("scorecard_url") or ""
            cb=url.rstrip("/").split("/")[-1]
            dup_ids=url_to_ids.get(cb,[]) if (cb and cb!="00000") else []
            m["duplicate_url"]=len(dup_ids)>1
            m["duplicate_with"]=[x for x in dup_ids if x!=m["id"]]
        return jsonify({"ok":True,"matches":matches})
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
        if _IS_HOSTED:
            # No Cricbuzz egress in cloud \u2014 push the URL change so the GitHub
            # Actions runner sees the new URL on checkout, then dispatch the
            # daily_sync workflow so it scrapes immediately rather than
            # waiting for the next scheduled run at 18:30 / 21:30 UTC.
            _push_if_hosted(f"admin-url:{match_id}={cb_id}")
            dispatched, dmsg = cloud_sync.dispatch_workflow(
                "daily_sync.yml", ref="main", log=_log,
            )
            if dispatched:
                msg = ("URL saved, pushed to git, cloud scrape triggered. "
                       "Click Refresh in 60-90s to see new scores.")
            else:
                msg = (f"URL saved & pushed. Scrape NOT triggered: {dmsg}. "
                       f"Check PAT has actions:write scope. Falls back to "
                       f"the next scheduled run at 18:30 / 21:30 UTC.")
        else:
            tasks.start_bg_scrape(match_id,BASE_DIR)
            msg = "URL saved. Scraping started in background \u2014 refresh in ~30 seconds."
        return jsonify({"ok":True,"match_id":match_id,"cb_id":cb_id,"url":clean_url,
                        "message":msg})
    except Exception as e:
        _log(f"POST /api/update-match-url: {e}","error")
        return jsonify({"error":str(e),"ok":False,"code":500}),500


@bp.route("/api/sync-now", methods=["POST"])
def api_sync_now():
    """
    Manual trigger for the daily discovery+scrape pipeline.

    Same code path as the APScheduler daily 23:55 IST job (tasks.py v2.0.0),
    but run on demand from the Admin tab.  Returns immediately; the actual
    work runs in a daemon thread.  Caller polls /api/matches-status or
    /api/state to detect when fresh data lands (typically 30-90 seconds,
    depending on how many newly-completed matches need scraping).

    Request body (optional)
    -----------------------
    {"debug": true}   — verbose logging in the server console.
    Or pass ?debug=1 in the query string.

    Response
    --------
    {
        "ok": true,
        "thread_name": "ipl-daily-sync",
        "debug": false,
        "message": "Discovery+scrape started ..."
    }

    A transient Cricbuzz hiccup will not crash the request:
    tasks.run_discovery_and_scrape() is fault-tolerant; it catches all
    exceptions internally and logs the outcome.  This route only returns
    500 if the daemon thread couldn't be spawned (i.e. OS-level failure).
    """
    re_ = _check_rate(_write_limiter)
    if re_: return re_

    # ── HOSTED mode (Phase 2 + post-deploy fix): pull, then trigger scrape ──
    # The cloud host cannot reach Cricbuzz. Refresh does TWO things:
    #   1. git pull --ff-only — picks up anything the daily Action / local
    #      box / rollover workflow pushed since the last call.
    #   2. workflow_dispatch on daily_sync.yml — asks the GitHub Actions
    #      runner to scrape NOW (it CAN reach Cricbuzz for scorecards by
    #      cricbuzz_id, even though discovery from Actions is blocked).
    # The user clicks Refresh, sees "Triggered scrape — check back in
    # 60-90s", clicks Refresh again later to pull the new commit.
    if _IS_HOSTED:
        try:
            changed, msg = cloud_sync.pull_latest(log=_log)
            if changed:
                # Reconnect any cached DB handle so the next request reads
                # the freshly-pulled fantasy.db, not the file the connection
                # was opened against. Best-effort — SQLite reopens per
                # request anyway, but explicit is safer.
                try:
                    if hasattr(db, "reload_from_disk"):
                        db.reload_from_disk()
                except Exception as e:
                    _log(f"DB reload after pull failed (will retry next request): {e}",
                         "warn")

            # Best-effort dispatch. If the PAT lacks actions:write the
            # request fails 403; we surface that in the response so the
            # operator can fix the PAT, but the pull half still counts.
            dispatched, dmsg = cloud_sync.dispatch_workflow(
                "daily_sync.yml", ref="main", log=_log,
            )
            return jsonify({
                "ok":         True,
                "mode":       "pull_and_dispatch",
                "pulled":     changed,
                "pull_msg":   msg,
                "dispatched": dispatched,
                "dispatch_msg": dmsg,
                "message":    ("Pulled latest + triggered cloud scrape. "
                               "Click Refresh again in 60-90s to see new scores.")
                              if dispatched
                              else (f"Pulled latest. Scrape NOT triggered: {dmsg}. "
                                    f"Check PAT has actions:write scope."),
            })
        except Exception as e:
            _log(f"POST /api/sync-now (HOSTED): {e}", "error")
            return jsonify({"error": str(e), "ok": False, "code": 500}), 500

    # ── LOCAL mode: existing Cricbuzz discovery + scrape behaviour ──────────
    try:
        d     = request.get_json(force=True, silent=True) or {}
        debug = bool(d.get("debug")) or \
                (request.args.get("debug", "").lower() in ("1", "true", "yes"))
        t = tasks.start_bg_sync(debug=debug)
        return jsonify({
            "ok":          True,
            "thread_name": t.name if t else None,
            "debug":       debug,
            "message":     "Discovery+scrape started in background. "
                           "Refresh /api/matches-status in 30-90 seconds.",
        })
    except Exception as e:
        _log(f"POST /api/sync-now: {e}", "error")
        return jsonify({"error": str(e), "ok": False, "code": 500}), 500


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

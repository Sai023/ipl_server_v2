#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Cricbuzz JSON Scraper  v10.11
=================================================
v10.11 (Resilience Upgrade):
  FIX-015: Generalised auto-add via _generate_dynamic_player().
    _auto_add_player() now uses logic.fuzzy_match._generate_dynamic_player()
    to build the player dict, passing the Cricbuzz player ID (batId/bowlId)
    so the generated ID is ext_{cricbuzz_id} — globally unique, never
    collides with Seed_Players.py short-form IDs.
  FIX-016: Defensive extraction — all batsman/bowler field accesses use
    .get() with safe defaults. KeyError on missing scorecard fields can no
    longer crash a match scrape.
  FIX-017: Non-blocking player errors — if a single player entry cannot
    be processed after auto-add, it is logged as NON_BLOCKING_ERROR and
    skipped; the rest of the innings continues.
  FIX-018: Match-level recovery — the entire match processing block is
    wrapped in try/except. A data anomaly on one match logs MATCH_FAILED
    and advances to the next match automatically.
v10.10 (Phase 4): fuzzy functions imported from logic.fuzzy_match.
v10.9 (Phase 3): run_full_scrape() programmatic entry point.
v10.8: FIX-014 per-match atomic point update.
"""

import json
import math
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required.  pip install requests")
    sys.exit(1)

from db_manager import DatabaseManager, _upsert_match
from config import DB_PATH, IPL_YEAR, SCRAPER_VER  # noqa: F401
from logic.fuzzy_match import (
    _norm, _build_player_index, _fuzzy_match, _fuzzy_fielder,
    _generate_dynamic_player,
)

# ── Paths
BASE_DIR    = Path(__file__).resolve().parent
MATCHES_DIR = BASE_DIR / "data" / "matches"
SERIES_ID   = "9237"
MAX_RETRIES = 3
RETRY_DELAY = 3

_ID_RE       = re.compile(r'^[a-z]{1,3}\d{1,2}$')
_MATCH_NO_RE = re.compile(r'_m(\d+)', re.IGNORECASE)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://www.cricbuzz.com/",
    "Accept-Language": "en-US,en;q=0.9",
}

_TEAM_PREFIX = {
    "CSK": "c",  "DC": "d",   "GT": "g",  "KKR": "k",
    "LSG": "l",  "MI": "m",   "PBKS": "p", "RCB": "r",
    "RR": "rr", "SRH": "s",
}

_IPL_TEAMS = frozenset(_TEAM_PREFIX.keys())

_NO_RESULT_STATES = frozenset({
    "no result", "match abandoned", "abandoned", "cancelled", "match cancelled"
})


# ════ OVERS NORMALISATION

def _normalise_overs(raw: float) -> float:
    if raw <= 0:
        return 0.0
    full  = math.floor(raw)
    balls = min(5, max(0, round((raw - full) * 10)))
    return round(full + balls / 6, 4)


# ════ MATCH ID DISCOVERY: POSITION-BASED

_ORDERED_CB_IDS: list = []


def _fetch_ordered_ids() -> list:
    global _ORDERED_CB_IDS
    if _ORDERED_CB_IDS:
        return _ORDERED_CB_IDS
    url = (
        f"https://www.cricbuzz.com/cricket-series/{SERIES_ID}/"
        f"indian-premier-league-{IPL_YEAR}/matches"
    )
    print(f"  [discover] {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            print(f"  [discover] HTTP {r.status_code} — discovery unavailable")
            return []
        seen = set()
        for m in re.finditer(r'/live-cricket-scores/(\d{5,})/', r.text):
            mid = m.group(1)
            if mid not in seen:
                seen.add(mid)
                _ORDERED_CB_IDS.append(mid)
        print(f"  [discover] {len(_ORDERED_CB_IDS)} match IDs (position-indexed)")
    except Exception as e:
        print(f"  [discover] Error: {e}")
    return _ORDERED_CB_IDS


def _match_no_from_id(iid: str) -> int:
    m = _MATCH_NO_RE.search(iid)
    return int(m.group(1)) if m else 0


# ════ AUTO-ADD UNKNOWN PLAYERS (FIX-015)

def _auto_add_player(
    name: str,
    team_code: str,
    pidx: dict,
    cricbuzz_id=None,
) -> str:
    """
    Resolve or create a DB entry for a player not found by fuzzy matching.

    Strategy (FIX-015):
      1. Check if the normalised name already exists in the live index —
         handles the case where a previous innings already added the player.
      2. Use _generate_dynamic_player() to build a fully-keyed player dict
         with a collision-safe ext_{cricbuzz_id} ID.
      3. INSERT OR IGNORE into the players table (safe if called twice).
      4. Add to the in-memory index so subsequent innings / matches reuse
         the same ID without another DB round-trip.

    Returns the player's ID string (always safe to use as a dict key).
    """
    n = _norm(name)

    # 1. Already in live index (e.g. added earlier this run)
    if n in pidx["by_name"]:
        existing = pidx["by_name"][n]
        return existing.get("id") or existing["id"]  # safe .get with fallback

    # 2. Generate canonical player dict
    player = _generate_dynamic_player(name, team_code, cricbuzz_id)
    new_id = player["id"]

    # 3. Persist to DB (INSERT OR IGNORE so re-runs are idempotent)
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute(
            "INSERT OR IGNORE INTO players (id, name, team, price, role) "
            "VALUES (?,?,?,?,?)",
            (new_id, player["name"], player["team"], player["price"], player["role"])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"    ⚠ DB write failed for '{name}' ({new_id}): {e}")

    # 4. Update live index
    pidx["all"].append(player)
    pidx["by_name"][n] = player
    pidx["by_name_team"][(n, player["team"])] = player
    parts = n.split()
    if parts:
        pidx["by_surname"].setdefault(parts[-1], []).append(player)

    print(f"    ➕ Auto-added: '{name}' → {new_id} ({player['team'] or 'UNK'})")
    return new_id


# ════ CRICBUZZ JSON EXTRACTION

def fetch_scorecard_json(cricbuzz_match_id: str) -> dict | None:
    url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cricbuzz_match_id}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code} (attempt {attempt})"); time.sleep(RETRY_DELAY); continue
            idx = r.text.find("scorecardApiData")
            if idx == -1:
                print(f"    No scorecardApiData (attempt {attempt})"); time.sleep(RETRY_DELAY); continue
            start       = r.text.rfind("self.__next_f.push", 0, idx)
            chunk       = r.text[start:]
            inner_start = chunk.find('"') + 1
            end_idx = chunk.find('"]\'\n', inner_start)
            if end_idx == -1: end_idx = chunk.find('\'"]\')', inner_start)
            if end_idx == -1: end_idx = chunk.find('"]\')',  inner_start)
            if end_idx == -1: end_idx = chunk.find('\'"]\'\n', inner_start)
            for pat in ['"]\'\n', '\'"]\')', '"]\')', '\'"]\'\n', '\'"]\'\r\n']:
                ei = chunk.find(pat, inner_start)
                if ei != -1:
                    end_idx = ei
                    break
            json_str    = chunk[inner_start:end_idx].encode().decode("unicode_escape")
            sc_idx      = json_str.find("scorecardApiData")
            brace_start = json_str.find("{", sc_idx)
            depth = 0; end = brace_start
            for i, c in enumerate(json_str[brace_start:], brace_start):
                if c == "{": depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0: end = i; break
            return json.loads(json_str[brace_start:end + 1])
        except requests.RequestException as e:
            print(f"    Network error: {e} (attempt {attempt})"); time.sleep(RETRY_DELAY)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"    JSON error: {e} (attempt {attempt})"); time.sleep(RETRY_DELAY)
    return None


# ════ DISMISSAL PARSER

def _parse_dismissal(out_desc: str) -> dict:
    result = {"is_out": False, "is_lbw_bowled": False,
              "caught_by": None, "stumped_by": None,
              "run_out_fielders": [], "bowler": None}
    if not out_desc: return result
    desc = out_desc.strip()
    dl   = desc.lower()
    if dl in ("", "batting", "not out", "did not bat", "retired hurt"):
        return result
    result["is_out"] = True
    if dl.startswith("lbw"):
        result["is_lbw_bowled"] = True
        m = re.search(r"b\s+(.+)$", desc, re.I)
        if m: result["bowler"] = m.group(1).strip()
    elif re.match(r"^b\s+", dl):
        result["is_lbw_bowled"] = True
        result["bowler"]        = re.sub(r"^b\s+", "", desc, flags=re.I).strip()
    elif dl.startswith("c ") or dl.startswith("c&"):
        cb = re.match(r"c\s*(?:&|and)\s*b\s+(.+)", desc, re.I)
        if cb:
            n = cb.group(1).strip()
            result["caught_by"] = n; result["bowler"] = n
        else:
            m = re.match(r"c\s+(.+?)\s+b\s+(.+)", desc, re.I)
            if m:
                f = re.sub(r"\(sub\)", "", m.group(1)).replace("\u2020", "").strip()
                result["caught_by"] = f
                result["bowler"]    = m.group(2).strip()
    elif dl.startswith("st "):
        m = re.match(r"st\s+(.+?)\s+b\s+(.+)", desc, re.I)
        if m:
            result["stumped_by"] = m.group(1).replace("\u2020", "").strip()
            result["bowler"]     = m.group(2).strip()
    elif "run out" in dl:
        m = re.search(r"run out\s*\((.+?)\)", desc, re.I)
        if m:
            result["run_out_fielders"] = [
                f.strip().replace("\u2020", "")
                for f in re.split(r"[/,]", m.group(1)) if f.strip()
            ]
    return result


# ════ SCORECARD PROCESSOR (FIX-016, FIX-017)

def process_cricbuzz_scorecard(data: dict, pidx: dict) -> dict:
    """
    Extract per-player stats from a Cricbuzz scorecard JSON.

    FIX-016: All field accesses on batsman/bowler dicts use .get() with
    safe defaults — missing keys can no longer raise KeyError.

    FIX-017: Each batsman and bowler is processed in its own try/except.
    If a player entry is corrupt beyond recovery, it is logged as
    NON_BLOCKING_ERROR and skipped; the rest of the innings continues.
    """
    cards = data.get("scoreCard", [])
    if not cards: return {}
    stats = {}; fc = {}; lbw_c = {}; dropped_fielding = []; nb_errors = []

    def _ep(pid: str) -> None:
        """Initialise a player stats slot if not already present."""
        if pid not in stats:
            stats[pid] = {
                "played": True, "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                "got_out": 0, "duck": 0, "overs": 0.0, "runs_conceded": 0,
                "wickets": 0, "maidens": 0, "lbw_bowled": 0,
                "catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0,
            }

    for inn in cards:
        bat_team  = inn.get("batTeamDetails") or {}
        bowl_team = inn.get("bowlTeamDetails") or {}
        bat_code  = bat_team.get("batTeamShortName") or ""
        bowl_code = bowl_team.get("batTeamShortName") or bowl_team.get("bowlTeamShortName") or ""

        # ── Batting
        for entry_key, b in (bat_team.get("batsmenData") or {}).items():
            try:
                cb     = (b.get("batName") or "").strip()
                cb_cid = b.get("batId")               # Cricbuzz player ID (FIX-015)
                if not cb:
                    continue

                pid = _fuzzy_match(cb, pidx, team_hint=bat_code)
                if not pid:
                    if bat_code:
                        pid = _auto_add_player(cb, bat_code, pidx, cricbuzz_id=cb_cid)
                    else:
                        # No team code — generate deterministic fallback without DB write
                        dyn = _generate_dynamic_player(cb, "", cb_cid)
                        pid = dyn["id"]
                        n   = _norm(cb)
                        if n not in pidx["by_name"]:
                            pidx["all"].append(dyn)
                            pidx["by_name"][n] = dyn
                        print(f"    ⚠ No team for '{cb}' — using fallback id {pid}")

                _ep(pid)
                runs = int(b.get("runs") or 0)
                d    = _parse_dismissal(b.get("outDesc") or "batting")
                stats[pid]["runs"]  += runs
                stats[pid]["balls"] += int(b.get("balls") or 0)
                stats[pid]["fours"] += int(b.get("fours") or 0)
                stats[pid]["sixes"] += int(b.get("sixes") or 0)
                if d["is_out"]:
                    stats[pid]["got_out"] = 1
                    if runs == 0: stats[pid]["duck"] = 1

                if d["caught_by"]:
                    fid = _fuzzy_fielder(d["caught_by"], pidx, bowl_code)
                    if fid:
                        fc.setdefault(fid, {"catches": 0, "stumpings": 0,
                                            "run_out_direct": 0, "run_out_assist": 0})
                        fc[fid]["catches"] += 1
                    else:
                        dropped_fielding.append(f"catch: '{d['caught_by']}'")

                if d["stumped_by"]:
                    fid = _fuzzy_fielder(d["stumped_by"], pidx, bowl_code)
                    if fid:
                        fc.setdefault(fid, {"catches": 0, "stumpings": 0,
                                            "run_out_direct": 0, "run_out_assist": 0})
                        fc[fid]["stumpings"] += 1
                    else:
                        dropped_fielding.append(f"stumping: '{d['stumped_by']}'")

                if d["run_out_fielders"]:
                    for i, fn in enumerate(d["run_out_fielders"]):
                        fid = _fuzzy_fielder(fn, pidx, bowl_code)
                        if fid:
                            fc.setdefault(fid, {"catches": 0, "stumpings": 0,
                                                "run_out_direct": 0, "run_out_assist": 0})
                            if i == 0 and len(d["run_out_fielders"]) == 1:
                                fc[fid]["run_out_direct"] += 1
                            elif i == 0:
                                fc[fid]["run_out_assist"] += 1
                            else:
                                fc[fid]["run_out_direct"] += 1
                        else:
                            dropped_fielding.append(f"run-out: '{fn}'")

                if d["is_lbw_bowled"] and d["bowler"]:
                    bid = _fuzzy_fielder(d["bowler"], pidx, bowl_code)
                    if bid:
                        lbw_c[bid] = lbw_c.get(bid, 0) + 1

            except Exception as exc:  # FIX-017: non-blocking player error
                player_label = (b.get("batName") or entry_key or "unknown") if isinstance(b, dict) else entry_key
                nb_errors.append(f"bat '{player_label}': {exc}")
                print(f"    ⚠ NON_BLOCKING_ERROR (bat): '{player_label}' — {exc}")
                continue

        # ── Bowling
        for entry_key, bw in (bowl_team.get("bowlersData") or {}).items():
            try:
                cb     = (bw.get("bowlName") or "").strip()
                cb_cid = bw.get("bowlId")             # Cricbuzz player ID (FIX-015)
                if not cb:
                    continue

                pid = _fuzzy_match(cb, pidx, team_hint=bowl_code)
                if not pid:
                    if bowl_code:
                        pid = _auto_add_player(cb, bowl_code, pidx, cricbuzz_id=cb_cid)
                    else:
                        dyn = _generate_dynamic_player(cb, "", cb_cid)
                        pid = dyn["id"]
                        n   = _norm(cb)
                        if n not in pidx["by_name"]:
                            pidx["all"].append(dyn)
                            pidx["by_name"][n] = dyn
                        print(f"    ⚠ No team for bowler '{cb}' — using fallback id {pid}")

                _ep(pid)
                stats[pid]["overs"]        += _normalise_overs(float(bw.get("overs") or 0))
                stats[pid]["runs_conceded"] += int(bw.get("runs") or 0)
                stats[pid]["wickets"]       += int(bw.get("wickets") or 0)
                stats[pid]["maidens"]       += int(bw.get("maidens") or 0)

            except Exception as exc:  # FIX-017
                player_label = (bw.get("bowlName") or entry_key or "unknown") if isinstance(bw, dict) else entry_key
                nb_errors.append(f"bowl '{player_label}': {exc}")
                print(f"    ⚠ NON_BLOCKING_ERROR (bowl): '{player_label}' — {exc}")
                continue

    # ── Apply fielding credits
    for fid, cr in fc.items():
        _ep(fid)
        stats[fid]["catches"]        += cr["catches"]
        stats[fid]["stumpings"]      += cr["stumpings"]
        stats[fid]["run_out_direct"] += cr["run_out_direct"]
        stats[fid]["run_out_assist"] += cr["run_out_assist"]
    for bid, cnt in lbw_c.items():
        _ep(bid)
        stats[bid]["lbw_bowled"] += cnt

    if dropped_fielding:
        print(f"    ⚠ DROPPED FIELDING CREDITS ({len(dropped_fielding)}):")
        for df in dropped_fielding: print(f"      - {df}")
    if nb_errors:
        print(f"    ⚠ NON_BLOCKING_ERRORS ({len(nb_errors)}) — these players scored 0 pts:")
        for e in nb_errors: print(f"      - {e}")

    return stats


def _extract_meta(data: dict, iid: str, wk: int) -> tuple[dict, bool]:
    """
    Returns (meta_dict, is_no_result).
    is_no_result=True for abandoned/no-result — caller writes empty scores.
    """
    h     = data.get("matchHeader") or {}
    teams = []
    for inn in data.get("scoreCard") or []:
        t = (inn.get("batTeamDetails") or {}).get("batTeamShortName", "")
        if t and t not in teams: teams.append(t)
    title     = h.get("matchDescription", "")
    raw_state = (h.get("state") or "").strip()
    st        = raw_state.lower()

    is_no_result = st in _NO_RESULT_STATES

    if st in ("complete", "mom complete", "result", "abandoned", "no result",
               "match abandoned", "cancelled", "match cancelled"):
        status = "completed"
    elif st in ("in progress", "innings break", "toss", "stumps", "drinks", "rain", "review"):
        status = "live"
    else:
        status = "upcoming"

    meta = {"id": iid, "wk": wk, "title": title or f"Match (Week {wk})",
            "teams": teams, "date": str(h.get("matchStartTimestamp", "")), "status": status}
    return meta, is_no_result


def _reset_url(iid: str) -> None:
    """Reset scorecard URL to /00000 so next run retries discovery."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("UPDATE matches SET scorecard_url=? WHERE id=?",
                     ("https://www.cricbuzz.com/live-cricket-scorecard/00000", iid))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"    ⚠ Could not reset URL for {iid}: {e}")


def _update_points_for_match(db: DatabaseManager, iid: str, wk: int) -> None:
    """FIX-014: Atomic per-match point pipeline."""
    try:
        pmp_rows = db.recalculate_points(match_id=iid)
        db.update_week_points()
        print(f"  ⚡ Points updated: {iid} (W{wk}) — {pmp_rows} player rows recalculated")
    except Exception as e:
        print(f"  ⚠ Point update failed for {iid}: {e}")


# ════ PROGRAMMATIC ENTRY POINT

def run_full_scrape(db: DatabaseManager = None) -> dict:
    """
    Programmatic entry point — callable by tasks.py and other modules.
    Returns {processed, failed, skipped_non_ipl, no_result_count}.

    FIX-018: Each match is processed inside a try/except. A data anomaly
    on one match is logged as MATCH_FAILED and the loop advances to the
    next match automatically — no single bad scorecard can crash the run.
    """
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    if db is None:
        db = DatabaseManager(DB_PATH)

    with db._read() as con:
        con.row_factory = sqlite3.Row
        pidx = _build_player_index(con)
        print(f"  Player index: {len(pidx['all'])} players loaded")
        if pidx["name_conflicts"]:
            print(f"  Name conflicts (team-resolved): {sorted(pidx['name_conflicts'])}")
        if not pidx["all"]:
            raise RuntimeError("players table empty — run Seed_Players.py first")
        sched_teams = {}
        for row in con.execute("SELECT id, teams_json FROM matches").fetchall():
            if row["teams_json"]:
                try: sched_teams[row["id"]] = set(json.loads(row["teams_json"]))
                except: pass
        targets = [dict(r) for r in con.execute(
            "SELECT * FROM matches WHERE LOWER(status)='completed'").fetchall()]
    print(f"  Targets: {len(targets)} completed matches")

    needs_discovery = any(
        (m.get("scorecard_url") or "").endswith("/00000")
        for m in targets
    )
    if needs_discovery:
        n_miss = sum(1 for m in targets if (m.get("scorecard_url") or "").endswith("/00000"))
        print(f"  {n_miss} matches need ID discovery — fetching series page...")
        _fetch_ordered_ids()

    processed = 0; failed = 0; skipped_non_ipl = 0; no_result_count = 0

    for m in targets:
        iid = m.get("id") or ""      # FIX-016: .get() instead of direct key access
        wk  = m.get("week_no", 1)
        if not iid:
            print(f"  ⚠ MATCH_FAILED: row missing 'id' field — {m}")
            failed += 1
            continue

        try:  # FIX-018: match-level recovery wrapper
            url = m.get("scorecard_url") or ""

            last_seg = url.split("/")[-1] if url else ""
            cb_m     = re.search(r'(\d+)', last_seg)
            cb_id    = cb_m.group(1) if cb_m else "0"

            if not cb_m or int(cb_id) == 0:
                mno  = _match_no_from_id(iid)
                ids  = _fetch_ordered_ids()
                discovered = ids[mno - 1] if 0 < mno <= len(ids) else None
                if discovered:
                    cb_id   = discovered
                    new_url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cb_id}"
                    print(f"  DISCOVERED: {iid} — CB#{cb_id} (match #{mno})")
                    try:
                        conn = sqlite3.connect(str(DB_PATH), timeout=30)
                        conn.execute("PRAGMA busy_timeout = 30000")
                        conn.execute("UPDATE matches SET scorecard_url=? WHERE id=?", (new_url, iid))
                        conn.commit(); conn.close()
                    except Exception as e:
                        print(f"    ⚠ Could not persist URL: {e}")
                else:
                    print(f"  SKIP: {iid} — match #{mno} not in series page ({len(ids)} found)")
                    continue

            mno = _match_no_from_id(iid)
            mns = str(mno).zfill(2) if mno else re.sub(r'[^\d]', '', iid).zfill(2)
            jp  = MATCHES_DIR / f"match_{mns}.json"
            if jp.exists() and jp.stat().st_size > 500:
                print(f"  CACHED: {iid}"); continue

            print(f"  SCRAPING: {iid} — CB#{cb_id}")
            time.sleep(1.5)
            data = fetch_scorecard_json(cb_id)
            if not data:
                print(f"  ❌ FAILED: {iid}"); failed += 1; continue

            meta, is_no_result = _extract_meta(data, iid, wk)

            if not is_no_result:
                unknown = [t for t in meta["teams"] if t not in _IPL_TEAMS]
                if unknown or len(meta["teams"]) < 2:
                    reason = f"non-IPL teams {unknown}" if unknown else "missing team data"
                    print(f"  ⚠ SKIP {iid}: {reason} in CB#{cb_id} — wrong scorecard, resetting URL")
                    if jp.exists(): jp.unlink()
                    _reset_url(iid)
                    _ORDERED_CB_IDS.clear()
                    skipped_non_ipl += 1; failed += 1
                    continue

                expected = sched_teams.get(iid, set())
                if expected:
                    scraped_set = set(meta["teams"])
                    if not scraped_set.intersection(expected):
                        print(f"  ⚠ SKIP {iid}: scraped {meta['teams']} vs scheduled {sorted(expected)} — mismatch")
                        if jp.exists(): jp.unlink()
                        _reset_url(iid)
                        _ORDERED_CB_IDS.clear()
                        skipped_non_ipl += 1; failed += 1
                        continue

            if is_no_result:
                scores = {}
                print(f"  ⚪ NO RESULT: {iid} — abandoned/no-result, no points awarded")
                no_result_count += 1
            else:
                scores = process_cricbuzz_scorecard(data, pidx)

            resolved = sum(1 for pid in scores if _ID_RE.match(pid))
            fallback  = len(scores) - resolved
            if fallback > 5:
                print(f"    ⚠ {fallback}/{len(scores)} players unresolved (dynamic IDs)")
            if resolved < 15 and scores:
                print(f"    ⚠ Only {resolved} resolved players (expected ~22)")

            payload = {**meta, "scores": scores}
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            with db._write() as wc:
                _upsert_match(wc, payload)

            if is_no_result:
                print(f"  ✅ PERSISTED: {iid} (no-result, 0 pts, teams: {meta['teams']})")
            else:
                print(f"  ✅ PERSISTED: {iid} ({len(scores)} players, {resolved} seeded "
                      f"+ {fallback} dynamic, teams: {meta['teams']})")

            _update_points_for_match(db, iid, wk)
            processed += 1

        except Exception as exc:  # FIX-018: match-level recovery
            print(f"  ❌ MATCH_FAILED: {iid} — {exc}")
            failed += 1
            continue

    if skipped_non_ipl:
        print(f"\n  ⚠ Skipped {skipped_non_ipl} non-IPL scorecards — run scraper again to retry")
    if no_result_count:
        print(f"  ⚪ {no_result_count} no-result matches recorded (0 points each)")

    if processed > 0:
        print(f"\n  Syncing season_pts for all players...")
        pp = db.update_player_season_pts()
        print(f"  season_pts updated: {pp} players with pts > 0.")

    return {
        "processed":       processed,
        "failed":          failed,
        "skipped_non_ipl": skipped_non_ipl,
        "no_result_count": no_result_count,
    }


# ════ CLI ENTRY POINT

def main():
    print(f"\n--- IPL {IPL_YEAR} SCRAPER v{SCRAPER_VER} ---")
    try:
        result = run_full_scrape()
    except RuntimeError as e:
        print(f"  ❌ FATAL: {e}")
        sys.exit(1)
    print(f"\n--- COMPLETE: {result['processed']} ok, {result['failed']} failed ---")


if __name__ == "__main__":
    main()

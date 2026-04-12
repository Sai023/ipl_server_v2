#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Cricbuzz JSON Scraper  v10.1
================================================
v10.1 changes:
  FIX-004: Position-based match ID discovery replaces slug-based.
           Works for generic 'Match N' titles AND rematches (same teams twice).
           Match N = index N-1 in the ordered Cricbuzz series page list.
  FIX-005: All raw sqlite3 connections use timeout=30 + PRAGMA busy_timeout=30000
           to prevent 'database is locked' when server.py runs concurrently.
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

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data" / "fantasy.db"
MATCHES_DIR = BASE_DIR / "data" / "matches"
SERIES_ID   = "9237"
MAX_RETRIES = 3
RETRY_DELAY = 3

_ID_RE = re.compile(r'^[a-z]{1,3}\d{1,2}$')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://www.cricbuzz.com/",
    "Accept-Language": "en-US,en;q=0.9",
}

# Team prefix map for auto-generated player IDs
_TEAM_PREFIX = {
    "CSK": "c",  "DC": "d",   "GT": "g",  "KKR": "k",
    "LSG": "l",  "MI": "m",   "PBKS": "p", "RCB": "r",
    "RR": "rr", "SRH": "s",
}


# ════ OVERS NORMALISATION ════════════════════════════════════════════════════════

def _normalise_overs(raw: float) -> float:
    if raw <= 0:
        return 0.0
    full  = math.floor(raw)
    balls = min(5, max(0, round((raw - full) * 10)))
    return round(full + balls / 6, 4)


# ════ MATCH ID DISCOVERY: POSITION-BASED (FIX-004) ═════════════════════════════════
#
# WHY POSITION-BASED instead of slug-based:
#   Slug-based: parse team names from title -> look up "srh-vs-rcb" in a dict.
#   Problem 1: Fails if title is "Match 11" (no team names to parse).
#   Problem 2: Teams play twice (SRH vs RCB in M1 AND M67) -> slug maps to M1 only.
#   Position-based: Cricbuzz lists matches chronologically on the series page.
#   _ORDERED_CB_IDS[0] = Match 1, _ORDERED_CB_IDS[10] = Match 11, etc.

_ORDERED_CB_IDS: list = []   # populated once: [match1_id, match2_id, ...]


def _fetch_ordered_ids() -> list:
    """
    Fetch series page once, return CB IDs in chronological match order.
    Index 0 = Match 1, index N-1 = Match N.
    """
    global _ORDERED_CB_IDS
    if _ORDERED_CB_IDS:
        return _ORDERED_CB_IDS
    url = (
        f"https://www.cricbuzz.com/cricket-series/{SERIES_ID}/"
        f"indian-premier-league-2026/matches"
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


# ════ AUTO-ADD UNKNOWN PLAYERS (FIX-002) ═══════════════════════════════════════

def _auto_add_player(name: str, team_code: str, pidx: dict) -> str:
    """Auto-insert unknown player, update in-memory index, return new ID."""
    n = _norm(name)
    if n in pidx["by_name"]:
        return pidx["by_name"][n]["id"]

    prefix = _TEAM_PREFIX.get(team_code.upper(), "x")
    existing_nums = [
        int(m.group()) for p in pidx["all"]
        if p["id"].startswith(prefix)
        for m in [re.search(r'\d+$', p["id"])] if m
    ]
    next_num = max(existing_nums, default=22) + 1
    new_id   = f"{prefix}{next_num:02d}"

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)  # FIX-005
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")        # FIX-005
        conn.execute(
            "INSERT OR IGNORE INTO players (id, name, team, price, role) VALUES (?,?,?,?,?)",
            (new_id, name, team_code.upper(), 5.0, "AR")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"    \u26a0 DB write failed for '{name}': {e}")

    player = {"id": new_id, "name": name, "team": team_code.upper(), "role": "AR"}
    pidx["all"].append(player)
    pidx["by_name"][n] = player
    parts = n.split()
    if parts:
        pidx["by_surname"].setdefault(parts[-1], []).append(player)

    print(f"    \u2795 Auto-added: '{name}' \u2192 {new_id} ({team_code.upper()}, 5.0 CR, AR)")
    return new_id


# ════ CRICBUZZ JSON EXTRACTION ══════════════════════════════════════════════════════════

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
            end_idx     = chunk.find('"]\n', inner_start)
            if end_idx == -1:
                end_idx = chunk.find('"])')
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


# ════ DISMISSAL PARSER ═════════════════════════════════════════════════════════════════════

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
        cb = re.match(r"c\s*&\s*b\s+(.+)", desc, re.I)
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


# ════ FUZZY NAME MATCHER ═══════════════════════════════════════════════════════════════════

def _norm(s: str) -> str:
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/\u2020]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _build_player_index(con: sqlite3.Connection) -> dict:
    rows    = con.execute("SELECT id, name, team, role FROM players").fetchall()
    players = [{"id": r[0], "name": r[1], "team": r[2], "role": r[3]} for r in rows]
    by_name = {}; by_surname = {}
    for p in players:
        n = _norm(p["name"])
        by_name[n] = p
        parts = n.split()
        if parts:
            by_surname.setdefault(parts[-1], []).append(p)
    return {"by_name": by_name, "by_surname": by_surname, "all": players}


def _fuzzy_match(name: str, idx: dict) -> str | None:
    n = _norm(name)
    if not n: return None
    p = idx["by_name"].get(n)
    if p: return p["id"]
    parts   = n.split()
    surname = parts[-1] if parts else n
    cands   = idx["by_surname"].get(surname, [])
    if len(cands) == 1: return cands[0]["id"]
    tokens = set(n.split()); best = 0.0; best_id = None
    for p in idx["all"]:
        pt  = set(_norm(p["name"]).split())
        if not pt: continue
        exp = set()
        for t in tokens:
            if len(t) == 1:
                for x in pt:
                    if x.startswith(t): exp.add(x)
            else: exp.add(t)
        inter = exp & pt; union = exp | pt
        sc = len(inter) / len(union) if union else 0
        if sc > best: best = sc; best_id = p["id"]
    return best_id if best >= 0.45 else None


def _fuzzy_fielder(name: str, idx: dict, bowling_team: str = None) -> str | None:
    n = _norm(name)
    if not n: return None
    p = idx["by_name"].get(n)
    if p: return p["id"]
    cands = idx["by_surname"].get(n, [])
    if len(cands) == 1: return cands[0]["id"]
    if len(cands) > 1 and bowling_team:
        tf = [c for c in cands if c["team"].upper() == bowling_team.upper()]
        if len(tf) == 1: return tf[0]["id"]
    parts = n.split()
    if len(parts) > 1:
        cands2 = idx["by_surname"].get(parts[-1], [])
        if len(cands2) == 1: return cands2[0]["id"]
        if len(cands2) > 1 and bowling_team:
            tf = [c for c in cands2 if c["team"].upper() == bowling_team.upper()]
            if len(tf) == 1: return tf[0]["id"]
    return None


# ════ SCORECARD PROCESSOR ═════════════════════════════════════════════════════════════════

def process_cricbuzz_scorecard(data: dict, pidx: dict) -> dict:
    cards = data.get("scoreCard", [])
    if not cards: return {}
    stats = {}; fc = {}; lbw_c = {}; dropped_fielding = []

    def _ep(pid):
        if pid not in stats:
            stats[pid] = {
                "played": True, "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                "got_out": 0, "duck": 0, "overs": 0.0, "runs_conceded": 0,
                "wickets": 0, "maidens": 0, "lbw_bowled": 0,
                "catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0,
            }

    for inn in cards:
        bat_team  = inn.get("batTeamDetails", {})
        bowl_team = inn.get("bowlTeamDetails", {})
        bat_code  = bat_team.get("batTeamShortName", "")
        bowl_code = (bowl_team.get("batTeamShortName", "") or bowl_team.get("bowlTeamShortName", ""))

        for _, b in bat_team.get("batsmenData", {}).items():
            cb = (b.get("batName") or "").strip()
            if not cb: continue
            pid = _fuzzy_match(cb, pidx)
            if not pid:
                pid = _auto_add_player(cb, bat_code, pidx) if bat_code else _norm(cb).replace(" ", "_")
            _ep(pid)
            runs = int(b.get("runs", 0))
            d    = _parse_dismissal(b.get("outDesc", "batting"))
            stats[pid]["runs"]  += runs
            stats[pid]["balls"] += int(b.get("balls", 0))
            stats[pid]["fours"] += int(b.get("fours", 0))
            stats[pid]["sixes"] += int(b.get("sixes", 0))
            if d["is_out"]:
                stats[pid]["got_out"] = 1
                if runs == 0: stats[pid]["duck"] = 1

            if d["caught_by"]:
                fid = _fuzzy_fielder(d["caught_by"], pidx, bowl_code)
                if fid:
                    fc.setdefault(fid, {"catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0})
                    fc[fid]["catches"] += 1
                else:
                    dropped_fielding.append(f"catch: '{d['caught_by']}'")
            if d["stumped_by"]:
                fid = _fuzzy_fielder(d["stumped_by"], pidx, bowl_code)
                if fid:
                    fc.setdefault(fid, {"catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0})
                    fc[fid]["stumpings"] += 1
                else:
                    dropped_fielding.append(f"stumping: '{d['stumped_by']}'")
            if d["run_out_fielders"]:
                for i, fn in enumerate(d["run_out_fielders"]):
                    fid = _fuzzy_fielder(fn, pidx, bowl_code)
                    if fid:
                        fc.setdefault(fid, {"catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0})
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

        for _, bw in bowl_team.get("bowlersData", {}).items():
            cb = (bw.get("bowlName") or "").strip()
            if not cb: continue
            pid = _fuzzy_match(cb, pidx)
            if not pid:
                pid = _auto_add_player(cb, bowl_code, pidx) if bowl_code else _norm(cb).replace(" ", "_")
            _ep(pid)
            stats[pid]["overs"]        += _normalise_overs(float(bw.get("overs", 0)))
            stats[pid]["runs_conceded"] += int(bw.get("runs", 0))
            stats[pid]["wickets"]       += int(bw.get("wickets", 0))
            stats[pid]["maidens"]       += int(bw.get("maidens", 0))

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
        print(f"    \u26a0 DROPPED FIELDING CREDITS ({len(dropped_fielding)}):")
        for df in dropped_fielding:
            print(f"      - {df}")
    return stats


def _extract_meta(data: dict, iid: str, wk: int) -> dict:
    h     = data.get("matchHeader", {})
    teams = []
    for inn in data.get("scoreCard", []):
        t = inn.get("batTeamDetails", {}).get("batTeamShortName", "")
        if t and t not in teams: teams.append(t)
    title = h.get("matchDescription", "")
    st    = (h.get("state", "") or "").lower()
    if st in ("complete", "mom complete", "result", "abandoned", "no result"):
        status = "completed"
    elif st in ("in progress", "innings break", "toss", "stumps", "drinks", "rain", "review"):
        status = "live"
    else:
        status = "upcoming"
    return {"id": iid, "wk": wk, "title": title or f"Match (Week {wk})",
            "teams": teams, "date": str(h.get("matchStartTimestamp", "")), "status": status}


# ════ MAIN ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n--- IPL 2026 SCRAPER v10.1 (Position-based discovery) ---")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(DB_PATH)

    with db._read() as con:
        con.row_factory = sqlite3.Row
        pidx    = _build_player_index(con)
        print(f"  Player index: {len(pidx['all'])} players loaded")
        if not pidx["all"]:
            print("  \u274c FATAL: players table empty. Run: python Seed_Players.py")
            sys.exit(1)
        targets = [dict(r) for r in con.execute(
            "SELECT * FROM matches WHERE LOWER(status)='completed'").fetchall()]
    print(f"  Targets: {len(targets)} completed matches")

    # Pre-fetch ordered IDs once if any match has placeholder ID 00000
    needs_discovery = any(
        (m.get("scorecard_url") or "").endswith("/00000")
        for m in targets
    )
    if needs_discovery:
        n_missing = sum(1 for m in targets if (m.get("scorecard_url") or "").endswith("/00000"))
        print(f"  {n_missing} matches need ID discovery — fetching series page...")
        _fetch_ordered_ids()

    processed = 0; failed = 0
    for m in targets:
        iid   = m["id"]
        wk    = m.get("week_no", 1)
        url   = m.get("scorecard_url", "")
        title = m.get("title", "")

        # Extract stored CB ID
        last_seg = url.split("/")[-1] if url else ""
        cb_m     = re.search(r'(\d+)', last_seg)
        cb_id    = cb_m.group(1) if cb_m else "0"

        if not cb_m or int(cb_id) == 0:
            # FIX-004: position-based discovery via match number
            mn_m = re.search(r'(\d+)', iid)
            mno  = int(mn_m.group(1)) if mn_m else 0
            ids  = _fetch_ordered_ids()
            discovered = ids[mno - 1] if 0 < mno <= len(ids) else None
            if discovered:
                cb_id   = discovered
                new_url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cb_id}"
                print(f"  DISCOVERED: {iid} \u2014 CB#{cb_id} (match #{mno})")
                # Persist so next run skips discovery
                try:
                    conn = sqlite3.connect(str(DB_PATH), timeout=30)
                    conn.execute("PRAGMA busy_timeout = 30000")
                    conn.execute("UPDATE matches SET scorecard_url=? WHERE id=?", (new_url, iid))
                    conn.commit(); conn.close()
                except Exception as e:
                    print(f"    \u26a0 Could not persist URL: {e}")
            else:
                print(f"  SKIP: {iid} \u2014 match #{mno} not in series page ({len(ids)} found so far)")
                continue

        mn  = re.search(r'(\d+)', iid)
        mns = mn.group(1) if mn else iid
        jp  = MATCHES_DIR / f"match_{mns.zfill(2)}.json"
        if jp.exists() and jp.stat().st_size > 500:
            print(f"  CACHED: {iid}"); continue

        print(f"  SCRAPING: {iid} \u2014 CB#{cb_id}")
        time.sleep(1.5)
        data = fetch_scorecard_json(cb_id)
        if not data:
            print(f"  \u274c FAILED: {iid}"); failed += 1; continue

        scores = process_cricbuzz_scorecard(data, pidx)
        meta   = _extract_meta(data, iid, wk)

        resolved = sum(1 for pid in scores if _ID_RE.match(pid))
        fallback = len(scores) - resolved
        if fallback > 5:
            print(f"    \u26a0 {fallback}/{len(scores)} players unresolved in {iid}")
        if resolved < 15 and scores:
            print(f"    \u26a0 Only {resolved} resolved players (expected ~22)")

        payload = {**meta, "scores": scores}
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        with db._write() as wc:
            _upsert_match(wc, payload)
        print(f"  \u2705 PERSISTED: {iid} ({len(scores)} players, {resolved} resolved)")
        processed += 1

    if processed > 0:
        print(f"\n  Recalculating fantasy points...")
        n = db.recalculate_points()
        print(f"  Points recalculated: {n} rows.")
    print(f"\n--- COMPLETE: {processed} ok, {failed} failed ---")


if __name__ == "__main__":
    main()

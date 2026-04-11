#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Cricbuzz JSON Scraper                    v9.1 (Phase-2 fixes)
================================================================================
Pure HTTP requests against Cricbuzz's Next.js embedded JSON.
Zero browser dependencies. Runs in ~10 seconds on GitHub Actions.

Phase-2 fixes applied:
  DEF-005: Batting stats accumulated (+=) not overwritten (=)
  DEF-006: Fielding credit drops are logged with warnings
  DEF-007: Expanded Cricbuzz status mapping ("mom complete" etc.)
  DEF-009: Overs normalised before storage (3.4 → 3.667)
  DEF-010: Fielder matching uses bowling-team context
  DEF-012: Post-scrape validation warns on low resolved-player count
  DEF-013: unicodedata imported at module level
"""

import json
import math
import os
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
MAX_RETRIES = 3
RETRY_DELAY = 3

_ID_RE = re.compile(r'^[a-z]{1,3}\d{1,2}$')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cricbuzz.com/",
    "Accept-Language": "en-US,en;q=0.9",
}


# ════ OVERS NORMALISATION (mirrors db_manager._normalise_overs) ═════════════════

def _normalise_overs(raw: float) -> float:
    """Convert Cricbuzz notation (3.4 = 3 overs 4 balls) to real overs (3.667)."""
    if raw <= 0:
        return 0.0
    full = math.floor(raw)
    balls = min(5, max(0, round((raw - full) * 10)))
    return round(full + balls / 6, 4)


# ════ CRICBUZZ JSON EXTRACTION ════════════════════════════════════════════

def fetch_scorecard_json(cricbuzz_match_id: str) -> dict | None:
    url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cricbuzz_match_id}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code} (attempt {attempt})")
                time.sleep(RETRY_DELAY); continue
            idx = r.text.find("scorecardApiData")
            if idx == -1:
                print(f"    No scorecardApiData (attempt {attempt})")
                time.sleep(RETRY_DELAY); continue
            start = r.text.rfind("self.__next_f.push", 0, idx)
            chunk = r.text[start:]
            inner_start = chunk.find('"') + 1
            end_idx = chunk.find('"]\n', inner_start)
            if end_idx == -1:
                end_idx = chunk.find('"])')
            json_str = chunk[inner_start:end_idx].encode().decode("unicode_escape")
            sc_idx = json_str.find("scorecardApiData")
            brace_start = json_str.find("{", sc_idx)
            depth = 0; end = brace_start
            for i, c in enumerate(json_str[brace_start:], brace_start):
                if c == "{": depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0: end = i; break
            return json.loads(json_str[brace_start:end + 1])
        except requests.RequestException as e:
            print(f"    Network error: {e} (attempt {attempt})")
            time.sleep(RETRY_DELAY)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"    JSON error: {e} (attempt {attempt})")
            time.sleep(RETRY_DELAY)
    return None


# ════ DISMISSAL PARSER ═════════════════════════════════════════════════════

def _parse_dismissal(out_desc: str) -> dict:
    result = {"is_out": False, "is_lbw_bowled": False,
              "caught_by": None, "stumped_by": None,
              "run_out_fielders": [], "bowler": None}
    if not out_desc: return result
    desc = out_desc.strip()
    dl = desc.lower()
    if dl in ("", "batting", "not out", "did not bat", "retired hurt"):
        return result
    result["is_out"] = True
    if dl.startswith("lbw"):
        result["is_lbw_bowled"] = True
        m = re.search(r"b\s+(.+)$", desc, re.I)
        if m: result["bowler"] = m.group(1).strip()
    elif re.match(r"^b\s+", dl):
        result["is_lbw_bowled"] = True
        result["bowler"] = re.sub(r"^b\s+", "", desc, flags=re.I).strip()
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
                result["bowler"] = m.group(2).strip()
    elif dl.startswith("st "):
        m = re.match(r"st\s+(.+?)\s+b\s+(.+)", desc, re.I)
        if m:
            result["stumped_by"] = m.group(1).replace("\u2020", "").strip()
            result["bowler"] = m.group(2).strip()
    elif "run out" in dl:
        m = re.search(r"run out\s*\((.+?)\)", desc, re.I)
        if m:
            parts = m.group(1)
            result["run_out_fielders"] = [
                f.strip().replace("\u2020", "")
                for f in re.split(r"[/,]", parts) if f.strip()
            ]
    return result


# ════ FUZZY NAME MATCHER ═══════════════════════════════════════════════════

def _norm(s: str) -> str:
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/\u2020]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _build_player_index(con: sqlite3.Connection) -> dict:
    rows = con.execute("SELECT id, name, team, role FROM players").fetchall()
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
    parts = n.split()
    surname = parts[-1] if parts else n
    cands = idx["by_surname"].get(surname, [])
    if len(cands) == 1: return cands[0]["id"]
    tokens = set(n.split())
    best = 0.0; best_id = None
    for p in idx["all"]:
        pt = set(_norm(p["name"]).split())
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
    """DEF-010: Match fielder name with optional team context."""
    n = _norm(name)
    if not n: return None
    p = idx["by_name"].get(n)
    if p: return p["id"]
    cands = idx["by_surname"].get(n, [])
    if len(cands) == 1: return cands[0]["id"]
    # DEF-010: Disambiguate by bowling team
    if len(cands) > 1 and bowling_team:
        team_filtered = [c for c in cands if c["team"].upper() == bowling_team.upper()]
        if len(team_filtered) == 1: return team_filtered[0]["id"]
    parts = n.split()
    if len(parts) > 1:
        cands2 = idx["by_surname"].get(parts[-1], [])
        if len(cands2) == 1: return cands2[0]["id"]
        if len(cands2) > 1 and bowling_team:
            tf = [c for c in cands2 if c["team"].upper() == bowling_team.upper()]
            if len(tf) == 1: return tf[0]["id"]
    return None


# ════ SCORECARD PROCESSOR ══════════════════════════════════════════════════

def process_cricbuzz_scorecard(data: dict, pidx: dict) -> dict:
    cards = data.get("scoreCard", [])
    if not cards: return {}
    stats = {}; fc = {}; lbw_c = {}
    dropped_fielding = []  # DEF-006 tracking

    def _ep(pid):
        if pid not in stats:
            stats[pid] = {
                "played": True, "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                "got_out": 0, "duck": 0, "overs": 0.0, "runs_conceded": 0,
                "wickets": 0, "maidens": 0, "lbw_bowled": 0,
                "catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0,
            }

    for inn in cards:
        bat_team = inn.get("batTeamDetails", {})
        bowl_team = inn.get("bowlTeamDetails", {})
        # DEF-010: Get bowling team name for fielder disambiguation
        bowling_team_name = bowl_team.get("batTeamShortName", "") or bowl_team.get("bowlTeamShortName", "")

        for _, b in bat_team.get("batsmenData", {}).items():
            cb = (b.get("batName") or "").strip()
            if not cb: continue
            pid = _fuzzy_match(cb, pidx)
            if not pid:
                pid = _norm(cb).replace(" ", "_")
                print(f"    \u26a0 Unresolved batter: '{cb}' \u2192 fallback '{pid}'")
            _ep(pid)
            runs = int(b.get("runs", 0))
            d = _parse_dismissal(b.get("outDesc", "batting"))
            # DEF-005: Accumulate, don't overwrite
            stats[pid]["runs"]  += runs
            stats[pid]["balls"] += int(b.get("balls", 0))
            stats[pid]["fours"] += int(b.get("fours", 0))
            stats[pid]["sixes"] += int(b.get("sixes", 0))
            if d["is_out"]:
                stats[pid]["got_out"] = 1
                if runs == 0:
                    stats[pid]["duck"] = 1

            # Fielding credits
            if d["caught_by"]:
                fid = _fuzzy_fielder(d["caught_by"], pidx, bowling_team_name)
                if fid:
                    fc.setdefault(fid, {"catches":0,"stumpings":0,"run_out_direct":0,"run_out_assist":0})
                    fc[fid]["catches"] += 1
                else:  # DEF-006
                    dropped_fielding.append(f"catch: '{d['caught_by']}'")

            if d["stumped_by"]:
                fid = _fuzzy_fielder(d["stumped_by"], pidx, bowling_team_name)
                if fid:
                    fc.setdefault(fid, {"catches":0,"stumpings":0,"run_out_direct":0,"run_out_assist":0})
                    fc[fid]["stumpings"] += 1
                else:
                    dropped_fielding.append(f"stumping: '{d['stumped_by']}'")

            if d["run_out_fielders"]:
                for i, fn in enumerate(d["run_out_fielders"]):
                    fid = _fuzzy_fielder(fn, pidx, bowling_team_name)
                    if fid:
                        fc.setdefault(fid, {"catches":0,"stumpings":0,"run_out_direct":0,"run_out_assist":0})
                        if i == 0 and len(d["run_out_fielders"]) == 1:
                            fc[fid]["run_out_direct"] += 1
                        elif i == 0:
                            fc[fid]["run_out_assist"] += 1
                        else:
                            fc[fid]["run_out_direct"] += 1
                    else:
                        dropped_fielding.append(f"run-out: '{fn}'")

            if d["is_lbw_bowled"] and d["bowler"]:
                bid = _fuzzy_fielder(d["bowler"], pidx, bowling_team_name)
                if bid:
                    lbw_c[bid] = lbw_c.get(bid, 0) + 1

        for _, bw in bowl_team.get("bowlersData", {}).items():
            cb = (bw.get("bowlName") or "").strip()
            if not cb: continue
            pid = _fuzzy_match(cb, pidx)
            if not pid:
                pid = _norm(cb).replace(" ", "_")
                print(f"    \u26a0 Unresolved bowler: '{cb}' \u2192 fallback '{pid}'")
            _ep(pid)
            # DEF-009: Normalise overs before storage
            stats[pid]["overs"] += _normalise_overs(float(bw.get("overs", 0)))
            stats[pid]["runs_conceded"] += int(bw.get("runs", 0))
            stats[pid]["wickets"] += int(bw.get("wickets", 0))
            stats[pid]["maidens"] += int(bw.get("maidens", 0))

    for fid, cr in fc.items():
        _ep(fid)
        stats[fid]["catches"] += cr["catches"]
        stats[fid]["stumpings"] += cr["stumpings"]
        stats[fid]["run_out_direct"] += cr["run_out_direct"]
        stats[fid]["run_out_assist"] += cr["run_out_assist"]
    for bid, cnt in lbw_c.items():
        _ep(bid)
        stats[bid]["lbw_bowled"] += cnt

    # DEF-006: Log dropped fielding credits
    if dropped_fielding:
        print(f"    \u26a0 DROPPED FIELDING CREDITS ({len(dropped_fielding)}):")
        for df in dropped_fielding:
            print(f"      - {df}")

    return stats


def _extract_meta(data: dict, iid: str, wk: int) -> dict:
    h = data.get("matchHeader", {})
    teams = []
    for inn in data.get("scoreCard", []):
        t = inn.get("batTeamDetails", {}).get("batTeamShortName", "")
        if t and t not in teams: teams.append(t)
    title = h.get("matchDescription", "")
    st = (h.get("state", "") or "").lower()
    # DEF-007: Expanded status mapping
    if st in ("complete", "mom complete", "result", "abandoned", "no result"):
        status = "completed"
    elif st in ("in progress", "innings break", "toss", "stumps", "drinks", "rain", "review"):
        status = "live"
    else:
        status = "upcoming"
    return {"id": iid, "wk": wk, "title": title or f"Match (Week {wk})",
            "teams": teams, "date": str(h.get("matchStartTimestamp", "")), "status": status}


# ════ MAIN ═══════════════════════════════════════════════════════════════

def main():
    print("\n--- IPL 2026 SCRAPER v9.1 (Cricbuzz JSON) ---")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(DB_PATH)

    with db._read() as con:
        con.row_factory = sqlite3.Row
        pidx = _build_player_index(con)
        print(f"  Player index: {len(pidx['all'])} players loaded")
        if len(pidx['all']) == 0:
            print("  \u274c FATAL: players table is empty! Run: python Seed_Players.py")
            sys.exit(1)
        targets = [dict(r) for r in con.execute(
            "SELECT * FROM matches WHERE LOWER(status) = 'completed'").fetchall()]
    print(f"  Targets: {len(targets)} completed matches")

    processed = 0; failed = 0
    for m in targets:
        iid = m["id"]; wk = m.get("week_no", 1); url = m.get("scorecard_url", "")
        # DEF-003: Accept any numeric ID (\d+), validate > 0
        cb_m = re.search(r"(\d+)", url.split("/")[-1] if url else "")
        if not cb_m or int(cb_m.group(1)) == 0:
            print(f"  SKIP: {iid} \u2014 no valid Cricbuzz ID in: {url}")
            continue
        cb_id = cb_m.group(1)
        mn = re.search(r"(\d+)", iid)
        mns = mn.group(1) if mn else iid
        jp = MATCHES_DIR / f"match_{mns.zfill(2)}.json"
        if jp.exists() and jp.stat().st_size > 500:
            print(f"  CACHED: {iid}"); continue
        print(f"  SCRAPING: {iid} \u2014 CB#{cb_id}")
        time.sleep(1.5)
        data = fetch_scorecard_json(cb_id)
        if not data:
            print(f"  \u274c FAILED: {iid}"); failed += 1; continue
        scores = process_cricbuzz_scorecard(data, pidx)
        meta = _extract_meta(data, iid, wk)

        # DEF-012: Validate resolved player count
        resolved = sum(1 for pid in scores if _ID_RE.match(pid))
        fallback = len(scores) - resolved
        if fallback > 5:
            print(f"    \u26a0 WARNING: {fallback}/{len(scores)} players unresolved in {iid}")
        if resolved < 15 and len(scores) > 0:
            print(f"    \u26a0 WARNING: Only {resolved} resolved players (expected ~22)")

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

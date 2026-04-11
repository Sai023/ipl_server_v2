#!/usr/bin/env python3
"""
IPL Fantasy 2026 — Cricbuzz JSON Scraper                    v9.0 (API-first)
===========================================================================
Replaces the Playwright/ESPN HTML scraper with pure HTTP requests against
Cricbuzz's Next.js embedded JSON + mcenter API.

Zero browser dependencies. Runs in ~10 seconds on GitHub Actions.

Data flow:
  Cricbuzz page → extract scorecardApiData JSON → parse batting/bowling/
  fielding → write match JSON to data/matches/ → upsert into fantasy.db
  via _upsert_match() → recalculate fantasy points.
"""

import json
import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Run: pip install requests")
    sys.exit(1)

from db_manager import DatabaseManager, _upsert_match

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
DB_PATH     = BASE_DIR / "data" / "fantasy.db"
MATCHES_DIR = BASE_DIR / "data" / "matches"
MAX_RETRIES = 3
RETRY_DELAY = 3

# ── HTTP headers (mimic real browser) ────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cricbuzz.com/",
    "Accept-Language": "en-US,en;q=0.9",
}


# ═══════════════════════════════════════════════════════════════════════════════
# CRICBUZZ JSON EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_scorecard_json(cricbuzz_match_id: str) -> dict | None:
    """
    Fetch the scorecardApiData JSON blob from Cricbuzz's Next.js scorecard page.
    Falls back to None on failure. No browser needed — pure HTTP.
    """
    url = f"https://www.cricbuzz.com/live-cricket-scorecard/{cricbuzz_match_id}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code} for {cricbuzz_match_id} (attempt {attempt})")
                time.sleep(RETRY_DELAY)
                continue

            idx = r.text.find("scorecardApiData")
            if idx == -1:
                print(f"    No scorecardApiData found for {cricbuzz_match_id} (attempt {attempt})")
                time.sleep(RETRY_DELAY)
                continue

            # Extract the JSON blob from Next.js self.__next_f.push() payload
            start = r.text.rfind("self.__next_f.push", 0, idx)
            chunk = r.text[start:]
            inner_start = chunk.find('"') + 1
            end_idx = chunk.find('"]\n', inner_start)
            if end_idx == -1:
                end_idx = chunk.find('"])')
            json_str = chunk[inner_start:end_idx].encode().decode("unicode_escape")

            sc_idx = json_str.find("scorecardApiData")
            brace_start = json_str.find("{", sc_idx)
            depth = 0
            end = brace_start
            for i, c in enumerate(json_str[brace_start:], brace_start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break

            return json.loads(json_str[brace_start : end + 1])

        except requests.RequestException as e:
            print(f"    Network error for {cricbuzz_match_id}: {e} (attempt {attempt})")
            time.sleep(RETRY_DELAY)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"    JSON parse error for {cricbuzz_match_id}: {e} (attempt {attempt})")
            time.sleep(RETRY_DELAY)

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# DISMISSAL PARSER  (extracts fielding credits from outDesc strings)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_dismissal(out_desc: str) -> dict:
    """
    Parse Cricbuzz outDesc like:
      'c Kohli b Bumrah', 'lbw b Siraj', 'b Bumrah',
      'st Pant b Chahal', 'run out (Jadeja/Pant)'
    Returns fielding attribution dict.
    """
    result = {
        "is_out": False,
        "is_lbw_bowled": False,
        "caught_by": None,
        "stumped_by": None,
        "run_out_fielders": [],
        "bowler": None,
    }
    if not out_desc:
        return result

    desc = out_desc.strip()
    desc_lower = desc.lower()

    # Not out cases
    if desc_lower in ("", "batting", "not out", "did not bat", "retired hurt"):
        return result

    result["is_out"] = True

    # LBW: "lbw b Bowler"
    if desc_lower.startswith("lbw"):
        result["is_lbw_bowled"] = True
        m = re.search(r"b\s+(.+)$", desc, re.I)
        if m:
            result["bowler"] = m.group(1).strip()

    # Clean bowled: "b Bowler"
    elif re.match(r"^b\s+", desc_lower):
        result["is_lbw_bowled"] = True
        result["bowler"] = re.sub(r"^b\s+", "", desc, flags=re.I).strip()

    # Caught: "c Fielder b Bowler" or "c & b Bowler"
    elif desc_lower.startswith("c ") or desc_lower.startswith("c&"):
        cb_match = re.match(r"c\s*&\s*b\s+(.+)", desc, re.I)
        if cb_match:
            # Caught & bowled — bowler gets the catch
            bowler_name = cb_match.group(1).strip()
            result["caught_by"] = bowler_name
            result["bowler"] = bowler_name
        else:
            m = re.match(r"c\s+(.+?)\s+b\s+(.+)", desc, re.I)
            if m:
                fielder = m.group(1).strip()
                # Remove sub/† markers
                fielder = re.sub(r"\(sub\)", "", fielder).strip()
                fielder = fielder.replace("†", "").strip()
                result["caught_by"] = fielder
                result["bowler"] = m.group(2).strip()

    # Stumped: "st Keeper b Bowler"
    elif desc_lower.startswith("st "):
        m = re.match(r"st\s+(.+?)\s+b\s+(.+)", desc, re.I)
        if m:
            result["stumped_by"] = m.group(1).replace("†", "").strip()
            result["bowler"] = m.group(2).strip()

    # Run out: "run out (Fielder)" or "run out (Thrower/Catcher)"
    elif "run out" in desc_lower:
        m = re.search(r"run out\s*\((.+?)\)", desc, re.I)
        if m:
            fielders_str = m.group(1)
            fielders = [f.strip().replace("†", "") for f in re.split(r"[/,]", fielders_str) if f.strip()]
            result["run_out_fielders"] = fielders

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# FUZZY NAME MATCHER  (maps Cricbuzz names → DB player IDs)
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise_name(s: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace."""
    import unicodedata
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/†]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_player_index(con: sqlite3.Connection) -> dict:
    """
    Build lookup indices from the players table.
    Returns {"by_id": {...}, "by_name_norm": {...}, "by_surname": {...}, "all": [...]}
    """
    rows = con.execute("SELECT id, name, team, role FROM players").fetchall()
    players = [{"id": r[0], "name": r[1], "team": r[2], "role": r[3]} for r in rows]

    by_id = {p["id"]: p for p in players}
    by_name_norm = {}
    by_surname = {}

    for p in players:
        norm = _normalise_name(p["name"])
        by_name_norm[norm] = p
        parts = norm.split()
        if parts:
            surname = parts[-1]
            by_surname.setdefault(surname, []).append(p)

    return {"by_id": by_id, "by_name_norm": by_name_norm, "by_surname": by_surname, "all": players}


def _fuzzy_match(name: str, player_index: dict) -> str | None:
    """
    Resolve a Cricbuzz player name to a DB player ID.
    Tiers: exact name → surname match → token-set fuzzy.
    Returns player ID string or None.
    """
    norm = _normalise_name(name)
    if not norm:
        return None

    # Tier 1: exact normalised name match
    p = player_index["by_name_norm"].get(norm)
    if p:
        return p["id"]

    # Tier 2: surname match (last word)
    parts = norm.split()
    surname = parts[-1] if parts else norm
    candidates = player_index["by_surname"].get(surname, [])
    if len(candidates) == 1:
        return candidates[0]["id"]

    # Tier 3: token-set overlap
    input_tokens = set(norm.split())
    best_score = 0.0
    best_id = None
    for p in player_index["all"]:
        p_tokens = set(_normalise_name(p["name"]).split())
        if not p_tokens:
            continue
        # Expand single-char initials
        expanded = set()
        for t in input_tokens:
            if len(t) == 1:
                for pt in p_tokens:
                    if pt.startswith(t):
                        expanded.add(pt)
            else:
                expanded.add(t)
        intersection = expanded & p_tokens
        union = expanded | p_tokens
        score = len(intersection) / len(union) if union else 0
        if score > best_score:
            best_score = score
            best_id = p["id"]

    if best_score >= 0.45:
        return best_id

    return None


def _fuzzy_match_fielder(fielder_name: str, player_index: dict) -> str | None:
    """Match a fielder name from dismissal text (often surname-only)."""
    norm = _normalise_name(fielder_name)
    if not norm:
        return None

    # Direct lookup
    p = player_index["by_name_norm"].get(norm)
    if p:
        return p["id"]

    # Surname-only (very common in dismissal strings)
    candidates = player_index["by_surname"].get(norm, [])
    if len(candidates) == 1:
        return candidates[0]["id"]

    # Multi-word fielder name: try last token as surname
    parts = norm.split()
    if len(parts) > 1:
        candidates = player_index["by_surname"].get(parts[-1], [])
        if len(candidates) == 1:
            return candidates[0]["id"]

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SCORECARD → FANTASY STATS PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════

def process_cricbuzz_scorecard(data: dict, player_index: dict) -> dict:
    """
    Transform Cricbuzz scorecardApiData into a dict of:
      { player_id: { played, runs, balls, fours, sixes, got_out, duck,
                     overs, runs_conceded, wickets, maidens, lbw_bowled,
                     catches, stumpings, run_out_direct, run_out_assist } }

    Uses fuzzy name matching to resolve Cricbuzz names → DB player IDs.
    """
    score_cards = data.get("scoreCard", [])
    if not score_cards:
        return {}

    all_stats = {}  # player_id → stats dict
    fielding_credits = {}  # player_id → {catches, stumpings, run_out_direct, run_out_assist}
    lbw_bowled_credits = {}  # player_id → count

    def _ensure_player(pid: str):
        if pid not in all_stats:
            all_stats[pid] = {
                "played": True, "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                "got_out": 0, "duck": 0, "overs": 0.0, "runs_conceded": 0,
                "wickets": 0, "maidens": 0, "lbw_bowled": 0,
                "catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0,
            }

    for innings in score_cards:
        bat_team = innings.get("batTeamDetails", {})
        bowl_team = innings.get("bowlTeamDetails", {})

        # ── Batting ──────────────────────────────────────────────────────
        for key, b in bat_team.get("batsmenData", {}).items():
            cb_name = (b.get("batName") or "").strip()
            if not cb_name:
                continue
            pid = _fuzzy_match(cb_name, player_index)
            if not pid:
                # Use normalised name as fallback ID
                pid = _normalise_name(cb_name).replace(" ", "_")
                print(f"    ⚠ Unresolved batter: '{cb_name}' → fallback '{pid}'")

            _ensure_player(pid)
            runs = int(b.get("runs", 0))
            out_desc = b.get("outDesc", "batting")
            dismissal = _parse_dismissal(out_desc)
            is_out = dismissal["is_out"]

            all_stats[pid]["runs"] = runs
            all_stats[pid]["balls"] = int(b.get("balls", 0))
            all_stats[pid]["fours"] = int(b.get("fours", 0))
            all_stats[pid]["sixes"] = int(b.get("sixes", 0))
            all_stats[pid]["got_out"] = 1 if is_out else 0
            all_stats[pid]["duck"] = 1 if (is_out and runs == 0) else 0

            # ── Fielding credits from dismissal ──────────────────────
            if dismissal["caught_by"]:
                fid = _fuzzy_match_fielder(dismissal["caught_by"], player_index)
                if fid:
                    fielding_credits.setdefault(fid, {"catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0})
                    fielding_credits[fid]["catches"] += 1

            if dismissal["stumped_by"]:
                fid = _fuzzy_match_fielder(dismissal["stumped_by"], player_index)
                if fid:
                    fielding_credits.setdefault(fid, {"catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0})
                    fielding_credits[fid]["stumpings"] += 1

            if dismissal["run_out_fielders"]:
                for i, fname in enumerate(dismissal["run_out_fielders"]):
                    fid = _fuzzy_match_fielder(fname, player_index)
                    if fid:
                        fielding_credits.setdefault(fid, {"catches": 0, "stumpings": 0, "run_out_direct": 0, "run_out_assist": 0})
                        if i == 0 and len(dismissal["run_out_fielders"]) == 1:
                            fielding_credits[fid]["run_out_direct"] += 1
                        elif i == 0:
                            fielding_credits[fid]["run_out_assist"] += 1
                        else:
                            fielding_credits[fid]["run_out_direct"] += 1

            if dismissal["is_lbw_bowled"] and dismissal["bowler"]:
                bid = _fuzzy_match_fielder(dismissal["bowler"], player_index)
                if bid:
                    lbw_bowled_credits[bid] = lbw_bowled_credits.get(bid, 0) + 1

        # ── Bowling ──────────────────────────────────────────────────────
        for key, bw in bowl_team.get("bowlersData", {}).items():
            cb_name = (bw.get("bowlName") or "").strip()
            if not cb_name:
                continue
            pid = _fuzzy_match(cb_name, player_index)
            if not pid:
                pid = _normalise_name(cb_name).replace(" ", "_")
                print(f"    ⚠ Unresolved bowler: '{cb_name}' → fallback '{pid}'")

            _ensure_player(pid)
            all_stats[pid]["overs"] = float(bw.get("overs", 0))
            all_stats[pid]["runs_conceded"] = int(bw.get("runs", 0))
            all_stats[pid]["wickets"] = int(bw.get("wickets", 0))
            all_stats[pid]["maidens"] = int(bw.get("maidens", 0))

    # ── Apply fielding credits ───────────────────────────────────────────
    for fid, credits in fielding_credits.items():
        _ensure_player(fid)
        all_stats[fid]["catches"] += credits["catches"]
        all_stats[fid]["stumpings"] += credits["stumpings"]
        all_stats[fid]["run_out_direct"] += credits["run_out_direct"]
        all_stats[fid]["run_out_assist"] += credits["run_out_assist"]

    # ── Apply lbw/bowled credits ─────────────────────────────────────────
    for bid, count in lbw_bowled_credits.items():
        _ensure_player(bid)
        all_stats[bid]["lbw_bowled"] += count

    return all_stats


def _extract_match_meta(data: dict, internal_id: str, week_no: int) -> dict:
    """Extract match-level metadata from scorecardApiData."""
    header = data.get("matchHeader", {})
    score_cards = data.get("scoreCard", [])

    teams = []
    for inn in score_cards:
        team_name = inn.get("batTeamDetails", {}).get("batTeamShortName", "")
        if team_name and team_name not in teams:
            teams.append(team_name)

    title = header.get("matchDescription", "")
    status_str = header.get("state", "")
    if status_str.lower() in ("complete",):
        status = "completed"
    elif status_str.lower() in ("in progress", "innings break", "toss"):
        status = "live"
    else:
        status = "upcoming"

    return {
        "id": internal_id,
        "wk": week_no,
        "title": title or f"Match (Week {week_no})",
        "teams": teams,
        "date": header.get("matchStartTimestamp", ""),
        "status": status,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n--- IPL 2026 SCRAPER v9.0 (Cricbuzz JSON API) STARTING ---")
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)

    db = DatabaseManager(DB_PATH)

    # Build player index for fuzzy matching
    with db._read() as con:
        con.row_factory = sqlite3.Row
        player_index = _build_player_index(con)
        print(f"  Player index: {len(player_index['all'])} players loaded")

        targets = [
            dict(r) for r in
            con.execute(
                "SELECT * FROM matches WHERE LOWER(status) = 'completed'"
            ).fetchall()
        ]

    print(f"  Targets: {len(targets)} completed matches to process")

    processed = 0
    failed = 0

    for m in targets:
        internal_id = m["id"]
        week_no = m.get("week_no", 1)
        url = m.get("scorecard_url", "")

        # Extract Cricbuzz match ID from URL
        cb_match = re.search(r"(\d{4,})", url)
        if not cb_match:
            print(f"  SKIP: {internal_id} — no Cricbuzz match ID in URL: {url}")
            continue
        cb_id = cb_match.group(1)

        # Check if already scraped
        m_num = re.search(r"(\d+)", internal_id)
        m_num_str = m_num.group(1) if m_num else internal_id
        json_path = MATCHES_DIR / f"match_{m_num_str.zfill(2)}.json"
        if json_path.exists() and json_path.stat().st_size > 500:
            print(f"  CACHED: {internal_id} (match_{m_num_str.zfill(2)}.json exists)")
            continue

        print(f"  SCRAPING: {internal_id} — Cricbuzz ID {cb_id}")
        time.sleep(1.5)  # Rate limiting

        data = fetch_scorecard_json(cb_id)
        if not data:
            print(f"  ❌ FAILED: {internal_id}")
            failed += 1
            continue

        # Process scorecard
        scores = process_cricbuzz_scorecard(data, player_index)
        meta = _extract_match_meta(data, internal_id, week_no)

        payload = {**meta, "scores": scores}

        # Write JSON archive
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        # Persist to DB
        with db._write() as w_con:
            _upsert_match(w_con, payload)

        print(f"  ✅ PERSISTED: {internal_id} ({len(scores)} players)")
        processed += 1

    # Recalculate all points
    if processed > 0:
        print(f"\n  Recalculating fantasy points...")
        n = db.recalculate_points()
        print(f"  Points recalculated for {n} player-match rows.")

    print(f"\n--- SYNC COMPLETE: {processed} processed, {failed} failed ---")


if __name__ == "__main__":
    main()

"""
ESPNcricinfo IPL 2026 Scraper
═════════════════════════════
Phase 1 — discover_match_urls()
    Scrapes the Series Schedule page for IPL 2026 (series_id=1510719),
    maps each match to the local DB row via match_no / team-slug comparison,
    and performs a one-time UPDATE on matches.scorecard_url.

Phase 2 — scraper_loop()
    Polls the DB for Live/Completed matches that have a scorecard_url,
    scrapes the full scorecard with Playwright (headless), parses batting,
    bowling, fielding columns and dismissal text, maps ESPNcricinfo player
    names to local player_ids, and writes raw stats + Player_Match_Points
    in a single atomic transaction.

Requirements:
    pip install playwright aiohttp aiosqlite
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Optional async SQLite (falls back to sync if unavailable) ────────────────
try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PWTimeout

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

DB_PATH         = Path(__file__).parent / "data" / "fantasy.db"
SERIES_ID       = 1510719                          # IPL 2026
SERIES_URL      = f"https://www.espncricinfo.com/series/ipl-2026-{SERIES_ID}/match-schedule-fixtures"
SCORECARD_BASE  = "https://www.espncricinfo.com"
SCRAPE_INTERVAL = 300                              # 5-minute throttle (seconds)
LOG_PATH        = Path(__file__).parent / "scraper.log"

# Circuit-breaker: after CB_THRESHOLD consecutive 403/404 on a URL → skip it
CB_THRESHOLD    = 3
CB_RESET_SECS   = 1800                            # 30 min cool-down

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("ipl_scraper")


# ═══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _CircuitBreaker:
    failures: dict[str, int]       = field(default_factory=dict)
    tripped_at: dict[str, float]   = field(default_factory=dict)

    def record_failure(self, url: str) -> None:
        self.failures[url] = self.failures.get(url, 0) + 1
        if self.failures[url] >= CB_THRESHOLD:
            self.tripped_at[url] = time.monotonic()
            log.warning("CIRCUIT BREAKER tripped for %s (failures=%d)", url, self.failures[url])

    def record_success(self, url: str) -> None:
        self.failures.pop(url, None)
        self.tripped_at.pop(url, None)

    def is_open(self, url: str) -> bool:
        if url not in self.tripped_at:
            return False
        elapsed = time.monotonic() - self.tripped_at[url]
        if elapsed >= CB_RESET_SECS:
            log.info("CIRCUIT BREAKER reset for %s after %.0fs", url, elapsed)
            self.failures.pop(url, None)
            self.tripped_at.pop(url, None)
            return False
        return True


_cb = _CircuitBreaker()


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY — TEXT CLEANING
# ═══════════════════════════════════════════════════════════════════════════════

_STRIP_RE   = re.compile(r"[*†/\s]+")
_OVER_RE    = re.compile(r"^(\d+)(?:\.(\d))?$")          # "3.4" → (3, 4)
_SCORE_RE   = re.compile(r"^(\d+)(?:/(\d+))?$")          # "145/3" → (145, 3)
_DNB_RE     = re.compile(r"did not bat", re.I)


def clean_name(raw: str) -> str:
    """Strip diacritics-lite: lowercase, collapse spaces, drop punctuation."""
    return re.sub(r"[^a-z0-9\s]", "", raw.lower()).strip()


def name_to_slug(raw: str) -> str:
    """'Virat Kohli' → 'virat-kohli'  (ESPNcricinfo style)."""
    return re.sub(r"\s+", "-", clean_name(raw))


def parse_overs(text: str) -> float:
    """'3.4' → 3.667  |  '4' → 4.0"""
    t = text.strip()
    m = _OVER_RE.match(t)
    if not m:
        return 0.0
    full, ball = int(m.group(1)), int(m.group(2) or 0)
    return round(full + ball / 6, 4)


def parse_int(text: str, default: int = 0) -> int:
    t = _STRIP_RE.sub("", text)
    try:
        return int(t)
    except ValueError:
        return default


def parse_score_string(text: str) -> tuple[int, int]:
    """'145/3' → (145, 3)  |  '145' → (145, 0)"""
    m = _SCORE_RE.match(text.strip())
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2) or 0)


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY — DISMISSAL PARSER
# ═══════════════════════════════════════════════════════════════════════════════

# Patterns for fielding credit extraction from dismissal strings like:
#   "c Rohit Sharma b Bumrah"
#   "st †Dhoni b Chahal"
#   "run out (Jadeja/†Dhoni)"
#   "lbw b Bumrah"

_C_RE      = re.compile(r"^c\s+(?!&\s)([A-Za-z '\-]+?)\s+b\s+", re.I)
_ST_RE     = re.compile(r"^st\s+[†]?([A-Za-z '\-]+?)\s+b\s+", re.I)
_RO_RE     = re.compile(r"run out\s*\(([^/)]+?)(?:/([^)]+))?\)", re.I)
_LBW_RE    = re.compile(r"^lbw\b", re.I)
_BOWLED_RE = re.compile(r"^b\s+", re.I)
_SUB_RE    = re.compile(r"\(sub\)", re.I)


@dataclass
class DismissalInfo:
    mode: str                       # "caught","stumped","run_out","lbw","bowled","other","not_out"
    catcher:        Optional[str] = None
    stumper:        Optional[str] = None
    run_out_direct: Optional[str] = None
    run_out_assist: Optional[str] = None
    bowler:         Optional[str] = None


def parse_dismissal(text: str) -> DismissalInfo:
    t = text.strip()

    if not t or t.lower() in ("not out", "absent", "retired hurt", "retired not out"):
        return DismissalInfo(mode="not_out")

    if _DNB_RE.search(t):
        return DismissalInfo(mode="not_out")

    # run out
    m = _RO_RE.search(t)
    if m:
        direct = m.group(1).strip().lstrip("†") if m.group(1) else None
        assist = m.group(2).strip().lstrip("†") if m.group(2) else None
        return DismissalInfo(mode="run_out", run_out_direct=direct, run_out_assist=assist)

    # stumped
    m = _ST_RE.match(t)
    if m:
        return DismissalInfo(mode="stumped", stumper=m.group(1).strip().lstrip("†"))

    # caught
    m = _C_RE.match(t)
    if m:
        fielder = m.group(1).strip().lstrip("†")
        # "c & b Bowler" → fielder == bowler (credit to bowler, not as catcher)
        return DismissalInfo(mode="caught", catcher=fielder)

    if _LBW_RE.match(t):
        return DismissalInfo(mode="lbw")

    if _BOWLED_RE.match(t):
        return DismissalInfo(mode="bowled")

    return DismissalInfo(mode="other")


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY — NAME MAPPER
# ═══════════════════════════════════════════════════════════════════════════════

class NameMapper:
    """
    Resolves ESPNcricinfo display names → local player_id.

    Strategy (in priority order):
    1. Exact slug match   ('virat-kohli' → 'r01')
    2. Partial token match (all tokens of DB name present in ESPN name)
    3. Logged as mismatch for manual review.
    """

    def __init__(self, con: sqlite3.Connection):
        rows = con.execute("SELECT id, name FROM players").fetchall()
        # Build lookup: slug → id,  lower-name → id
        self._slug_map:  dict[str, str] = {}
        self._token_map: dict[str, str] = {}
        self._all: list[tuple[str, str, str]] = []    # (id, name, slug)

        for row in rows:
            pid, pname = row["id"], row["name"]
            slug = name_to_slug(pname)
            self._slug_map[slug] = pid
            self._token_map[pname.lower()] = pid
            self._all.append((pid, pname.lower(), slug))

        self._cache: dict[str, Optional[str]] = {}
        self._mismatches: list[str] = []

    def resolve(self, espn_name: str) -> Optional[str]:
        espn_name = espn_name.strip().lstrip("†")
        if espn_name in self._cache:
            return self._cache[espn_name]

        slug = name_to_slug(espn_name)

        # 1. Exact slug
        pid = self._slug_map.get(slug)
        if pid:
            self._cache[espn_name] = pid
            return pid

        # 2. Partial token: every token in db_name must appear in espn slug
        espn_tokens = set(slug.split("-"))
        best: Optional[str] = None
        best_score = 0
        for db_id, db_name_lower, db_slug in self._all:
            db_tokens = set(db_slug.split("-"))
            overlap = len(db_tokens & espn_tokens)
            # Require at least 50 % of DB tokens to match, and at least 2
            if overlap >= max(2, len(db_tokens) * 0.5) and overlap > best_score:
                best = db_id
                best_score = overlap

        if best:
            self._cache[espn_name] = best
            return best

        # 3. Mismatch — log for manual review
        msg = f"NAME_MISMATCH | espn='{espn_name}' slug='{slug}'"
        if msg not in self._mismatches:
            self._mismatches.append(msg)
            log.warning(msg)
        self._cache[espn_name] = None
        return None

    def dump_mismatches(self) -> list[str]:
        return list(self._mismatches)


# ═══════════════════════════════════════════════════════════════════════════════
# POINTS CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_points(sc: dict) -> int:
    """
    Mirror of the JS scoring engine in server.py / tests_bva.py:
      Batting  : 1pt/run, 1pt/4, 2pt/6, −5pt/duck (played, balls>0, runs=0, got_out)
      SR bonus : ≥200→+10, ≥175→+8, ≥150→+6, ≥125→+4, ≥100→+2  (min 10 balls)
               : <50→−6,  <60→−4,  <70→−2
      Bowling  : 25pt/wkt, 8pt/lbw_bowled (stacked), 4pt/maiden
               : haul bonuses: ≥2wkt→+4, ≥3→+4+4, ≥4→+8, ≥5→+8
      Eco      : ≥2 overs: <5→+6, <6→+4, <7→+2, ≥11→−2, ≥12→−4, ≥13→−6 (approx bands)
      Fielding : 8pt/catch, +4 if ≥3 catches, 12pt/stumping, 12pt/run_out_direct, 6pt/run_out_assist
    """
    if not sc.get("played"):
        return 0

    runs   = int(sc.get("runs",  0))
    balls  = int(sc.get("balls", 0))
    fours  = int(sc.get("fours", 0))
    sixes  = int(sc.get("sixes", 0))
    duck   = bool(sc.get("duck", False))
    got_out = bool(sc.get("gotOut", sc.get("got_out", False)))

    overs  = float(sc.get("overs", 0))
    rc     = int(sc.get("runsConceded", sc.get("runs_conceded", 0)))
    wkts   = int(sc.get("wickets", 0))
    lbwb   = int(sc.get("lbwBowled", sc.get("lbw_bowled", 0)))
    maid   = int(sc.get("maidens", 0))

    catches = int(sc.get("catches", 0))
    stump   = int(sc.get("stumpings", 0))
    rod     = int(sc.get("runOutDirect", sc.get("run_out_direct", 0)))
    roa     = int(sc.get("runOutAssist", sc.get("run_out_assist", 0)))

    pts = 0

    # ── Batting ───────────────────────────────────────────────────────────────
    pts += runs + fours + sixes * 2
    if duck and got_out and balls > 0:
        pts -= 5

    if balls >= 10:
        sr = (runs / balls) * 100
        if   sr >= 200: pts += 10
        elif sr >= 175: pts += 8
        elif sr >= 150: pts += 6
        elif sr >= 125: pts += 4
        elif sr >= 100: pts += 2
        elif sr < 50:   pts -= 6
        elif sr < 60:   pts -= 4
        elif sr < 70:   pts -= 2

    # ── Bowling ───────────────────────────────────────────────────────────────
    pts += wkts * 25 + lbwb * 8 + maid * 4
    if wkts >= 5: pts += 8 + 8 + 4 + 4
    elif wkts >= 4: pts += 8 + 4 + 4
    elif wkts >= 3: pts += 4 + 4
    elif wkts >= 2: pts += 4

    if overs >= 2:
        eco = rc / overs
        if   eco < 5:   pts += 6
        elif eco < 6:   pts += 4
        elif eco < 7:   pts += 2
        elif eco >= 13: pts -= 6
        elif eco >= 12: pts -= 4
        elif eco >= 11: pts -= 2

    # ── Fielding ─────────────────────────────────────────────────────────────
    pts += catches * 8
    if catches >= 3: pts += 4
    pts += stump * 12 + rod * 12 + roa * 6

    return round(pts)


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE HELPERS (sync SQLite — WAL, same pattern as server.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _get_pending_matches(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("""
        SELECT id, title, teams_json, week_no, scorecard_url, status
        FROM   matches
        WHERE  status IN ('live','completed')
          AND  scorecard_url IS NOT NULL
          AND  scorecard_url != ''
        ORDER  BY week_no, id
    """).fetchall()


def _upsert_scores(
    con: sqlite3.Connection,
    match_id: str,
    week_no: int,
    player_stats: dict[str, dict],
) -> None:
    """
    Single transaction: write match_scores + player_match_points.
    Mirrors _upsert_match() in server.py.
    """
    for pid, sc in player_stats.items():
        base_pts = calculate_points(sc)
        con.execute("""
            INSERT INTO match_scores (
                match_id, player_id,
                runs, balls, fours, sixes, got_out, duck,
                overs, runs_conceded, wickets, maidens, lbw_bowled,
                catches, stumpings, run_out_direct, run_out_assist,
                played, raw_score_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(match_id, player_id) DO UPDATE SET
                runs            = excluded.runs,
                balls           = excluded.balls,
                fours           = excluded.fours,
                sixes           = excluded.sixes,
                got_out         = excluded.got_out,
                duck            = excluded.duck,
                overs           = excluded.overs,
                runs_conceded   = excluded.runs_conceded,
                wickets         = excluded.wickets,
                maidens         = excluded.maidens,
                lbw_bowled      = excluded.lbw_bowled,
                catches         = excluded.catches,
                stumpings       = excluded.stumpings,
                run_out_direct  = excluded.run_out_direct,
                run_out_assist  = excluded.run_out_assist,
                played          = excluded.played,
                raw_score_json  = excluded.raw_score_json
        """, (
            match_id, pid,
            max(0, int(sc.get("runs", 0))),
            max(0, int(sc.get("balls", 0))),
            max(0, int(sc.get("fours", 0))),
            max(0, int(sc.get("sixes", 0))),
            1 if sc.get("gotOut") else 0,
            1 if sc.get("duck") else 0,
            max(0.0, float(sc.get("overs", 0))),
            max(0, int(sc.get("runsConceded", 0))),
            min(10, max(0, int(sc.get("wickets", 0)))),
            max(0, int(sc.get("maidens", 0))),
            max(0, int(sc.get("lbwBowled", 0))),
            min(10, max(0, int(sc.get("catches", 0)))),
            max(0, int(sc.get("stumpings", 0))),
            max(0, int(sc.get("runOutDirect", 0))),
            max(0, int(sc.get("runOutAssist", 0))),
            1 if sc.get("played") else 0,
            json.dumps(sc),
        ))

        con.execute("""
            INSERT INTO player_match_points
                (match_id, player_id, week_no, base_pts, multiplier, final_pts, calculated_at)
            VALUES (?,?,?,?,1.0,?,?)
            ON CONFLICT(match_id, player_id) DO UPDATE SET
                base_pts      = excluded.base_pts,
                final_pts     = excluded.final_pts,
                calculated_at = excluded.calculated_at
        """, (
            match_id, pid, week_no, base_pts, float(base_pts),
            datetime.now(timezone.utc).isoformat(),
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_schedule_page(page: Page) -> list[dict]:
    """
    Navigate to the series fixture page and extract:
      - match_no (from heading, e.g. "Match 1")
      - team_slugs (two team names slug-ified)
      - full scorecard URL
    Returns list of dicts.
    """
    log.info("Navigating to series schedule: %s", SERIES_URL)
    await page.goto(SERIES_URL, wait_until="domcontentloaded", timeout=60_000)

    # Wait for at least one match card to load
    try:
        await page.wait_for_selector('a[href*="/full-scorecard"]', timeout=30_000)
    except PWTimeout:
        log.warning("No scorecard links found on schedule page — may be pre-season")

    # Collect all anchor tags pointing to scorecards or match pages
    entries: list[dict] = []

    links = await page.query_selector_all('a[href*="-scorecard"]')
    for link in links:
        href = await link.get_attribute("href") or ""
        if not href:
            continue

        # ESPNcricinfo URL pattern:
        # /series/ipl-2026-1510719/csk-vs-mi-1st-match-1234567/full-scorecard
        # Extract match_id (numeric) and slug
        m_id = re.search(r"/(\d{6,9})(?:/|$)", href)
        if not m_id:
            continue
        espn_match_id = m_id.group(1)

        # Derive match_no from slug: "1st-match", "2nd-match", etc.
        m_no = re.search(r"(\d+)(?:st|nd|rd|th)-match", href, re.I)
        match_no = int(m_no.group(1)) if m_no else None

        # Team slugs: first two hyphen-separated words before the ordinal
        slug_part = href.split("/")[-2] if "/full-scorecard" in href else href.split("/")[-1]
        team_match = re.match(r"([a-z\-]+?)-vs-([a-z\-]+?)-", slug_part)
        team_a_slug = team_match.group(1) if team_match else ""
        team_b_slug = team_match.group(2) if team_match else ""

        scorecard_url = (
            SCORECARD_BASE + href
            if href.startswith("/")
            else href
        )

        entries.append({
            "espn_match_id": espn_match_id,
            "match_no":      match_no,
            "team_a_slug":   team_a_slug,
            "team_b_slug":   team_b_slug,
            "slug_part":     slug_part,
            "scorecard_url": scorecard_url,
        })
        log.debug("  Found: match_no=%s  espn_id=%s  url=%s",
                  match_no, espn_match_id, scorecard_url)

    log.info("Discovery: found %d scorecard links", len(entries))
    return entries


def _match_local_rows(
    espn_entries: list[dict],
    con: sqlite3.Connection,
) -> list[tuple[str, str]]:
    """
    Map ESPN entries → local match IDs.
    Returns list of (local_match_id, scorecard_url) pairs.

    Matching strategy (first match wins):
    1. match_no exact match via 'Match N' substring in title
    2. Both team slugs appear in teams_json (team name normalisation)
    """
    local_rows = con.execute(
        "SELECT id, title, teams_json, week_no FROM matches"
    ).fetchall()

    updates: list[tuple[str, str]] = []
    used_local: set[str] = set()

    def _slug_set(teams_json: str) -> set[str]:
        teams = json.loads(teams_json or "[]")
        return {name_to_slug(t) for t in teams}

    for entry in espn_entries:
        url   = entry["scorecard_url"]
        mno   = entry["match_no"]
        ta    = entry["team_a_slug"]
        tb    = entry["team_b_slug"]

        matched_id: Optional[str] = None

        # Strategy 1: match number in title
        if mno is not None:
            for row in local_rows:
                if row["id"] in used_local:
                    continue
                title_lower = row["title"].lower()
                if f"match {mno}" in title_lower or f"match{mno}" in title_lower:
                    matched_id = row["id"]
                    break

        # Strategy 2: team slug overlap
        if not matched_id:
            for row in local_rows:
                if row["id"] in used_local:
                    continue
                local_slugs = _slug_set(row["teams_json"])
                # require both ESPN team slugs to share a token with a local team slug
                a_hit = any(ta and ta in ls or ls in ta for ls in local_slugs)
                b_hit = any(tb and tb in ls or ls in tb for ls in local_slugs)
                if a_hit and b_hit:
                    matched_id = row["id"]
                    break

        if matched_id:
            used_local.add(matched_id)
            updates.append((url, matched_id))
            log.info("  Mapped espn_id=%s → local_id=%s  url=%s",
                     entry["espn_match_id"], matched_id, url)
        else:
            log.warning("  NO MATCH for espn_id=%s  match_no=%s  teams=%s-vs-%s",
                        entry["espn_match_id"], mno, ta, tb)

    return updates


async def discover_match_urls() -> None:
    """
    Phase 1 entry point.

    Scrapes the IPL 2026 series schedule, maps ESPN match entries to local
    DB rows, and persists scorecard_url via a single UPDATE batch.
    Run once (or re-run — idempotent).
    """
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        page: Page = await ctx.new_page()

        try:
            espn_entries = await _fetch_schedule_page(page)
        finally:
            await browser.close()

    if not espn_entries:
        log.error("Discovery returned no entries — aborting")
        return

    con = _get_db()
    try:
        updates = _match_local_rows(espn_entries, con)
        if not updates:
            log.warning("Discovery: no local rows matched — check DB population")
            return

        with con:
            con.executemany(
                "UPDATE matches SET scorecard_url = ? WHERE id = ?",
                updates,
            )
        log.info(
            "Discovery complete: updated %d/%d matches with scorecard URLs",
            len(updates), len(espn_entries),
        )
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — SCORECARD EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

async def _scrape_scorecard(page: Page, url: str) -> Optional[dict]:
    """
    Load a full-scorecard page and extract structured data.
    Returns a dict of { player_name: raw_stats_dict } or None on error.
    """
    log.info("Scraping scorecard: %s", url)
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PWTimeout:
        log.error("Timeout loading %s", url)
        _cb.record_failure(url)
        return None

    if resp is None or resp.status in (404, 403):
        log.warning("HTTP %s for %s", resp.status if resp else "None", url)
        _cb.record_failure(url)
        return None

    if resp.status >= 400:
        log.warning("HTTP %s for %s", resp.status, url)
        _cb.record_failure(url)
        return None

    _cb.record_success(url)

    # Wait for at least one innings table
    try:
        await page.wait_for_selector(
            'table[class*="Collapsible"], div[class*="scorecard"]',
            timeout=20_000,
        )
    except PWTimeout:
        log.warning("Scorecard tables not found on %s — page may be pre-toss", url)
        return {}

    # ── Extract via JavaScript evaluation ────────────────────────────────────
    # ESPNcricinfo renders tables server-side; we parse the DOM directly.
    raw = await page.evaluate("""
    () => {
        const result = { innings: [] };

        // Each innings is a section with batting + bowling tables
        const sections = document.querySelectorAll(
            '[class*="Innings"], [data-testid*="innings"], section'
        );

        sections.forEach(sec => {
            const inningData = { batting: [], bowling: [] };

            // ── Batting rows ──────────────────────────────────────────────
            const battingTable = sec.querySelector('table[class*="Batting"], table:first-of-type');
            if (battingTable) {
                const rows = battingTable.querySelectorAll('tbody tr');
                rows.forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                    if (cells.length >= 8) {
                        // [name, dismissal, runs, balls, 4s, 6s, sr]
                        inningData.batting.push({
                            name:      cells[0],
                            dismissal: cells[1],
                            runs:      cells[2],
                            balls:     cells[3],
                            fours:     cells[4],
                            sixes:     cells[5],
                            sr:        cells[6],
                            isSub:     cells[0].includes('(sub)'),
                        });
                    }
                });
            }

            // ── Bowling rows ──────────────────────────────────────────────
            const tables = sec.querySelectorAll('table');
            const bowlingTable = tables[tables.length - 1];  // typically last table
            if (bowlingTable && bowlingTable !== battingTable) {
                const rows = bowlingTable.querySelectorAll('tbody tr');
                rows.forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                    if (cells.length >= 6) {
                        // [name, overs, maidens, runs, wickets, economy]
                        inningData.bowling.push({
                            name:    cells[0],
                            overs:   cells[1],
                            maidens: cells[2],
                            runs:    cells[3],
                            wickets: cells[4],
                            economy: cells[5],
                        });
                    }
                });
            }

            if (inningData.batting.length > 0 || inningData.bowling.length > 0) {
                result.innings.push(inningData);
            }
        });

        return result;
    }
    """)

    return raw


def _process_raw_scorecard(
    raw: dict,
    mapper: NameMapper,
    match_id: str,
) -> dict[str, dict]:
    """
    Convert the raw JS-extracted dict into a player_id → stats dict
    suitable for _upsert_scores().

    Fielding credits are accumulated by cross-referencing dismissal strings
    across all innings batting tables.
    """
    player_stats: dict[str, dict] = {}

    def _get_or_create(pid: str) -> dict:
        if pid not in player_stats:
            player_stats[pid] = {
                "played": True,
                "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                "gotOut": False, "duck": False,
                "overs": 0.0, "runsConceded": 0, "wickets": 0,
                "maidens": 0, "lbwBowled": 0,
                "catches": 0, "stumpings": 0,
                "runOutDirect": 0, "runOutAssist": 0,
            }
        return player_stats[pid]

    for innings in raw.get("innings", []):

        # ── Batting ──────────────────────────────────────────────────────────
        for row in innings.get("batting", []):
            raw_name = row.get("name", "").strip()
            raw_name = _SUB_RE.sub("", raw_name).strip()
            if not raw_name or raw_name.lower() in ("extras", "total", "fall of wickets", "did not bat"):
                continue

            pid = mapper.resolve(raw_name)
            if not pid:
                continue

            sc = _get_or_create(pid)
            dismissal_text = row.get("dismissal", "not out")

            runs_raw = row.get("runs", "0")
            balls_raw = row.get("balls", "0")

            if _DNB_RE.search(dismissal_text):
                sc["played"] = False
                continue

            runs  = parse_int(runs_raw)
            balls = parse_int(balls_raw)

            dis = parse_dismissal(dismissal_text)
            got_out = dis.mode not in ("not_out",)

            sc["runs"]   += runs
            sc["balls"]  += balls
            sc["fours"]  += parse_int(row.get("fours", "0"))
            sc["sixes"]  += parse_int(row.get("sixes", "0"))
            sc["gotOut"]  = got_out

            if got_out and runs == 0 and balls > 0:
                sc["duck"] = True

            # Fielding credits from dismissal
            if dis.mode == "caught" and dis.catcher:
                fielder_pid = mapper.resolve(dis.catcher)
                if fielder_pid:
                    _get_or_create(fielder_pid)["catches"] += 1

            elif dis.mode == "stumped" and dis.stumper:
                keeper_pid = mapper.resolve(dis.stumper)
                if keeper_pid:
                    _get_or_create(keeper_pid)["stumpings"] += 1

            elif dis.mode == "run_out":
                if dis.run_out_direct:
                    d_pid = mapper.resolve(dis.run_out_direct)
                    if d_pid:
                        _get_or_create(d_pid)["runOutDirect"] += 1
                if dis.run_out_assist:
                    a_pid = mapper.resolve(dis.run_out_assist)
                    if a_pid:
                        _get_or_create(a_pid)["runOutAssist"] += 1

            elif dis.mode == "lbw":
                # LBW counted as lbwBowled on the bowler — extracted from bowling rows
                pass

        # ── Bowling ──────────────────────────────────────────────────────────
        for row in innings.get("bowling", []):
            raw_name = row.get("name", "").strip()
            if not raw_name:
                continue

            pid = mapper.resolve(raw_name)
            if not pid:
                continue

            sc = _get_or_create(pid)
            overs_raw = row.get("overs", "0")
            overs     = parse_overs(overs_raw)
            wkts      = parse_int(row.get("wickets", "0"))
            rc        = parse_int(row.get("runs", "0"))
            maidens   = parse_int(row.get("maidens", "0"))

            sc["overs"]        = round(sc["overs"] + overs, 4)
            sc["wickets"]      += wkts
            sc["runsConceded"] += rc
            sc["maidens"]      += maidens

    # ── LBW / Bowled extraction from batting dismissals (re-pass) ────────────
    # We already handled caught/stumped/run_out above.
    # For lbw_bowled on bowling rows, cross-reference bowling with batting dismissals
    # (ESPNcricinfo doesn't show lbw/bowled counts in bowling table directly).
    for innings in raw.get("innings", []):
        for row in innings.get("batting", []):
            dis = parse_dismissal(row.get("dismissal", ""))
            if dis.mode in ("lbw", "bowled"):
                bowler_name = row.get("dismissal", "")
                bowler_m = re.search(r"\bb\s+([A-Za-z '\-]+)$", bowler_name.strip())
                if bowler_m:
                    b_pid = mapper.resolve(bowler_m.group(1).strip())
                    if b_pid:
                        _get_or_create(b_pid)["lbwBowled"] += 1

    log.info(
        "  Processed %d player score entries for match=%s",
        len(player_stats), match_id,
    )
    return player_stats


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — MAIN SCRAPER LOOP
# ═══════════════════════════════════════════════════════════════════════════════

async def scraper_loop(run_once: bool = False) -> None:
    """
    Main Phase 2 loop.
    - Polls DB for Live/Completed matches with a scorecard_url every SCRAPE_INTERVAL seconds.
    - Skips URLs whose circuit-breaker is open.
    - Writes all stats + points atomically per match.

    Args:
        run_once: If True, scrapes all eligible matches once then exits (useful for cron).
    """
    log.info("Scraper loop starting (interval=%ds, run_once=%s)", SCRAPE_INTERVAL, run_once)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )

        try:
            while True:
                cycle_start = time.monotonic()

                con = _get_db()
                mapper = NameMapper(con)
                matches = _get_pending_matches(con)
                log.info("Found %d eligible matches to scrape", len(matches))

                page: Page = await ctx.new_page()
                try:
                    for match in matches:
                        mid     = match["id"]
                        url     = match["scorecard_url"]
                        week_no = match["week_no"]

                        if _cb.is_open(url):
                            log.info("Skipping %s — circuit breaker open", mid)
                            continue

                        try:
                            raw = await _scrape_scorecard(page, url)
                        except Exception as exc:
                            log.exception("Unexpected error scraping %s: %s", url, exc)
                            _cb.record_failure(url)
                            continue

                        if raw is None:
                            continue

                        if not raw.get("innings"):
                            log.info("  %s: scorecard empty (pre-toss or no play)", mid)
                            continue

                        player_stats = _process_raw_scorecard(raw, mapper, mid)

                        if not player_stats:
                            log.warning("  %s: no player stats extracted", mid)
                            continue

                        try:
                            with con:
                                _upsert_scores(con, mid, week_no, player_stats)
                            log.info(
                                "  %s: committed %d player stats",
                                mid, len(player_stats),
                            )
                        except sqlite3.Error as exc:
                            log.error("  %s: DB error — %s", mid, exc)

                        # Polite inter-match delay
                        await asyncio.sleep(2)

                finally:
                    await page.close()
                    con.close()

                # Log any name mismatches from this cycle
                mismatches = mapper.dump_mismatches()
                if mismatches:
                    log.warning(
                        "NAME MISMATCHES THIS CYCLE (%d) — manual mapping needed:\n  %s",
                        len(mismatches), "\n  ".join(mismatches),
                    )

                if run_once:
                    log.info("run_once=True — exiting loop")
                    break

                elapsed = time.monotonic() - cycle_start
                sleep_for = max(0, SCRAPE_INTERVAL - elapsed)
                log.info("Cycle done in %.1fs. Next cycle in %.0fs.", elapsed, sleep_for)
                await asyncio.sleep(sleep_for)

        finally:
            await browser.close()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def _main():
    import argparse
    parser = argparse.ArgumentParser(description="IPL 2026 ESPNcricinfo Scraper")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("discover",
        help="Phase 1: discover scorecard URLs and write to DB")

    loop_p = sub.add_parser("scrape",
        help="Phase 2: scrape scorecards from DB into match_scores / player_match_points")
    loop_p.add_argument("--once", action="store_true",
        help="Run one scrape cycle then exit (for cron)")

    args = parser.parse_args()

    if args.cmd == "discover":
        await discover_match_urls()
    elif args.cmd == "scrape":
        await scraper_loop(run_once=args.once)
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(_main())
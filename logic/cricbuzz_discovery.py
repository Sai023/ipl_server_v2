"""
IPL Fantasy 2026 — Cricbuzz Match-ID Discovery       cricbuzz_discovery v1.2.0
===========================================================================
v1.2.0 — second smoke-test pass on 2026-05-14:
  ROOT CAUSE FOUND: series_id was wrong all along. Cricbuzz's actual IPL 2026
  series ID is 9241, not the hardcoded 9237 (likely a 2025 leftover).
  Resolver caught it from the cricbuzz.com homepage on the 4th URL tried.
  schedule.json corrected.  Other improvements:
    • _SERIES_LOOKUP_URLS reordered — homepage first (observed winner).
    • resolve_series_id + _fetch_html: 404 = permanent, no retry/backoff
      (saves ~18s per dead URL on smoke runs).
    • fetch_series_matches now scrapes /results and /points-table too,
      not just /matches and overview — catches completed matches that
      rotate off the current-window view.
    • _strategy_regex added /live-cricket-scorecard/ as a third URL
      pattern (Cricbuzz uses this for direct scorecard links in cards
      the matches-list page doesn't surface).

v1.1.0 — first smoke-test fixes:
  • resolve_series_id: /cricket-series/league 404s on Cricbuzz now —
    rewritten to try several listing URLs (homepage, schedule, etc.).
    Caller already tolerates None, so this only reduces log noise +
    increases hit rate.
  • merge_discoveries: critical dedup hazard fixed. The bucket-by-team-pair
    merge could reassign a known cricbuzz_id (e.g. M1 SRH/RCB) to a
    different unfilled slot with the same team-pair (M67 SRH/RCB).
    Now filters out IDs already present elsewhere in the schedule before
    bucketing. New stats field: dedup_skipped.
  • _strategy_api: dropped the /api/html/... URL (always returned HTML,
    never JSON — was always silently discarded). Added diagnostic prints
    showing HTTP status and response prefix so we can see why JSON APIs
    fail when they do.
  • fetch_series_matches: now hits both /matches and the series overview
    page, runs BOTH nextjs and regex extractors on each, merges all
    results deduplicated by cb_match_id. Roughly doubles coverage when
    Cricbuzz lazy-loads the matches list. New helper _fetch_html()
    centralises the retry/backoff/Cloudflare-detect logic.

v1.0.0 — Initial. Single shared discovery module. Used by:
  • Seed_Matches.py (bootstrap / manual re-resolve)
  • tasks.start_daily_discovery_scheduler() (in-server cron)
  • routes.api_sync_now (manual Admin trigger)

What it does
------------
1. resolve_series_id(year)       — dynamically resolves the IPL {year} Cricbuzz
                                   series ID (no more hardcoded "9237").
2. fetch_series_matches(...)     — 3-strategy discovery (API → Next.js JSON →
                                   HTML regex) with retry + backoff and
                                   Cloudflare-challenge detection.
3. merge_discoveries(...)        — TITLE-KEYED merge: matches discovered IDs
                                   to scheduled matches by frozenset(teams),
                                   walking both lists in order so duplicate
                                   team-pairs align chronologically.
4. run_discovery(...)            — Orchestrator: load → resolve → fetch →
                                   merge → atomic write to schedule.json.

Imports: stdlib (hashlib, json, os, random, re, time, datetime, pathlib,
tempfile) + requests only.  ZERO project imports — keeps logic/ pure.
"""

import json
import os
import random
import re
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:                                       # pragma: no cover
    raise ImportError("cricbuzz_discovery requires 'requests'. pip install requests")


CRICBUZZ_DISCOVERY_VER = "1.2.0"

IST = timezone(timedelta(hours=5, minutes=30))

IPL_TEAMS = frozenset({
    "CSK", "DC", "GT", "KKR", "LSG", "MI", "PBKS", "RCB", "RR", "SRH",
})

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
]

_RETRY_ATTEMPTS = 3
_RETRY_BASE_SEC = 2.0
_REQUEST_TIMEOUT = 25


def _hdrs() -> dict:
    return {
        "User-Agent":      random.choice(_USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://www.cricbuzz.com/",
        "DNT":             "1",
        "Cache-Control":   "no-cache",
    }


def _now_ist_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _is_cloudflare(html: str) -> bool:
    return "cf-browser-verification" in html or "Just a moment" in html


# ─────────────────────────────────────────────────────────────────────────────
# JSON I/O — atomic, no partial writes
# ─────────────────────────────────────────────────────────────────────────────

def load_schedule(path: Path) -> dict:
    """Read schedule.json. Raises FileNotFoundError if missing."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(path: Path, data: dict) -> None:
    """
    Atomic write: serialise to a temp file in the same directory, then
    os.replace() into place.  Guarantees no half-written JSON on crash.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".schedule.", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# SERIES-ID RESOLUTION — replaces hardcoded "9237"
# ─────────────────────────────────────────────────────────────────────────────
#
# Cricbuzz's URL layout has churned over the years — /cricket-series/league
# returns 404 as of 2026-05. We try several candidate listing pages in
# decreasing order of specificity and accept whichever returns a hit.
# All callers tolerate None (falls back to schedule.json's cached value).
# ─────────────────────────────────────────────────────────────────────────────

# Homepage first — observed (2026-05-14) to be the most reliable hit.
# 404s are skipped without retry; backoff only applies to transient errors.
_SERIES_LOOKUP_URLS = (
    "https://www.cricbuzz.com/",                                # current winner
    "https://www.cricbuzz.com/cricket-schedule/series",
    "https://www.cricbuzz.com/cricket-schedule/series-archives",
    "https://www.cricbuzz.com/cricket-schedule/upcoming-series",
    "https://www.cricbuzz.com/cricket-series/league",           # legacy
)


def resolve_series_id(year: int, debug: bool = False) -> str | None:
    """
    Find the Cricbuzz series ID for IPL {year} by trying multiple
    listing pages.  Returns the numeric ID as a string, or None if
    every candidate URL fails — caller falls back to schedule.json's
    cached series_id in that case.

    Pattern: /cricket-series/{ID}/indian-premier-league-{year}/...

    Retry policy
    ------------
    Only transient failures (network errors, 5xx) get retried.
    404 means "page genuinely doesn't exist" — we skip to the next URL
    immediately rather than waste exponential backoff on a dead URL.
    """
    slug = f"indian-premier-league-{year}"
    pat  = re.compile(rf"/cricket-series/(\d+)/{re.escape(slug)}(?:/|\")")

    for url in _SERIES_LOOKUP_URLS:
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                if debug:
                    print(f"  [series-id] GET {url} (attempt {attempt})")
                r = requests.get(url, headers=_hdrs(),
                                 timeout=_REQUEST_TIMEOUT)
                if r.status_code == 404:
                    # Permanent — don't waste retries
                    if debug: print(f"  [series-id]   HTTP 404 — skipping URL")
                    break
                if r.status_code != 200:
                    if debug: print(f"  [series-id]   HTTP {r.status_code}")
                elif _is_cloudflare(r.text):
                    if debug: print("  [series-id]   Cloudflare challenge")
                else:
                    m = pat.search(r.text)
                    if m:
                        sid = m.group(1)
                        if debug:
                            print(f"  [series-id] ✅ resolved IPL {year} → {sid} "
                                  f"(from {url})")
                        return sid
                    if debug:
                        print(f"  [series-id]   slug '{slug}' not on this page")
                    break  # 200 but no match — move to next URL, no retry
            except requests.RequestException as e:
                if debug: print(f"  [series-id]   network error: {e}")
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BASE_SEC ** attempt)

    if debug:
        print(f"  [series-id] ⚠ all {len(_SERIES_LOOKUP_URLS)} lookup URLs "
              f"failed — caller will use cached series_id from schedule.json")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVERY STRATEGIES (3 fallback layers)
# ─────────────────────────────────────────────────────────────────────────────

def _strategy_api(series_id: str, debug: bool = False) -> list[dict]:
    """
    Try Cricbuzz JSON APIs. Returns [] if no endpoint is currently
    reachable — common since Cricbuzz changes API shapes frequently.
    Logs status and response prefix when --debug so we can diagnose
    which endpoint is the failing one.

    Note: the legacy `/api/html/cricket-series/...` path was removed —
    it served HTML, never JSON, and was always discarded by the
    `text[:1] not in ('{','[')` guard.
    """
    out, seen = [], set()
    candidates = (
        f"https://www.cricbuzz.com/api/cricket-series/{series_id}/matches",
    )
    for url in candidates:
        try:
            if debug: print(f"  [api] GET {url}")
            r = requests.get(url, headers=_hdrs(), timeout=15)
            prefix = (r.text or "")[:80].replace("\n", " ")
            if r.status_code != 200:
                if debug:
                    print(f"  [api]   HTTP {r.status_code} — skipping")
                continue
            if r.text.strip()[:1] not in ("{", "["):
                if debug:
                    print(f"  [api]   non-JSON response (prefix: {prefix!r}) "
                          f"— skipping")
                continue
            data = r.json()
            items = data if isinstance(data, list) else \
                    data.get("matches") or data.get("matchDetails") or []
            if not isinstance(items, list):
                if debug:
                    print(f"  [api]   unexpected JSON shape "
                          f"(top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__})")
                continue
            for item in items:
                for k in ("id", "matchId", "match_id"):
                    cid = str(item.get(k, "") or "")
                    if cid and len(cid) >= 4 and cid not in seen:
                        seen.add(cid)
                        title = (item.get("matchDescription")
                                 or item.get("title")
                                 or f"Match {len(out)+1}")
                        out.append({"cb_match_id": cid, "title": title,
                                    "source": "api"})
                        break
            if out:
                return out
        except (requests.RequestException, ValueError) as e:
            if debug: print(f"  [api]   error: {e}")
    return out


def _strategy_nextjs(html: str, debug: bool = False) -> list[dict]:
    out, seen = [], set()
    for block in re.findall(r"self\.__next_f\.push\(\[.*?\]\)", html, re.S):
        for m in re.finditer(
            r'/(?:live-)?cricket-scores/(\d{5,})/([^"\'<>\s\\]+)', block
        ):
            cid = m.group(1)
            if cid in seen: continue
            seen.add(cid)
            out.append({
                "cb_match_id": cid,
                "title": m.group(2).replace("-", " ").title(),
                "source": "nextjs",
            })
    if debug: print(f"  [nextjs] {len(out)} IDs")
    return out


def _strategy_regex(html: str, debug: bool = False) -> list[dict]:
    """
    Three URL families to scan:
      • /live-cricket-scorecard/{id}/...   ← direct scorecard links (slug optional)
      • /live-cricket-scores/{id}/{slug}   ← live-score widgets
      • /cricket-scores/{id}/{slug}        ← legacy completed-match URLs
    Cricbuzz uses different families on different page templates; scanning
    all three widens coverage without false positives (the {5,} digit
    constraint plus slug shape filter is restrictive enough).
    """
    out, seen = [], set()

    # Scorecard URLs — slug is optional
    for m in re.finditer(
        r'/live-cricket-scorecard/(\d{5,})(?:/([^"\'<>\s\\]+))?', html
    ):
        cid = m.group(1)
        if cid in seen: continue
        seen.add(cid)
        slug = m.group(2) or ""
        title = slug.replace("-", " ").title() or f"CB#{cid}"
        out.append({"cb_match_id": cid, "title": title, "source": "regex"})

    # Live-scores + legacy URLs — slug required (provides team-pair info)
    for pat in (
        r'/live-cricket-scores/(\d{5,})/([^"\'<>\s\\]+)',
        r'/cricket-scores/(\d{5,})/([^"\'<>\s\\]+)',
    ):
        for m in re.finditer(pat, html):
            cid = m.group(1)
            if cid in seen: continue
            seen.add(cid)
            out.append({
                "cb_match_id": cid,
                "title": m.group(2).replace("-", " ").title(),
                "source": "regex",
            })
    if debug: print(f"  [regex] {len(out)} IDs")
    return out


def _fetch_html(url: str, debug: bool = False) -> str:
    """
    Fetch an HTML page with retry/backoff. Returns '' on total failure.

    404 is treated as permanent — no retry, no backoff. This matters
    because fetch_series_matches now tries several candidate page URLs
    (some of which simply don't exist for every series); we don't want
    to burn 18s of backoff per missing URL.
    """
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            if debug: print(f"  [html] GET {url} (attempt {attempt})")
            r = requests.get(url, headers=_hdrs(), timeout=_REQUEST_TIMEOUT)
            if r.status_code == 200:
                if _is_cloudflare(r.text):
                    if debug: print(f"  [html]   Cloudflare challenge on {url}")
                    return ""
                return r.text
            if r.status_code == 404:
                if debug: print(f"  [html]   HTTP 404 — skipping URL")
                return ""
            if debug: print(f"  [html]   HTTP {r.status_code}")
        except requests.RequestException as e:
            if debug: print(f"  [html]   network error: {e}")
        if attempt < _RETRY_ATTEMPTS:
            time.sleep(_RETRY_BASE_SEC ** attempt)
    return ""


def fetch_series_matches(
    series_id: str,
    slug: str = "indian-premier-league-2026",
    debug: bool = False,
) -> list[dict]:
    """
    Returns a deduplicated list of dicts:
        [{"cb_match_id": "149618", "title": "Srh Vs Rcb 1st Match",
          "source": "api|nextjs|regex"}, ...]

    Strategy
    --------
    1. Try the JSON API first (cleanest if it works).
    2. Then fetch BOTH the `/matches` page AND the series overview page,
       and run BOTH nextjs and regex extractors on each.  Merge all
       results, deduplicating by cb_match_id.

    Why union-of-strategies instead of first-wins
    ---------------------------------------------
    Cricbuzz lazy-loads matches: the static HTML of `/matches` only shows
    the current-week window (~10-15 unique IDs).  The series overview page
    sometimes embeds different match cards (top scorers, upcoming highlights)
    that surface IDs the `/matches` view doesn't.  Merging widens coverage
    at the cost of two extra HTTP requests per run.
    """
    seen: set = set()
    out: list[dict] = []

    def _absorb(items: list[dict], label: str) -> int:
        added = 0
        for it in items:
            cid = str(it.get("cb_match_id", "") or "")
            if cid and cid not in seen:
                seen.add(cid)
                out.append(it)
                added += 1
        if debug and added:
            print(f"  [discovery] +{added} new from {label} "
                  f"(running total: {len(out)})")
        return added

    # Step 1: JSON API (often dead, but cheap to try)
    _absorb(_strategy_api(series_id, debug), "api")

    # Step 2: HTML pages — scrape every viable Cricbuzz view of the series.
    # Each page exposes different sets of match cards:
    #   /matches     — current-week schedule (always has nextjs + regex hits)
    #   overview     — featured match cards (overlaps but adds some highlights)
    #   /results     — completed matches (catches matches rotated off /matches)
    #   /points-table — standings page; sometimes embeds match links
    # Missing URLs (404) are skipped in _fetch_html without backoff.
    html_targets = (
        ("matches",  f"https://www.cricbuzz.com/cricket-series/{series_id}/{slug}/matches"),
        ("overview", f"https://www.cricbuzz.com/cricket-series/{series_id}/{slug}"),
        ("results",  f"https://www.cricbuzz.com/cricket-series/{series_id}/{slug}/results"),
        ("points",   f"https://www.cricbuzz.com/cricket-series/{series_id}/{slug}/points-table"),
    )
    for label, url in html_targets:
        html = _fetch_html(url, debug)
        if not html:
            continue
        _absorb(_strategy_nextjs(html, debug), f"nextjs:{label}")
        _absorb(_strategy_regex(html,  debug), f"regex:{label}")

    if debug:
        if out:
            print(f"  ✅ [discovery] {len(out)} unique match IDs from "
                  f"all sources")
        else:
            print("  ⚠ [discovery] no match IDs found from any source")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# TITLE-KEYED MERGE
# ─────────────────────────────────────────────────────────────────────────────

def _extract_teams_from_title(title: str) -> list[str]:
    """Extract IPL team codes from a Cricbuzz title, preserving order."""
    if not title: return []
    tokens = re.findall(r"[A-Za-z]+", title.upper())
    found, seen = [], set()
    for t in tokens:
        if t in IPL_TEAMS and t not in seen:
            seen.add(t)
            found.append(t)
            if len(found) == 2:
                break
    return found


def merge_discoveries(
    schedule: dict,
    discovered: list[dict],
    debug: bool = False,
) -> tuple[dict, dict]:
    """
    Merge discovered Cricbuzz IDs into schedule by frozenset(teams) equality.

    Why this beats positional indexing
    -----------------------------------
    Cricbuzz orders the series page by date.  If discovery returns 60 matches
    but the schedule's first 49 are already filled, naive positional indexing
    (discovered[49] → schedule[49]) breaks if any match was skipped, abandoned,
    or re-ordered.  Frozenset-of-teams matching is order-independent.

    Duplicate team-pairs (e.g., GT vs RCB plays twice per season) are handled
    by walking both lists in chronological order — the Nth occurrence of
    GT/RCB in the discovery list maps to the Nth unfilled GT/RCB slot in the
    schedule.

    Returns
    -------
    (new_schedule_dict, stats_dict).  Input is not mutated.
    """
    # Build set of IDs already used in the schedule.  CRITICAL: without this
    # guard, the merge could reassign a known ID to a different unfilled slot
    # that happens to share the same team-pair.  E.g., M1 (SRH vs RCB) has
    # cricbuzz_id 149618.  If discovery returns 149618 again, and M67
    # (also SRH vs RCB) is unfilled, the loop below would pop 149618 and
    # plant it on M67 — corrupting the schedule with a duplicate ID.
    existing_ids: set = {
        str(m.get("cricbuzz_id"))
        for m in schedule.get("matches", [])
        if m.get("cricbuzz_id")
    }

    # Bucket discoveries by team-pair; preserve chronological order within bucket
    by_pair: dict[frozenset, list[str]] = {}
    unmatched_discovered: list[dict] = []
    dedup_skipped = 0
    for d in discovered:
        cid = str(d.get("cb_match_id", "") or "")
        if not cid:
            continue
        if cid in existing_ids:
            dedup_skipped += 1
            continue
        teams = _extract_teams_from_title(d.get("title", ""))
        if len(teams) == 2:
            by_pair.setdefault(frozenset(teams), []).append(cid)
        else:
            unmatched_discovered.append(d)

    if debug and dedup_skipped:
        print(f"  [merge] skipped {dedup_skipped} discovered IDs already "
              f"present elsewhere in schedule.json")

    # Sort each team-pair bucket by integer ID ascending. Within a single
    # IPL series Cricbuzz allocates IDs in chronological batches (lower ID
    # = earlier match), so the Nth pop now corresponds to the Nth unfilled
    # slot in match_no order. Without this sort, the bucket follows
    # Cricbuzz's response order — which is NOT chronological — and the
    # round-1 / round-2 IDs of any repeat team-pair end up swapped.
    for key in by_pair:
        by_pair[key].sort(key=lambda cid: int(cid))

    new_matches: list[dict] = []
    filled = already_had = unfilled_known = unfilled_playoff = 0

    for m in schedule.get("matches", []):
        m = dict(m)  # copy — don't mutate input
        if m.get("cricbuzz_id"):
            already_had += 1
            new_matches.append(m)
            continue

        teams = m.get("teams") or []
        if len(teams) < 2:
            # Playoffs — team pair is TBD until W9 ends
            unfilled_playoff += 1
            new_matches.append(m)
            continue

        key   = frozenset(teams)
        queue = by_pair.get(key)
        if queue:
            new_id = queue.pop(0)
            m["cricbuzz_id"] = new_id
            filled += 1
            if debug:
                print(f"  [merge] M{m['match_no']:02d} "
                      f"{teams[0]}/{teams[1]} ← CB#{new_id}")
        else:
            unfilled_known += 1
            if debug:
                print(f"  [merge] M{m['match_no']:02d} "
                      f"{teams[0]}/{teams[1]} — no discovery candidate")

        new_matches.append(m)

    # Surplus discoveries — IDs Cricbuzz returned but the schedule didn't claim
    surplus = sum(len(v) for v in by_pair.values()) + len(unmatched_discovered)

    out = dict(schedule)
    out["matches"]      = new_matches
    out["last_updated"] = _now_ist_iso()

    stats = {
        "discovered_total":    len(discovered),
        "dedup_skipped":       dedup_skipped,
        "filled":              filled,
        "already_had_id":      already_had,
        "unfilled_known":      unfilled_known,
        "unfilled_playoff":    unfilled_playoff,
        "surplus_discoveries": surplus,
    }
    return out, stats


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run_discovery(
    schedule_path: Path,
    year: int = 2026,
    debug: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Full discovery pipeline.  Safe to call from any process.

    Steps
    -----
    1. Load existing schedule.json (preserves whatever IDs are already there).
    2. Resolve current IPL {year} series ID from Cricbuzz.  If resolution
       fails, fall back to the schedule's stored series_id.
    3. Fetch series matches from Cricbuzz.
    4. Title-keyed merge.
    5. Atomic write back to schedule.json (unless dry_run=True).

    Returns
    -------
    {
        "ok":            bool,
        "series_id":     str | None,
        "discovered":    int,
        "filled":        int,
        "already_had":   int,
        "unfilled_known": int,
        "unfilled_playoff": int,
        "surplus":       int,
        "error":         str | None,   # populated only on failure
        "written":       bool,
    }

    Never raises — all exceptions caught and reported in the stats dict so a
    scheduled job can keep running tomorrow.
    """
    result = {
        "ok": False, "series_id": None, "discovered": 0,
        "filled": 0, "already_had": 0, "unfilled_known": 0,
        "unfilled_playoff": 0, "surplus": 0,
        "error": None, "written": False,
    }

    try:
        schedule = load_schedule(schedule_path)
    except Exception as e:
        result["error"] = f"load_schedule: {e}"
        if debug: print(f"  ❌ {result['error']}")
        return result

    # 2. Resolve series ID (fall back to whatever's in the file)
    sid = resolve_series_id(year, debug=debug)
    if not sid:
        sid = schedule.get("series_id")
        if debug: print(f"  ⚠ using cached series_id from JSON: {sid}")
    if not sid:
        result["error"] = "no series_id available (resolve failed, JSON had none)"
        return result
    result["series_id"] = sid

    # If resolution found a different ID than what's stored, persist it
    if sid != schedule.get("series_id"):
        schedule["series_id"]  = sid
        schedule["series_slug"] = f"indian-premier-league-{year}"

    slug = schedule.get("series_slug") or f"indian-premier-league-{year}"

    # 3. Fetch
    try:
        discovered = fetch_series_matches(sid, slug, debug=debug)
    except Exception as e:
        result["error"] = f"fetch_series_matches: {e}"
        if debug: print(f"  ❌ {result['error']}")
        return result

    result["discovered"] = len(discovered)
    if not discovered:
        result["error"] = "discovery returned 0 matches (Cloudflare? wrong series?)"
        # Still persist the resolved series_id even if no matches found
        if not dry_run and sid != schedule.get("series_id"):
            try:
                schedule["last_updated"] = _now_ist_iso()
                save_schedule(schedule_path, schedule)
                result["written"] = True
            except Exception as e:
                result["error"] = f"{result['error']}; save failed: {e}"
        return result

    # 4. Merge
    new_schedule, stats = merge_discoveries(schedule, discovered, debug=debug)
    result.update({
        "filled":            stats["filled"],
        "already_had":       stats["already_had_id"],
        "unfilled_known":    stats["unfilled_known"],
        "unfilled_playoff":  stats["unfilled_playoff"],
        "surplus":           stats["surplus_discoveries"],
    })

    # 5. Write
    if dry_run:
        if debug: print("  [dry-run] would have written schedule.json")
        result["ok"] = True
        return result

    try:
        save_schedule(schedule_path, new_schedule)
        result["written"] = True
        result["ok"]      = True
    except Exception as e:
        result["error"] = f"save_schedule: {e}"
        return result

    if debug:
        print(f"\n  ✅ run_discovery complete: "
              f"+{result['filled']} new IDs "
              f"(had {result['already_had']}, "
              f"{result['unfilled_known']} still unfilled, "
              f"{result['unfilled_playoff']} playoff TBD, "
              f"{result['surplus']} surplus)")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI — `python -m logic.cricbuzz_discovery <schedule.json>` for ad-hoc runs
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":                                # pragma: no cover
    import argparse
    p = argparse.ArgumentParser(description="IPL 2026 Cricbuzz discovery")
    p.add_argument("schedule_path", nargs="?",
                   default="data/schedule.json", type=Path)
    p.add_argument("--year",    type=int, default=2026)
    p.add_argument("--debug",   action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print(f"\n--- cricbuzz_discovery v{CRICBUZZ_DISCOVERY_VER} ---")
    print(f"  schedule_path: {args.schedule_path}")
    print(f"  year:          {args.year}")
    print(f"  dry_run:       {args.dry_run}\n")

    res = run_discovery(args.schedule_path, year=args.year,
                        debug=args.debug, dry_run=args.dry_run)
    print(f"\n{json.dumps(res, indent=2)}")
    raise SystemExit(0 if res["ok"] else 1)

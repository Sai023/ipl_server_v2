#!/usr/bin/env python3
"""
BCL Fantasy - competition-aware scoring runner (Postgres)          score_competition v1.0.0
===========================================================================================
Scores ANY competition (SA20 2027, a future IPL...) the same way the IPL was scored: it reads each finished
match's Cricbuzz scorecard, turns it into per-player stats with the existing parser (scraper.py), stores the
stats and recalculates points with the existing scoring engine (logic/scoring_engine.calc_pts).

Why a separate runner: Cricbuzz blocks cloud hosts, so scoring runs on YOUR PC and writes straight to the
production database (DATABASE_URL), exactly like the verified import scripts. Nothing here runs on Render.

  py -3.14 score_competition.py sa20_2027                 # discover missing match ids, then score finished matches
  py -3.14 score_competition.py sa20_2027 --dry-run       # read + parse only, write nothing
  py -3.14 score_competition.py sa20_2027 --no-discover   # score only matches that already have a Cricbuzz link
  py -3.14 score_competition.py sa20_2027 --force sa20_2027_m01   # re-score one match
  options: --series-id 12791 --series-slug sa20-2027 (override what is stored on the competition)

Match ids ("Cricbuzz links") come from, in order: the link already stored on the match (Admin -> Match URL Admin,
or an earlier run), then discovery from the Cricbuzz series page. Discovery only accepts a fixture whose title
contains the series slug AND whose two teams are both this competition's teams, so other series are never mixed in.

Safety: a scorecard whose teams do not match the fixture is SKIPPED and reported - the stored link is never wiped.
Idempotent: a finished, already-scored match is skipped; re-running changes nothing.
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

SCORER_VER = "1.0.0"
PLACEHOLDER = "https://www.cricbuzz.com/live-cricket-scorecard/00000"
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


# -- small pure helpers (unit-tested) ------------------------------------------------------------

def cb_id_from_url(url):
    """Cricbuzz numeric match id from a stored scorecard link, or None for the /00000 placeholder / empty."""
    if not url:
        return None
    m = re.search(r"live-cricket-(?:scorecard|scores)/(\d+)", url) or re.search(r"/(\d+)(?:/[^/]*)?/?$", url)
    if not m or int(m.group(1)) == 0:
        return None
    return m.group(1)


def match_day(label, anchor):
    """'Jan 17' -> datetime (UTC midnight) in the season the competition's week-1 date belongs to, else None."""
    try:
        mon, day = (label or "").split()
        month, day = _MONTHS[mon[:3].title()], int(day)
        d = datetime(anchor.year, month, day, tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return None
    if d < anchor - timedelta(days=60):          # a Jan/Feb date in a season whose week 1 is in Nov/Dec
        d = d.replace(year=d.year + 1)
    return d


def title_teams(title, allowed):
    """The two competition team codes named in a discovered title, or [] (needs exactly two distinct ones)."""
    found = []
    for t in re.findall(r"[A-Za-z0-9]+", (title or "").upper()):
        if t in allowed and t not in found:
            found.append(t)
        if len(found) == 2:
            return found
    return []


def pair_discoveries(db_matches, discovered, allowed, series_slug):
    """[(match_id, cricbuzz_id)] to fill. db_matches: dicts {id, teams(list), cb(None|str)} in match order.
    Nth discovered fixture of a team pair (by ascending Cricbuzz id) -> Nth unfilled fixture of that pair."""
    used = {m["cb"] for m in db_matches if m["cb"]}
    needle = (series_slug or "").replace("-", " ").lower()
    buckets = {}
    for d in discovered:
        cid = str(d.get("cb_match_id") or "")
        title = d.get("title") or ""
        if not cid or cid in used or (needle and needle not in title.lower()):
            continue
        teams = title_teams(title, allowed)
        if len(teams) == 2:
            buckets.setdefault(frozenset(teams), set()).add(cid)
    queues = {k: sorted(v, key=int) for k, v in buckets.items()}
    out = []
    for m in db_matches:
        if m["cb"] or len(m["teams"]) != 2:
            continue
        q = queues.get(frozenset(m["teams"]))
        if q:
            out.append((m["id"], q.pop(0)))
    return out


# -- the runner ------------------------------------------------------------------------------------

def _make_db():
    from config import DATABASE_URL
    if not DATABASE_URL:
        sys.exit("DATABASE_URL is not set (the Neon connection string). Set it for this session, e.g. in PowerShell:\n"
                 "  $env:DATABASE_URL = (Get-Content -Raw <path to the file holding the connection string>).Trim()")
    from db_manager import DatabaseManager
    return DatabaseManager(DATABASE_URL)


def discover(db, slug, comp, series_id, series_slug, allowed, dry, log):
    from logic import cricbuzz_discovery as cd
    if not series_id or not series_slug:
        log("  discovery: this competition has no Cricbuzz series id/slug (pass --series-id / --series-slug)")
        return 0
    with db._read() as con:
        rows = con.execute("SELECT id, teams_json, scorecard_url FROM matches WHERE competition_id=%s ORDER BY id", (slug,)).fetchall()
    import json
    dbm = [{"id": r["id"], "teams": json.loads(r["teams_json"] or "[]"), "cb": cb_id_from_url(r["scorecard_url"])} for r in rows]
    if all(m["cb"] or len(m["teams"]) != 2 for m in dbm):
        log("  discovery: nothing to fill")
        return 0
    found = cd.fetch_series_matches(str(series_id), series_slug, debug=False)
    fills = pair_discoveries(dbm, found, allowed, series_slug)
    log(f"  discovery: {len(found)} fixtures on the Cricbuzz series page, {len(fills)} matched to our unfilled matches")
    for mid, cid in fills:
        log(f"    {mid} <- Cricbuzz #{cid}")
        if not dry:
            with db._write() as con:
                con.execute("UPDATE matches SET scorecard_url=%s WHERE id=%s AND competition_id=%s AND scorecard_url LIKE %s",
                            (f"https://www.cricbuzz.com/live-cricket-scorecard/{cid}", mid, slug, "%/00000"))
    return len(fills)


def score(db, slug, *, dry=False, do_discover=True, force=(), series_id=None, series_slug=None, log=print):
    """Score one competition. Returns a summary dict."""
    import json
    import scraper as sc
    from db_manager import _upsert_match
    from logic.fuzzy_match import _index_players
    from logic import competition_import as ci

    comp = db.get_competition(slug)
    if not comp:
        raise SystemExit(f"competition '{slug}' not found")
    allowed = set(comp.get("valid_teams") or [])
    anchor = ci.parse_anchor(comp.get("week1_anchor_utc")) or datetime.now(timezone.utc)
    series_id = series_id or comp.get("series_id")
    series_slug = series_slug or comp.get("series_slug")
    summary = {"scored": 0, "live": 0, "skipped": 0, "failed": 0, "discovered": 0}
    log(f"\n--- score_competition v{SCORER_VER}: {slug} ({comp['status']}){' [DRY RUN]' if dry else ''} ---")

    if do_discover:
        try:
            summary["discovered"] = discover(db, slug, comp, series_id, series_slug, allowed, dry, log)
        except Exception as e:
            log(f"  discovery failed (continuing with the links already stored): {type(e).__name__}: {e}")

    def load():
        with db._read() as con:
            players = [dict(r) for r in con.execute("SELECT id,name,team,role FROM players WHERE competition_id=%s", (slug,)).fetchall()]
            matches = [dict(r) for r in con.execute(
                "SELECT id,week_no,title,teams_json,date_label,status,scorecard_url FROM matches WHERE competition_id=%s ORDER BY id", (slug,)).fetchall()]
            scored = {r["match_id"] for r in con.execute("SELECT DISTINCT match_id FROM match_scores WHERE competition_id=%s", (slug,)).fetchall()}
        return players, matches, scored

    players, matches, scored = load()
    if not players:
        raise SystemExit("this competition has no players - import players.json first")
    pidx = _index_players(players)

    def persist_player(p):                                  # unknown (replacement / late-added) players
        if dry:
            return
        with db._write() as con:
            con.execute("INSERT INTO players (competition_id,id,name,team,price,role) VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (competition_id,id) DO NOTHING", (slug, p["id"], p["name"], p["team"], p["price"], p["role"]))
    pidx["persist_player"] = persist_player

    now = datetime.now(timezone.utc)
    for m in matches:
        iid, forced = m["id"], m["id"] in set(force)
        cb = cb_id_from_url(m["scorecard_url"])
        if not cb:
            continue                                           # no Cricbuzz link yet
        if m["status"] == "completed" and iid in scored and not forced:
            continue                                           # already done
        day = match_day(m["date_label"], anchor)
        if not forced and day and day > now:
            continue                                           # not match day yet: don't poll Cricbuzz
        try:
            time.sleep(1.5)
            data = sc.fetch_scorecard_json(cb)
            if not data:
                log(f"  FAILED  {iid} (Cricbuzz #{cb}): no scorecard data"); summary["failed"] += 1; continue
            meta, no_result = sc._extract_meta(data, iid, m["week_no"])
            expected = set(json.loads(m["teams_json"] or "[]"))
            if not no_result and meta["status"] != "upcoming":
                got = set(meta["teams"])
                if (allowed and not got <= allowed) or (expected and not got & expected):
                    log(f"  SKIPPED {iid}: Cricbuzz #{cb} shows {sorted(got)} but the fixture is {sorted(expected) or '?'} "
                        f"(allowed {sorted(allowed)}) - link NOT changed; check the link / team codes")
                    summary["skipped"] += 1; continue
            if meta["status"] == "upcoming":
                continue
            if meta["status"] == "live":
                log(f"  LIVE    {iid}")
                if not dry and m["status"] == "upcoming":
                    with db._write() as con:
                        con.execute("UPDATE matches SET status='live' WHERE id=%s AND competition_id=%s", (iid, slug))
                summary["live"] += 1; continue
            scores = {} if no_result else sc.process_cricbuzz_scorecard(data, pidx)
            if not no_result and not scores:
                log(f"  FAILED  {iid}: scorecard parsed to no players"); summary["failed"] += 1; continue
            log(f"  {'WOULD SCORE' if dry else 'SCORED'}  {iid}: {len(scores)} players, teams {meta['teams']}{' (no result)' if no_result else ''}")
            if not dry:
                payload = {**meta, "title": m["title"], "date": m["date_label"], "wk": m["week_no"],
                           "teams": json.loads(m["teams_json"] or "[]") or meta["teams"],
                           "status": "completed", "scores": scores}
                with db._write() as con:
                    _upsert_match(con, payload, slug)
                db.recalculate_points(match_id=iid, competition_id=slug)
                db.update_week_points(competition_id=slug)
            summary["scored"] += 1
        except Exception as e:                                 # one bad match never stops the run
            log(f"  FAILED  {iid}: {type(e).__name__}: {e}"); summary["failed"] += 1

    if summary["scored"] and not dry:
        n = db.update_player_season_pts(competition_id=slug)
        log(f"  season points refreshed ({n} players with points)")
    log(f"--- done: {summary} ---")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Score a competition from Cricbuzz scorecards (run on your PC).")
    ap.add_argument("slug")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-discover", action="store_true")
    ap.add_argument("--force", action="append", default=[], metavar="MATCH_ID")
    ap.add_argument("--series-id")
    ap.add_argument("--series-slug")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    db = _make_db()
    score(db, a.slug, dry=a.dry_run, do_discover=not a.no_discover, force=a.force, series_id=a.series_id, series_slug=a.series_slug)


if __name__ == "__main__":
    main()

"""
BCL Fantasy — competition data import (validation + row building)   v1.0.0
===========================================================================
Pure functions only: stdlib imports, no DB, no Flask. Turns an uploaded roster
(`players.json`) and fixture list (`schedule.json`) into validated rows that
`DatabaseManager.import_competition_data()` can write. This is the Postgres-era
replacement for the SQLite-only `Seed_Players.py` / `Seed_Matches.py` onboarding
scripts: an admin uploads the two files in the Admin tab; nothing is committed
to git and no deploy is needed.

Input shapes (the same files the legacy seeders read, so existing files work):

  players.json   [ {"id","name","team","price","role"}, ... ]
                 (legacy [id,name,team,price,role] tuples are also accepted;
                  a {"players": [...]} wrapper is tolerated)

  schedule.json  {"series_id"?, "series_slug"?, "matches": [
                    {"match_no", "title", "date": "YYYY-MM-DD",
                     "time_ist": "HH:MM", "cricbuzz_id"?: "149618"|null,
                     "teams"?: ["SRH","RCB"]} ... ]}
                 (a bare list of matches is also accepted. `time_ist` is the
                  legacy field name: the time is read as IST, so convert local
                  kick-off times when preparing a league in another timezone.)

Every function returns {"rows", "errors", "warnings", "stats", ...}. Any entry in
`errors` blocks the import; `warnings` are advisory.

Rules mirrored from the legacy code so behaviour does not change:
  * player id  ^[a-z]{1,3}\\d{1,2}$   (base._ID_RE — captain/vice-captain ids are
                                       validated against it, so a roster id that
                                       fails it could never be picked as captain)
  * roles      BAT / BOWL / AR / WK    (players.role CHECK constraint)
  * match id   <slug>_m<NN>            (Seed_Matches.seed_to_db)
  * week no    week 1 = before the competition's first rollover (`anchor`);
               then 2 + floor(days since anchor / 7)   (Seed_Matches._week_no_for_match)
  * date label "Mon D" (e.g. "Jan 17")
  * scorecard  https://www.cricbuzz.com/live-cricket-scorecard/<id>, or the
               /00000 placeholder when the Cricbuzz id is not known yet
"""

import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

COMP_IMPORT_VER = "1.0.0"

PLAYER_ID_RE = re.compile(r"^[a-z]{1,3}\d{1,2}$")
ROLES = ("BAT", "BOWL", "AR", "WK")
IST = timezone(timedelta(hours=5, minutes=30))

MAX_PLAYERS = 1000
MAX_MATCHES = 400
MAX_NAME_LEN = 60
MAX_TEAM_LEN = 10
MAX_MESSAGES = 40            # cap on listed errors/warnings (the rest are summarised)

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_TITLE_TEAMS_RE = re.compile(r"^([A-Z]+)\s+vs\s+([A-Z]+)")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
PLACEHOLDER_URL = "https://www.cricbuzz.com/live-cricket-scorecard/00000"


# ── helpers ───────────────────────────────────────────────────────────────────

def _cap(msgs):
    """Keep at most MAX_MESSAGES entries, summarising the overflow."""
    if len(msgs) <= MAX_MESSAGES:
        return msgs
    return msgs[:MAX_MESSAGES] + ["... and %d more" % (len(msgs) - MAX_MESSAGES)]


def _is_number(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def parse_anchor(value):
    """ISO-8601 week-1 anchor -> tz-aware datetime, or None if missing/unusable
    (the 1970 default that a competition gets when no anchor was entered counts
    as unusable)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return None if dt.year < 2000 else dt


def week_no_for_match(date_str, time_str, anchor):
    """Fantasy week for a match. Week 1 is everything before the first rollover
    (`anchor`, a tz-aware datetime); every later 7-day block is the next week.
    Identical to the legacy Seed_Matches._week_no_for_match, except that bad
    input raises instead of silently returning week 1."""
    h, m = map(int, time_str.split(":"))
    y, mo, d = map(int, date_str.split("-"))
    match_dt = datetime(y, mo, d, h, m, tzinfo=IST)
    if match_dt < anchor:
        return 1
    return 2 + int(((match_dt - anchor).total_seconds() / 86400.0) // 7)


def _date_label(date_str):
    y, mo, d = map(int, date_str.split("-"))
    return "%s %d" % (_MONTHS[mo - 1], d)


# ── players ───────────────────────────────────────────────────────────────────

def parse_players(raw, *, budget_total=100.0, xi_size=11, valid_teams=None):
    """Validate a roster. Returns {"rows": [(id,name,team,price,role)], "errors",
    "warnings", "stats"}. `valid_teams` (the competition's team codes, if it has
    any) makes an unknown team an error."""
    errors, warnings = [], []
    if isinstance(raw, dict) and isinstance(raw.get("players"), list):
        raw = raw["players"]
    if not isinstance(raw, list):
        return {"rows": [], "stats": None, "warnings": [],
                "errors": ["players must be a JSON list of {id, name, team, price, role}"]}
    if not raw:
        return {"rows": [], "stats": None, "warnings": [],
                "errors": ["the players list is empty"]}
    if len(raw) > MAX_PLAYERS:
        return {"rows": [], "stats": None, "warnings": [],
                "errors": ["too many players (%d, the limit is %d)" % (len(raw), MAX_PLAYERS)]}

    allowed = set(valid_teams or [])
    rows, first_seen, name_seen = [], {}, {}
    for i, e in enumerate(raw, 1):
        if isinstance(e, dict):
            pid, name, team, price, role = (e.get(k) for k in ("id", "name", "team", "price", "role"))
        elif isinstance(e, (list, tuple)) and len(e) == 5:
            pid, name, team, price, role = e
        else:
            errors.append("player #%d: expected an object with id, name, team, price and role" % i)
            continue
        where = "player #%d (%s)" % (i, pid if isinstance(pid, str) and pid else "?")
        bad = False

        if not isinstance(pid, str) or not PLAYER_ID_RE.match(pid.strip()):
            errors.append("%s: id must be 1-3 lowercase letters then 1-2 digits, e.g. 'mct01'" % where)
            bad = True
        else:
            pid = pid.strip()
        if not isinstance(name, str) or not name.strip():
            errors.append("%s: name is missing" % where)
            bad = True
        else:
            name = " ".join(name.split())
            if len(name) > MAX_NAME_LEN:
                errors.append("%s: name is longer than %d characters" % (where, MAX_NAME_LEN))
                bad = True
        if not isinstance(team, str) or not team.strip():
            errors.append("%s: team is missing" % where)
            bad = True
        else:
            team = team.strip()
            if len(team) > MAX_TEAM_LEN:
                errors.append("%s: team code '%s' is longer than %d characters" % (where, team, MAX_TEAM_LEN))
                bad = True
            elif allowed and team not in allowed:
                errors.append("%s: team '%s' is not one of this competition's teams (%s)"
                              % (where, team, ", ".join(sorted(allowed))))
                bad = True
        if not _is_number(price):
            errors.append("%s: price must be a number" % where)
            bad = True
        elif price < 0 or price > budget_total:
            errors.append("%s: price %s is outside 0 - %s (the budget)" % (where, price, budget_total))
            bad = True
        elif price == 0:
            warnings.append("%s: price is 0 (a free player)" % where)
        role_s = role.strip().upper() if isinstance(role, str) else None
        if role_s not in ROLES:
            errors.append("%s: role must be one of %s" % (where, ", ".join(ROLES)))
            bad = True
        else:
            role = role_s

        if isinstance(pid, str) and PLAYER_ID_RE.match(pid):
            if pid in first_seen:
                errors.append("%s: duplicate id (also player #%d)" % (where, first_seen[pid]))
                bad = True
            else:
                first_seen[pid] = i
        if not bad:
            key = name.lower()
            if key in name_seen:
                warnings.append("%s: the name '%s' is also used by player #%d (the id is what counts, "
                                "but duplicate names confuse player matching)" % (where, name, name_seen[key]))
            else:
                name_seen[key] = i
            rows.append((pid, name, team, float(price), role))

    stats = None
    if rows:
        prices = sorted((r[3] for r in rows), reverse=True)
        top_xi = sum(prices[:xi_size])
        cheap_xi = sum(prices[-xi_size:])
        stats = {
            "total": len(rows),
            "by_team": dict(sorted(Counter(r[2] for r in rows).items())),
            "by_role": dict(sorted(Counter(r[4] for r in rows).items())),
            "price": {"min": prices[-1], "max": prices[0],
                      "mean": round(sum(prices) / len(prices), 2), "sum": round(sum(prices), 1)},
            "top_xi_cost": round(top_xi, 1),
        }
        if len(rows) < xi_size:
            errors.append("the roster has only %d players, fewer than the XI size (%d)" % (len(rows), xi_size))
        elif cheap_xi > budget_total:
            errors.append("even the %d cheapest players cost %.1f, more than the %.0f budget - nobody could "
                          "pick a team" % (xi_size, cheap_xi, budget_total))
        elif top_xi < budget_total:
            warnings.append("the %d priciest players cost only %.1f, below the %.0f budget - the budget will "
                            "never bind; check the prices" % (xi_size, top_xi, budget_total))
        if allowed:
            empty = sorted(allowed - set(stats["by_team"]))
            if empty:
                warnings.append("no players for team(s): %s" % ", ".join(empty))

    return {"rows": rows, "errors": _cap(errors), "warnings": _cap(warnings), "stats": stats}


# ── schedule ──────────────────────────────────────────────────────────────────

def parse_schedule(raw, *, slug, anchor, valid_teams=None):
    """Validate a fixture list. Returns {"rows": [dict], "errors", "warnings",
    "stats", "meta"}. `anchor` is the competition's week-1 anchor as a tz-aware
    datetime (see parse_anchor); without one week numbers would be meaningless,
    so a missing anchor is an error."""
    errors, warnings, meta = [], [], {}
    fail = lambda msg: {"rows": [], "stats": None, "meta": meta, "warnings": [], "errors": [msg]}  # noqa: E731

    if isinstance(raw, dict):
        meta = {k: raw.get(k) for k in ("series_id", "series_slug") if raw.get(k) not in (None, "")}
        year = raw.get("year", raw.get("ipl_year"))
        if isinstance(year, int) and not isinstance(year, bool):
            meta["year"] = year
        raw = raw.get("matches")
    if not isinstance(raw, list):
        return fail("schedule must contain a list of matches (a \"matches\" array)")
    if not raw:
        return fail("the schedule has no matches")
    if len(raw) > MAX_MATCHES:
        return fail("too many matches (%d, the limit is %d)" % (len(raw), MAX_MATCHES))
    if anchor is None:
        return fail("this competition has no week-1 date yet - pick the first Monday after the opening match (the first "
                    "weekly lock); week numbers cannot be worked out without it")

    allowed = set(valid_teams or [])
    rows, seen_no, tbd = [], {}, []
    for i, m in enumerate(raw, 1):
        if not isinstance(m, dict):
            errors.append("match #%d: expected an object" % i)
            continue
        no = m.get("match_no")
        where = "match #%d" % (no if isinstance(no, int) and not isinstance(no, bool) else i)
        bad = False

        if not isinstance(no, int) or isinstance(no, bool) or no < 1:
            errors.append("%s: match_no must be a whole number >= 1" % where)
            bad = True
        elif no in seen_no:
            errors.append("%s: duplicate match_no" % where)
            bad = True
        else:
            seen_no[no] = i
        title = m.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append("%s: title is missing" % where)
            bad = True
        else:
            title = " ".join(title.split())
        date_s, time_s = m.get("date"), m.get("time_ist")
        when_ok = (isinstance(date_s, str) and _DATE_RE.match(date_s)
                   and isinstance(time_s, str) and _TIME_RE.match(time_s))
        if when_ok:
            try:
                week = week_no_for_match(date_s, time_s, anchor)
                label = _date_label(date_s)
            except (ValueError, IndexError):
                when_ok = False
        if not when_ok:
            errors.append("%s: date must be YYYY-MM-DD and time_ist HH:MM (a real date and time)" % where)
            bad = True

        teams = m.get("teams")
        if teams is None and isinstance(title, str):
            t = _TITLE_TEAMS_RE.match(title)
            teams = [t.group(1), t.group(2)] if t else []
        if teams == []:
            # Teams not known yet (e.g. playoffs: "Qualifier 1"). The legacy seeder stored
            # these with teams_json "[]" too; the scraper fills them in once they are known.
            tbd.append(no if isinstance(no, int) and not isinstance(no, bool) else i)
        elif (not isinstance(teams, list) or len(teams) != 2
                or not all(isinstance(x, str) and x.strip() for x in teams)):
            errors.append("%s: \"teams\" must list exactly two team codes, e.g. [\"AAA\", \"BBB\"]" % where)
            bad = True
        else:
            teams = [x.strip() for x in teams]
            if teams[0] == teams[1]:
                errors.append("%s: both teams are '%s'" % (where, teams[0]))
                bad = True
            elif allowed and not set(teams) <= allowed:
                errors.append("%s: team(s) %s are not among this competition's teams (%s)"
                              % (where, ", ".join(sorted(set(teams) - allowed)), ", ".join(sorted(allowed))))
                bad = True
        cb = m.get("cricbuzz_id")
        if cb in (None, ""):
            cb = None
        else:
            cb = str(cb).strip()
            if not cb.isdigit():
                errors.append("%s: cricbuzz_id must be digits only" % where)
                bad = True
        if bad:
            continue
        rows.append({
            "id": "%s_m%02d" % (slug, no), "match_no": no, "week_no": week,
            "title": title, "teams": teams,
            "teams_json": json.dumps(teams),
            "date": date_s, "time_ist": time_s, "date_label": label,
            "cricbuzz_id": cb,
            "scorecard_url": ("https://www.cricbuzz.com/live-cricket-scorecard/%s" % cb) if cb else PLACEHOLDER_URL,
            "start_utc": datetime(*map(int, date_s.split("-")), *map(int, time_s.split(":")),
                                  tzinfo=IST).astimezone(timezone.utc),
        })

    stats = None
    if rows:
        rows.sort(key=lambda r: r["match_no"])
        weeks = Counter(r["week_no"] for r in rows)
        first = min(rows, key=lambda r: r["start_utc"])["start_utc"]
        last = max(rows, key=lambda r: r["start_utc"])["start_utc"]
        stats = {
            "total": len(rows),
            "weeks": {str(k): weeks[k] for k in sorted(weeks)},
            "max_week": max(weeks),
            "first_match_utc": first.isoformat(),
            "last_match_utc": last.isoformat(),
            "with_cricbuzz_id": sum(1 for r in rows if r["cricbuzz_id"]),
            "teams": sorted({t for r in rows for t in r["teams"]}),
            "teams_tbd": len([r for r in rows if not r["teams"]]),
        }
        if tbd:
            warnings.append("%d match(es) have no teams yet (usually playoffs): %s - fine, they are stored "
                            "without teams" % (len(tbd), ", ".join("#%d" % n for n in sorted(tbd)[:12])
                                               + (" ..." if len(tbd) > 12 else "")))
        nums = [r["match_no"] for r in rows]
        if nums != list(range(1, len(nums) + 1)):
            warnings.append("match numbers are not a continuous 1..%d sequence" % len(rows))
        if [r["start_utc"] for r in rows] != sorted(r["start_utc"] for r in rows):
            warnings.append("match numbers are not in date order")
        if len(rows) < 10:
            warnings.append("only %d matches - is this the whole fixture list?" % len(rows))
        if first < anchor - timedelta(days=14):
            warnings.append("the first match is more than 14 days before the week-1 date (%s) - "
                            "check that date" % anchor.date().isoformat())
        if stats["max_week"] > 20:
            warnings.append("the schedule spans %d weeks - check the dates" % stats["max_week"])

    return {"rows": rows, "errors": _cap(errors), "warnings": _cap(warnings), "stats": stats, "meta": meta}


def cross_check(player_rows, match_rows):
    """Warnings when a roster and a fixture list disagree about team codes."""
    if not player_rows or not match_rows:
        return []
    roster = {r[2] for r in player_rows}
    fixtures = {t for m in match_rows for t in m["teams"]}
    out = []
    if fixtures - roster:
        out.append("fixtures mention team(s) with no players in the roster: %s"
                   % ", ".join(sorted(fixtures - roster)))
    if roster - fixtures:
        out.append("the roster has team(s) that play in no fixture: %s" % ", ".join(sorted(roster - fixtures)))
    return out

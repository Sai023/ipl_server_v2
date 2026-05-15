"""
IPL Fantasy 2026 — Fuzzy Player Name Matcher         fuzzy_match v1.1.0
===========================================================================
v1.1.0 (Resilience Upgrade):
  _generate_dynamic_player() added.
    • Returns a fully-keyed player dict (id, name, team, role, price)
      for any unknown player that survives all fuzzy tiers.
    • ID strategy: `ext_{cricbuzz_id}` when a Cricbuzz numeric ID is
      supplied (zero collision risk with Seed_Players.py short-form IDs
      such as `c09`, `rr11`).  Falls back to `ext_{6-char md5}` when
      no CB id is available.
    • role defaults to "AR" — "UNCAPPED" is intentionally NOT used
      because the DB schema enforces CHECK(role IN ('BAT','BOWL','AR','WK')).
    • price defaults to 7.0 (midrange — does not affect fantasy budget
      for existing users; only relevant if a future user drafts the player).

v1.0.0 (Phase 4): extracted verbatim from scraper.py v10.9.

Imports: stdlib (hashlib, re, sqlite3, unicodedata) only — zero project imports.
"""

import hashlib
import re
import sqlite3
import unicodedata


# ── Nickname / shorthand map — single source of truth ────────────────────────
# Originally lived in base.py. Moved here so the project has ONE curated
# nickname dictionary instead of risking silent drift between two copies.
#
# Used by base.resolve_player_id (the UI-side resolver). NOT used by
# _fuzzy_match (the scraper-side resolver) — Cricbuzz scorecards always
# carry formal names like "V Kohli", and applying nicknames to scrape
# data risks misattributing surname-only entries (e.g. "Patel" → Axar
# Patel even when another Patel batted).
#
# When adding entries here, keys must be lowercase. The values are the
# canonical player name as it appears in the players table.
_SEMANTIC_MAP = {
    "vk":"virat kohli","rohit":"rohit sharma","ms":"ms dhoni","msd":"ms dhoni",
    "bumrah":"jasprit bumrah","bumpy":"jasprit bumrah","jadeja":"ravindra jadeja",
    "sky":"suryakumar yadav","kl":"kl rahul","klr":"kl rahul",
    "hp":"hardik pandya","h pandya":"hardik pandya","pandya":"hardik pandya",
    "shami":"mohammed shami","siraj":"mohammed siraj","chahal":"yuzvendra chahal",
    "sam":"sanju samson","ishan":"ishan kishan","ik":"ishan kishan",
    "salt":"phil salt","klaasen":"heinrich klaasen","david":"tim david",
    "shepherd":"romario shepherd","rutherford":"shimron rutherford",
    "patidar":"rajat patidar","chakravarthy":"varun chakravarthy",
    "chakra":"varun chakravarthy","chakar":"varun chakravarthy",
    "vc":"varun chakravarthy","chahar":"deepak chahar","duffy":"jacob duffy",
    "patel":"axar patel","varma":"tilak varma","rahane":"ajinkya rahane",
    "ravindra":"rachin ravindra",
    "sooryavanshi":"vaibhav sooryavanshi","suryavanshi":"vaibhav sooryavanshi",
    "vaibhav":"vaibhav sooryavanshi",
    "jansen":"marco jansen","brevis":"dewald brevis","rickelton":"ryan rickelton",
    "ngidi":"lungi ngidi","hetmyer":"shimron hetmyer","rana":"harshit rana",
    "pant":"rishabh pant","noor":"noor ahmad","dube":"shivam dube",
    "samson":"sanju samson","tharva":"atharva taide","markram":"aiden markram",
    "rashid":"rashid khan","prabhsimran":"prabhsimran singh",
}


def _norm(s: str) -> str:
    """
    Normalise a player name for fuzzy comparison.
    Steps: lowercase → NFD → strip combining marks → collapse punctuation → strip.
    """
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/\u2020]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _generate_dynamic_player(
    name: str,
    team_code: str,
    cricbuzz_id=None,
) -> dict:
    """
    Generate a fully-keyed fallback player dict for a player not found in
    Seed_Players.py.

    Called by _auto_add_player() in scraper.py when all fuzzy tiers fail.
    The returned dict contains every key the scraper and DB expect:
        id    — unique, never collides with Seed_Players.py short IDs
        name  — raw Cricbuzz display name (preserved for audit logs)
        team  — uppercased IPL team code
        role  — "AR" (satisfies DB CHECK constraint)
        price — 7.0 (default midrange value)

    ID strategy
    -----------
    1. ext_{cricbuzz_id}  when a Cricbuzz numeric player ID is available
       e.g.  ext_1234567  — globally unique, matches Cricbuzz records.
    2. ext_{6-char-md5}   fallback using MD5 of the normalised name when
       no CB id is supplied.
       e.g.  ext_a3f9b2

    Both prefixes are outside [a-z]{1,3}\\d{1,2} so _ID_RE.match() in
    the scraper correctly counts them as "unresolved" for stats reporting.

    Parameters
    ----------
    name        : str  — player's display name from the scorecard
    team_code   : str  — IPL team short code (e.g. "CSK", "MI"); may be ""
    cricbuzz_id : int|str|None  — Cricbuzz numeric player ID if known

    Returns
    -------
    dict with keys: id, name, team, role, price
    """
    if cricbuzz_id:
        pid = f"ext_{cricbuzz_id}"
    else:
        h   = hashlib.md5(_norm(name).encode()).hexdigest()[:6]
        pid = f"ext_{h}"

    return {
        "id":    pid,
        "name":  name,
        "team":  (team_code or "").upper(),
        "role":  "AR",    # "UNCAPPED" violates CHECK(role IN ('BAT','BOWL','AR','WK'))
        "price": 7.0,
    }


def _build_player_index(con: sqlite3.Connection) -> dict:
    """
    v10.4: Build by_name_team and name_conflicts for team-aware disambiguation.

    Returns dict with keys:
        by_name        : {norm_name → player_dict}
        by_surname     : {surname   → [player_dict, ...]}
        all            : [player_dict, ...]
        by_name_team   : {(norm_name, TEAM_UPPER) → player_dict}
        name_conflicts : {norm_name} — names shared by >1 player
    """
    rows    = con.execute("SELECT id, name, team, role FROM players").fetchall()
    players = [{"id": r[0], "name": r[1], "team": r[2], "role": r[3]} for r in rows]
    by_name = {}; by_surname = {}; by_name_team = {}; name_conflicts = set()
    for p in players:
        n = _norm(p["name"])
        if n in by_name:
            name_conflicts.add(n)
        by_name[n] = p
        by_name_team[(n, p["team"].upper())] = p
        parts = n.split()
        if parts:
            by_surname.setdefault(parts[-1], []).append(p)
    return {
        "by_name": by_name, "by_surname": by_surname, "all": players,
        "by_name_team": by_name_team, "name_conflicts": name_conflicts,
    }


def _fuzzy_match(name: str, idx: dict, team_hint: str = None) -> str | None:
    """
    v10.4: Resolve a batter / bowler name to a player ID.
    team_hint is used first for names in name_conflicts.

    Resolution tiers:
      1. Exact normalised name, with team hint for conflict names.
      2. Exact normalised name, any team.
      3. Single-match surname lookup.
      4. Token-set fuzzy ratio ≥ 0.45.
    """
    n = _norm(name)
    if not n: return None
    if team_hint and n in idx.get("name_conflicts", set()):
        p = idx.get("by_name_team", {}).get((n, team_hint.upper()))
        if p: return p["id"]
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
    """
    Resolve a fielder name to a player ID.

    Resolution tiers:
      1. Exact normalised name.
      2. Single-match surname, optionally filtered by bowling_team.
      3. Surname of last word in multi-word name, team-filtered.
    """
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
            if len(tf) == 1: return tf[0]["id"]  # fixed: was `tf[0][" id"]` typo
    return None

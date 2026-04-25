"""
IPL Fantasy 2026 — Fuzzy Player Name Matcher         fuzzy_match v1.0.0
===========================================================================
Phase 4 — Extracted verbatim from scraper.py v10.9.

Imports: stdlib (re, sqlite3, unicodedata) only — zero project imports.
Logic is bit-for-bit identical to scraper.py v10.9; nothing has been
changed, including known quirks (see _fuzzy_fielder note below).

Public API
----------
_norm(s)
    Normalise a name string for comparison (lower, strip accents, etc.).

_build_player_index(con)
    Build the in-memory player lookup dict from a sqlite3 Connection.
    Returns: {by_name, by_surname, all, by_name_team, name_conflicts}.

_fuzzy_match(name, idx, team_hint=None) -> str | None
    Resolve a batter / bowler name string to a player ID.
    Uses team_hint first for names flagged in name_conflicts.

_fuzzy_fielder(name, idx, bowling_team=None) -> str | None
    Resolve a fielder name string to a player ID.
    Note: preserves the original `tf[0][" id"]` key (space before "id")
    from scraper.py — intentionally not corrected under Phase 4 strict scope.
"""

import re
import sqlite3
import unicodedata


def _norm(s: str) -> str:
    """
    Normalise a player name for fuzzy comparison.
    Identical to scraper.py v10.9 _norm().

    Steps: lowercase → NFD → strip combining marks → collapse punctuation → strip.
    """
    s = str(s).lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-'/\u2020]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _build_player_index(con: sqlite3.Connection) -> dict:
    """
    v10.4: Build by_name_team and name_conflicts for team-aware disambiguation.
    Identical to scraper.py v10.9 _build_player_index().

    Parameters
    ----------
    con : sqlite3.Connection — open connection to the fantasy DB (players table).

    Returns
    -------
    dict with keys:
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
    Identical to scraper.py v10.9 _fuzzy_match().

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
    Identical to scraper.py v10.9 _fuzzy_fielder().

    Resolution tiers:
      1. Exact normalised name.
      2. Single-match surname, optionally filtered by bowling_team.
      3. Surname of last word in multi-word name, team-filtered.

    Note: the key `tf[0][" id"]` (with a leading space) on the final branch
    is preserved verbatim from scraper.py — not corrected under Phase 4
    strict-scope rules.
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
            if len(tf) == 1: return tf[0][" id"]  # verbatim from scraper.py v10.9
    return None

"""
IPL Fantasy 2026 — API Route Handlers                       routes v1.4.0
===========================================================================
v1.15.0 (Google sign-in, step 1, 2026-11-21, app 2.22.0):
  GET /api/auth/config, GET /api/auth/nonce, POST /api/auth/google, POST /api/auth/link, POST /api/auth/logout, POST /api/admin/auth/unlink. The server checks the ID token
  Google hands the page (logic/google_identity.py) and a single-use nonce it issued; a member switches from the passcode to Google and the passcode is retired in the same
  transaction; the admin can unlink (recovery). Nothing works until GOOGLE_CLIENT_ID is set. /api/login answers a switched member with reason pin_retired.
v1.14.0 (hardening release, 2026-11-21, app 2.21.0):
  Routes no longer catch `Exception`: base.py answers every unexpected failure (a logged traceback under a short request id, a fixed sentence to the
  caller) and every HTTP error (JSON on /api/, never the app page). A route only answers failures it EXPECTS, with its own sentence and `reason`.
  GET /api/health — one SELECT 1, the page's banner probe. /api/state reads its tag before the data and answers 304 before any heavy read.
  The player resolver (base.py) is read-only and looks at one competition. Removed: /api/audit-scores/<n>, /api/clean-scores, /api/audit-player-ids,
  /api/audit-blobs, /api/snapshot (the v1.2.0 entry below lists three of them).
v1.4.0 (Phase 9 — Daily auto-sync):
  /api/sync-now (POST) — manual trigger for the discovery+scrape pipeline.
    Spawns tasks.start_bg_sync() in a daemon thread; returns immediately
    with the thread name. Same code path as the APScheduler 23:55 IST job.
    Accepts {"debug": true} in body or ?debug=1 query string.
  /api/version — modules dict extended with two new pins:
    seed_matches      (SEED_MATCHES_VER       — JSON-aware shim version)
    cricbuzz_discovery (CRICBUZZ_DISCOVERY_VER — shared discovery module)

v1.3.0 (Phase 9.1 — Match Centre Backend):
  /api/match-centre          — Hub endpoint (requires ?user=<name>).
    Returns all matches grouped by week. Each match entry includes:
    match_id, teams, venue, date_label, result, status, user_match_pts.
    Season summary (total_pts, matches_played, avg_per_match, best_pts,
    best_match) included at the top level. Single fetch — no live tracking.
  /api/match-details/<match_id> — Box Score endpoint (requires ?user=<name>).
    Returns the user's historical XI snapshot for that specific match with
    per-player pts and C/VC multipliers applied. Reads tw_team_json from
    the week the match belongs to — historically accurate even if the user
    changed their squad in later weeks. No writes, no live logic.

v1.2.0 (Backend Audit & Snapshot):
  /api/audit-player-ids, /api/audit-blobs, /api/snapshot

v1.1.0 (Phase 8 — Scouting & UX): player_pts badge support.
v1.0.0 (Phase 7): 24 @bp.route handlers in 8 groups.

Dependency: base.py → routes.py  (routes never imports from server)
"""

import hmac
import json as _json
import os
import re
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, render_template, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

import cloud_sync   # Phase 2: git pull / push helpers (HOSTED mode)

# HOSTED mode flag — same source of truth as server.py.
# Routes only branch when behaviour MUST differ in the cloud (Cricbuzz scrape
# replaced with git pull). Everything else stays single-codepath.
_IS_HOSTED = os.environ.get("HOSTED", "").lower() in ("true", "1", "yes")


def _push_if_hosted(reason: str, extra_paths: list | None = None) -> None:
    """
    Phase 4: After a successful write in HOSTED mode, commit fantasy.db and
    push so other clients (and the local box / Actions) see the change on
    their next git pull.

    `extra_paths` lets callers include adjacent file changes in the same
    commit — e.g. `api_update_match_url` deletes a cached
    `data/matches/match_NN.json` to force the workflow to re-scrape that
    match. Without passing the JSON path here, the deletion stays local
    and the workflow finds the stale JSON on its checkout, then skips
    the match (scraper.py:615 cache short-circuit). All non-fantasy.db
    paths in `extra_paths` get the same `git add -Af` treatment as
    fantasy.db.

    Sync, not async — durability matters more than the 2-5s latency. If the
    container dies between the local DB write and the git push, the change
    is lost forever (Render redeploys wipe the container's filesystem).
    Sync push means the write is durable as soon as the user sees "OK".

    Failures are logged but do not surface to the caller as a 500. The
    local DB has the change; the next successful push will catch it up.
    """
    return  # INF-1: no-op — Postgres persists writes directly; there is no git DB sync.
    if not _IS_HOSTED:
        return
    try:
        # CRITICAL — flush WAL before git add. Without this, the just-committed
        # write is sitting in fantasy.db-wal and git captures a pre-write
        # snapshot, returning "nothing to commit" → silent push failure → lost
        # data on next Render redeploy. See db.checkpoint() docstring for the
        # passcode-change-lost-on-redeploy bug this resolves.
        db.checkpoint()
        paths = ["data/fantasy.db"] + list(extra_paths or [])
        ok, msg = cloud_sync.commit_and_push(
            paths=paths,
            message=f"ui: {reason}",
        )
        if not ok:
            _log(f"git push after '{reason}' failed: {msg}. "
                 f"Local DB has the change; will catch up on next write.",
                 "warn")
        else:
            _log(f"git push after '{reason}': {msg}")
    except Exception as e:
        _log(f"_push_if_hosted({reason}) crashed: {e}", "error")

import base as _base
from base import (
    db, _log, _write_limiter, _auth_limiter, _check_rate,
    resolve_player_id, resolve_id_list, needs_resolving, _ID_RE, _jloads,
    BASE_DIR, DATA_DIR, STATIC_DIR,
    BUDGET_TOTAL, XI_SIZE, MAX_WEEKS,
    PASSCODE_RE, hash_passcode, verify_passcode, NO_PIN,
    new_session_token, get_bearer_token,
)
from config import (
    DB_PATH, DEADLINE_HOUR, DEADLINE_MIN,
    APP_VERSION, VERSION_MAP,
    SERVER_VER, ROUTES_VER, DB_VER, SCRAPER_VER, INIT_DB_VER, TASKS_VER,
    SEED_MATCHES_VER,
    SCORING_ENGINE_VER, ROLLOVER_ENGINE_VER, ROLLOVER_PLAN_VER, SESSION_POLICY_VER, GOOGLE_IDENTITY_VER, FUZZY_MATCH_VER,
    CRICBUZZ_DISCOVERY_VER, MAX_MEMBERS, HANDOFF_VER, ORIGIN_VER, OPS_PLAN_VER, OPS_NOTIFY_VER,
)
import tasks
from logic.rollover_engine import last_monday_deadline, selections_locked   # the old Monday-14:00 rules: only for a competition with NO stored lock schedule
from logic import rollover_plan as _rp                                       # the stored lock schedule: what is due, and whether picking is locked (Phase 1.5)
from logic import competition_import as _ci
from logic import google_identity as _gid                                    # checks the ID token Google hands the page (Phase 3 sign-in, 2026-11-21)
from logic import app_handoff as _handoff                                    # one-time codes that carry a signed-in member from the browser into the installed app (2026-11-21)
from logic import origin as _origin                                          # the app's public address (PUBLIC_BASE_URL) and the "We've moved" switch (plan item 1.10, 2026-11-22)
from logic import ops_plan as _ops                                           # what the clocks, the Ops card and the admin alerts need to know (Phase 2, 2026-11-22)
import ops_notify as _notify                                                 # the only code that talks to Telegram (dormant until a bot token is set)

bp = Blueprint("api", __name__)


# ════════════════════════════════════════════════════════════════════════════
# Competition resolution (multi-competition)
# ════════════════════════════════════════════════════════════════════════════
# Every read/write endpoint resolves a target competition from ?comp=<slug>,
# defaulting to the active competition. The DAO defaults the same way, so
# omitting ?comp= is always safe (single-competition back-compat).

def _comp():
    """Resolve this request's competition: ?comp=<slug> if it exists, else the
    active competition. Always returns a valid slug string."""
    slug = (request.args.get("comp") or "").strip()
    if slug and db.get_competition(slug):
        return slug
    return db._active_slug()

def _comp_cfg(slug):
    """(budget_total, xi_size, max_weeks, deadline_hour, deadline_min) for a
    competition, falling back to the legacy config constants if the row is
    absent (e.g. an un-migrated DB)."""
    c = db.get_competition(slug) or {}
    return (
        c.get("budget_total", BUDGET_TOTAL),
        c.get("xi_size", XI_SIZE),
        c.get("max_weeks", MAX_WEEKS),
        c.get("deadline_hour", DEADLINE_HOUR),
        c.get("deadline_min", DEADLINE_MIN),
    )


# ════════════════════════════════════════════════════════════════════════════
# 1. SYSTEM
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/version", methods=["GET"])
def api_version():
    base_url, base_source = _origin.resolve_public_base(os.environ, request.host)
    return jsonify({
        "ok": True, "app_version": APP_VERSION,
        # where this server believes it lives (PUBLIC_BASE_URL, else the platform's own address, else the one this request came to), the new address if this is an
        # OLD address that has been switched to the "We've moved" page, and any setting that is set but unusable (ignored, never fatal)
        "public_base_url": base_url, "public_base_source": base_source,
        "moved_to": _origin.moved_target(os.environ), "origin_problems": _origin.config_problems(os.environ),
        "modules": {
            "server":             SERVER_VER,
            "routes":             ROUTES_VER,
            "db_manager":         DB_VER,
            "scraper":            SCRAPER_VER,
            "init_db":            INIT_DB_VER,
            "tasks":              TASKS_VER,
            "seed_matches":       SEED_MATCHES_VER,         # Phase 9
            "scoring_engine":     SCORING_ENGINE_VER,
            "rollover_engine":    ROLLOVER_ENGINE_VER,
            "rollover_plan":      ROLLOVER_PLAN_VER,
            "session_policy":     SESSION_POLICY_VER,
            "google_identity":    GOOGLE_IDENTITY_VER,
            "app_handoff":        HANDOFF_VER,
            "origin":             ORIGIN_VER,
            "ops_plan":           OPS_PLAN_VER,
            "ops_notify":         OPS_NOTIFY_VER,
            "fuzzy_match":        FUZZY_MATCH_VER,
            "cricbuzz_discovery": CRICBUZZ_DISCOVERY_VER,   # Phase 9
        },
        "version_map": VERSION_MAP,
    })

@bp.route("/api/ping")
def api_ping():
    comp = _comp()
    stats = db.ping_stats(competition_id=comp)
    budget, xi, maxw, _dh, _dm = _comp_cfg(comp)
    stats.update({"ok": True, "public_url": _base.CURRENT_PUBLIC_URL,
                  "competition": comp,
                  "budget": budget, "xi_size": xi, "max_weeks": maxw})
    return jsonify(stats)


@bp.route("/api/health")
def api_health():
    """Is the server up, and can it reach its database? One `SELECT 1` and nothing else: the page's banner probes this again and again while the server is in
    trouble, so it must be the lightest honest answer (`/api/ping` costs five statements). `/api/ping` stays for what it also carries: the competition's
    config the page loads at start, the counts, and Render's health check."""
    with db._read() as con:
        con.execute("SELECT 1")
    return jsonify({"ok": True})


@bp.route("/api/_test-push", methods=["POST"])
def api_test_push():
    """
    Diagnostic for HOSTED mode. Bumps `meta._last_test_push` to "now" and
    calls `cloud_sync.commit_and_push` directly, returning the raw
    `(ok, msg)` result. This is the only way to verify that the host can
    write back to git on Render's free tier (no shell access available).

    Why it exists: in the first 24h of HOSTED operation, zero `ui:` prefix
    commits appeared on `origin/main` — confirming `_push_if_hosted` was
    silently failing for every save-next-week and every passcode change.
    The wrapper logs at WARN level but the raw git error wasn't surfacing
    anywhere accessible. This endpoint exposes it.

    No auth — safe because the only side effect is one extra commit and
    the response leaks no secrets (raw git stderr is the most sensitive
    thing returned, which has been sanitised by `cloud_sync` of any token).
    Local mode returns 400 (the test is meaningful only in HOSTED).
    """
    if not _IS_HOSTED:
        return jsonify({"ok": False, "code": 400,
                        "error": "test-push is only meaningful in HOSTED mode "
                                 "(set HOSTED=true)"}), 400
    _u, err = _require_admin()
    if err: return err
    now_iso = datetime.now(timezone.utc).isoformat()
    db.set_meta("_last_test_push", now_iso)
    # Same WAL-flush as _push_if_hosted — without this, the meta-table
    # write sits in fantasy.db-wal and the test push reports
    # "nothing to commit" while actually doing nothing useful.
    db.checkpoint()
    ok, msg = cloud_sync.commit_and_push(
        paths=["data/fantasy.db"],
        message=f"diag: test-push at {now_iso}",
    )
    if ok:
        _log(f"/api/_test-push SUCCESS: {msg}")
    else:
        _log(f"/api/_test-push FAILED: {msg}", "warn")
    return jsonify({
        "ok": True,
        "test_push_succeeded": ok,
        "git_result": msg,
        "instructions": (
            "If test_push_succeeded=true, a commit titled "
            "'diag: test-push at <iso>' should appear on "
            "github.com/<owner>/<repo>/commits/main within 5s. "
            "If false, git_result contains the raw error — paste it "
            "to the operator."
        ),
    })

@bp.route("/api/poll", methods=["GET"])
def api_poll():
    recv = datetime.now(timezone.utc)   # when the request reached us
    # The server's clock, so the page can count down to a lock on IT and not on a phone clock that may be wrong. Two stamps (when the request
    # arrived and when the reply leaves) let the page cancel out the time we spent on it, as NTP does.
    etag = db.get_etags(competition_id=_comp())["state"]
    return jsonify({"state_etag": etag, "ok": True,
                    "server_recv_utc": recv.isoformat(), "server_now_utc": datetime.now(timezone.utc).isoformat()})

@bp.route("/api/current-week", methods=["GET"])
def api_current_week():
    comp = _comp()
    _b, _x, maxw, _dh, _dm = _comp_cfg(comp)
    return jsonify({"week_no": db.get_current_week(competition_id=comp),
                    "max_weeks": maxw, "ok": True})


@bp.route("/api/competitions", methods=["GET"])
def api_competitions():
    """List all competitions + the active slug + the championship tally.
    Drives the header competition switcher and per-competition IplConfig."""
    comps = db.list_competitions()
    return jsonify({
        "ok": True,
        "active": db._active_slug(),
        "competitions": [
            {"slug": c["slug"], "name": c["name"], "status": c["status"],
             "format": c["format"], "budget_total": c["budget_total"],
             "xi_size": c["xi_size"], "max_weeks": c["max_weeks"],
             "champion": c["champion"]}
            for c in comps
        ],
        "championship": db.get_championship_tally(),
    })


# ════════════════════════════════════════════════════════════════════════════
# 1.5 AUTH (Passcodes feature)
# ════════════════════════════════════════════════════════════════════════════
# Bearer-token surface for /api/passcode/* and /api/admin/* only. The rest of
# the API remains trust-based (?user=<n>) per the project's "private league"
# stance documented in user_capabilities.md §1.

def _require_token():
    """Returns (username, None) on success, (None, (json_resp, status)) on failure."""
    token = get_bearer_token()
    if not token:
        return None, (jsonify({"ok": False, "error": "auth required", "code": 401, "reason": "auth_required"}), 401)
    sess = db.get_session(token)
    if not sess:
        return None, (jsonify({"ok": False, "error": "invalid or expired token", "code": 401, "reason": "session_ended"}), 401)
    return sess["username"], None

def _require_admin():
    username, err = _require_token()
    if err: return None, err
    m = db.get_member_auth(username)
    if not m or not m["is_admin"]:
        return None, (jsonify({"ok": False, "error": "admin only", "code": 403, "reason": "admin_only"}), 403)
    return username, None

def _require_self(n):
    """Token required AND the acting user must be `n` — a member may only modify
    their own data. (No admin-edits-others; that would be a separate tool.)"""
    username, err = _require_token()
    if err: return None, err
    if username != n:
        return None, (jsonify({"ok": False, "error": "you can only modify your own team", "code": 403, "reason": "not_your_team"}), 403)
    return username, None


@bp.route("/api/register", methods=["POST"])
def api_register():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    if _gid.enabled():
        # New members join with Google (POST /api/auth/join): no passcode to choose. This route is the passcode way in and only exists while Google sign-in is off.
        return jsonify({"ok": False, "error": "New members join with Google. Tap Continue with Google.", "code": 403, "reason": "register_closed"}), 403
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    passcode = str(d.get("passcode") or "").strip()
    if not name or len(name) > 30:
        return jsonify({"ok": False, "error": "name must be 1-30 chars", "code": 400, "reason": "name_invalid"}), 400
    if not PASSCODE_RE.match(passcode):
        return jsonify({"ok": False, "error": "passcode must be exactly 4 digits", "code": 400, "reason": "passcode_invalid"}), 400
    if db.get_member_auth(name):
        return jsonify({"ok": False, "error": "name already registered", "code": 409, "reason": "name_taken"}), 409
    # Create empty user_selections row using existing helper.
    db.upsert_member(name, {"this_week": {"team": [], "cap": None, "vc": None},
                            "next_week": {"team": [], "cap": None, "vc": None}})
    db.upsert_member_auth(name, hash_passcode(passcode, name),
                          must_change=0, is_admin=0)
    token = new_session_token()
    db.create_session(token, name)
    _push_if_hosted(f"register:{name}")
    return jsonify({"ok": True, "name": name, "token": token,
                    "must_change": False, "is_admin": False})


@bp.route("/api/login", methods=["POST"])
def api_login():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    passcode = str(d.get("passcode") or "").strip()
    if not name or not PASSCODE_RE.match(passcode):
        return jsonify({"ok": False, "error": "name + 4-digit passcode required", "code": 400, "reason": "credentials_required"}), 400
    m = db.get_member_auth(name)
    if m and m["passcode_hash"] == NO_PIN:
        # A member who switched to Google (or joined with it) has no passcode any more. Say so rather than "wrong passcode": the name list is public anyway.
        return jsonify({"ok": False, "error": "This account signs in with Google.", "code": 401, "reason": "pin_retired"}), 401
    if not m or not verify_passcode(passcode, name, m["passcode_hash"]):
        # Same error message for unknown-user vs wrong-passcode — don't
        # leak which member names exist beyond what /api/state already shows.
        return jsonify({"ok": False, "error": "wrong name or passcode", "code": 401, "reason": "bad_credentials"}), 401
    token = new_session_token()
    db.create_session(token, name)
    return jsonify({"ok": True, "name": name, "token": token,
                    "must_change": bool(m["must_change"]),
                    "is_admin": bool(m["is_admin"])})


@bp.route("/api/whoami", methods=["GET"])
def api_whoami():
    username, err = _require_token()
    if err: return err
    m = db.get_member_auth(username) or {}
    return jsonify({"ok": True, "name": username,
                    "must_change": bool(m.get("must_change")),
                    "is_admin": bool(m.get("is_admin")),
                    "linked": bool(m.get("google_email")),                              # a Google account is linked (the passcode is then retired)
                    "email_masked": _gid.mask_email(m.get("google_email")) or None,     # "s***@gmail.com": only the member themself ever sees even this
                    "pin_enabled": m.get("passcode_hash", NO_PIN) != NO_PIN})


@bp.route("/api/passcode/change", methods=["POST"])
def api_passcode_change():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    username, err = _require_token()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    new_pc = str(d.get("new") or "").strip()
    if not PASSCODE_RE.match(new_pc):
        return jsonify({"ok": False, "error": "new passcode must be exactly 4 digits", "code": 400, "reason": "passcode_invalid"}), 400
    if (db.get_member_auth(username) or {}).get("passcode_hash") == NO_PIN:
        # Setting a passcode would quietly bring the passcode way in back for a member who chose Google. Recovery is the admin's unlink, not this.
        return jsonify({"ok": False, "error": "This account signs in with Google.", "code": 403, "reason": "pin_retired"}), 403
    # No current-passcode check by design — user is already authenticated via
    # bearer token; for forced resets (must_change=1) the user can't verify
    # the current passcode anyway because the admin reset it without telling
    # them. Matches the user's stated UX: "no verification is needed".
    db.set_passcode(username, hash_passcode(new_pc, username), must_change=0)
    # Rotate all sessions for this user so any other tab is forced to re-login.
    db.delete_sessions_for_user(username)
    token = new_session_token()
    db.create_session(token, username)
    _push_if_hosted(f"passcode-change:{username}")
    return jsonify({"ok": True, "token": token, "must_change": False})


@bp.route("/api/admin/passcode/reset", methods=["POST"])
def api_admin_passcode_reset():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    username, err = _require_admin()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    target = (d.get("target_username") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "target_username required", "code": 400, "reason": "target_required"}), 400
    m = db.get_member_auth(target)
    if not m:
        return jsonify({"ok": False, "error": "target user not found", "code": 404, "reason": "member_not_found"}), 404
    if m.get("google_email") or m.get("passcode_hash") == NO_PIN:
        return jsonify({"ok": False, "error": "That member signs in with Google. Unlink their Google account first (it gives them a temporary passcode).", "code": 409, "reason": "member_signs_in_with_google"}), 409
    db.set_passcode(target, hash_passcode("1234", target), must_change=1)
    # Force the target to re-login with 1234 on whatever device they're on.
    db.delete_sessions_for_user(target)
    _push_if_hosted(f"admin-passcode-reset:{target}")
    return jsonify({"ok": True, "target": target, "reset_to": "1234", "must_change": True})


@bp.route("/api/admin/members", methods=["GET"])
def api_admin_members():
    username, err = _require_admin()
    if err: return err
    members = []
    for m in db.list_members_admin_view():
        g = m.pop("google_email", None)
        members.append({**m, "linked": bool(g), "email_masked": _gid.mask_email(g) or None})   # even the admin sees the address masked
    return jsonify({"ok": True, "members": members, **_joining_summary()})


# ════════════════════════════════════════════════════════════════════════════
# 1.6 GOOGLE SIGN-IN (Phase 3, 2026-11-21)
# ════════════════════════════════════════════════════════════════════════════
# The page shows Google's own button; Google hands the page a signed ID token; the page sends it here together with the nonce it was issued for this attempt.
# logic/google_identity.py checks the token; these routes decide what a good token MEANS:
#   /api/auth/google  sign in (a member whose Google account is linked), or hand back a CLAIM for a good account nobody has linked yet
#   /api/auth/claim   {ticket, name, passcode}: that member proves who they are once; the account is linked, the passcode retired in the same transaction, they are signed in
#   /api/auth/join    {ticket, name}: a NEW member joins with that Google account and a display name; no passcode exists (an admin can close joining; there is a member cap)
#   /api/auth/link    a signed-in member links their Google account; their passcode is retired in the same transaction
#   /api/auth/handoff, /api/auth/handoff/redeem   the installed app cannot sign in with Google itself (a WebView): the browser signs the member in and hands the app a one-time code
# Nothing above the hand-off works until GOOGLE_CLIENT_ID is set in the environment (503 google_not_configured). The hand-off does not need Google at all (it carries ANY signed-in
# member into the app). The devices list comes in a later release.

_NONCES = _gid.NonceStore()
_CLAIMS = _gid.ClaimStore()
_HANDOFFS = _handoff.HandoffStore()


def _claim_members():
    """The 'Which member are you?' list: every member by name, with why they can or cannot be picked. 'free': they have a passcode of their own and no Google account
    yet. 'linked': a Google account is already linked to them, so the name can never be picked for another one. 'temp': their passcode is only a temporary one (a
    known value, so it must not be enough to take a name): they sign in with it first, choose their own passcode, then switch. Names and one word each: no hash,
    no address."""
    out = []
    for r in db.list_members_for_claim():
        status = "linked" if (r["linked"] or not r["has_passcode"]) else ("temp" if r["must_change"] else "free")
        out.append({"name": r["username"], "status": status})
    return out


def _claim_locked():
    return jsonify({"ok": False, "error": "Too many wrong passcodes. Try again in about 10 minutes.", "code": 429, "reason": "claim_locked"}), 429


def _clean_member_name(raw):
    """A display name for a NEW member: white space trimmed and collapsed (a tab or a line break never survives), 1 to 30 characters, printable only. '' when it is not usable."""
    if not isinstance(raw, str):
        return ""
    s = " ".join(raw.split())
    return s if 1 <= len(s) <= 30 and s.isprintable() else ""


def _joining_open():
    """An admin can close joining (meta key joining_open = "0"); it is open until then."""
    return db.get_meta("joining_open", "1") != "0"


def _join_state():
    """Can a new member join right now? {allowed, why}: why is '' when allowed, 'closed' (an admin closed joining) or 'full' (the league is at MAX_MEMBERS). (Not named `reason`: that
    word is reserved for the machine reason of an ERROR, which the page has a sentence for.)"""
    if not _joining_open():
        return {"allowed": False, "why": "closed"}
    if db.count_members() >= MAX_MEMBERS:
        return {"allowed": False, "why": "full"}
    return {"allowed": True, "why": ""}


def _joining_summary():
    return {"joining_open": _joining_open(), "member_count": db.count_members(), "member_cap": MAX_MEMBERS}


def _google_refusal(e):
    """The reply for a refused token. The reason and detail go to the log (never the token); the caller gets a sentence and the machine reason."""
    _log(f"google sign-in refused: {e.reason} ({e.detail})", "warning")
    r = e.reason
    if r == "google_not_configured":
        return jsonify({"ok": False, "error": "Google sign-in is not set up yet.", "code": 503, "reason": "google_not_configured"}), 503
    if r == "google_unreachable":
        return jsonify({"ok": False, "error": "Could not reach Google. Please try again in a moment.", "code": 503, "reason": "google_unreachable"}), 503
    if r == "token_expired":
        return jsonify({"ok": False, "error": "That took too long. Please try again.", "code": 401, "reason": "token_expired"}), 401
    if r == "nonce_invalid":
        return jsonify({"ok": False, "error": "That sign-in has expired. Please try again.", "code": 401, "reason": "nonce_invalid"}), 401
    if r == "email_unverified":
        return jsonify({"ok": False, "error": "Google says that email address is not verified.", "code": 403, "reason": "email_unverified"}), 403
    return jsonify({"ok": False, "error": "Google sign-in could not be verified. Please try again.", "code": 401, "reason": "token_invalid"}), 401


def _verify_google_body(d):
    """(claims, None) for a good token, or (None, reply). The token is checked first and the nonce is spent only when the token is good, so a Google outage
    in the middle does not burn it (the page can send the same token again)."""
    if not isinstance(d, dict):
        d = {}
    nonce = d.get("nonce")
    try:
        claims = _gid.verify_id_token(d.get("credential"), audience=_gid.client_id(), expected_nonce=nonce)
        if not _NONCES.consume(nonce):
            raise _gid.GoogleAuthError("nonce_invalid", "not issued here, already used, or expired")
    except _gid.GoogleAuthError as e:
        return None, _google_refusal(e)
    return claims, None


@bp.route("/api/auth/config", methods=["GET"])
def api_auth_config():
    """What the sign-in screen needs: is Google sign-in set up (and our client id, which is public by design), and which members can still sign in with a passcode
    (`pin_members`, names only: the card offers a passcode chip to these and to nobody else; `pin_enabled` is whether there are any). Which names have no passcode is no secret:
    the member list is public, and a passcode sign-in for a switched member already answers `pin_retired`."""
    cid = _gid.client_id()
    pin_members = db.list_pin_members()
    return jsonify({"ok": True, "google": {"enabled": bool(cid), "client_id": cid or None}, "pin_enabled": len(pin_members) > 0, "pin_members": pin_members})


@bp.route("/api/auth/nonce", methods=["GET"])
def api_auth_nonce():
    """A single-use, ten-minute nonce for ONE sign-in attempt. The page gives it to Google, which puts it inside the token it signs."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    if not _gid.enabled():
        return jsonify({"ok": False, "error": "Google sign-in is not set up yet.", "code": 503, "reason": "google_not_configured"}), 503
    nonce = _NONCES.issue()
    if nonce is None:
        return jsonify({"ok": False, "error": "Too many sign-ins at once. Wait a minute and try again.", "code": 429, "reason": "rate_limited"}), 429
    return jsonify({"ok": True, "nonce": nonce})


@bp.route("/api/auth/google", methods=["POST"])
def api_auth_google():
    """Sign in with Google. Body {credential, nonce}. A linked member gets a session exactly as /api/login gives one. A good Google account that belongs to nobody
    yet is neither an error nor a dead end: {linked: false, claim: {ticket, members}} lets the member say who they are, ONCE, with their passcode (POST
    /api/auth/claim): no second trip to Google."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    claims, refusal = _verify_google_body(request.get_json(force=True, silent=True))
    if refusal: return refusal
    ident = db.get_identity("google", claims["sub"])
    if not ident:
        if _CLAIMS.locked(claims["sub"]):
            return _claim_locked()
        ticket = _CLAIMS.issue(claims["sub"], claims["email"])
        if ticket is None:
            return jsonify({"ok": False, "error": "Too many sign-ins at once. Wait a minute and try again.", "code": 429, "reason": "rate_limited"}), 429
        masked = _gid.mask_email(claims["email"])
        return jsonify({"ok": True, "linked": False, "email_masked": masked,
                        "claim": {"ticket": ticket, "expires_in": _CLAIMS.ttl, "email_masked": masked, "members": _claim_members(), "join": _join_state()}})
    m = db.get_member_auth(ident["member"]) or {}
    db.touch_identity("google", claims["sub"], claims["email"])
    token = new_session_token()
    db.create_session(token, ident["member"])
    return jsonify({"ok": True, "linked": True, "name": ident["member"], "token": token, "must_change": False,
                    "is_admin": bool(m.get("is_admin")), "email_masked": _gid.mask_email(claims["email"])})


@bp.route("/api/auth/claim", methods=["POST"])
def api_auth_claim():
    """Claim a member with a Google account that belongs to nobody yet. Body {ticket, name, passcode}: the ticket came with /api/auth/google (it stands for the
    Google account the SERVER verified: nothing the page sends can change which account that is), the name and passcode prove who the member is. On success the
    account is linked and the passcode retired in ONE transaction (db.link_identity) and the member is signed in, exactly as a linked Google sign-in would.
    Refused: a member who already has a Google account (409 member_taken: a name is linked to one account and is never offered again), one whose passcode is only
    a temporary one (409 temp_passcode: that value is known to everybody), a wrong passcode (401 bad_credentials with attempts_left; five wrong passcodes lock
    THAT GOOGLE ACCOUNT out of claiming for ten minutes, 429 claim_locked, however often it signs in again), a ticket that is unknown, used or older than ten minutes
    (401 claim_expired). The passcode is the only secret here, so this door is no weaker than /api/login and no stronger than the passcode itself."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    if not _gid.enabled():
        return jsonify({"ok": False, "error": "Google sign-in is not set up yet.", "code": 503, "reason": "google_not_configured"}), 503
    d = request.get_json(force=True, silent=True)
    d = d if isinstance(d, dict) else {}
    ticket = d.get("ticket")
    name = str(d.get("name") or "").strip()
    passcode = str(d.get("passcode") or "").strip()
    if not isinstance(ticket, str) or not ticket or not name or not PASSCODE_RE.match(passcode):
        return jsonify({"ok": False, "error": "ticket, name and 4-digit passcode required", "code": 400, "reason": "credentials_required"}), 400
    pending = _CLAIMS.peek(ticket)
    if not pending:
        return jsonify({"ok": False, "error": "That sign-in has expired. Tap Continue with Google to start again.", "code": 401, "reason": "claim_expired"}), 401
    if _CLAIMS.locked(pending["sub"]):
        return _claim_locked()
    m = db.get_member_auth(name)
    if not m:
        return jsonify({"ok": False, "error": "target user not found", "code": 404, "reason": "member_not_found"}), 404
    if m.get("google_email") or m["passcode_hash"] == NO_PIN:
        return jsonify({"ok": False, "error": "That member already has a Google account linked.", "code": 409, "reason": "member_taken"}), 409
    if m["must_change"]:
        return jsonify({"ok": False, "error": "That member has a temporary passcode. Sign in with it first, choose your own, then switch to Google.", "code": 409, "reason": "temp_passcode"}), 409
    if not verify_passcode(passcode, name, m["passcode_hash"]):
        left = _CLAIMS.wrong_passcode(ticket)
        if left is None:
            return jsonify({"ok": False, "error": "That sign-in has expired. Tap Continue with Google to start again.", "code": 401, "reason": "claim_expired"}), 401
        if left == 0:
            return _claim_locked()
        return jsonify({"ok": False, "error": "wrong passcode", "code": 401, "reason": "bad_credentials", "attempts_left": left}), 401
    spent = _CLAIMS.spend(ticket)                       # the passcode was right: the ticket is used up here, whatever happens next
    if not spent:
        return jsonify({"ok": False, "error": "That sign-in has expired. Tap Continue with Google to start again.", "code": 401, "reason": "claim_expired"}), 401
    try:
        db.link_identity(name, "google", spent["sub"], spent["email"])
    except LookupError:
        return jsonify({"ok": False, "error": "target user not found", "code": 404, "reason": "member_not_found"}), 404
    except ValueError as e:
        if str(e) == "in_use":
            return jsonify({"ok": False, "error": "That Google account already belongs to another member.", "code": 409, "reason": "google_account_in_use"}), 409
        return jsonify({"ok": False, "error": "That member already has a Google account linked.", "code": 409, "reason": "member_taken"}), 409
    token = new_session_token()
    db.create_session(token, name)
    _log(f"google account claimed {name}")
    return jsonify({"ok": True, "linked": True, "claimed": True, "name": name, "token": token, "must_change": False,
                    "is_admin": bool(m.get("is_admin")), "email_masked": _gid.mask_email(spent["email"])})


@bp.route("/api/auth/join", methods=["POST"])
def api_auth_join():
    """Join the league with a Google account that belongs to nobody yet. Body {ticket, name}: the ticket came with /api/auth/google (it stands for the Google account the
    SERVER verified), the name is the display name the new member chose. NO passcode is chosen or kept: the member's only way in is that Google account. The member and the
    identity are created together in ONE transaction (db.create_member_with_identity), so there is never a member nobody can sign in as. Refused: an unknown or old ticket
    (401 claim_expired), a name that is not 1-30 printable characters (400 name_invalid), a name somebody already has, compared case-insensitively (409 name_taken: the ticket
    is NOT used up, so the member can try another), joining closed by the admin (403 joining_closed), a full league (409 members_full), a Google account that already belongs to a
    member (409 google_account_in_use). Shares the 60/min sign-in limiter."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    if not _gid.enabled():
        return jsonify({"ok": False, "error": "Google sign-in is not set up yet.", "code": 503, "reason": "google_not_configured"}), 503
    d = request.get_json(force=True, silent=True)
    d = d if isinstance(d, dict) else {}
    ticket = d.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        return jsonify({"ok": False, "error": "ticket and name required", "code": 400, "reason": "credentials_required"}), 400
    name = _clean_member_name(d.get("name"))
    if not name:
        return jsonify({"ok": False, "error": "name must be 1-30 characters", "code": 400, "reason": "name_invalid"}), 400
    pending = _CLAIMS.peek(ticket)
    if not pending:
        return jsonify({"ok": False, "error": "That sign-in has expired. Tap Continue with Google to start again.", "code": 401, "reason": "claim_expired"}), 401
    state = _join_state()
    if not state["allowed"]:
        if state["why"] == "full":
            return jsonify({"ok": False, "error": "The league is full.", "code": 409, "reason": "members_full"}), 409
        return jsonify({"ok": False, "error": "New members cannot join right now. Ask the admin.", "code": 403, "reason": "joining_closed"}), 403
    try:
        db.create_member_with_identity(name, "google", pending["sub"], pending["email"], MAX_MEMBERS)
    except ValueError as e:
        why = str(e)
        if why == "name_taken":
            return jsonify({"ok": False, "error": "Someone already has that name. Try another.", "code": 409, "reason": "name_taken"}), 409
        if why == "in_use":
            return jsonify({"ok": False, "error": "That Google account already belongs to another member.", "code": 409, "reason": "google_account_in_use"}), 409
        return jsonify({"ok": False, "error": "The league is full.", "code": 409, "reason": "members_full"}), 409
    _CLAIMS.spend(ticket)                                # the member exists: the ticket is used up (a name that was refused left it alone)
    db.upsert_member(name, {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
    token = new_session_token()
    db.create_session(token, name)
    _log(f"google account joined the league as {name}")
    return jsonify({"ok": True, "linked": True, "joined": True, "name": name, "token": token, "must_change": False,
                    "is_admin": False, "email_masked": _gid.mask_email(pending["email"])})


@bp.route("/api/auth/handoff", methods=["POST"])
def api_auth_handoff():
    """A browser the member is signed in on asks for a ONE-TIME CODE that carries them into the installed app. Body {challenge}: base64url(SHA-256(verifier)) of a secret the APP
    made and kept (the browser never sees the secret, only this hash). Authorization: Bearer <the browser's session>. Answers {code, expires_in}: the page puts the code in the link
    that opens the app (bclfantasy://auth?code=...). The code is single-use, lives two minutes, and is bound to THIS member and this challenge: another app that catches the link
    cannot use it without the verifier (POST /api/auth/handoff/redeem). Works for any signed-in member (Google or passcode). 401 auth_required / session_ended, 400 bad_request
    (not a 43-character base64url challenge), 429 rate_limited (the store is full). Shares the 60/min sign-in limiter."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    username, err = _require_token()
    if err: return err
    d = request.get_json(force=True, silent=True)
    d = d if isinstance(d, dict) else {}
    challenge = d.get("challenge")
    if not isinstance(challenge, str) or not _handoff.CHALLENGE_RE.match(challenge):
        return jsonify({"ok": False, "error": "challenge must be the 43-character hash the app made", "code": 400, "reason": "bad_request"}), 400
    code = _HANDOFFS.issue(username, challenge)
    if not code:
        return jsonify({"ok": False, "error": "Too many sign-ins at once. Wait a minute and try again.", "code": 429, "reason": "rate_limited"}), 429
    _log(f"hand-off code issued for {username}")        # the code itself is never logged
    return jsonify({"ok": True, "code": code, "expires_in": _HANDOFFS.ttl})


@bp.route("/api/auth/handoff/redeem", methods=["POST"])
def api_auth_handoff_redeem():
    """The APP gives back the code the browser handed it, with the verifier whose hash it sent: body {code, verifier}. No session is needed (the app has none yet): the proof is the
    verifier. On success the app gets its OWN new session for the member the code was issued to (the browser's session is untouched), exactly as a sign-in answers. The code is used up
    by ANY attempt, so a wrong verifier burns it. Refused: a code or verifier of the wrong shape (400 credentials_required: nothing is used up), an unknown, used, expired or
    wrongly proven code, or a member that no longer exists (all 401 handoff_expired: the reply never says which). Shares the 60/min sign-in limiter."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    d = request.get_json(force=True, silent=True)
    d = d if isinstance(d, dict) else {}
    code, verifier = d.get("code"), d.get("verifier")
    if not (isinstance(code, str) and _handoff.CODE_RE.match(code) and isinstance(verifier, str) and _handoff.VERIFIER_RE.match(verifier)):
        return jsonify({"ok": False, "error": "code and verifier required", "code": 400, "reason": "credentials_required"}), 400
    username = _HANDOFFS.redeem(code, verifier)
    m = db.get_member_auth(username) if username else None
    if not m:
        return jsonify({"ok": False, "error": "That browser sign-in has expired. Start again from the app.", "code": 401, "reason": "handoff_expired"}), 401
    token = new_session_token()
    db.create_session(token, username)
    _log(f"app signed in as {username} through the browser hand-off")
    return jsonify({"ok": True, "linked": bool(m.get("google_email")), "name": username, "token": token, "must_change": bool(m.get("must_change")),
                    "is_admin": bool(m.get("is_admin")), "email_masked": _gid.mask_email(m.get("google_email")) or None})


@bp.route("/api/auth/link", methods=["POST"])
def api_auth_link():
    """A signed-in member switches to Google: body {credential, nonce}, Authorization: Bearer <their session>. Links the Google account and retires their passcode in one
    transaction. 409 google_account_in_use when that account belongs to another member; 409 already_linked when they already have a different one."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    username, err = _require_token()
    if err: return err
    claims, refusal = _verify_google_body(request.get_json(force=True, silent=True))
    if refusal: return refusal
    try:
        res = db.link_identity(username, "google", claims["sub"], claims["email"])
    except LookupError:
        return jsonify({"ok": False, "error": "member not found", "code": 404, "reason": "member_not_found"}), 404
    except ValueError as e:
        if str(e) == "in_use":
            return jsonify({"ok": False, "error": "That Google account already belongs to another member.", "code": 409, "reason": "google_account_in_use"}), 409
        return jsonify({"ok": False, "error": "You already have a Google account linked. Ask the admin to unlink it first.", "code": 409, "reason": "already_linked"}), 409
    _log(f"google account {'linked to' if res['linked_now'] else 're-confirmed for'} {username}")
    return jsonify({"ok": True, "linked": True, "name": username, "email_masked": _gid.mask_email(claims["email"])})


@bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Sign THIS device out for real: the session token stops working at once. Safe to repeat; no token, or one the server does not know, is fine."""
    re_ = _check_rate(_auth_limiter)
    if re_: return re_
    db.delete_session(get_bearer_token())
    return jsonify({"ok": True})


@bp.route("/api/admin/auth/unlink", methods=["POST"])
def api_admin_auth_unlink():
    """Recovery (admin only): remove a member's Google link, give them the temporary passcode 1234 (they must change it at their next sign-in) and end their
    sessions. For a member who lost their Google account or cannot sign in with it."""
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    username, err = _require_admin()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    target = (d.get("target_username") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "target_username required", "code": 400, "reason": "target_required"}), 400
    if not db.get_member_auth(target):
        return jsonify({"ok": False, "error": "target user not found", "code": 404, "reason": "member_not_found"}), 404
    own = target == username                              # an admin who unlinks THEMSELVES stays signed in on this device (and picks a new passcode there and then)
    if not db.unlink_identity(target, hash_passcode("1234", target), keep_token=get_bearer_token() if own else None):
        return jsonify({"ok": False, "error": "That member has no Google account linked.", "code": 409, "reason": "not_linked"}), 409
    _log(f"admin {username} unlinked the google account of {target}")
    return jsonify({"ok": True, "target": target, "reset_to": "1234", "must_change": True, "self": own})


@bp.route("/api/admin/joining", methods=["POST"])
def api_admin_joining():
    """Admin only: open or close joining. Body {open: true|false}. Closing stops NEW members joining with Google (403 joining_closed); members who are already in are untouched."""
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    username, err = _require_admin()
    if err: return err
    d = request.get_json(force=True, silent=True)
    d = d if isinstance(d, dict) else {}
    if not isinstance(d.get("open"), bool):
        return jsonify({"ok": False, "error": "open must be true or false", "code": 400, "reason": "bad_request"}), 400
    db.set_meta("joining_open", "1" if d["open"] else "0")
    _log(f"admin {username} {'opened' if d['open'] else 'closed'} joining")
    return jsonify({"ok": True, **_joining_summary()})


@bp.route("/api/admin/health", methods=["GET"])
def api_admin_health():
    """INF-2: admin-only system health — PAT validity + data freshness. Lets an
    admin catch a silently-expired GitHub token (which stops writes persisting)
    before it bites, and see when data last changed."""
    username, err = _require_admin()
    if err: return err
    comp = _comp()
    out = {"ok": True, "hosted": _IS_HOSTED, "competition": comp,
           "last_saved": db.get_meta_comp("_saved", comp, "never"),
           "last_rollover": db.get_meta_comp("_last_rollover", comp, "never"),
           "scraping": "disabled until SA20 (Phase 3b-4)"}
    if _IS_HOSTED:
        pok, pmsg = cloud_sync.check_token(log=_log)
        out["pat_ok"] = pok
        out["pat_msg"] = pmsg
    else:
        out["pat_ok"] = None
        out["pat_msg"] = "local mode — PAT only used in HOSTED"
    return jsonify(out)


def _admin_import(slug, comp, d):
    """Admin data import for one competition (action "import" of
    POST /api/admin/competition). Validates the uploaded roster and/or fixture
    list (logic/competition_import.py), then upserts them in one transaction
    (db.import_competition_data). Safe by default: unless the body says
    "dry_run": false it only REPORTS what would change (the DAO rolls back).
    Returns the Flask response. (Body size is capped app-wide by MAX_CONTENT_LENGTH, 1 MB;
    a roster plus a schedule is about 25 KB.)"""
    if comp["status"] == "completed":
        return jsonify({"ok": False, "code": 409,
                        "error": "this competition is completed and read-only - reopen it before importing"}), 409
    players_raw, schedule_raw = d.get("players"), d.get("schedule")
    if players_raw is None and schedule_raw is None:
        return jsonify({"ok": False, "error": "nothing to import - send players and/or schedule", "code": 400}), 400
    dry = d.get("dry_run", True) is not False
    budget, xi, *_ = _comp_cfg(slug)
    valid_teams = comp.get("valid_teams") or []

    out = {"ok": True, "dry_run": dry, "slug": slug}
    errors, warnings, prow, mrow, meta = [], [], None, None, {}
    # The week-1 date can be filled in by an import when the competition was created without
    # one, but an existing date is never changed (it would shift every week number).
    anchor = _ci.parse_anchor(comp.get("week1_anchor_utc"))
    comp_updates, anchor_in = {}, d.get("week1_anchor_utc")
    if anchor_in:
        if anchor:
            errors.append("the week-1 date is already set (%s) and cannot be changed by an import"
                          % anchor.date().isoformat())
        else:
            anchor = _ci.parse_anchor(anchor_in)
            if anchor is None:
                errors.append("week1_anchor_utc is not a valid date")
            else:
                comp_updates["week1_anchor_utc"] = anchor.isoformat()
    needs_anchor = schedule_raw is not None and anchor is None
    if players_raw is not None:
        r = _ci.parse_players(players_raw, budget_total=budget, xi_size=xi, valid_teams=valid_teams)
        errors += ["players: " + e for e in r["errors"]]
        warnings += ["players: " + w for w in r["warnings"]]
        prow, out["players"] = r["rows"], (r["stats"] or {})
    if schedule_raw is not None:
        r = _ci.parse_schedule(schedule_raw, slug=slug, valid_teams=valid_teams, anchor=anchor)
        errors += ["schedule: " + e for e in r["errors"]]
        warnings += ["schedule: " + w for w in r["warnings"]]
        mrow, meta, out["matches"] = r["rows"], r["meta"], (r["stats"] or {})
    if errors:
        # Counts built from only the rows that passed would mislead, so an error response
        # carries just the errors and warnings (plus needs_anchor so the UI can offer a date picker).
        return jsonify({"ok": False, "dry_run": dry, "slug": slug, "code": 400, "needs_anchor": needs_anchor,
                        "errors": errors, "warnings": warnings}), 400
    warnings += _ci.cross_check(prow, mrow)

    # Fill in competition metadata the files imply, but never overwrite what an admin already set.
    if not valid_teams:
        teams = {r[2] for r in (prow or [])} | {t for m in (mrow or []) for t in m["teams"]}
        if teams:
            comp_updates["valid_teams_json"] = _json.dumps(sorted(teams))
    for key in ("series_id", "series_slug", "year"):
        if meta.get(key) is not None and not comp.get(key):
            comp_updates[key] = meta[key] if key == "year" else str(meta[key])

    try:
        rep = db.import_competition_data(slug, players=prow, matches=mrow,
                                         comp_updates=comp_updates, dry_run=dry)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e), "code": 404}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "code": 409}), 409

    if rep["players"]:
        out["players"] = {**out.get("players", {}), **rep["players"]}
        if rep["players"]["not_in_file"]:
            warnings.append("%d player(s) already in the database are not in this file (they are kept): %s"
                            % (len(rep["players"]["not_in_file"]), ", ".join(rep["players"]["not_in_file"][:8])
                               + (" ..." if len(rep["players"]["not_in_file"]) > 8 else "")))
        if rep["players"]["updated"] and rep["selections_with_teams"]:
            warnings.append("%d member(s) already have a saved team - changed prices or roles apply to their "
                            "next save" % rep["selections_with_teams"])
    if rep["matches"]:
        out["matches"] = {**out.get("matches", {}), **rep["matches"]}
        if rep["matches"]["locked"]:
            warnings.append("%d match(es) are already live or completed and were left untouched"
                            % rep["matches"]["locked"])
    out.update({"competition": rep["competition"], "status": rep["status"],
                "selections_with_teams": rep["selections_with_teams"], "warnings": warnings})
    if not dry:
        _push_if_hosted(f"comp-import:{slug}")
        _log("admin import into %s: players %s, matches %s, competition %s" % (
            slug, {k: rep["players"][k] for k in ("inserted", "updated", "unchanged")} if rep["players"] else "-",
            {k: rep["matches"][k] for k in ("inserted", "updated", "unchanged", "locked")} if rep["matches"] else "-",
            list(rep["competition"]) or "-"))
    return jsonify(out)


def _admin_schedule(slug, comp, d):
    """Action "schedule" of POST /api/admin/competition (Phase 1.5): store a competition's lock schedule, the moments each week's XI locks and the
    drafts become the teams that play. The locks are worked out from the fixture list (the same file the import takes): week 1 locks 60 minutes before
    the opener, every later week on the Monday 14:00 UTC before its first match (logic/rollover_plan.py). Safe by default: unless the body says
    "dry_run": false it only REPORTS (the DAO rolls back). "auto_activate": true lets an upcoming competition start and activate itself at its week-1
    lock; leave the key out to keep the setting as it is. A week whose handover has already run keeps its lock time. Returns the Flask response."""
    if comp["status"] == "completed":
        return jsonify({"ok": False, "code": 409, "error": "this competition is completed and read-only - reopen it before scheduling"}), 409
    schedule_raw = d.get("schedule")
    if schedule_raw is None:
        return jsonify({"ok": False, "code": 400, "error": "send the fixture list as `schedule` (the same file the import takes)"}), 400
    auto = d.get("auto_activate")
    if auto is not None and not isinstance(auto, bool):
        return jsonify({"ok": False, "code": 400, "error": "auto_activate must be true or false"}), 400
    dry = d.get("dry_run", True) is not False
    anchor = _ci.parse_anchor(comp.get("week1_anchor_utc"))
    if anchor is None:
        return jsonify({"ok": False, "code": 400, "error": "the competition has no week-1 date yet - import its fixtures first"}), 400
    r = _ci.parse_schedule(schedule_raw, slug=slug, valid_teams=comp.get("valid_teams") or [], anchor=anchor)
    if r["errors"]:
        return jsonify({"ok": False, "dry_run": dry, "slug": slug, "code": 400, "errors": ["schedule: " + e for e in r["errors"]],
                        "warnings": ["schedule: " + w for w in r["warnings"]]}), 400
    firsts, counts = {}, {}
    for m in r["rows"]:
        wk, t = m["week_no"], m["start_utc"]
        firsts[wk] = min(t, firsts.get(wk, t)); counts[wk] = counts.get(wk, 0) + 1
    built = _rp.build_schedule(firsts)
    if built["errors"]:
        return jsonify({"ok": False, "dry_run": dry, "slug": slug, "code": 400, "errors": ["schedule: " + e for e in built["errors"]]}), 400
    with db._read() as con:    # the fixtures already stored must sit in the same weeks as this file says, or the locks would belong to other weeks
        stored = {x["id"]: x["week_no"] for x in con.execute("SELECT id, week_no FROM matches WHERE competition_id=%s", (slug,)).fetchall()}
    wrong = sorted("%s (stored in week %s, this file says week %s)" % (m["id"], stored[m["id"]], m["week_no"])
                   for m in r["rows"] if m["id"] in stored and stored[m["id"]] != m["week_no"])
    if wrong:
        return jsonify({"ok": False, "dry_run": dry, "slug": slug, "code": 409,
                        "error": "the matches stored for this competition are in different weeks than this file puts them: " + "; ".join(wrong[:6])
                                 + (" ..." if len(wrong) > 6 else "") + ". Re-import the fixtures first."}), 409
    file_ids = {m["id"] for m in r["rows"]}
    warnings = ["schedule: " + w for w in r["warnings"]]
    if not stored:
        warnings.append("no matches are stored for this competition yet: import the fixtures too, or the weeks will have no games")
    elif file_ids - set(stored):
        warnings.append("%d match(es) in this file are not stored for this competition yet (import the fixtures)" % len(file_ids - set(stored)))
    elif set(stored) - file_ids:
        warnings.append("%d stored match(es) are not in this file" % len(set(stored) - file_ids))
    try:
        rep = db.set_schedule(slug, built["rows"], auto_activate=auto, dry_run=dry)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e), "code": 404}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "code": 409}), 409
    sast = timezone(timedelta(hours=2))
    weeks = [{"week_no": w["week_no"], "matches": counts.get(w["week_no"], 0), "first_match_utc": w["first_match_utc"], "lock_utc": w["lock_at_utc"],
              "lock_sast": _rp.parse_utc(w["lock_at_utc"]).astimezone(sast).strftime("%a %d %b %Y %H:%M SAST")} for w in rep["schedule"]]
    if not dry:
        _push_if_hosted(f"comp-schedule:{slug}")
        _log("admin schedule for %s: %d weeks, changed %s, already run %s, auto_activate %s" % (slug, rep["weeks"], rep["changed_weeks"], rep["already_run"], rep["auto_activate"]))
    return jsonify({"ok": True, "dry_run": dry, "slug": slug, "weeks": weeks, "changed_weeks": rep["changed_weeks"], "already_run": rep["already_run"],
                    "auto_activate": rep["auto_activate"], "status": comp["status"], "warnings": warnings})


@bp.route("/api/admin/competition", methods=["POST"])
def api_admin_competition():
    """Admin-only competition lifecycle: create / import / schedule / activate / reopen / close.

    Body: {"action": "...", "slug": "...", ...}
      create  : + name, week1_anchor_utc, budget_total, xi_size, max_weeks,
                series_id, series_slug, year, valid_teams[]
      schedule: + schedule (the fixture list, as for import), dry_run (default TRUE), auto_activate (true/false; omit to keep it).
                Stores the weekly lock schedule (week 1 = opener - 60 min, later weeks Monday 14:00 UTC) that drives the automatic weekly
                handover; see _admin_schedule and logic/rollover_plan.py.
      import  : + players (list of {id,name,team,price,role}) and/or schedule
                ({"matches":[...]} as in data/<slug>/schedule.json), dry_run
                (default TRUE - writes only when "dry_run": false). Upserts the
                roster / fixtures in one transaction and returns a change
                report; 400 {errors[], warnings[]} if validation fails, 409 if
                the competition is completed. See logic/competition_import.py.
      activate: status -> 'active'  (becomes the default competition; picks open)
      reopen  : status -> 'upcoming'
      close   : status -> 'completed', champion = body.champion OR the
                leaderboard #1 for this competition. On a tie for #1, returns
                409 {tie:true, tied:[...]} so the UI can ask the admin to pick.
    """
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    try:
        username, err = _require_admin()
        if err: return err
        d = request.get_json(force=True, silent=True) or {}
        action = (d.get("action") or "").strip()
        slug = (d.get("slug") or "").strip()
        if not slug:
            return jsonify({"ok": False, "error": "slug required", "code": 400}), 400

        if action == "create":
            if db.get_competition(slug):
                return jsonify({"ok": False, "error": f"competition '{slug}' already exists", "code": 400}), 400
            db.create_competition(
                slug, (d.get("name") or slug).strip(),
                status=d.get("status", "upcoming"),
                format=d.get("format", "T20"),
                budget_total=float(d.get("budget_total", 100.0)),
                xi_size=int(d.get("xi_size", 11)),
                max_weeks=int(d.get("max_weeks", 10)),
                week1_anchor_utc=d.get("week1_anchor_utc") or "1970-01-01T00:00:00+00:00",
                series_id=d.get("series_id"), series_slug=d.get("series_slug"),
                year=d.get("year"), valid_teams=d.get("valid_teams", []),
            )
            _push_if_hosted(f"comp-create:{slug}")
            return jsonify({"ok": True, "action": "create", "slug": slug})

        comp = db.get_competition(slug)
        if not comp:
            return jsonify({"ok": False, "error": f"competition '{slug}' not found", "code": 404}), 404

        if action == "import":
            return _admin_import(slug, comp, d)

        if action == "schedule":
            return _admin_schedule(slug, comp, d)

        if action in ("activate", "reopen"):
            new_status = "active" if action == "activate" else "upcoming"
            db.set_competition_status(slug, new_status)
            _push_if_hosted(f"comp-{action}:{slug}")
            return jsonify({"ok": True, "action": action, "slug": slug, "status": new_status})

        if action == "close":
            champion = (d.get("champion") or "").strip()
            if not champion:
                standings = db.get_leaderboard(competition_id=slug).get("standings", [])
                if not standings:
                    return jsonify({"ok": False, "error": "no standings yet; pass champion explicitly", "code": 400}), 400
                top = standings[0]["total_pts"]
                tied = [s["name"] for s in standings if s["total_pts"] == top]
                if len(tied) > 1:
                    return jsonify({"ok": False, "tie": True, "tied": tied,
                                    "error": f"tie for #1 ({', '.join(tied)}) — pass champion explicitly", "code": 409}), 409
                champion = standings[0]["name"]
            db.set_champion(slug, champion)
            _push_if_hosted(f"comp-close:{slug}:{champion}")
            return jsonify({"ok": True, "action": "close", "slug": slug, "champion": champion, "status": "completed"})

        return jsonify({"ok": False, "error": f"unknown action '{action}'", "code": 400}), 400
    except RequestEntityTooLarge:
        return jsonify({"ok": False, "error": "upload too large (the limit is 1 MB)", "code": 413}), 413


# ════════════════════════════════════════════════════════════════════════════
# 2. STATE
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/state", methods=["GET"])
def api_get_state():
    recv = datetime.now(timezone.utc)   # when the request reached us (see server_recv_utc below)
    comp = _comp()
    # The state tag is read BEFORE the data, and the 304 is decided before any of the heavy queries. Read in this order, a save that lands in between can only
    # make the tag OLDER than the data (the next poll then refetches, harmlessly). Read after the data (as it used to be) the tag could end up NEWER than
    # the data, and a page holding that tag would never refetch what it missed.
    etag = db.get_etags(competition_id=comp)["state"]
    if request.headers.get("If-None-Match") == etag: return "", 304, {"ETag": etag}
    state = db.get_state(competition_id=comp); state["_saved"] = etag   # the body carries the very tag the header does
    # UX-2: deadline info so the client can show a live countdown + lock state.
    crow = db.get_competition(comp) or {}
    dh = crow.get("deadline_hour", DEADLINE_HOUR); dm = crow.get("deadline_min", DEADLINE_MIN)
    nl, managed = None, {}
    ls = db.get_lock_state(comp)   # a competition with a stored lock schedule: the schedule and the ledger decide (None when it has none)
    if ls is not None:
        # next_lock_utc is the lock the countdown points at. It may already have passed: the transition has not run yet, picking stays locked
        # (the page treats a passed next_lock_utc as locked) and the lock moves on when the transition runs (the state tag changes with it).
        nl = _rp.iso(ls["next_lock"]) if ls["next_lock"] else None
        managed = {"managed": True, "locked": ls["locked"], "awaiting": ls["awaiting"], "season_over": ls["season_over"],
                   "applied_to": ls["applied_to"], "held": ls["held"], "reason": ls["reason"]}
    elif crow.get("status") != "completed":
        now = datetime.now(timezone.utc)
        try:
            anchor = datetime.fromisoformat(crow.get("week1_anchor_utc") or "")
            if anchor.tzinfo is None: anchor = anchor.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            anchor = None
        if anchor and now < anchor:
            nl = anchor.isoformat()  # pre-season: the draft locks at the week-1 anchor
        else:
            nl = (last_monday_deadline(now, dh, dm) + timedelta(days=7)).isoformat()
    state["deadline"] = {"hour": dh, "min": dm, "next_lock_utc": nl, "status": crow.get("status", ""), **managed}
    state["competition"] = comp   # lets the browser discard a reply that belongs to another competition
    # The server's clock when the request arrived and when this reply leaves (the page cancels out the time spent here, as NTP does). The 304 path
    # above never carries a body, so /api/poll carries the clock too.
    state["server_recv_utc"] = recv.isoformat(); state["server_now_utc"] = datetime.now(timezone.utc).isoformat()
    resp = jsonify(state); resp.headers["ETag"] = etag; return resp



# ════════════════════════════════════════════════════════════════════════════
# 3. PLAYERS
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/players", methods=["GET"])
def api_players():
    players = db.get_players(competition_id=_comp())
    return jsonify({"players": players, "by_id": {p["id"]: p for p in players},
                    "by_name": {p["name"].lower(): p for p in players}, "ok": True})

@bp.route("/api/resolve-player", methods=["POST"])
def api_resolve_player():
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    _u, err = _require_token()
    if err: return err
    d = request.get_json(force=True, silent=True) or {}
    query = (d.get("query") or "").strip(); team = (d.get("team") or "").strip() or None
    if not query: return jsonify({"error": "query required", "code": 400}), 400
    with db._read() as con:
        match = resolve_player_id(con, query, team_hint=team, competition_id=_comp())
    if not match: return jsonify({"ok": False, "error": "No match", "input": query}), 404
    tier = match.pop("_match_tier", None)
    return jsonify({"ok": True, "input": query, "match_tier": tier,
                    "resolved": {k: match[k] for k in ("id","name","team","role","price")}})

@bp.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    wp = request.args.get("week", "").strip()
    wn = int(wp) if wp.isdigit() else None
    return jsonify(db.get_leaderboard(week_no=wn, competition_id=_comp()))


# ════════════════════════════════════════════════════════════════════════════
# 4. HISTORY / POINTS
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/history/<n>", methods=["GET"])
def api_history(n):
    if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
    return jsonify(db.get_history(n, competition_id=_comp()))



# ════════════════════════════════════════════════════════════════════════════
# 5. SAVE
# ════════════════════════════════════════════════════════════════════════════

def _picks_lock(comp):
    """(locked, reason) for picking in a competition right now. A stored lock schedule decides (fail closed from a lock until its handover has run);
    a competition with none keeps the old Monday-14:00 rule. One definition, shared by saving and by "Keep this XI"."""
    ls = db.get_lock_state(comp)
    if ls is not None:
        return ls["locked"], ls["reason"]
    crow = db.get_competition(comp) or {}
    _b, _x, _mw, dh, dm = _comp_cfg(comp)
    return selections_locked(datetime.now(timezone.utc), crow.get("status", ""), crow.get("week1_anchor_utc", ""), dh, dm,
                             db.get_meta_comp("_last_rollover", comp, ""))


def _stated_week(body):
    """(week, error) for the optional "week" a save or keep request states: the week the page says its draft is FOR. None when it is not stated."""
    v = body.get("week") if isinstance(body, dict) else None
    if v is None:
        return None, None
    if isinstance(v, bool) or not isinstance(v, (int, str)) or (isinstance(v, str) and not v.strip().isdigit()):
        return None, "week must be a whole number"
    return int(v), None


def _week_moved(result):
    """The 409 a request gets when the draft it states is no longer the draft being collected (a handover ran since the page loaded): nothing was written."""
    cw, dw = result["week_no"], result.get("draft_week")
    return jsonify({"error": f"The week has moved on: week {cw} is now being played" + (f", so drafts are now for week {dw}." if dw else "."),
                    "code": 409, "reason": "week_changed", "current_week": cw, "draft_week": dw}), 409


@bp.route("/api/save-next-week/<n>", methods=["POST"])
def api_save_next_week(n):
    re_=_check_rate(_write_limiter)
    if re_: return re_
    if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
    _self,err=_require_self(n)
    if err: return err
    comp=_comp(); budget,xi,_mw,dh,dm=_comp_cfg(comp)
    locked,lreason=_picks_lock(comp)
    if locked:
        return jsonify({"error":lreason,"locked":True,"code":423,"reason":"locked"}),423
    d=request.get_json(force=True,silent=True)
    if not isinstance(d,dict): return jsonify({"error":"expected JSON object","code":400}),400
    week,werr=_stated_week(d)
    if werr: return jsonify({"error":werr,"code":400}),400
    team=d.get("team",[]); cap=d.get("cap"); vc=d.get("vc")
    if not isinstance(team,list): return jsonify({"error":"team must be list","code":400}),400
    rlog=[]
    if team:
        # Canonical ids (all the page ever sends) pass straight through: no database work at all. Only a name, a typo or an old id is looked up, in THIS
        # competition's players, on a pooled connection. Nothing here writes: a save that is then refused (422) leaves the stored draft exactly as it was.
        if needs_resolving(*team,cap,vc):
            with db._read() as con:
                team,rlog=resolve_id_list(con,team,competition_id=comp)
                if cap and not _ID_RE.match(str(cap)):
                    m=resolve_player_id(con,cap,competition_id=comp)
                    if m: cap=m["id"]
                if vc and not _ID_RE.match(str(vc)):
                    m=resolve_player_id(con,vc,competition_id=comp)
                    if m: vc=m["id"]
        else:
            team,rlog=resolve_id_list(None,team,competition_id=comp)
    ok,verr,total_cost=db.validate_selection(team,cap,vc,budget,xi,competition_id=comp)
    if not ok:
        return jsonify({"error":verr,"total_cost":total_cost,"budget":budget,"code":422,"reason":"invalid_team"}),422
    result=db.save_next_week(n,team,cap,vc,competition_id=comp,expect_week=week)
    if result.get("conflict"): return _week_moved(result)      # the week moved on while the page was open: nothing was written
    _push_if_hosted(f"save-next-week:{comp}:{n}:w{result['week_no']}")
    return jsonify({"ok":True,"week_no":result["week_no"],"touched_at":result["touched_at"],"total_cost":total_cost,"resolution_log":rlog})

@bp.route("/api/keep-xi/<n>", methods=["POST"])
def api_keep_xi(n):
    """"Keep this XI" (2026-11-20, Phase 1.6): the member confirms the draft they already have, usually last week's XI carried over, without changing it.
    The draft is only marked touched, which is what a reminder looks at ("no changes" = no save and no keep since the week began). Same lock rules as a
    save; optional body {"week": <the week the page believes is current>} (409 week_changed when it has moved on)."""
    re_=_check_rate(_write_limiter)
    if re_: return re_
    if not n or len(n)>30: return jsonify({"error":"invalid name","code":400}),400
    _self,err=_require_self(n)
    if err: return err
    comp=_comp()
    locked,lreason=_picks_lock(comp)
    if locked:
        return jsonify({"error":lreason,"locked":True,"code":423,"reason":"locked"}),423
    week,werr=_stated_week(request.get_json(force=True,silent=True))
    if werr: return jsonify({"error":werr,"code":400}),400
    result=db.keep_next_week(n,competition_id=comp,expect_week=week)
    if result.get("conflict"): return _week_moved(result)
    if result.get("nothing"):
        return jsonify({"error":"You have no team to keep yet. Pick your players first.","code":409,"reason":"nothing_to_keep"}),409
    _push_if_hosted(f"keep-xi:{comp}:{n}:w{result['week_no']}")
    return jsonify({"ok":True,"week_no":result["week_no"],"touched_at":result["touched_at"]})

@bp.route("/api/member/<n>", methods=["PUT"])
def api_member(n):
    re_=_check_rate(_write_limiter)
    if re_: return re_
    if not n or len(n)>30: return jsonify({"error":"name 1-30 chars","code":400}),400
    _self,err=_require_self(n)
    if err: return err
    d=request.get_json(force=True,silent=True)
    if not isinstance(d,dict): return jsonify({"error":"Invalid JSON","code":400}),400
    comp=_comp()
    db.upsert_member(n,d,competition_id=comp)
    _push_if_hosted(f"member:{comp}:{n}")
    return jsonify({"ok":True})



# ════════════════════════════════════════════════════════════════════════════
# 6. SCORING
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/recalculate-points", methods=["POST"])
def api_recalculate_points():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    _u,err=_require_admin()
    if err: return err
    comp=_comp()
    n=db.recalculate_points(competition_id=comp); wp=db.update_week_points(competition_id=comp); pp=db.update_player_season_pts(competition_id=comp)
    _push_if_hosted(f"recalc:{comp}:rows={n}")
    return jsonify({"ok":True,"rows_updated":n,"week_pts_rows":wp,"player_pts_updated":pp,
                    "message":f"Recalculated {n} player-match rows, {wp} week_pts rows, {pp} player season_pts."})



# ════════════════════════════════════════════════════════════════════════════
# 6c. MATCH CENTRE  (Phase 9.1)
#
# GET /api/match-centre          ?user=<display_name>
#   Hub view — all matches grouped by week with the user's per-match points.
#   Includes season summary (total, avg, best). Single network request.
#   Read-only, no live tracking, no writes.
#
# GET /api/match-details/<match_id>  ?user=<display_name>
#   Box Score — user's historical XI snapshot for ONE match. Reads
#   tw_team_json from the week that match belongs to, so later team
#   changes do not corrupt the historical record.
#   Read-only, no writes.
# ════════════════════════════════════════════════════════════════════════════

def _match_ordinal(match_id: str) -> str:
    """'ipl26_m12' → 'M12';  fallback strips non-digits."""
    m = re.search(r'_m(\d+)$', match_id, re.IGNORECASE)
    if m: return "M" + m.group(1)
    digits = re.sub(r'[^0-9]', '', match_id)
    return ("M" + digits) if digits else match_id


_IST = timezone(timedelta(hours=5, minutes=30))

def _fmt_date_label(raw: str) -> str:
    """Convert Unix ms timestamp to 'Apr 22' IST; pass through anything else."""
    if not raw:
        return ""
    try:
        ms = int(raw)
        dt = datetime.fromtimestamp(ms / 1000, tz=_IST)
        return dt.strftime("%b") + " " + str(dt.day)
    except (ValueError, TypeError):
        return raw


@bp.route("/api/match-centre", methods=["GET"])
def api_match_centre():
    """
    Match Centre Hub.

    Query param: ?user=<display_name>  (required)

    Response contract:
    {
      "ok": true,
      "name": "Rohan",
      "season": {
        "total_pts": 1363, "matches_played": 9,
        "avg_per_match": 151, "best_pts": 284, "best_match": "RCB vs CSK"
      },
      "weeks": [
        {
          "week_no": 1, "week_pts": 661,
          "matches_played": 3, "total_matches": 3,
          "matches": [
            {
              "match_id": "ipl26_m1", "match_no": "M1",
              "title": "RCB vs CSK",
              "teams": ["RCB", "CSK"],
              "venue": "Chinnaswamy",
              "date_label": "Mar 22",
              "result": "RCB won by 7 wkts",
              "status": "completed",
              "user_match_pts": 284
            }, ...
          ]
        }, ...
      ]
    }
    """
    n = (request.args.get("user") or "").strip()
    if not n or len(n) > 30:
        return jsonify({"error": "?user=<name> required (1-30 chars)", "code": 400}), 400
    comp = _comp()
    with db._read() as con:
        match_rows = con.execute(
            "SELECT id, week_no, title, teams_json, date_label, status, raw_json "
            "FROM matches WHERE competition_id=%s ORDER BY week_no, id", (comp,)
        ).fetchall()
        ump_rows = con.execute(
            "SELECT match_id, pts FROM user_match_points "
            "WHERE competition_id=%s AND display_name=%s", (comp, n)
        ).fetchall()
        wk_rows = con.execute(
            "SELECT week_no, week_pts, points_per_match FROM user_selections "
            "WHERE competition_id=%s AND display_name=%s ORDER BY week_no", (comp, n)
        ).fetchall()

    ump_map = {r["match_id"]: r["pts"] for r in ump_rows}
    wk_totals = {r["week_no"]: r["week_pts"] for r in wk_rows}

    # Fill gaps: for any match missing from user_match_points, use points_per_match blob.
    # This handles mixed state where some weeks are scraped and others are history-seeded.
    for r in wk_rows:
        try:
            blob = _jloads(r["points_per_match"], {})
            for mid, pts in blob.items():
                if mid not in ump_map:
                    ump_map[mid] = pts
        except Exception:
            pass

    weeks_map: dict = {}
    best_pts = 0
    best_match = ""

    for mr in match_rows:
        wk      = mr["week_no"]
        raw     = _jloads(mr["raw_json"], {})
        teams   = _jloads(mr["teams_json"], [])
        user_pts = ump_map.get(mr["id"], 0)

        if user_pts > best_pts:
            best_pts  = user_pts
            best_match = mr["title"] or mr["id"]

        # Pull venue / result from raw_json; keys vary by scraper version
        venue  = (raw.get("venue") or raw.get("location") or
                  raw.get("ground") or raw.get("stadium") or "")
        result = (raw.get("result") or raw.get("match_result") or
                  raw.get("matchResult") or "")

        entry = {
            "match_id":   mr["id"],
            "match_no":   _match_ordinal(mr["id"]),
            "title":      mr["title"] or mr["id"],
            "teams":      teams,
            "venue":      venue,
            "date_label": _fmt_date_label(mr["date_label"]),
            "result":     result,
            "status":     (mr["status"] or "upcoming").lower(),
            "user_match_pts": user_pts,
        }
        if wk not in weeks_map:
            weeks_map[wk] = {
                "week_no":  wk,
                "week_pts": wk_totals.get(wk, 0),
                "matches":  [],
            }
        weeks_map[wk]["matches"].append(entry)

    weeks = sorted(weeks_map.values(), key=lambda w: w["week_no"])
    for w in weeks:
        completed = sum(1 for m in w["matches"]
                        if m["status"] == "completed")
        w["matches_played"] = completed
        w["total_matches"]  = len(w["matches"])

    total_pts      = sum(ump_map.values())
    matches_played = sum(
        1 for mr in match_rows
        if (mr["status"] or "").lower() == "completed" and mr["id"] in ump_map
    )
    avg_per_match = round(total_pts / matches_played) if matches_played else 0

    return jsonify({
        "ok": True,
        "name": n,
        "season": {
            "total_pts":      total_pts,
            "matches_played": matches_played,
            "avg_per_match":  avg_per_match,
            "best_pts":       best_pts,
            "best_match":     best_match,
        },
        "weeks": weeks,
    })


@bp.route("/api/match-details/<match_id>", methods=["GET"])
def api_match_details(match_id):
    """
    Box Score for one match.

    Query param: ?user=<display_name>  (required)

    Returns the user's historical XI snapshot for that match, with per-player
    pts and C/VC multipliers applied.  Reads tw_team_json from the week this
    match belongs to — historically accurate regardless of later team changes.

    Response contract:
    {
      "ok": true,
      "match_id": "ipl26_m1", "match_no": "M1",
      "title": "RCB vs CSK", "week_no": 1,
      "venue": "Chinnaswamy", "date_label": "Mar 22",
      "result": "RCB won by 7 wkts", "status": "completed",
      "user_pts": 284,
      "cap_id": "vk18rcb", "vc_id": "jb93mi",
      "top_scorer": {"player_id": "vk18rcb", "name": "Virat Kohli", "pts": 176},
      "players": [
        {
          "player_id": "vk18rcb", "name": "Virat Kohli",
          "role": "BAT", "team": "RCB",
          "is_cap": true, "is_vc": false,
          "base_pts": 88, "multiplier": 2.0,
          "final_pts": 176, "multiplier_str": "88×2"
        }, ...
      ]
    }
    """
    n = (request.args.get("user") or "").strip()
    if not n or len(n) > 30:
        return jsonify({"error": "?user=<name> required (1-30 chars)", "code": 400}), 400
    comp = _comp()
    with db._read() as con:
        mr = con.execute(
            "SELECT id, week_no, title, teams_json, date_label, status, raw_json "
            "FROM matches WHERE id=%s AND competition_id=%s", (match_id, comp)
        ).fetchone()
        if not mr:
            return jsonify({"error": f"match '{match_id}' not found", "code": 404}), 404

        wk  = mr["week_no"]
        raw = _jloads(mr["raw_json"], {})

        # Historical XI: read from the week this match belongs to
        sel = con.execute(
            "SELECT tw_team_json, tw_cap_id, tw_vc_id "
            "FROM user_selections WHERE competition_id=%s AND display_name=%s AND week_no=%s",
            (comp, n, wk)
        ).fetchone()

        if not sel:
            return jsonify({
                "ok": True, "name": n,
                "match_id": match_id, "match_no": _match_ordinal(match_id),
                "title": mr["title"] or match_id, "week_no": wk,
                "venue": "", "date_label": _fmt_date_label(mr["date_label"]),
                "result": "", "status": (mr["status"] or "upcoming").lower(),
                "user_pts": 0, "cap_id": None, "vc_id": None,
                "top_scorer": None, "players": [],
                "note": f"No selection found for '{n}' in week {wk}",
            })

        team_ids = _jloads(sel["tw_team_json"], [])
        cap_id   = sel["tw_cap_id"]
        vc_id    = sel["tw_vc_id"]

        # Per-player base pts for this match
        pmp_map: dict = {}
        player_info: dict = {}
        if team_ids:
            ph = ",".join(["%s"] * len(team_ids))
            for r in con.execute(
                f"SELECT player_id, base_pts FROM player_match_points "
                f"WHERE match_id=%s AND competition_id=%s AND player_id IN ({ph})",
                [match_id, comp] + team_ids
            ).fetchall():
                pmp_map[r["player_id"]] = r["base_pts"]
            for r in con.execute(
                f"SELECT id, name, role, team FROM players WHERE competition_id=%s AND id IN ({ph})",
                [comp] + team_ids
            ).fetchall():
                player_info[r["id"]] = dict(r)

        # Authoritative per-match total from user_match_points
        ump_row = con.execute(
            "SELECT pts FROM user_match_points "
            "WHERE competition_id=%s AND display_name=%s AND match_id=%s", (comp, n, match_id)
        ).fetchone()
        user_total = ump_row["pts"] if ump_row else 0

        # Fallback: read from points_per_match blob in user_selections
        if not user_total:
            ppm_sel = con.execute(
                "SELECT points_per_match FROM user_selections "
                "WHERE competition_id=%s AND display_name=%s AND week_no=%s", (comp, n, wk)
            ).fetchone()
            if ppm_sel:
                blob = _jloads(ppm_sel["points_per_match"], {})
                user_total = blob.get(match_id, 0)

    venue  = (raw.get("venue") or raw.get("location") or
              raw.get("ground") or raw.get("stadium") or "")
    result = (raw.get("result") or raw.get("match_result") or
              raw.get("matchResult") or "")

    players_out = []
    for pid in team_ids:
        base_pts  = pmp_map.get(pid, 0)
        is_cap    = pid == cap_id
        is_vc     = pid == vc_id
        mult      = 2.0 if is_cap else (1.5 if is_vc else 1.0)
        final_pts = round(base_pts * mult)
        info      = player_info.get(pid, {"id": pid, "name": pid,
                                           "role": "", "team": ""})
        mult_str  = ""
        if is_cap and base_pts > 0:
            mult_str = f"{base_pts}\u00d72"
        elif is_vc and base_pts > 0:
            mult_str = f"{base_pts}\u00d71.5"

        players_out.append({
            "player_id":      pid,
            "name":           info.get("name", pid),
            "role":           info.get("role", ""),
            "team":           info.get("team", ""),
            "is_cap":         is_cap,
            "is_vc":          is_vc,
            "base_pts":       base_pts,
            "multiplier":     mult,
            "final_pts":      final_pts,
            "multiplier_str": mult_str,
        })

    players_out.sort(key=lambda x: -x["final_pts"])
    top = (players_out[0] if players_out and players_out[0]["final_pts"] > 0
           else None)

    return jsonify({
        "ok":         True,
        "name":       n,
        "match_id":   match_id,
        "match_no":   _match_ordinal(match_id),
        "title":      mr["title"] or match_id,
        "week_no":    wk,
        "teams":      _jloads(mr["teams_json"], []),
        "venue":      venue,
        "date_label": _fmt_date_label(mr["date_label"]),
        "result":     result,
        "status":     (mr["status"] or "upcoming").lower(),
        "user_pts":   user_total,
        "cap_id":     cap_id,
        "vc_id":      vc_id,
        "top_scorer": {
            "player_id": top["player_id"],
            "name":      top["name"],
            "pts":       top["final_pts"],
        } if top else None,
        "players": players_out,
    })


# ════════════════════════════════════════════════════════════════════════════
# 7. ADMIN / ROLLOVER
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/rollover", methods=["POST"])
def api_rollover():
    """Apply every weekly handover that is due for a competition (2026-11-20, Phase 1.5; before this the route moved everyone one week per call).

    A competition with a stored lock schedule (competition_weeks) is MANAGED: at each lock the members' drafts become the XI that plays the week
    ("start" = week 1, "rollover" = weeks 2..N). Every handover is one all-or-nothing transaction, recorded once in the rollover ledger, so it does not
    matter how many triggers call this, or how late: a call applies whatever is overdue, in order, and repeating it changes nothing.

      POST /api/rollover?comp=<slug>                       apply what is due
      POST /api/rollover?comp=<slug>&dry_run=1             the same, then roll everything back: the report says what WOULD happen
      POST /api/rollover?comp=<slug>&force=1&confirm=<slug>:<week>
                                                           an admin's rehearsal or repair: apply the NEXT week now, whatever its lock time says (also
                                                           re-applies a week an admin undid). Needs a signed-in admin (the scheduler token is not
                                                           enough) and the exact confirmation, which the refusal spells out.

    Callers: the scheduler token (Authorization: Bearer ROLLOVER_TOKEN) or a signed-in admin. A competition with no lock schedule is never rolled (a
    harmless 200 no-op that says why). A call that hits a failure answers 500 with what it did manage (rolled / applied), and the next call carries on."""
    expected = os.environ.get("ROLLOVER_TOKEN", "").strip()
    auth = request.headers.get("Authorization", "")
    sent = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    via_token = bool(expected and sent == expected)
    admin_name = ""
    if not via_token:
        admin_name, err = _require_admin()      # rollover is privileged: the scheduler token or an authenticated admin, nobody else (SEC-1)
        if err: return err
    force = request.args.get("force", "").strip() in ("1", "true", "yes")
    dry = request.args.get("dry_run", "").strip() in ("1", "true", "yes")
    comp = _comp()
    who = "scheduler" if via_token else f"admin:{admin_name}"

    def body_for(rep, **extra):
        applied = rep["applied"]; last = applied[-1] if applied else None
        body = {"ok": not rep.get("error"), "rolled": bool(applied) and not dry, "new_week_no": last["to_week"] if last else None,
                "season_complete": bool(last and last["season_complete"]), "competition": comp, "dry_run": dry, "applied": applied}
        if not applied and rep.get("skipped"): body["reason"] = "Not rolled: " + rep["skipped"]
        if rep.get("error"): body.update(error=rep["error"], code=500)
        if rep.get("points_error"): body["warning"] = "the handover is done, but recomputing points failed (the next scoring pass redoes it): " + rep["points_error"]
        body.update(extra)
        return body

    try:
        now = datetime.now(timezone.utc)
        expect_week = None
        if force:
            if via_token:
                return jsonify({"ok": False, "rolled": False, "new_week_no": None, "season_complete": False, "competition": comp, "applied": [],
                                "error": "A forced rollover needs a signed-in admin (the scheduler token is not enough).", "code": 403}), 403
            # What would a forced run apply? The same code path, rolled back; the admin must name that week to go ahead.
            preview = db.reconcile_competition(comp, now, triggered_by=who, force_next=True, dry_run=True, max_steps=1)
            if preview.get("error") or not preview["applied"]:
                rep = {**preview, "applied": []}
                _log(f"POST /api/rollover forced run refused for {comp}: {preview.get('error') or preview.get('skipped')}")
                return jsonify(body_for(rep)), (500 if preview.get("error") else 200)
            expect_week = preview["applied"][0]["to_week"]
            want = f"{comp}:{expect_week}"
            if request.args.get("confirm", "").strip() != want:
                msg = (f"A forced rollover applies week {expect_week} of '{comp}' to every member now. To go ahead, repeat the call with confirm={want}")
                return jsonify({"ok": False, "rolled": False, "new_week_no": None, "season_complete": False, "competition": comp, "applied": [],
                                "error": msg, "reason": msg, "code": 400}), 400
        rep = db.reconcile_competition(comp, now, triggered_by=who, force_next=force, expect_week=expect_week, dry_run=dry,
                                       max_steps=1 if force else 12)
        if not dry:
            try:
                what = ("applied week " + ", ".join(str(a["to_week"]) for a in rep["applied"])) if rep["applied"] else (rep.get("error") or rep.get("skipped") or "nothing due")
                db.touch_heartbeat("rollover:" + ("scheduler" if via_token else "admin"), f"{comp}: {what}")   # when something last asked for a rollover (Ops card, Phase 2)
            except Exception as e:
                _log(f"rollover heartbeat: {e}", "error")
            if rep["applied"]:
                _log(f"POST /api/rollover {comp} by {who}: applied weeks {[a['to_week'] for a in rep['applied']]}")
                _push_if_hosted(f"rollover:{comp}:w{rep['applied'][-1]['to_week']}")
        if rep.get("error"):
            _log(f"POST /api/rollover {comp} by {who}: {rep['error']}", "error")
        return jsonify(body_for(rep)), (500 if rep.get("error") else 200)
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e), "code": 404}), 404

# ════════════════════════════════════════════════════════════════════════════
# OPS (Phase 2, 2026-11-22): the tick, the status, the test alert, and the server's own clock
# ════════════════════════════════════════════════════════════════════════════
# Several things call the SAME routine: a clock (POST /api/ops/tick, from Supabase, the PC, GitHub) and the server itself (a thread that ticks at boot and then at
# the rhythm the server sets). The routine is db.reconcile_competition, which is safe to repeat and to run at the same moment from two places (one all-or-nothing
# transaction per week under a row lock, recorded once in the ledger), so every layer can fire without knowing about the others: whichever gets there first does the
# work and the rest find nothing due. What to say to the admin, and when, is decided by logic/ops_plan.py; Telegram is ops_notify.py (dormant until configured).

OPS_CLOCK_MAX_SLEEP_S = 300     # the in-server clock never sleeps longer than this, so a schedule stored a moment ago is noticed within five minutes


def _ops_caller():
    """Who is calling an ops route: ("clock", None) with the shared token (OPS_TOKEN, or the existing scheduler token ROLLOVER_TOKEN), ("admin:<name>", None) with an
    admin session, else (None, an error response). A caller that proves nothing counts against the sign-in rate limit, so a token cannot be guessed."""
    sent = get_bearer_token() or ""
    for var in ("OPS_TOKEN", "ROLLOVER_TOKEN"):
        expected = os.environ.get(var, "").strip()
        if sent and expected and hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
            return "clock", None
    name, err = _require_admin()
    if err:
        return None, (_check_rate(_auth_limiter) or err)
    return "admin:" + name, None


def _ops_source():
    """Which clock is calling (?source=supabase|pc|github ...): a short lower-case word, never "app" (that is the server's own clock), else "clock"."""
    s = (request.args.get("source") or "").strip().lower()
    return s if re.fullmatch(r"[a-z][a-z0-9_-]{0,19}", s) and s != "app" else "clock"


def _ops_alert(snap, now):
    """Judge the snapshot and send what has to be said. Dormant, and free, until Telegram is set up. Returns how many messages went out."""
    if not _notify.configured():
        return 0
    base_url = _origin.resolve_public_base(os.environ)[0]
    recent = db.recent_applied(12)
    messages = db.ops_alert_step(lambda state: _ops.evaluate_alerts(snap, recent, state, now))
    failed = []
    for m in messages:
        text = m["text"] + ((" Open: " + base_url) if base_url and m["kind"] in ("new", "still") else "")
        ok, detail = _notify.send(text)
        if not ok:
            failed.append(m)
            _log(f"ops alert {m['key']} not sent: {detail}", "warn")
    if failed:                                                  # a new incident or an event that did not get out is raised again by the next call
        db.ops_alert_step(lambda state: ([], _ops.forget_unsent(state, failed)))
    return len(messages) - len(failed)


def _ops_run(source, now=None, beat=True):
    """The one routine every clock calls: apply whatever handover is due for any competition (safe to repeat), leave this clock's heartbeat, judge how things look, and
    send the alerts that judgement calls for. Returns the report the tick route answers with."""
    now = _rp.parse_utc(now) or datetime.now(timezone.utc)
    applied, errors = [], []
    for st in _ops.build_snapshot(db.ops_competitions(), [], now)["states"]:
        if not st["due"]:
            continue
        try:
            rep = db.reconcile_competition(st["slug"], now, triggered_by="tick:" + source)
        except LookupError:
            continue
        for a in rep["applied"]:
            applied.append({"competition": st["slug"], "kind": a["kind"], "to_week": a["to_week"], "members": a["members"], "activated": a["activated"]})
        if rep["applied"]:
            _log(f"ops tick {source}: {st['slug']} applied weeks {[a['to_week'] for a in rep['applied']]}")
            _push_if_hosted(f"rollover:{st['slug']}:w{rep['applied'][-1]['to_week']}")
        if rep.get("error"):
            errors.append(f"{st['slug']}: {rep['error']}")
            _log(f"ops tick {source}: {st['slug']}: {rep['error']}", "error")
    if beat:
        what = " | ".join(p for p in (("applied " + ", ".join(f"{a['competition']} week {a['to_week']}" for a in applied)) if applied else "", ("ERROR " + "; ".join(errors)) if errors else "") if p) or "nothing due"
        try:
            db.touch_heartbeat("tick:" + source, what, now=now)
        except Exception as e:
            _log(f"ops heartbeat: {e}", "error")
    snap = _ops.build_snapshot(db.ops_competitions(), db.get_heartbeats(), now)
    sent = 0
    try:
        sent = _ops_alert(snap, now)
    except Exception as e:                                      # an alert that cannot be judged or sent must never fail the handover that has just run
        _log(f"ops alerts: {type(e).__name__}: {e}", "error")
    rep = {"ok": not errors, "now": snap["now"], "source": source, "applied": applied, "errors": errors, "mode": snap["rhythm"]["mode"],
           "next_tick_in_s": snap["rhythm"]["next_tick_in_s"], "health": snap["health"]["level"], "alerts_sent": sent}
    if errors:
        rep.update(error="; ".join(errors), code=500)
    return rep


def ops_clock_loop(stop, run=None, first_delay_s=3, max_cycles=None):
    """The server's own clock (the "app" source), run on a thread by server.py: it ticks once soon after boot (a visit that wakes a sleeping Render service is the "first
    touch after a lock": the boot catches the handover up at once) and then at the rhythm the server sets (every minute near a lock, never later than five minutes).
    `stop` is a threading.Event; `run` is for tests; a failed cycle is logged and tried again in a minute."""
    run = run or (lambda: _ops_run("app"))
    delay, cycles = first_delay_s, 0
    while not stop.wait(delay):
        try:
            delay = max(5, min(int(run().get("next_tick_in_s") or 60), OPS_CLOCK_MAX_SLEEP_S))
        except Exception as e:
            _log(f"ops clock: {type(e).__name__}: {e}", "error")
            delay = 60
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return


@bp.route("/api/ops/tick", methods=["POST"])
def api_ops_tick():
    """A clock calls this (Authorization: Bearer OPS_TOKEN, or the scheduler token ROLLOVER_TOKEN; ?source=supabase|pc|github names it) or an admin does. It applies
    whatever handover is due, records the heartbeat, sends any alert, and answers how soon to call again (`next_tick_in_s`: 60 near a lock, 1800 otherwise)."""
    who, err = _ops_caller()
    if err:
        return err
    rep = _ops_run(_ops_source() if who == "clock" else "admin")
    return jsonify(rep), (200 if rep["ok"] else 500)


@bp.route("/api/ops/status", methods=["GET"])
def api_ops_status():
    """The Ops card: health, the clocks and their ages, every competition's state with its last handover, and whether Telegram is set up. Admin or the clock token."""
    who, err = _ops_caller()
    if err:
        return err
    now = datetime.now(timezone.utc)
    snap = _ops.build_snapshot(db.ops_competitions(), db.get_heartbeats(), now)
    last = {}
    for r in db.recent_applied(30):
        last.setdefault(r["slug"], r)
    for st in snap["states"]:
        st["last_run"] = last.get(st["slug"])
    base_url, base_source = _origin.resolve_public_base(os.environ, request.host)
    return jsonify({"ok": True, **snap,
                    "alerts": {"telegram": "configured" if _notify.configured() else "not_configured", "open_incidents": sorted((db.ops_alert_state().get("incidents") or {}))},
                    "tokens": {"ops_token": bool(os.environ.get("OPS_TOKEN", "").strip()), "scheduler_token": bool(os.environ.get("ROLLOVER_TOKEN", "").strip())},
                    "address": {"public_base_url": base_url, "source": base_source}})


@bp.route("/api/ops/test-alert", methods=["POST"])
def api_ops_test_alert():
    """An admin asks for one test message to the Telegram chat ("Send test alert" on the Ops card). Says plainly whether Telegram is set up and what went wrong."""
    re_ = _check_rate(_write_limiter)
    if re_:
        return re_
    name, err = _require_admin()
    if err:
        return err
    ok, detail = _notify.send(f"TEST: an alert from the Ops card, asked for by {name}.")
    return jsonify({"ok": ok, "configured": _notify.configured(), "detail": detail})


@bp.route("/api/matches-status", methods=["GET"])
def api_matches_status():
    with db._read() as con:
        rows=con.execute(
            "SELECT id,week_no,title,status,scorecard_url,teams_json,date_label "
            "FROM matches WHERE competition_id=%s ORDER BY id", (_comp(),)
        ).fetchall()
    matches=[dict(r) for r in rows]
    # Flag duplicate Cricbuzz IDs so Admin Tab can warn the user
    url_to_ids: dict = {}
    for m in matches:
        url=m.get("scorecard_url") or ""
        cb=url.rstrip("/").split("/")[-1]
        if cb and cb!="00000" and cb.isdigit() and int(cb)>0:
            url_to_ids.setdefault(cb,[]).append(m["id"])
    for m in matches:
        url=m.get("scorecard_url") or ""
        cb=url.rstrip("/").split("/")[-1]
        dup_ids=url_to_ids.get(cb,[]) if (cb and cb!="00000") else []
        m["duplicate_url"]=len(dup_ids)>1
        m["duplicate_with"]=[x for x in dup_ids if x!=m["id"]]
    return jsonify({"ok":True,"matches":matches})

@bp.route("/api/update-match-url", methods=["POST"])
def api_update_match_url():
    re_=_check_rate(_write_limiter)
    if re_: return re_
    _u,err=_require_admin()
    if err: return err
    d=request.get_json(force=True,silent=True) or {}
    match_id=(d.get("match_id") or "").strip(); url=(d.get("url") or "").strip()
    if not match_id or not url: return jsonify({"error":"match_id and url required","code":400}),400
    m=re.search(r'(\d{5,})',url)
    if not m: return jsonify({"error":"URL must contain a 5+ digit Cricbuzz match ID","code":400}),400
    cb_id=m.group(1); clean_url=f"https://www.cricbuzz.com/live-cricket-scorecard/{cb_id}"
    comp=_comp()
    with db._read() as con:
        row=con.execute("SELECT id FROM matches WHERE id=%s AND competition_id=%s",(match_id,comp)).fetchone()
    if not row: return jsonify({"error":f"match '{match_id}' not found","code":404}),404
    with db._write() as con:
        con.execute("UPDATE matches SET scorecard_url=%s WHERE id=%s AND competition_id=%s",(clean_url,match_id,comp))
    if _IS_HOSTED:
        # No Cricbuzz egress in cloud \u2014 push the URL change so the GitHub
        # Actions runner sees the new URL on checkout, then dispatch the
        # daily_sync workflow so it scrapes immediately rather than
        # waiting for the next scheduled run at 18:30 / 21:30 UTC.
        #
        # Critically, we also DELETE this match's cached JSON before the
        # push. scraper.py at scraper.py:615 short-circuits on the
        # presence of data/matches/match_NN.json \u2014 without deleting it,
        # the workflow checks out a cached file, the scraper sees it,
        # skips this match, and we get a "data: scrape ..." commit with
        # no real change. Targeted deletion = targeted re-scrape.
        json_rel_path = None
        try:
            mno_match = re.search(r'(\d+)\s*$', match_id)
            if mno_match:
                mns = mno_match.group(1).zfill(2)
                jp = DATA_DIR / comp / "matches" / f"match_{mns}.json"
                # Path RELATIVE to the repo root — git add needs it this
                # way. Always pass it through to _push_if_hosted so the
                # deletion is STAGED, even if the file didn't exist
                # locally (the workflow's checkout might have it).
                json_rel_path = f"data/{comp}/matches/match_{mns}.json"
                if jp.exists():
                    jp.unlink()
                    _log(f"Deleted cached {jp.name} so workflow re-scrapes M{mns}")
        except Exception as e:
            _log(f"Could not delete cached match JSON for {match_id}: {e}",
                 "warn")
        # Pass the JSON path as extra so the deletion is committed —
        # without this the workflow checks out the stale JSON on
        # origin/main and scraper.py:615 short-circuits, defeating the
        # whole point of Save & Scrape.
        _push_if_hosted(f"admin-url:{match_id}={cb_id}",
                        extra_paths=[json_rel_path] if json_rel_path else None)
        dispatched, dmsg = cloud_sync.dispatch_workflow(
            "daily_sync.yml", ref="main", log=_log,
        )
        if dispatched:
            msg = ("URL saved, cached scorecard wiped, push & cloud "
                   "scrape triggered. Click Refresh in 60-90s to see "
                   "new scores.")
        else:
            msg = (f"URL saved & pushed. Scrape NOT triggered: {dmsg}. "
                   f"Check PAT has actions:write scope. Falls back to "
                   f"the next scheduled run at 18:30 / 21:30 UTC.")
    else:
        tasks.start_bg_scrape(match_id,BASE_DIR)
        msg = "URL saved. Scraping started in background \u2014 refresh in ~30 seconds."
    return jsonify({"ok":True,"match_id":match_id,"cb_id":cb_id,"url":clean_url,
                    "message":msg})


@bp.route("/api/sync-now", methods=["POST"])
def api_sync_now():
    """
    Manual trigger for the daily discovery+scrape pipeline.

    Same code path as the APScheduler daily 23:55 IST job (tasks.py v2.0.0),
    but run on demand from the Admin tab.  Returns immediately; the actual
    work runs in a daemon thread.  Caller polls /api/matches-status or
    /api/state to detect when fresh data lands (typically 30-90 seconds,
    depending on how many newly-completed matches need scraping).

    Request body (optional)
    -----------------------
    {"debug": true}   — verbose logging in the server console.
    Or pass ?debug=1 in the query string.

    Response
    --------
    {
        "ok": true,
        "thread_name": "ipl-daily-sync",
        "debug": false,
        "message": "Discovery+scrape started ..."
    }

    A transient Cricbuzz hiccup will not crash the request:
    tasks.run_discovery_and_scrape() is fault-tolerant; it catches all
    exceptions internally and logs the outcome.  This route only returns
    500 if the daemon thread couldn't be spawned (i.e. OS-level failure).
    """
    re_ = _check_rate(_write_limiter)
    if re_: return re_
    _u, err = _require_token()
    if err: return err

    # ── HOSTED mode (Phase 2 + post-deploy fix): pull, then trigger scrape ──
    # The cloud host cannot reach Cricbuzz. Refresh does TWO things:
    #   1. git pull --ff-only — picks up anything the daily Action / local
    #      box / rollover workflow pushed since the last call.
    #   2. workflow_dispatch on daily_sync.yml — asks the GitHub Actions
    #      runner to scrape NOW (it CAN reach Cricbuzz for scorecards by
    #      cricbuzz_id, even though discovery from Actions is blocked).
    # The user clicks Refresh, sees "Triggered scrape — check back in
    # 60-90s", clicks Refresh again later to pull the new commit.
    if _IS_HOSTED:
        # INF-1: no git pull — data lives in Postgres and is always current.

        # Best-effort dispatch. If the PAT lacks actions:write the
        # request fails 403; we surface that in the response so the
        # operator can fix the PAT, but the pull half still counts.
        # `force_full_rescrape=true` makes the workflow wipe the
        # data/matches/*.json cache before running scraper.py — without
        # this, scraper.py at scraper.py:615 silently skips every
        # match it has previously cached and the workflow produces no
        # new commits, no new data, no visible UI change.
        dispatched, dmsg = cloud_sync.dispatch_workflow(
            "daily_sync.yml", ref="main",
            inputs={"force_full_rescrape": "true"},
            log=_log,
        )
        return jsonify({
            "ok":         True,
            "mode":       "dispatch",
            "dispatched": dispatched,
            "dispatch_msg": dmsg,
            "message":    ("Triggered a cloud scrape. Click Refresh in 60-90s "
                           "to see new scores.")
                          if dispatched
                          else (f"Scrape NOT triggered: {dmsg}. "
                                f"Check the PAT has actions:write scope."),
        })

    # ── LOCAL mode: existing Cricbuzz discovery + scrape behaviour ──────────
    d     = request.get_json(force=True, silent=True) or {}
    debug = bool(d.get("debug")) or \
            (request.args.get("debug", "").lower() in ("1", "true", "yes"))
    t = tasks.start_bg_sync(debug=debug)
    return jsonify({
        "ok":          True,
        "thread_name": t.name if t else None,
        "debug":       debug,
        "message":     "Discovery+scrape started in background. "
                       "Refresh /api/matches-status in 30-90 seconds.",
    })


# ════════════════════════════════════════════════════════════════════════════
# 8. STATIC
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/")
def index():
    return render_template("index.html")

@bp.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_DIR,filename)

@bp.route("/manifest.json")
def manifest():
    return jsonify({"id":"/","name":"BCL Fantasy","short_name":"BCL Fantasy",
        "description":"BCL Fantasy - private fantasy cricket across our leagues","start_url":"/","scope":"/","display":"standalone",
        "orientation":"portrait","background_color":"#F3EBD8","theme_color":"#F3EBD8","categories":["sports","games"],
        "icons":[{"src":"/static/brand/icon-192.png","sizes":"192x192","type":"image/png","purpose":"any"},
                 {"src":"/static/brand/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any"},
                 {"src":"/static/brand/icon-maskable-512.png","sizes":"512x512","type":"image/png","purpose":"maskable"},
                 {"src":"/static/brand/icon-paper.svg","sizes":"any","type":"image/svg+xml","purpose":"any"}]})

@bp.route("/offline")
def offline_page():
    return ("<html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#F3EBD8'></head>"
            "<body style='background:#F3EBD8;color:#3A352C;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center;padding:0 24px'><img src='/static/brand/mark-paper.svg' width='120' height='120' alt=''>"
            "<h1 style='color:#231F18;margin:16px 0 8px;font-size:28px'>You're offline</h1>"
            "<p style='color:#6A6255'>Check your connection and try again.</p>"
            "</div></body></html>")

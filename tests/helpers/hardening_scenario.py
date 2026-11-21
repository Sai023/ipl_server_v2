"""Run in a SUBPROCESS by tests/test_hardening.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for the hardening release (2026-11-21, 2.21.0) and prints one JSON document:
  A  HTTP errors answer as what they are (JSON on /api/, plain text elsewhere, the page only for a person opening a link)
  B  an unexpected failure is logged with a request id and answered with a fixed sentence (never the exception's text)
  C  /api/health is one statement
  D  /api/state decides its 304 BEFORE the heavy reads, and reads its tag before the data
  E  the player resolver looks at one competition only, never writes, and a normal save does not touch the player list
  F  the removed diagnostic routes are gone
Environment: DATABASE_URL (a scratch schema)."""
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)

import psycopg  # noqa: E402
import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
import base  # noqa: E402
import routes  # noqa: E402
from base import app, db, hash_passcode  # noqa: E402

base._write_limiter._max = 10 ** 6            # these scenarios make many writes from one "IP"
NOW = datetime.now(timezone.utc)
FUTURE = (NOW + timedelta(days=60)).isoformat()


# ── the log: every record of the app's logger, with its traceback text, so the scenario can say what a request id found in it ───────────────
class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        tb = ""
        if record.exc_info:
            import traceback
            tb = "".join(traceback.format_exception(*record.exc_info))
        self.records.append({"level": record.levelname, "msg": record.getMessage(), "tb": tb})


cap = Capture()
logging.getLogger("ipl").addHandler(cap)


def call(method, url, token=None, body=None, headers=None, **kw):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = "Bearer " + token
    r = app.test_client().open(url, method=method, json=body, headers=headers, **kw)
    ct = r.headers.get("Content-Type", "")
    txt = r.get_data(as_text=True)
    return {"http": r.status_code, "ct": ct, "body": r.get_json(silent=True) if "json" in ct else None, "text": txt[:300], "is_html": "<html" in txt.lower() or "<!doctype" in txt.lower(),
            "headers": {k: v for k, v in r.headers.items()}}


class Counting:
    """Count the statements psycopg executes while a block runs (the number that matters on production, where the database is a millisecond away)."""
    n = 0

    def __enter__(self):
        self.n = 0
        self._orig = psycopg.Cursor.execute
        outer = self

        def counted(cur, query, params=None, **k):
            outer.n += 1
            return self._orig(cur, query, params, **k)
        psycopg.Cursor.execute = counted
        return self

    def __exit__(self, *a):
        psycopg.Cursor.execute = self._orig


class Patched:
    """Temporarily replace an attribute (on the db object, a module ...) and put it back."""
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.had = self.name in vars(self.obj)
        self.old = vars(self.obj).get(self.name)
        setattr(self.obj, self.name, self.value)

    def __exit__(self, *a):
        if self.had:
            setattr(self.obj, self.name, self.old)
        else:
            delattr(self.obj, self.name)


def boom(text):
    def f(*a, **k):
        raise text if isinstance(text, BaseException) else RuntimeError(text)
    return f


def ensure(slug):
    if not db.get_competition(slug):
        db.create_competition(slug, slug.upper(), status="upcoming", max_weeks=6, week1_anchor_utc=FUTURE)


def make(slug, names):
    """A competition open for picking (upcoming, week 1 in the future) with 12 players p1..p12 at 8.0; `names` renames some of them."""
    ensure(slug)
    with db._pool.connection() as con:
        con.execute("INSERT INTO players (competition_id,id,name,team,price,role) SELECT %s,'p'||g,'Player '||g,'AAA',8.0,'BAT' FROM generate_series(1,12) g", (slug,))
        for pid, nm in names.items():
            con.execute("UPDATE players SET name=%s WHERE competition_id=%s AND id=%s", (nm, slug, pid))


def draft(who, slug):
    h = app.test_client().get(f"/api/history/{who}?comp={slug}").get_json()
    row = next(w for w in h["weeks"] if w["is_current"])
    return row["next_week"]["team"]


out = {}

# ── A. HTTP errors ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
ensure("ipl_2026")                                               # the default competition (the app resolves "no ?comp=" to it)
out["a_unknown_get"] = call("GET", "/api/does-not-exist")
out["a_unknown_post"] = call("POST", "/api/does-not-exist", body={"x": 1})
out["a_api_root"] = call("GET", "/api")
out["a_api_slash"] = call("GET", "/api/")
out["a_wrong_method"] = call("POST", "/api/state", body={})
out["a_too_large"] = call("POST", "/api/login", data=b"x" * (1024 * 1024 + 50), content_type="application/json")
out["a_static_missing"] = call("GET", "/static/definitely-missing.css")
out["a_static_missing_deep"] = call("GET", "/static/brand/nope.png")
out["a_static_present"] = call("GET", "/static/ui.js")
out["a_favicon"] = call("GET", "/favicon.ico")
out["a_robots"] = call("GET", "/robots.txt")
out["a_php"] = call("GET", "/wp-login.php")
out["a_root"] = call("GET", "/")
out["a_deep_link"] = call("GET", "/some/deep/link")
out["a_index_html"] = call("GET", "/index.html")
out["a_post_deep_link"] = call("POST", "/some/deep/link", body={})
out["a_head_deep_link"] = call("HEAD", "/some/deep/link")
out["a_manifest"] = call("GET", "/manifest.json")

# ── B. an unexpected failure ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
SECRET = "ep-cool-123.us-west-2.aws.neon.tech password=hunter2"
cap.records.clear()
with Patched(db, "get_players", boom("boom " + SECRET)):
    out["b_runtime"] = call("GET", "/api/players")
with Patched(db, "get_leaderboard", boom(psycopg.OperationalError('connection to server at "%s" failed' % SECRET))):
    out["b_operational"] = call("GET", "/api/leaderboard")
with Patched(db, "get_history", boom(psycopg.errors.UniqueViolation('duplicate key value violates unique constraint "members_pkey" DETAIL: Key (username)=(Sai)'))):
    out["b_integrity"] = call("GET", "/api/history/Sai")
with Patched(db, "get_players", boom("second boom")):
    out["b_second"] = call("GET", "/api/players")
with Patched(routes, "render_template", boom("template exploded " + SECRET)):
    out["b_page"] = call("GET", "/")
out["b_log"] = [r for r in cap.records if r["level"] == "ERROR"]
out["b_expected_still_expected"] = call("POST", "/api/login", body={"name": "Nobody", "passcode": "0000"})   # an expected refusal keeps its own sentence and reason

# ── C. /api/health ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
with Counting() as n1:
    out["c_health"] = call("GET", "/api/health")
out["c_health_statements"] = n1.n
with Counting() as n2:
    out["c_ping"] = call("GET", "/api/ping")
out["c_ping_statements"] = n2.n
with Patched(db, "_read", boom(psycopg.OperationalError("the database is gone " + SECRET))):
    out["c_health_db_down"] = call("GET", "/api/health")

# ── members, a session each ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
for who, adm in (("Sai", 1), ("Moe", 0)):
    db.upsert_member_auth(who, hash_passcode("1234", who), 0, adm)
    db.create_session("tok-" + who.lower(), who, ttl_days=1)

# ── E. the resolver (two competitions whose ids and names collide) ───────────────────────────────────────────────────────────────────────
make("h_a", {"p1": "Zed Alpha"})                                 # in h_a, p1 is Zed Alpha
make("h_b", {"p1": "Bob Beta", "p2": "Zed Alpha"})               # in h_b, p1 is somebody else and Zed Alpha is p2
CANON = [f"p{i}" for i in range(1, 12)]
out["e_canonical_save"] = call("POST", "/api/save-next-week/Moe?comp=h_b", "tok-moe", {"team": CANON, "cap": "p1", "vc": "p2"})
D0 = draft("Moe", "h_b")
out["e_draft_after_canonical"] = D0
# a marker draft for the SAME member and the SAME week number in the other competition
db.upsert_member("Moe", {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": ["p5", "p6", "p7"], "cap": "p5", "vc": "p6"}}, competition_id="h_a")
MARK0 = draft("Moe", "h_a")
# a save that names a player and is then REFUSED (3 players): nothing may change, in either competition
out["e_refused"] = call("POST", "/api/save-next-week/Moe?comp=h_b", "tok-moe", {"team": ["Zed Alpha", "p3", "p4"], "cap": "p3", "vc": "p4"})
out["e_draft_after_refused"] = draft("Moe", "h_b")
out["e_marker_after_refused"] = draft("Moe", "h_a")
out["e_marker_before"] = MARK0
# what the save hands the resolver: a connection only when something needs looking up
seen = []
real_ril = routes.resolve_id_list


def spy(con, items, **k):
    seen.append("a connection" if con is not None else "no connection")
    return real_ril(con, items, **k)


# a save by NAME that is accepted: the name resolves inside THIS competition (h_b: Zed Alpha is p2, not h_a's p1)
BYNAME = ["Zed Alpha"] + [f"p{i}" for i in range(3, 13)]
with Patched(routes, "resolve_id_list", spy):
    out["e_by_name"] = call("POST", "/api/save-next-week/Moe?comp=h_b", "tok-moe", {"team": BYNAME, "cap": "p3", "vc": "p4"})
out["e_by_name_resolver_got"] = list(seen)
seen.clear()
out["e_draft_after_by_name"] = draft("Moe", "h_b")
out["e_marker_after_by_name"] = draft("Moe", "h_a")
# a normal save (canonical ids) must not touch the player list at all, and must not even check out a connection to resolve
with Patched(routes, "resolve_id_list", spy):
    with Patched(base, "_load_all_players", boom("the player list was read during a normal save")):
        out["e_fast_path"] = call("POST", "/api/save-next-week/Moe?comp=h_b", "tok-moe", {"team": CANON, "cap": "p1", "vc": "p2"})
out["e_fast_path_resolver_got"] = list(seen)
seen.clear()
out["e_needs_resolving"] = {"canonical_ids": base.needs_resolving(*CANON, "p1", "p2"), "only_empty_values": base.needs_resolving("p1", None, ""),
                            "a_name": base.needs_resolving("Zed Alpha", "p1"), "an_id_of_another_shape": base.needs_resolving("ext_10945"),
                            "a_name_as_captain": base.needs_resolving("p1", "p2", "Zed Alpha")}
out["e_resolve_b"] = call("POST", "/api/resolve-player?comp=h_b", "tok-moe", {"query": "Zed Alpha"})
out["e_resolve_a"] = call("POST", "/api/resolve-player?comp=h_a", "tok-moe", {"query": "Zed Alpha"})
out["e_no_db_con"] = not hasattr(base, "_db_con")

# ── D. /api/state ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["d_first"] = call("GET", "/api/state?comp=h_b")
etag = out["d_first"]["headers"].get("ETag")
out["d_etag"] = etag
out["d_body_saved"] = (out["d_first"]["body"] or {}).get("_saved")
with Patched(db, "get_state", boom("the heavy read ran for a 304")):
    out["d_304"] = call("GET", "/api/state?comp=h_b", headers={"If-None-Match": etag})
    out["d_200_needs_the_heavy_read"] = call("GET", "/api/state?comp=h_b", headers={"If-None-Match": "something-else"})
with Counting() as n3:
    call("GET", "/api/state?comp=h_b", headers={"If-None-Match": etag})
out["d_304_statements"] = n3.n
with Counting() as n4:
    call("GET", "/api/state?comp=h_b")
out["d_200_statements"] = n4.n

# a save that lands RIGHT AFTER the data was read (inside get_state, after its match_scores query): the page must get the OLDER tag (so its next poll refetches),
# never a tag newer than its data. A tag read after the data (the old order) picks up the save's tag; a tag read before the data cannot.
from contextlib import contextmanager  # noqa: E402

LATER = "2099-01-01T00:00:00+00:00"
real_read = db._read
armed = {"on": False}


class ConProxy:
    def __init__(self, con):
        self._con = con

    def execute(self, sql, *a, **k):
        r = self._con.execute(sql, *a, **k)
        if armed["on"] and "FROM match_scores" in sql:
            armed["on"] = False
            db.set_meta_comp("_saved", "h_b", LATER)               # somebody saves right after the data was read
        return r

    def __getattr__(self, name):
        return getattr(self._con, name)


@contextmanager
def read_with_a_save_in_the_middle():
    with real_read() as con:
        yield ConProxy(con)


before = db.get_etags(competition_id="h_b")["state"]
armed["on"] = True
with Patched(db, "_read", read_with_a_save_in_the_middle):
    out["d_race"] = call("GET", "/api/state?comp=h_b")
out["d_race_fired"] = not armed["on"]
out["d_race_before_tag"] = before
out["d_race_tag_now"] = db.get_etags(competition_id="h_b")["state"]
out["d_race_next_poll"] = call("GET", "/api/state?comp=h_b", headers={"If-None-Match": out["d_race"]["headers"].get("ETag")})

# ── F. the removed routes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["f_audit_scores"] = call("GET", "/api/audit-scores/Sai")
out["f_audit_ids"] = call("GET", "/api/audit-player-ids")
out["f_audit_blobs"] = call("GET", "/api/audit-blobs")
out["f_clean"] = call("POST", "/api/clean-scores", "tok-sai", {})
out["f_snapshot"] = call("POST", "/api/snapshot", "tok-sai", {})
out["f_recalc_still_guarded"] = call("POST", "/api/recalculate-points")

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")

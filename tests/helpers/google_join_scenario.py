"""Run in a SUBPROCESS by tests/test_google_join.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for JOINING the league with a Google account (Google sign-in step 3, 2026-11-21) and prints one JSON document.
Google itself is replaced by a throwaway RSA key pair: the tokens are really signed and really verified, only the place the public key comes from is local.
Environment: DATABASE_URL (a scratch schema)."""
import json
import os
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)
CLIENT = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_ID"] = CLIENT

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
import base  # noqa: E402
import routes  # noqa: E402
from base import app, db, hash_passcode  # noqa: E402
from logic import google_identity as gi  # noqa: E402

base._write_limiter._max = 10 ** 6
base._auth_limiter._max = 10 ** 6

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    def __init__(self, key):
        self.key = key.public_key()

    def key_for(self, token):
        return self.key


gi.set_key_source(FakeKeys(KEY))


def token(sub, email, nonce):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": sub, "email": email, "email_verified": True, "nonce": nonce, "iat": now, "exp": now + 3600, "name": "Test " + sub}
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})


def call(method, url, tok=None, body=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    r = app.test_client().open(url, method=method, json=body, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True), "text": r.get_data(as_text=True)[:3000]}


def sign_in(sub, email):
    n = call("GET", "/api/auth/nonce")["body"]["nonce"]
    return call("POST", "/api/auth/google", None, {"credential": token(sub, email, n), "nonce": n})


def ticket_of(reply):
    return ((reply["body"] or {}).get("claim") or {}).get("ticket")


def join(ticket, name, **extra):
    return call("POST", "/api/auth/join", None, {"ticket": ticket, "name": name, **extra})


def fresh(n):
    """A new Google account that belongs to nobody: (sign-in reply, its ticket)."""
    r = sign_in("g-join-%s" % n, "person.%s@gmail.com" % n)
    return r, ticket_of(r)


def sql(query, params=()):
    with db._pool.connection() as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


out = {}

# ── members: Sai (admin, 1111) and Moe (2222) already exist ──────────────────────────────────────────────────────────────────────────────────────────────────
if not db.get_competition("ipl_2026"):
    db.create_competition("ipl_2026", "IPL_2026", status="completed", max_weeks=10, week1_anchor_utc="2026-03-30T14:00:00+00:00")
for who, pc in (("Sai", "1111"), ("Moe", "2222")):
    db.upsert_member(who, {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
    db.upsert_member_auth(who, hash_passcode(pc, who), 0, 1 if who == "Sai" else 0)
db.create_session("tok-sai", "Sai")
db.create_session("tok-moe", "Moe")
ALL_HASHES = [r["passcode_hash"] for r in sql("SELECT passcode_hash FROM members")]

# ── A. a new Google account is told whether it may join ─────────────────────────────────────────────────────────────────────────────────────────────────────
r1, t1 = fresh("one")
out["a_offer"] = r1
out["a_statuses"] = {m["name"]: m["status"] for m in r1["body"]["claim"]["members"]}

# ── B. joining: a display name and nothing else ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["b_members_before"] = sql("SELECT count(*) AS n FROM members")[0]["n"]
out["b_join"] = join(t1, "Nina", sub="evil-sub", email="evil@example.com", is_admin=True, passcode="1234")      # extra fields are ignored
nina_tok = (out["b_join"]["body"] or {}).get("token") or "none"
out["b_whoami"] = call("GET", "/api/whoami", nina_tok)
out["b_member_db"] = sql("SELECT username, passcode_hash, must_change, is_admin FROM members WHERE username='Nina'")
out["b_identity_db"] = sql("SELECT subject, member, email FROM identities WHERE member='Nina'")
out["b_selection_rows"] = sql("SELECT count(*) AS n FROM user_selections WHERE display_name='Nina'")[0]["n"]
out["b_state_has_member"] = "Nina" in ((call("GET", "/api/state")["body"] or {}).get("members") or {})
out["b_admin_list"] = call("GET", "/api/admin/members", "tok-sai")
out["b_pin_login"] = call("POST", "/api/login", None, {"name": "Nina", "passcode": "1234"})
out["b_google_again"] = sign_in("g-join-one", "person.one@gmail.com")
out["b_config"] = call("GET", "/api/auth/config")
out["b_ticket_reuse"] = join(t1, "Nina Two")
out["b_evil_absent"] = sql("SELECT count(*) AS n FROM identities WHERE subject='evil-sub' OR email='evil@example.com'")[0]["n"] == 0
out["b_next_offer_statuses"] = {m["name"]: m["status"] for m in fresh("probe")[0]["body"]["claim"]["members"]}

# ── C. what a name may be ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
r2, t2 = fresh("two")
out["c_blank"] = join(t2, "   ")
out["c_none"] = call("POST", "/api/auth/join", None, {"ticket": t2})
out["c_too_long"] = join(t2, "x" * 31)
out["c_control_chars"] = join(t2, "a\x00b")
out["c_line_breaks_only"] = join(t2, "\n\t\r")
out["c_exact_duplicate"] = join(t2, "Moe")
out["c_case_duplicate"] = join(t2, "moe")
out["c_padded_duplicate"] = join(t2, "  MOE  ")
out["c_duplicate_of_a_joiner"] = join(t2, "nina")
out["c_not_a_string"] = join(t2, 12345)
out["c_no_ticket"] = call("POST", "/api/auth/join", None, {"name": "Zoe"})
out["c_made_up_ticket"] = join("made-up-by-an-attacker", "Zoe")
out["c_not_json"] = app.test_client().post("/api/auth/join", data="ticket=x", content_type="text/plain").status_code
out["c_ticket_survived_every_refusal"] = routes._CLAIMS.peek(t2) is not None
out["c_spaces_collapsed"] = join(t2, "  Ravi \t  Kumar ")
out["c_ravi_db"] = sql("SELECT username FROM members WHERE lower(username) LIKE 'ravi%%'")
r3, t3 = fresh("three")
out["c_exactly_30"] = join(t3, "y" * 30)

# ── D. an admin can close joining; the league has a cap ─────────────────────────────────────────────────────────────────────────────────────────────────────
r4, t4 = fresh("four")
out["d_non_admin"] = call("POST", "/api/admin/joining", "tok-moe", {"open": False})
out["d_no_session"] = call("POST", "/api/admin/joining", None, {"open": False})
out["d_bad_body"] = call("POST", "/api/admin/joining", "tok-sai", {"open": "yes"})
out["d_close"] = call("POST", "/api/admin/joining", "tok-sai", {"open": False})
out["d_members_view"] = call("GET", "/api/admin/members", "tok-sai")
r5, t5 = fresh("five")
out["d_offer_closed"] = r5
out["d_join_closed"] = join(t5, "Closed Out")
out["d_ticket_alive_after_closed"] = routes._CLAIMS.peek(t5) is not None
out["d_reopen"] = call("POST", "/api/admin/joining", "tok-sai", {"open": True})
out["d_join_after_reopen"] = join(t5, "Closed Out")
n_now = sql("SELECT count(*) AS n FROM members")[0]["n"]
r6, t6 = fresh("six")
real_cap = routes.MAX_MEMBERS
routes.MAX_MEMBERS = n_now
out["d_offer_full"] = sign_in("g-join-six-b", "person.sixb@gmail.com")
out["d_join_full"] = join(t6, "Too Late")
out["d_dao_full"] = None
try:
    db.create_member_with_identity("Direct", "google", "g-direct", "direct@gmail.com", n_now)
    out["d_dao_full"] = "created"
except ValueError as e:
    out["d_dao_full"] = str(e)
out["d_direct_absent"] = sql("SELECT count(*) AS n FROM members WHERE username='Direct'")[0]["n"] == 0 and sql("SELECT count(*) AS n FROM identities WHERE subject='g-direct'")[0]["n"] == 0
routes.MAX_MEMBERS = real_cap
out["d_cap_in_view"] = out["d_members_view"]["body"].get("member_cap")

# ── E. the passcode registration is closed while Google is on, and works again when Google is off ──────────────────────────────────────────────────────────
out["e_register_with_google_on"] = call("POST", "/api/register", None, {"name": "OldWay", "passcode": "5555"})
out["e_oldway_absent"] = sql("SELECT count(*) AS n FROM members WHERE username='OldWay'")[0]["n"] == 0
os.environ.pop("GOOGLE_CLIENT_ID")
out["e_register_with_google_off"] = call("POST", "/api/register", None, {"name": "OldWay", "passcode": "5555"})
out["e_join_with_google_off"] = join("anything", "Whoever")
os.environ["GOOGLE_CLIENT_ID"] = CLIENT

# ── F. races ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
ra, ta = fresh("race-a")
rb, tb = fresh("race-b")
res = {}
gate = threading.Barrier(2)


def racer(key, t, name):
    gate.wait()
    res[key] = join(t, name)


ths = [threading.Thread(target=racer, args=(k, t, n)) for k, t, n in (("a", ta, "Racer"), ("b", tb, "racer"))]
[t.start() for t in ths]
[t.join() for t in ths]
out["f_same_name"] = {k: {"http": r["http"], "reason": (r["body"] or {}).get("reason")} for k, r in res.items()}
out["f_same_name_db"] = sql("SELECT username FROM members WHERE lower(username)='racer'")
rc, tc = fresh("race-c")
res2 = {}
gate2 = threading.Barrier(2)


def racer2(key, name):
    gate2.wait()
    res2[key] = join(tc, name)


ths = [threading.Thread(target=racer2, args=(k, n)) for k, n in (("x", "Twin One"), ("y", "Twin Two"))]
[t.start() for t in ths]
[t.join() for t in ths]
out["f_one_ticket"] = {k: {"http": r["http"], "reason": (r["body"] or {}).get("reason")} for k, r in res2.items()}
out["f_one_ticket_identities"] = sql("SELECT count(*) AS n FROM identities WHERE subject='g-join-race-c'")[0]["n"]
out["f_one_ticket_members"] = sql("SELECT count(*) AS n FROM members WHERE username IN ('Twin One','Twin Two')")[0]["n"]
# one slot left, two Google accounts: exactly one joins
n_slot = sql("SELECT count(*) AS n FROM members")[0]["n"]
rd, td = fresh("slot-a")
re_, te = fresh("slot-b")
routes.MAX_MEMBERS = n_slot + 1
res3 = {}
gate3 = threading.Barrier(2)


def racer3(key, t, name):
    gate3.wait()
    res3[key] = join(t, name)


ths = [threading.Thread(target=racer3, args=(k, t, n)) for k, t, n in (("a", td, "Slot A"), ("b", te, "Slot B"))]
[t.start() for t in ths]
[t.join() for t in ths]
routes.MAX_MEMBERS = real_cap
out["f_last_slot"] = {k: {"http": r["http"], "reason": (r["body"] or {}).get("reason")} for k, r in res3.items()}
out["f_last_slot_members"] = sql("SELECT count(*) AS n FROM members WHERE username IN ('Slot A','Slot B')")[0]["n"]
# the same Google account signing in twice leaves only the newest ticket alive
rg1, tg1 = fresh("twice")
rg2, tg2 = fresh("twice")
out["f_old_ticket"] = join(tg1, "Twice")
out["f_new_ticket"] = join(tg2, "Twice")

# ── G. what a caller can see ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
every = "\n".join([out["a_offer"]["text"], out["b_join"]["text"], out["b_google_again"]["text"], out["d_offer_closed"]["text"], out["b_admin_list"]["text"]])
out["g_full_addresses_seen"] = [e for e in ("person.one@gmail.com", "person.five@gmail.com", "person.two@gmail.com") if e in every]
out["g_join_has_ticket"] = "ticket" in (out["b_join"]["body"] or {})
out["g_join_keys"] = sorted(out["b_join"]["body"])
out["g_offer_join_keys"] = sorted(out["a_offer"]["body"]["claim"]["join"])
out["g_hash_leak"] = any(h and h in every for h in ALL_HASHES)

# ── H. the sign-in rate limit ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
base._auth_limiter._max = 3
base._auth_limiter._calls.clear()
out["h_limited"] = [call("POST", "/api/auth/join", None, {"ticket": "x", "name": "Zoe"})["http"] for _ in range(5)]
base._auth_limiter._max = 10 ** 6

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")

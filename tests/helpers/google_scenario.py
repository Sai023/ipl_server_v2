"""Run in a SUBPROCESS by tests/test_google_signin.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for Google sign-in (Phase 3, 2026-11-21) and prints one JSON document. Google itself is replaced by a
throwaway RSA key pair: the tokens are really signed and really verified, only the place the public key comes from is local.
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
from base import app, db, hash_passcode  # noqa: E402
from logic import google_identity as gi  # noqa: E402

base._write_limiter._max = 10 ** 6
base._auth_limiter._max = 10 ** 6

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    def __init__(self, key):
        self.key = key.public_key()
        self.down = False

    def key_for(self, token):
        if self.down:
            raise gi.GoogleAuthError("google_unreachable", "simulated outage")
        return self.key


FAKE = FakeKeys(KEY)
gi.set_key_source(FAKE)


def token(sub, email, nonce, key=None, **over):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": sub, "email": email, "email_verified": True, "nonce": nonce, "iat": now, "exp": now + 3600, "name": "Test " + sub}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key or KEY, algorithm="RS256", headers={"kid": "k1"})


def call(method, url, tok=None, body=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    r = app.test_client().open(url, method=method, json=body, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True), "text": r.get_data(as_text=True)[:2000]}


def new_nonce():
    return call("GET", "/api/auth/nonce")["body"]["nonce"]


def sign_in(sub, email, **over):
    """A complete Google sign-in attempt: a fresh nonce, a token carrying it, the POST. Returns (reply, credential, nonce)."""
    n = new_nonce()
    cred = token(sub, email, n, **over)
    return call("POST", "/api/auth/google", None, {"credential": cred, "nonce": n}), cred, n


def link(session, sub, email, **over):
    n = new_nonce()
    return call("POST", "/api/auth/link", session, {"credential": token(sub, email, n, **over), "nonce": n})


def sql(query, params=()):
    with db._pool.connection() as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


def sql_exec(query, params=()):
    with db._pool.connection() as con:
        con.execute(query, params)


out = {}

# ── members: Sai (admin), Moe, Buddy, Zed, each with the passcode 1234 and a session ─────────────────────────────────────────────────────────
if not db.get_competition("ipl_2026"):
    db.create_competition("ipl_2026", "IPL_2026", status="completed", max_weeks=10, week1_anchor_utc="2026-03-30T14:00:00+00:00")
for who in ("Sai", "Moe", "Buddy", "Zed"):
    db.upsert_member(who, {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
    db.upsert_member_auth(who, hash_passcode("1234", who), 0, 1 if who == "Sai" else 0)
    db.create_session("tok-" + who.lower(), who)

# ── A. configuration and nonces ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["a_config"] = call("GET", "/api/auth/config")
out["a_nonce1"], out["a_nonce2"] = call("GET", "/api/auth/nonce"), call("GET", "/api/auth/nonce")
os.environ.pop("GOOGLE_CLIENT_ID")
out["a_off_config"] = call("GET", "/api/auth/config")
out["a_off_nonce"] = call("GET", "/api/auth/nonce")
out["a_off_google"] = call("POST", "/api/auth/google", None, {"credential": "a.b.c", "nonce": "x"})
os.environ["GOOGLE_CLIENT_ID"] = CLIENT

# ── B. signing in ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
sessions_before = sql("SELECT count(*) AS n FROM sessions")[0]["n"]
out["b_unlinked"], cred_new, n_new = sign_in("g-new", "newcomer.test@gmail.com")
out["b_sessions_unchanged"] = sql("SELECT count(*) AS n FROM sessions")[0]["n"] == sessions_before
out["b_replay"] = call("POST", "/api/auth/google", None, {"credential": cred_new, "nonce": n_new})            # the same credential and nonce again
n_a, n_b = new_nonce(), new_nonce()
out["b_nonce_not_in_token"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a), "nonce": n_b})
out["b_nonce_never_issued"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", "made-up-by-an-attacker"), "nonce": "made-up-by-an-attacker"})
out["b_no_nonce"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a)})
out["b_wrong_audience"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a, aud="9999-other.apps.googleusercontent.com"), "nonce": n_a})
out["b_wrong_signature"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a, key=OTHER_KEY), "nonce": n_a})
out["b_expired"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a, iat=int(time.time()) - 7200, exp=int(time.time()) - 3600), "nonce": n_a})
out["b_unverified_email"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a, email_verified=False), "nonce": n_a})
out["b_junk"] = call("POST", "/api/auth/google", None, {"credential": "not-a-token", "nonce": n_a})
out["b_empty_body"] = call("POST", "/api/auth/google", None, {})
out["b_not_json"] = app.test_client().post("/api/auth/google", data="credential=abc", content_type="text/plain").status_code
# the nonce survived all those refusals except where a good token spent it: it still works for a good token
out["b_nonce_survives_refusals"] = call("POST", "/api/auth/google", None, {"credential": token("g-x", "x.test@gmail.com", n_a), "nonce": n_a})
# a Google outage in the middle does not burn the nonce: the same credential works once Google is back
n_c = new_nonce()
cred_c = token("g-outage", "outage.test@gmail.com", n_c)
FAKE.down = True
out["b_outage"] = call("POST", "/api/auth/google", None, {"credential": cred_c, "nonce": n_c})
FAKE.down = False
out["b_after_outage"] = call("POST", "/api/auth/google", None, {"credential": cred_c, "nonce": n_c})

# ── C. switching (linking) ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
n_nt = new_nonce()
out["c_link_no_session"] = call("POST", "/api/auth/link", None, {"credential": token("g-moe", "moe.test@gmail.com", n_nt), "nonce": n_nt})
out["c_link_bad_session"] = call("POST", "/api/auth/link", "not-a-session", {"credential": token("g-moe", "moe.test@gmail.com", n_nt), "nonce": n_nt})
out["c_moe_whoami_before"] = call("GET", "/api/whoami", "tok-moe")
out["c_link_moe"] = link("tok-moe", "g-moe", "moe.test@gmail.com")
out["c_moe_whoami_after"] = call("GET", "/api/whoami", "tok-moe")
out["c_moe_pin_login"] = call("POST", "/api/login", None, {"name": "Moe", "passcode": "1234"})
out["c_moe_db"] = sql("SELECT passcode_hash, must_change FROM members WHERE username='Moe'")[0]
out["c_moe_identity_db"] = sql("SELECT provider, subject, member, email FROM identities WHERE member='Moe'")
# a sign-in refreshes what Google reports today (an address can change at Google, the sub never does) and the time: plant a stale address and an old time first
sql_exec("UPDATE identities SET email=%s, last_login_at=%s WHERE member='Moe'", ("stale.address@gmail.com", "2000-01-01T00:00:00+00:00"))
out["c_moe_google_signin"], _, _ = sign_in("g-moe", "moe.test@gmail.com")
out["c_moe_identity_after_signin"] = sql("SELECT email, last_login_at FROM identities WHERE member='Moe'")
new_tok = (out["c_moe_google_signin"]["body"] or {}).get("token") or "none"
out["c_moe_new_session_whoami"] = call("GET", "/api/whoami", new_tok)
out["c_moe_pin_change"] = call("POST", "/api/passcode/change", "tok-moe", {"new": "4321"})
out["c_admin_reset_moe"] = call("POST", "/api/admin/passcode/reset", "tok-sai", {"target_username": "Moe"})
out["c_moe_db_after_refusals"] = sql("SELECT passcode_hash FROM members WHERE username='Moe'")[0]["passcode_hash"]
# the PIN members who have NOT switched are untouched
out["c_buddy_pin_login"] = call("POST", "/api/login", None, {"name": "Buddy", "passcode": "1234"})
out["c_buddy_whoami"] = call("GET", "/api/whoami", "tok-buddy")
out["c_config_still_pin"] = call("GET", "/api/auth/config")
out["c_pin_names_db"] = [r["username"] for r in sql("SELECT username FROM members WHERE passcode_hash <> '-' ORDER BY lower(username), username")]

# conflicts
out["c_buddy_takes_moes_account"] = link("tok-buddy", "g-moe", "moe.test@gmail.com")
out["c_buddy_db_after"] = sql("SELECT passcode_hash FROM members WHERE username='Buddy'")[0]["passcode_hash"] != "-"
out["c_moe_second_account"] = link("tok-moe", "g-moe-2", "moe.second@gmail.com")
out["c_moe_identity_after_second"] = sql("SELECT subject FROM identities WHERE member='Moe'")
out["c_moe_relink_same"] = link("tok-moe", "g-moe", "moe.test@gmail.com")
out["c_identity_count_after_relink"] = sql("SELECT count(*) AS n FROM identities")[0]["n"]
out["c_link_email_unverified"] = link("tok-zed", "g-zed", "zed.test@gmail.com", email_verified=False)
out["c_zed_still_pin"] = sql("SELECT passcode_hash FROM members WHERE username='Zed'")[0]["passcode_hash"] != "-"

# a race: Buddy and Zed link the SAME Google account at the same moment; exactly one may win
results = {}
gate = threading.Barrier(2)


def racer(who):
    n = new_nonce()
    cred = token("g-race", "race.test@gmail.com", n)
    gate.wait()
    results[who] = call("POST", "/api/auth/link", "tok-" + who.lower(), {"credential": cred, "nonce": n})


ts = [threading.Thread(target=racer, args=(w,)) for w in ("Buddy", "Zed")]
[t.start() for t in ts]
[t.join() for t in ts]
out["c_race"] = {w: {"http": r["http"], "reason": (r["body"] or {}).get("reason")} for w, r in results.items()}
out["c_race_db"] = sql("SELECT member FROM identities WHERE subject='g-race'")
out["c_race_pins_retired"] = {w: sql("SELECT passcode_hash FROM members WHERE username=%s", (w,))[0]["passcode_hash"] == "-" for w in ("Buddy", "Zed")}

# ── D. recovery by the admin ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["d_non_admin_unlink"] = call("POST", "/api/admin/auth/unlink", "tok-moe", {"target_username": "Zed"})
out["d_no_target"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {})
out["d_unknown"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Nobody"})
out["d_not_linked"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Sai"})
out["d_unlink_moe"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Moe"})
out["d_moe_old_session"] = call("GET", "/api/whoami", "tok-moe")
out["d_moe_google_session"] = call("GET", "/api/whoami", new_tok)
out["d_moe_identity_gone"] = sql("SELECT count(*) AS n FROM identities WHERE member='Moe'")[0]["n"] == 0
out["d_moe_pin_login"] = call("POST", "/api/login", None, {"name": "Moe", "passcode": "1234"})
out["d_moe_google_now_unlinked"], _, _ = sign_in("g-moe", "moe.test@gmail.com")
out["d_unlink_again"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Moe"})

# ── E. what other people can see: never an address ───────────────────────────────────────────────────────────────────────────────────────────
out["e_link_moe_again"] = link((out["d_moe_pin_login"]["body"] or {}).get("token") or "none", "g-moe", "moe.test@gmail.com")
out["e_admin_members"] = call("GET", "/api/admin/members", "tok-sai")
out["e_state"] = call("GET", "/api/state")
out["e_whoami_sai"] = call("GET", "/api/whoami", "tok-sai")
out["e_emails"] = ["moe.test@gmail.com", "race.test@gmail.com", "newcomer.test@gmail.com", "zed.test@gmail.com", "moe.second@gmail.com"]

# ── F. signing out for real ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["f_logout"] = call("POST", "/api/auth/logout", "tok-buddy")
out["f_after_logout"] = call("GET", "/api/whoami", "tok-buddy")
out["f_logout_no_token"] = call("POST", "/api/auth/logout")
out["f_logout_junk"] = call("POST", "/api/auth/logout", "junk")
out["f_logout_twice"] = call("POST", "/api/auth/logout", "tok-buddy")

# ── G. once everybody has switched, nobody has a passcode ────────────────────────────────────────────────────────────────────────────────────
for who in ("Sai", "Buddy", "Zed"):
    if sql("SELECT passcode_hash FROM members WHERE username=%s", (who,))[0]["passcode_hash"] == "-":
        continue                                                      # already switched (the winner of the race)
    sess = (call("POST", "/api/login", None, {"name": who, "passcode": "1234"})["body"] or {}).get("token")
    link(sess, "g-" + who.lower(), who.lower() + ".test@gmail.com")
out["g_pins_left"] = sql("SELECT username FROM members WHERE passcode_hash <> '-' ORDER BY username")
out["g_config"] = call("GET", "/api/auth/config")
out["g_config_keys"] = sorted(out["g_config"]["body"])

# ── H. the sign-in budget (last: it uses the limiter up) ────────────────────────────────────────────────────────────────────────────────────
base._auth_limiter._max = 5
base._auth_limiter._calls.clear()
codes = [call("GET", "/api/auth/nonce")["http"] for _ in range(8)]
out["h_codes"] = codes
out["h_limited_body"] = call("GET", "/api/auth/nonce")["body"]
base._auth_limiter._max = 10 ** 6

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")

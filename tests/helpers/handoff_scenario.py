"""Run in a SUBPROCESS by tests/test_app_handoff.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for the browser-to-app hand-off (sign-in from the installed app, 2026-11-21) and prints one JSON document.
The hand-off does not depend on Google, so most of it runs with GOOGLE_CLIENT_ID unset; the end checks it with Google on.
Environment: DATABASE_URL (a scratch schema)."""
import json
import os
import secrets
import sys
import threading

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.pop("HOSTED", None)
os.environ.pop("GOOGLE_CLIENT_ID", None)

import server  # noqa: E402,F401   (registers the blueprint on base.app; nothing is started: that is under __main__)
import base  # noqa: E402
import routes  # noqa: E402
from base import app, db, hash_passcode  # noqa: E402
from logic import app_handoff as ah  # noqa: E402

base._write_limiter._max = 10 ** 6
base._auth_limiter._max = 10 ** 6
LOGGED = []
routes._log = lambda msg, *a, **k: LOGGED.append(str(msg))


def call(method, url, tok=None, body=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    r = app.test_client().open(url, method=method, json=body, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True), "text": r.get_data(as_text=True)[:3000]}


def pair():
    """(verifier, challenge): what the app makes."""
    v = secrets.token_urlsafe(32)
    return v, ah.challenge_of(v)


def ask(tok, challenge, **extra):
    return call("POST", "/api/auth/handoff", tok, {"challenge": challenge, **extra})


def redeem(code, verifier, **extra):
    return call("POST", "/api/auth/handoff/redeem", None, {"code": code, "verifier": verifier, **extra})


def sql(query, params=()):
    with db._pool.connection() as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


def sessions_of(name):
    return sorted(r["token"] for r in sql("SELECT token FROM sessions WHERE username=%s", (name,)))


out = {}

# ── members: Sai (admin, 1111), Moe (2222), Buddy (a TEMPORARY passcode: must change), Gia (Google only) ─────────────────────────────────────────────────────
if not db.get_competition("ipl_2026"):
    db.create_competition("ipl_2026", "IPL_2026", status="completed", max_weeks=10, week1_anchor_utc="2026-03-30T14:00:00+00:00")
for who, pc, must, admin in (("Sai", "1111", 0, 1), ("Moe", "2222", 0, 0), ("Buddy", "1234", 1, 0)):
    db.upsert_member(who, {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
    db.upsert_member_auth(who, hash_passcode(pc, who), must, admin)
db.create_member_with_identity("Gia", "google", "g-gia", "gia.player@gmail.com", 30)
db.create_session("tok-sai", "Sai")
db.create_session("tok-moe", "Moe")
db.create_session("tok-buddy", "Buddy")
db.create_session("tok-gia", "Gia")
V, C = pair()

# ── A. the browser asks for a code ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["a_no_session"] = ask(None, C)
out["a_unknown_session"] = ask("f" * 64, C)
out["a_bad_challenges"] = {k: ask("tok-moe", c)["http"] for k, c in {"none": None, "number": 123, "short": C[:42], "long": C + "a", "plus": C[:42] + "+", "slash": C[:42] + "/",
                                                                      "padded": C[:42] + "=", "space": C[:42] + " ", "empty": ""}.items()}
out["a_bad_reason"] = ask("tok-moe", C[:42])
out["a_missing_body"] = call("POST", "/api/auth/handoff", "tok-moe", None)
out["a_not_json"] = app.test_client().post("/api/auth/handoff", data="challenge=x", content_type="text/plain", headers={"Authorization": "Bearer tok-moe"}).status_code
out["a_methods"] = [call("GET", "/api/auth/handoff", "tok-moe")["http"], call("GET", "/api/auth/handoff/redeem")["http"]]
out["a_issue"] = ask("tok-moe", C)
CODE = (out["a_issue"]["body"] or {}).get("code") or "none"

# ── B. the app redeems it ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
out["b_sessions_before"] = sessions_of("Moe")
out["b_redeem"] = redeem(CODE, V)
new_tok = (out["b_redeem"]["body"] or {}).get("token") or "none"
out["b_sessions_after"] = sessions_of("Moe")
out["b_whoami_new"] = call("GET", "/api/whoami", new_tok)
out["b_whoami_old"] = call("GET", "/api/whoami", "tok-moe")
out["b_again"] = redeem(CODE, V)
out["b_sessions_after_again"] = sessions_of("Moe")

# ── C. what is refused ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
v1, c1 = pair()
code1 = ask("tok-moe", c1)["body"]["code"]
out["c_wrong_verifier"] = redeem(code1, pair()[0])
out["c_right_verifier_after_a_wrong_one"] = redeem(code1, v1)          # the wrong guess burned the code
out["c_unknown_code"] = redeem(secrets.token_urlsafe(32), v1)
v2, c2 = pair()
code2 = ask("tok-moe", c2)["body"]["code"]
out["c_malformed"] = {k: redeem(*args)["http"] for k, args in {"short_code": ("abc", v2), "short_verifier": (code2, "abc"), "long_verifier": (code2, "a" * 129), "bad_chars": (code2, v2[:40] + "+/=")}.items()}
out["c_malformed_not_strings"] = [call("POST", "/api/auth/handoff/redeem", None, b)["http"] for b in ({}, {"code": 1, "verifier": 2}, {"code": code2}, {"verifier": v2}, {"code": [code2], "verifier": [v2]})]
out["c_malformed_reason"] = redeem("abc", v2)
out["c_live_code_survived_malformed"] = redeem(code2, v2)
out["c_code_as_session"] = call("GET", "/api/whoami", code1)
v3, c3 = pair()
code3 = ask("tok-moe", c3, username="Sai", name="Sai", is_admin=True)["body"]["code"]
out["c_bound_to_the_asker"] = redeem(code3, v3, name="Sai", username="Sai", is_admin=True)
# one live code per (member, challenge): asking again for the same challenge replaces the older code
v4, c4 = pair()
old4 = ask("tok-moe", c4)["body"]["code"]
new4 = ask("tok-moe", c4)["body"]["code"]
out["c_replaced_code"] = {"different": old4 != new4, "old": redeem(old4, v4)["http"], "new": redeem(new4, v4)["http"]}
# two challenges of one member live side by side
v5, c5 = pair()
v6, c6 = pair()
k5, k6 = ask("tok-moe", c5)["body"]["code"], ask("tok-moe", c6)["body"]["code"]
out["c_two_challenges"] = [redeem(k6, v6)["http"], redeem(k5, v5)["http"]]
# a code for a challenge cannot be redeemed with the verifier of ANOTHER challenge of the same member
v7, c7 = pair()
v8, c8 = pair()
k7 = ask("tok-moe", c7)["body"]["code"]
out["c_other_verifier_same_member"] = redeem(k7, v8)["http"]
# a code whose member is gone
v9, c9 = pair()
ghost = routes._HANDOFFS.issue("Ghost", c9)
out["c_missing_member"] = redeem(ghost, v9)
out["c_missing_member_sessions"] = sessions_of("Ghost")
# expiry on the store's own clock (a fresh store, so a clock that jumps never mixes with codes issued on the real one)
now = [1000.0]
real_store = routes._HANDOFFS
routes._HANDOFFS = ah.HandoffStore(clock=lambda: now[0])
v10, c10 = pair()
k10 = ask("tok-moe", c10)["body"]["code"]
now[0] += 119
v11, c11 = pair()
k11 = ask("tok-moe", c11)["body"]["code"]
out["c_just_in_time"] = redeem(k11, v11)["http"]
now[0] += 2
out["c_expired"] = redeem(k10, v10)
routes._HANDOFFS = real_store

# ── D. who can be handed over ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
va, ca = pair()
out["d_admin"] = redeem(ask("tok-sai", ca)["body"]["code"], va)
vg, cg = pair()
out["d_google_member"] = redeem(ask("tok-gia", cg)["body"]["code"], vg)
vb, cb = pair()
out["d_temp_passcode"] = redeem(ask("tok-buddy", cb)["body"]["code"], vb)
out["d_new_admin_session_is_admin"] = call("GET", "/api/whoami", out["d_admin"]["body"]["token"])["body"]
out["d_new_google_session"] = call("GET", "/api/whoami", out["d_google_member"]["body"]["token"])["body"]

# ── E. the rate limits and a full store ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
base._auth_limiter._max = 3
base._auth_limiter._calls.clear()
out["e_redeem_limited"] = [redeem(secrets.token_urlsafe(32), pair()[0])["http"] for _ in range(5)]
base._auth_limiter._calls.clear()
out["e_ask_limited"] = [ask("tok-moe", pair()[1])["http"] for _ in range(5)]
base._auth_limiter._max = 10 ** 6
base._auth_limiter._calls.clear()
real_cap = routes._HANDOFFS._cap
routes._HANDOFFS._cap = 0
out["e_full"] = ask("tok-moe", pair()[1])
routes._HANDOFFS._cap = real_cap

# ── F. a race for one code ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
vr, cr = pair()
kr = ask("tok-moe", cr)["body"]["code"]
res = {}
gate = threading.Barrier(4)


def racer(i):
    gate.wait()
    res[i] = redeem(kr, vr)["http"]


ths = [threading.Thread(target=racer, args=(i,)) for i in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
out["f_race"] = sorted(res.values())

# ── G. with Google on the hand-off is the same ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
os.environ["GOOGLE_CLIENT_ID"] = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"
vo, co = pair()
out["g_with_google_on"] = redeem(ask("tok-moe", co)["body"]["code"], vo)["http"]
os.environ.pop("GOOGLE_CLIENT_ID")

# ── H. what a caller can see, and what the log keeps ────────────────────────────────────────────────────────────────────────────────────────────────────────
every = "\n".join([out["a_issue"]["text"], out["b_redeem"]["text"], out["d_google_member"]["text"], out["d_admin"]["text"], out["c_expired"]["text"]])
out["h_full_address_seen"] = "gia.player@gmail.com" in every
out["h_redeem_keys"] = sorted(out["b_redeem"]["body"])
out["h_issue_keys"] = sorted(out["a_issue"]["body"])
secrets_used = [V, C, CODE, v1, c1, code1, v2, code2, v3, code3, new_tok]
out["h_log_leaks"] = [s[:6] for s in secrets_used if any(s in line for line in LOGGED)]
out["h_log_sample"] = [line for line in LOGGED if "hand-off" in line][:3]
out["h_hash_leak"] = any(h and h in every for h in [r["passcode_hash"] for r in sql("SELECT passcode_hash FROM members")] if h != "-")

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")

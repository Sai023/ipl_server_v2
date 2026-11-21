"""Run in a SUBPROCESS by tests/test_google_claim.py (so the app is imported fresh against a scratch schema, whatever else the test process loaded).
Drives the REAL routes through Flask's test client for CLAIMING a name with a Google account that belongs to nobody yet (Google sign-in step 2, 2026-11-21) and prints
one JSON document. Google itself is replaced by a throwaway RSA key pair: the tokens are really signed and really verified, only the place the public key comes from
is local.   Environment: DATABASE_URL (a scratch schema)."""
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


def token(sub, email, nonce, **over):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": sub, "email": email, "email_verified": True, "nonce": nonce, "iat": now, "exp": now + 3600, "name": "Test " + sub}
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, KEY, algorithm="RS256", headers={"kid": "k1"})


def call(method, url, tok=None, body=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    r = app.test_client().open(url, method=method, json=body, headers=headers)
    return {"http": r.status_code, "body": r.get_json(silent=True), "text": r.get_data(as_text=True)[:3000]}


def sign_in(sub, email):
    """A complete Google sign-in attempt (fresh nonce, a token carrying it, the POST)."""
    n = call("GET", "/api/auth/nonce")["body"]["nonce"]
    return call("POST", "/api/auth/google", None, {"credential": token(sub, email, n), "nonce": n})


def ticket_of(reply):
    return ((reply["body"] or {}).get("claim") or {}).get("ticket")


def claim(ticket, name, passcode, **extra):
    return call("POST", "/api/auth/claim", None, {"ticket": ticket, "name": name, "passcode": passcode, **extra})


def sql(query, params=()):
    with db._pool.connection() as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


def statuses(reply):
    return {m["name"]: m["status"] for m in ((reply["body"] or {}).get("claim") or {}).get("members", [])}


out = {}

# ── members: Sai (admin, 1111), Moe (2222), Buddy (3333), Zed (a TEMPORARY passcode: must change), Kim (5555), Lin (already linked) ─────────────────────────────
if not db.get_competition("ipl_2026"):
    db.create_competition("ipl_2026", "IPL_2026", status="completed", max_weeks=10, week1_anchor_utc="2026-03-30T14:00:00+00:00")
PASS = {"Sai": "1111", "Moe": "2222", "Buddy": "3333", "Zed": "1234", "Kim": "5555", "Lin": "6666"}
for who, pc in PASS.items():
    db.upsert_member(who, {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
    db.upsert_member_auth(who, hash_passcode(pc, who), 1 if who == "Zed" else 0, 1 if who == "Sai" else 0)
db.create_session("tok-sai", "Sai")
ALL_HASHES = [r["passcode_hash"] for r in sql("SELECT passcode_hash FROM members")]       # remembered BEFORE any claim retires one: none of them may ever appear in a reply
db.link_identity("Lin", "google", "g-lin", "lin.real@gmail.com")             # Lin switched earlier: the name is taken

# ── A. a good Google account that belongs to nobody gets a claim, not a dead end ────────────────────────────────────────────────────────────────────────────
sessions_before = sql("SELECT count(*) AS n FROM sessions")[0]["n"]
out["a_unlinked"] = sign_in("g-moe-acct", "moe.acct@gmail.com")
out["a_sessions_unchanged"] = sql("SELECT count(*) AS n FROM sessions")[0]["n"] == sessions_before
out["a_statuses"] = statuses(out["a_unlinked"])
out["a_identities_before"] = sql("SELECT count(*) AS n FROM identities")[0]["n"]
t_moe = ticket_of(out["a_unlinked"])

# ── B. the claim: pick a name, enter the passcode once ──────────────────────────────────────────────────────────────────────────────────────────────────────────
out["b_claim_moe"] = claim(t_moe, "Moe", "2222", sub="evil-sub", email="evil@example.com", member="Sai")     # extra fields are ignored: the ticket decides WHICH account
moe_tok = (out["b_claim_moe"]["body"] or {}).get("token") or "none"
out["b_whoami"] = call("GET", "/api/whoami", moe_tok)
out["b_identity_db"] = sql("SELECT subject, member, email FROM identities WHERE member='Moe'")
out["b_member_db"] = sql("SELECT passcode_hash, must_change FROM members WHERE username='Moe'")[0]
out["b_pin_login"] = call("POST", "/api/login", None, {"name": "Moe", "passcode": "2222"})
out["b_google_again"] = sign_in("g-moe-acct", "moe.acct@gmail.com")                                          # the Gmail now recognises who is signing in
out["b_ticket_reuse"] = claim(t_moe, "Buddy", "3333")
out["b_buddy_untouched"] = sql("SELECT passcode_hash <> '-' AS has_pin FROM members WHERE username='Buddy'")[0]["has_pin"]
out["b_evil_identity_absent"] = sql("SELECT count(*) AS n FROM identities WHERE subject='evil-sub' OR email='evil@example.com'")[0]["n"] == 0

# ── C. a name that is linked can never be picked again; other refusals do not burn the ticket ───────────────────────────────────────────────────────────────────
out["c_unlinked"] = sign_in("g-other", "other.acct@gmail.com")
out["c_statuses"] = statuses(out["c_unlinked"])
t_other = ticket_of(out["c_unlinked"])
out["c_pick_moe"] = claim(t_other, "Moe", "2222")                      # linked above
out["c_pick_lin"] = claim(t_other, "Lin", "6666")                      # linked before the test
out["c_pick_zed"] = claim(t_other, "Zed", "1234")                      # a temporary passcode is a known value: it cannot take a name
out["c_unknown_name"] = claim(t_other, "Nobody", "1111")
out["c_no_ticket"] = call("POST", "/api/auth/claim", None, {"name": "Buddy", "passcode": "3333"})
out["c_short_passcode"] = claim(t_other, "Buddy", "33")
out["c_blank_name"] = claim(t_other, "  ", "3333")
out["c_not_json"] = app.test_client().post("/api/auth/claim", data="ticket=x", content_type="text/plain").status_code
out["c_made_up_ticket"] = claim("made-up-by-an-attacker", "Buddy", "3333")
out["c_tries_left_after_refusals"] = routes._CLAIMS.tries_left("g-other")
out["c_pick_buddy"] = claim(t_other, "Buddy", "3333")                  # the ticket survived every refusal above
out["c_buddy_db"] = sql("SELECT i.subject, m.passcode_hash FROM identities i JOIN members m ON m.username=i.member WHERE i.member='Buddy'")

# ── D. a four-digit passcode cannot be guessed by signing in with Google again and again ────────────────────────────────────────────────────────────────────────
t_guess = ticket_of(sign_in("g-guess", "guess.acct@gmail.com"))
out["d_wrong"] = [claim(t_guess, "Kim", pc) for pc in ("0000", "1111", "2222", "3333", "4444")]      # 2222 is Moe's, 1111 is Sai's: another member's passcode is just wrong here
out["d_right_after_lock"] = claim(t_guess, "Kim", "5555")
out["d_new_sign_in_while_locked"] = sign_in("g-guess", "guess.acct@gmail.com")
out["d_locked"] = routes._CLAIMS.locked("g-guess")
out["d_other_account_unaffected"] = sign_in("g-fresh", "fresh.acct@gmail.com")
out["d_kim_untouched"] = sql("SELECT passcode_hash <> '-' AS has_pin FROM members WHERE username='Kim'")[0]["has_pin"]
out["d_kim_identity_absent"] = sql("SELECT count(*) AS n FROM identities WHERE member='Kim'")[0]["n"] == 0
# wrong passcodes then the right one: it works, and the count is cleared
t_cross = ticket_of(sign_in("g-cross", "cross.acct@gmail.com"))
out["d_cross_wrong"] = claim(t_cross, "Sai", "2222")
out["d_cross_right"] = claim(t_cross, "Sai", "1111")
out["d_cross_tries_after"] = routes._CLAIMS.tries_left("g-cross")

# ── E. races ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# two Google accounts claim the SAME free member at the same moment: exactly one wins
tx, ty = ticket_of(sign_in("g-x", "x.acct@gmail.com")), ticket_of(sign_in("g-y", "y.acct@gmail.com"))
res = {}
gate = threading.Barrier(2)


def racer(key, t):
    gate.wait()
    res[key] = claim(t, "Kim", "5555")


ths = [threading.Thread(target=racer, args=(k, t)) for k, t in (("x", tx), ("y", ty))]
[t.start() for t in ths]
[t.join() for t in ths]
out["e_race_members"] = {k: {"http": r["http"], "reason": (r["body"] or {}).get("reason")} for k, r in res.items()}
out["e_race_db"] = sql("SELECT subject FROM identities WHERE member='Kim'")
# one ticket, eight requests at once: it is used up once
t_one = ticket_of(sign_in("g-one", "one.acct@gmail.com"))
db.upsert_member("Raj", {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
db.upsert_member_auth("Raj", hash_passcode("7777", "Raj"), 0, 0)
res8 = []
gate8 = threading.Barrier(8)


def racer8():
    gate8.wait()
    res8.append(claim(t_one, "Raj", "7777"))


ths = [threading.Thread(target=racer8) for _ in range(8)]
[t.start() for t in ths]
[t.join() for t in ths]
out["e_one_ticket_http"] = sorted(r["http"] for r in res8)
out["e_one_ticket_identity_rows"] = sql("SELECT count(*) AS n FROM identities WHERE member='Raj'")[0]["n"]
# the same Google account signing in twice: only the newest ticket is alive
db.upsert_member("Ann", {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
db.upsert_member_auth("Ann", hash_passcode("8888", "Ann"), 0, 0)
t_old = ticket_of(sign_in("g-twice", "twice.acct@gmail.com"))
t_new = ticket_of(sign_in("g-twice", "twice.acct@gmail.com"))
out["e_old_ticket"] = claim(t_old, "Ann", "8888")
out["e_new_ticket"] = claim(t_new, "Ann", "8888")

# ── F. a ticket is only good for ten minutes ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
db.upsert_member("Ben", {"this_week": {"team": [], "cap": None, "vc": None}, "next_week": {"team": [], "cap": None, "vc": None}})
db.upsert_member_auth("Ben", hash_passcode("9999", "Ben"), 0, 0)
t_late = ticket_of(sign_in("g-late", "late.acct@gmail.com"))
real_clock, skew = routes._CLAIMS._clock, [0.0]
routes._CLAIMS._clock = lambda: real_clock() + skew[0]
skew[0] = 570                                   # 30 s inside the ten minutes (the answer to a sign-in takes a moment on a slow database)
out["f_just_in_time_peek"] = routes._CLAIMS.peek(t_late) is not None
skew[0] = 601
out["f_late"] = claim(t_late, "Ben", "9999")
routes._CLAIMS._clock = real_clock
out["f_ben_untouched"] = sql("SELECT passcode_hash <> '-' AS has_pin FROM members WHERE username='Ben'")[0]["has_pin"]

# ── G. the admin removes a link: the name is free again, but only through a passcode the member chooses ───────────────────────────────────────────────────────
db.create_session("tok-sai-2", "Sai")                                  # a second device of Sai's
out["g_sai_linked"] = sql("SELECT count(*) AS n FROM identities WHERE member='Sai'")[0]["n"] == 1
out["g_unlink_moe"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Moe"})
out["g_moe_old_session"] = call("GET", "/api/whoami", moe_tok)         # Moe's own sessions ended
g_unlinked = out["g_unlinked"] = sign_in("g-after", "after.acct@gmail.com")
out["g_statuses"] = statuses(g_unlinked)
out["g_pick_moe_temp"] = claim(ticket_of(g_unlinked), "Moe", "1234")   # the temporary passcode is a known value: not enough to take the name
# an admin who unlinks THEMSELVES stays signed in on the device they are using; their other devices are signed out
out["g_unlink_self"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Sai"})
out["g_self_whoami_same_device"] = call("GET", "/api/whoami", "tok-sai")
out["g_self_whoami_other_device"] = call("GET", "/api/whoami", "tok-sai-2")
out["g_self_unlink_not_linked_again"] = call("POST", "/api/admin/auth/unlink", "tok-sai", {"target_username": "Sai"})
buddy_tok = (out["c_pick_buddy"]["body"] or {}).get("token") or "none"
out["g_non_admin_unlink"] = call("POST", "/api/admin/auth/unlink", buddy_tok, {"target_username": "Kim"})

# ── H. what a caller can see: names and a word each, a masked address, never a hash or a full address ────────────────────────────────────────────────────────────
every_text = "\n".join(out[k]["text"] for k in ("a_unlinked", "b_claim_moe", "b_google_again", "c_unlinked", "c_pick_buddy", "g_unlinked", "d_cross_right"))
out["h_full_addresses_seen"] = [e for e in ("moe.acct@gmail.com", "other.acct@gmail.com", "after.acct@gmail.com", "lin.real@gmail.com", "cross.acct@gmail.com") if e in every_text]
out["h_member_entry_keys"] = sorted({k for m in (out["a_unlinked"]["body"]["claim"]["members"]) for k in m})
out["h_claim_keys"] = sorted(out["a_unlinked"]["body"]["claim"])
out["h_claim_success_has_ticket"] = "ticket" in (out["b_claim_moe"]["body"] or {})
out["h_hash_leak"] = any(h and h in every_text for h in ALL_HASHES)

# ── I. the doors close with Google off, and share the sign-in rate limit ────────────────────────────────────────────────────────────────────────────────────────
base._auth_limiter._max = 3
base._auth_limiter._calls.clear()
out["i_limited"] = [call("POST", "/api/auth/claim", None, {"ticket": "x", "name": "Buddy", "passcode": "3333"})["http"] for _ in range(5)]
base._auth_limiter._max = 10 ** 6
os.environ.pop("GOOGLE_CLIENT_ID")
out["i_off"] = claim("anything", "Buddy", "3333")
os.environ["GOOGLE_CLIENT_ID"] = CLIENT

sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str) + "\n")

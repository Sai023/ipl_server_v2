# Google sign-in (Phase 3, steps 1 to 3; 2026-11-21, app 2.22.0, 2.23.0 and 2.24.0)

`logic/google_identity.py` (the server's check of Google's ID token, and the claim tickets), the `identities` table and nine routes in `routes.py` (section 1.6), and
`Static/auth_google.js` (the page's half). It replaces the 4-digit passcode over time (SA20 readiness plan, Phase 3). **Step 1** (2.22.0): the three current members switch from
the passcode to Google. **Step 2** (2.23.0): a Google account that belongs to nobody yet is claimed in ONE visit (see "Claiming a name"). **Step 3** (2.24.0): a NEW member joins
with Google and a display name, no passcode (see "Joining with Google"). The devices list, the hand-off sign-in for installed apps and removing the passcode code are the next releases.

## What it does (business view)

A member taps Google's own **Continue with Google** button, picks their Google account, and is signed in. A member who still has a passcode is offered, once
per launch, to **switch**: they sign in once with the passcode, tap Continue with Google, and from then on the passcode is retired. Nothing is shown at all until
the server says Google sign-in is set up (the environment variable `GOOGLE_CLIENT_ID` is set in Render; it is public by design, not a secret).

A member can also START from Google: tap Continue with Google and pick the Gmail. A linked Gmail signs in as its member at once (the Gmail recognises who is signing in). A Gmail
nobody has linked yet gets the sheet **Which member are you?**: the member picks their name (only members who do not have a Google account yet can be picked) and enters their
passcode once; the account is linked, the passcode retired and the member signed in. A person who is NEW chooses **I'm new here** on the same sheet, types a display name and is
in: there is no passcode to choose, and the sign-in card has no registration form any more while Google sign-in is on.

## How a token is believed (logic/google_identity.py)

The page sends `{credential, nonce}`. A token is accepted only when ALL of these hold:
signed by one of Google's published keys with the algorithm **pinned to RS256** (`none`, `HS256` and the rest are refused before any key is looked up);
`aud` is our client id; `iss` is accounts.google.com or https://accounts.google.com; not expired (60 s of clock leeway) and not issued in the future; `sub`, `exp`,
`iat`, `iss`, `aud` present; `email` present and `email_verified` true; and its `nonce` is the one the server issued for this attempt.
The identity is Google's **`sub`** (a stable account id, never reused), never the address. Google's keys come from its certificate endpoint through PyJWT's
`PyJWKClient` (cached; it refetches when a token names a key it does not hold, which is how a rotation is picked up). PyJWT is imported lazily: if it were missing,
only Google sign-in would be unavailable, never the app.

**Nonces** (`NonceStore`): `GET /api/auth/nonce` hands out a random single-use string, valid ten minutes, kept in memory (a restart forgets them: a sign-in begun
just before a restart is started again). The page gives it to Google, which signs it into the token. The server checks the token (including its nonce) FIRST and spends
the nonce only when the token is good, so a Google outage in the middle does not burn it. A captured token cannot be replayed, and a token Google signed for a nonce we
never issued is refused. The store is capped (5,000 live nonces: a flood is answered 429, never an unbounded memory).

## Routes (all in routes.py, section 1.6; Google has its own rate limit, 60/min per address, `_auth_limiter`)

| Route | What it does |
|---|---|
| `GET /api/auth/config` | `{google: {enabled, client_id}, pin_enabled}`: is Google set up (and our public client id), and does anybody still use a passcode? |
| `GET /api/auth/nonce` | A fresh single-use nonce (503 `google_not_configured` until the client id is set). |
| `POST /api/auth/google` | Sign in. A linked member gets a session exactly as `/api/login` gives one. A good Google account nobody has linked is neither an error nor a dead end: `{linked: false, email_masked, claim: {ticket, expires_in, email_masked, members: [{name, status}]}}` and no session (see "Claiming a name"). 429 `claim_locked` while that Google account is locked out of claiming. |
| `POST /api/auth/claim` | `{ticket, name, passcode}`: that member proves who they are, ONCE; the account is linked, the passcode retired (one transaction) and they are signed in (`claimed: true`). 401 `claim_expired`, 429 `claim_locked`, 409 `member_taken`, 409 `temp_passcode`, 401 `bad_credentials` + `attempts_left`, 404 `member_not_found`, 400 `credentials_required`. |
| `POST /api/auth/join` | `{ticket, name}`: a NEW member joins with the Google account the ticket stands for; the member and the identity are created in ONE transaction and they are signed in (`joined: true`). No passcode. 401 `claim_expired`, 400 `name_invalid`, 409 `name_taken`, 403 `joining_closed`, 409 `members_full`, 409 `google_account_in_use`, 400 `credentials_required`, 503 `google_not_configured`. |
| `POST /api/admin/joining` | Admin only: `{open: true\|false}` opens or closes joining (meta key `joining_open`; open until an admin closes it). Answers `{joining_open, member_count, member_cap}`. 400 `bad_request` unless `open` is a boolean. |
| `POST /api/auth/link` | A signed-in member switches: links their Google account and **retires their passcode in the same transaction** (`db.link_identity`). 409 `google_account_in_use` (it belongs to another member) or 409 `already_linked` (they have a different one: an admin must unlink first). Linking the same account again is a harmless repeat. |
| `POST /api/auth/logout` | Ends THIS device's session on the server (the page used to just forget its token). Safe to repeat. |
| `POST /api/admin/auth/unlink` | Admin only: removes a member's link, gives them the temporary passcode `1234` (must be changed at the next sign-in) and ends their sessions, so the name can be claimed again. An admin who unlinks THEMSELVES keeps the session of the device they use (`self: true` in the reply). |

Also changed: `/api/login` answers a switched member with 401 `pin_retired` ("This account signs in with Google."); `/api/passcode/change` refuses them (403
`pin_retired`); the admin passcode reset refuses them (409 `member_signs_in_with_google`: unlink first); `/api/whoami` carries `linked`, `email_masked` and
`pin_enabled`; `/api/admin/members` carries `linked` and `email_masked` (and, since 2.24.0, `joining_open`, `member_count`, `member_cap`); `POST /api/register` answers 403
`register_closed` ("New members join with Google. Tap Continue with Google.") while Google sign-in is on, and works as before while it is off (the fallback). **No address ever
appears except masked** (`s***@gmail.com`), not even to the admin.

## Claiming a name (step 2, 2.23.0): one visit instead of two

Step 1 had a dead end: a Google account nobody had linked was told "pick your name, sign in with your passcode, then choose Switch to Google", so the member tapped Google,
picked their name, typed the passcode and was then asked to tap Google AGAIN. Now a good Google account that belongs to nobody yet gets a **claim**:

1. `POST /api/auth/google` verifies the token as always. For an unlinked account it answers `{linked: false, email_masked, claim: {ticket, expires_in: 600, email_masked, members}}`
   and creates NO session. The **ticket** (`ClaimStore`, kept in memory like the nonces) stands for the Google account the SERVER verified: single-use, ten minutes, one live ticket
   per Google account (a newer sign-in replaces the older one), capped at 2,000. Nothing the page sends can change which account a ticket stands for.
2. `members` lists every member by name with one word: `free` (a passcode of their own and no Google account), `linked` (a Google account is linked: the name can NEVER be picked
   again), `temp` (their passcode is only a temporary one, a known value). Names and a word each: no hash, no address.
3. The page shows the sheet **Which member are you?** (`view.claimSheet` in Static/auth_google.js): only `free` names can be picked; the others are shown greyed with their reason
   ("has Google", "temporary passcode"). The member picks their name and types their passcode; the fourth digit sends it.
4. `POST /api/auth/claim {ticket, name, passcode}` checks the ticket, the name and the passcode; then `db.link_identity` links the account AND retires the passcode in ONE
   transaction and the member is signed in (the reply is a Google sign-in plus `claimed: true`). Refusals: 401 `claim_expired` (unknown, used, or older than ten minutes),
   429 `claim_locked`, 409 `member_taken` (the name already has a Google account), 409 `temp_passcode`, 401 `bad_credentials` with `attempts_left`, 404 `member_not_found`,
   400 `credentials_required`, 503 `google_not_configured`. It shares the 60/min sign-in limiter.
5. **Guessing is bounded.** A passcode has only four digits, so wrong ones are counted PER GOOGLE ACCOUNT over the ten-minute window (not per ticket): the fifth wrong passcode
   kills the ticket and answers 429 `claim_locked`, and signing in with Google again is no fresh start (`POST /api/auth/google` answers `claim_locked` as well). Other Google
   accounts and the passcode door are unaffected. A claim that works clears the count.
6. A member who is ALREADY signed in with a passcode still switches the old way (More > Switch to Google sign-in, `POST /api/auth/link`): they have a session, so no passcode
   is asked.

An admin removes a link from the Members tab (**Unlink Google**: the member gets the temporary passcode 1234 and must change it, their devices are signed out; the name can be
claimed again once they have chosen their own passcode). An admin who unlinks THEMSELVES stays signed in on the device they use (`unlink_identity(..., keep_token)`, reply
`self: true`) and the page opens "Set a New Passcode" at once; their other devices are signed out. Members cannot remove their own link: that is the admin's job (a name stays
linked to one Google account until the admin says otherwise).

## Joining with Google (step 3, 2.24.0): no passcode to choose

Registration used to ask a new person for a name and a 4-digit passcode (`POST /api/register`). While Google sign-in is on that route is closed (403 `register_closed`) and a new
member joins like this:

1. They tap Continue with Google and pick the Gmail. For a Gmail nobody has linked, `POST /api/auth/google` answers the claim (as in step 2) and now also `claim.join: {allowed, why}`
   (`why` is `""`, `closed` or `full`; it is not called `reason` because the reason contract reserves that word for error reasons). The same ticket serves both ways forward.
2. On the sheet **Which member are you?** the members who are still free are listed (an existing member who has not switched yet can still claim their name with their passcode),
   then "or", then **I'm new here**. That choice shows a **Your name** box and the line "You sign in with Google: there is no passcode to choose." When joining is closed or the league is
   full the option is not offered and the sheet says why ("New members can't join right now. Ask the admin." / "The league is full.").
3. `POST /api/auth/join {ticket, name}`. The name is trimmed and its white space collapsed, 1 to 30 printable characters (not a string: 400 `name_invalid`), and must be unique
   **case-insensitively** ("priya" is refused when "Priya" exists; 409 `name_taken`, and the ticket is NOT used up, so the member can try another). The ticket is checked BEFORE
   anything is written (401 `claim_expired`); then `db.create_member_with_identity` creates the member (`passcode_hash = NO_PIN`, not admin) and their identity in ONE transaction,
   serialised by a Postgres advisory lock so the cap and the name check cannot be raced. Only then is the ticket spent, an empty selection row made (so they appear in the league
   everywhere) and a session created. The page shows "Welcome, NAME! You joined with p***@gmail.com. From now on just tap Continue with Google."
4. **The cap** `MAX_MEMBERS = 30` (config.py) and the **admin switch** (Admin > Members & passcodes > "New members can join: On/Off" with Close joining / Open joining; the same
   panel shows "N of 30 places used") both stop new members only; members who are already in are untouched. The route checks both and so does the database transaction.
5. A member who joined has no passcode, so the admin's unlink gives them the temporary passcode `1234` like anybody else (`unlink_identity` always sets one): nobody is ever
   left with no way in. A Google-joined member who loses their Google account (an admin unlink) signs in with that temporary passcode, then chooses their own.
6. The sign-in card has no registration form while Google is on (a hint says "New here? Continue with Google, then choose I'm new here."); with Google off (the variable unset) it is
   exactly as before, which is what makes the rollback safe.

## Data (db_manager.py v6.11)

`identities(provider, subject, member, email, created_at, last_login_at)`, primary key `(provider, subject)`, unique `(provider, member)`, `member` references
`members(username)` (cascade). Additive and created at boot. A member without a passcode has `members.passcode_hash = NO_PIN` (`"-"`): it can never equal a real
passcode hash, so the passcode check can never pass for them. DAO: `get_identity`, `touch_identity`, `link_identity`, `unlink_identity`, `count_pin_members`,
`delete_session`, `list_members_for_claim` (names + flags for the claim list: `linked`, `has_passcode`, `must_change`), `unlink_identity(..., keep_token)`; `get_member_auth` and
`list_members_admin_view` also return `google_email`. Step 3 adds `count_members` and `create_member_with_identity(name, provider, subject, email, max_members)` (member + identity in
one transaction under `pg_advisory_xact_lock(7430011)`; raises `ValueError("full" | "name_taken" | "in_use")`). The admin switch is one `meta` row, `joining_open` (`"0"` closed;
absent or anything else = open), written only when an admin toggles it: **no schema change**.

## The page (Static/auth_google.js, `window.IplGoogle`)

`config()` (cached; a failure is not), `embeddedBrowser()` (Google refuses sign-in inside Instagram, Facebook, the old Android app ... so the member gets an "open it
in Chrome" notice with Copy link instead of a Google error), `mount(host, "signin" | "link", done)` (config, then Google's library, then a nonce, then
`google.accounts.id.initialize({client_id, nonce})` and `renderButton`; after every attempt the button is armed again with a NEW nonce; a button left on screen renews
its nonce before the server forgets it), `openSwitch()`, `offerSwitch()` (once per launch, never when a lock is within 24 hours or the member must set a passcode),
`signInAgain(name)` (a signed-out Google member, or a retired passcode). It reports through DOM events: `ipl:google-signed-in`, `ipl:google-claimed`, `ipl:google-linked`,
`ipl:google-config`, and (step 3) `ipl:google-joined` (fired right after `ipl:google-signed-in`; the page welcomes the new member); the page (templates/index.html) decides what they
mean. The DOM part is a replaceable `view`, which is how the tests drive the logic.
`Static/ipl_glue.js` gained `authConfig`, `authNonce`, `authGoogle`, `authLink`, `authLogout`, `adminUnlinkGoogle`, `authClaim`, and (step 3) `authJoin` and `adminSetJoining`; its 401
rule changed (see ipl_glue.md). The claim sheet is `view.claimSheet` (with `offerClaim` / `submitClaim` / `submitJoin`): free names, "or", "I'm new here", the name box, and
`fail(text, {noJoin | keepName | notFree | clear})` to say what went wrong without losing the member's place. `templates/index.html`: the login card without a register form while Google
is on (`_buildLoginCard`), the admin joining row (`_adminJoining`, `_adminSetJoining`) and the welcome toast.

## Turning it on, and off (operator steps)

1. **Google Cloud console:** a project; OAuth consent screen = External (in the setup wizard's Audience step choose **External**: NOT "Personal mode", which lets only the account
   that created the project ever sign in and can never be switched, and NOT "Internal", which is for Google Workspace organisations), app name + support email only (no logo,
   no links); **Testing status is enough** for a sign-in that
   asks only for the basic scopes (Google's Manage App Audience page, Testing section: such a sign-in works for any Google account, with no test-user list, no warning screen and
   no 7-day expiry; publishing is not needed); Credentials >
   Create OAuth client ID > **Web application**; **Authorized JavaScript origins** = `https://ipl-fantasy-2026-h0m9.onrender.com` (no redirect URIs; for local tests both
   `http://localhost` and `http://localhost:<port>`). The Client ID (`....apps.googleusercontent.com`) is public.
2. **Render dashboard:** Environment > add `GOOGLE_CLIENT_ID` = that value > save (Render restarts the service). Check `GET /api/auth/config`: `google.enabled` is `true`.
3. **Off again:** delete the variable (the routes answer 503 `google_not_configured`, the page shows no Google block, the passcode works as before for everyone who has not
   switched). A member who HAS switched can only get back in through the admin's unlink, so switch the three one at a time and keep the `pre-google` rollback tag.

## Headers (checked against Google's own page, 2026-11-21)

Google's page "Get your Google API client ID" says: the library `gsi/client` is a cross-origin load and **`Referrer-Policy: strict-origin-when-cross-origin` is recommended**
for the page that loads it; a **`Cross-Origin-Opener-Policy`** that is plain `same-origin` breaks the sign-in popup (it must include `same-origin-allow-popups`; no COOP at
all is fine); and a **Content-Security-Policy**, if there is one, must allow `https://accounts.google.com/gsi/` (`connect-src`, `frame-src`, `default-src`),
`https://accounts.google.com/gsi/client` (`script-src`) and `https://accounts.google.com/gsi/style` (`style-src`). `X-Frame-Options` is not mentioned (it only limits who may
frame OUR page). The server therefore sends `strict-origin-when-cross-origin` (it sent `same-origin` until 2.22.0, which sends Google no referrer at all; the addresses carry
no secrets and a cross-origin request gets the origin only), and **no COOP and no CSP**. `tests/test_hardening.py` (`test_no_header_can_stop_googles_sign_in` and the
runtime twin) fails if anybody changes the policy, or adds a COOP without `same-origin-allow-popups`, or a CSP without Google's addresses.

## Business rules

1. Nothing is reachable until `GOOGLE_CLIENT_ID` is set; unset means the app behaves exactly as before.
2. The identity is `sub`, never the address. A Google account belongs to at most one member and a member has at most one Google account.
3. A switch retires the passcode in the same transaction (a member is never left with neither way in). The way back is the admin's unlink (recovery).
4. The old Android app is a WebView and cannot sign in with Google: members use Chrome (or the installed web app) from the switch on; the switch sheet says so.
5. Sessions are the same 60-day sliding sessions as for the passcode.
6. A Google account that belongs to nobody can be claimed by a member who proves who they are with their passcode, once (step 2). The server decides which Google account (the ticket
   it issued for the account it verified); the page decides nothing about it.
7. A member who already has a Google account is never offered again and the server refuses the name too (`member_taken`): a name is linked to ONE Google account until the admin removes
   the link. Only the admin removes a link (Members tab > Unlink Google); members cannot remove their own.
8. A temporary passcode (the admin's reset or unlink value 1234) can never take a name: that member signs in with it first, chooses their own passcode, then switches.
9. Wrong passcodes at a claim are counted per Google account (five in ten minutes, then `claim_locked`); signing in with Google again is no fresh start.
10. While Google sign-in is on, a new member joins ONLY with Google (a display name, no passcode); the passcode registration is closed on the server (`register_closed`), not just
    hidden on the page. With Google off the old registration works (the fallback).
11. A display name is unique case-insensitively, 1 to 30 printable characters, white space collapsed; a refused name leaves the ticket alone.
12. The league takes at most `MAX_MEMBERS` (30) members and an admin can close joining at any time; both only stop NEW members. The member, their identity and (after the ticket is spent) their
    league row exist together or not at all.
13. A Google account belongs to at most one member, whether it got there by switching, claiming or joining; the server decides which account (the ticket), the page never does.

## Tests

`tests/test_google_identity.py` (34: real signatures, a local certs server, rotation, outage, and the claim store's rules), `tests/test_google_signin.py` + `tests/helpers/google_scenario.py` (25 on the
real routes, including two members racing for one account and the refresh of the stored address and last-login time), `tests/js/google_signin.test.js` (57: the glue, the
module, the page functions), the error-reason contract (`tests/test_error_reasons.py`: every new `reason` needs a sentence in `Static/ui.js` or an entry in the
pass-through list), and the header guard in `tests/test_hardening.py`. Deliberate-breakage runs (a script that breaks ONE thing in an exported copy of the committed tree
and requires the tests to fail): 24 server-side and 26 page-side mutants, all killed (one server survivor, "a sign-in does not refresh the identity", led to the 25th route test). Step 2 (claiming a name) added `tests/test_google_claim.py` + `tests/helpers/google_claim_scenario.py` (23 route tests on a scratch schema: the claim,
every refusal, the lockout, races, expiry, admin removal, leaks) and its own breakage run (`mutate_claim.py`: 20 server-side and 13 page-side mutants, all killed). Step 3 (joining)
added `tests/test_google_join.py` + `tests/helpers/google_join_scenario.py` (22 route tests on a scratch schema: the offer, the display name and nothing else, every refusal, the cap and the
admin switch, the closed passcode registration, races for one name and for the last place, leaks, the rate limit) and 10 page tests, and its own breakage run (`mutate_join.py`: 31 server-side
and 20 page-side mutants, all killed; the page run first showed that nothing covered the admin list remembering the joining state, which led to one more page test). The sheet's markup itself
(the join option, the name box, the closed and full notes) is not covered by the Node tests, which replace the sheet with a stand-in: it was checked in a real browser at desktop and phone width.

## Known limits (harmless for three members; settle them BEFORE the link is opened to everybody)

1. **One process.** The nonce store lives in the memory of the one server process (Render runs a single `python server.py` with threads). With several worker processes a
   nonce issued by one would be unknown to another and sign-ins would fail at random: the store would first have to move into the database.
2. **"Per address" is really "per proxy".** The server does not read `X-Forwarded-For` (no `ProxyFix`), so behind Render's load balancer `request.remote_addr` is the proxy, and
   the two limiters (writes 30/min, Google 60/min) are each ONE bucket shared by everybody. Three members never notice; a stranger hammering `/api/auth/nonce` could use up the
   Google bucket and make everyone's Google sign-in answer 429 for a minute (the passcode door is not affected). Still to do (it did not ship with the join release): a trusted-proxy
   setting (`ProxyFix`, one proxy, rightmost address) and tests for it, before the join door is used by strangers in numbers.
3. **The admin's unlink hands out the well-known temporary passcode `1234`** (the member must change it at their next sign-in), exactly like the existing admin passcode
   reset. Fine among three friends; once strangers can join, the admin should be shown a random one-time passcode instead.
4. **First sign-in after a start fetches Google's keys** (one extra request, cached afterwards). If Google is unreachable at that moment the answer is 503 `google_unreachable`
   and the nonce is not spent.
5. **Claims live in memory too.** The claim tickets are in the memory of the one server process, like the nonces (same limit as #1): a restart between the Google tap and the
   passcode means the member taps Continue with Google again.
6. **The passcode is the only secret of a claim.** It has four digits; wrong ones are counted per Google account (five in ten minutes, then `claim_locked`), so guessing is bounded,
   but a member who mistypes five times locks THEIR OWN Google account out of claiming for ten minutes (the passcode chips still work). A determined attacker with several Google
   accounts gets five guesses per account per ten minutes.
7. **Google deletes an OAuth client with no sign-in and no settings change for six months** (it emails the project's contact address 30 days before; a deleted client can be restored
   for 30 days). One Google sign-in, or renaming the client in the console, resets the timer.
8. **Joining is open to anybody who has the link** until an admin closes it or the league reaches 30 members. There is no approval step and no alert to the admin (a new name simply
   appears in the Members tab and the leaderboard); the cap and the Close joining switch are the brakes.
9. **Display names are compared case-insensitively only**, not for look-alike letters ("Sai" and "Sаi" with a Cyrillic "а" are different names), and a member cannot rename
   themselves (the admin cannot either: there is no rename feature).
10. **The join is one transaction, but the steps after it are not.** The member and the identity are created together; the ticket is spent and the empty league row (`upsert_member`) made
    after that. If the league row failed (a database error), the person sees a server error and the ticket is gone, but the member exists: tapping Continue with Google again signs them in
    (the identity is linked), and picking the same name again would say "Someone already has that name" (it is their own).
11. **The passcode chips on the sign-in card list every member**, also a Google-only member (who has no passcode). Tapping such a chip asks for a passcode that cannot exist; the server answers
    `pin_retired` and the page moves the member on to Google. The chips go away once nobody has a passcode (`pin_enabled` false); until then the list grows with every member who joins.
12. **Inside the old Android app (APK 1.4 and earlier) a new member cannot join**: Google refuses to sign anybody in inside the app's WebView, so the card shows the "open in Chrome" notice and
    the hint "Continue with Google, then choose I'm new here" cannot be followed there. They join in Chrome. The hand-off release (2.25.0 with APK 1.5) lets the app do the Google step in the browser.

## Open questions / next releases

An alert to the admin when somebody joins, the devices list and "sign out other devices", the browser-tab hand-off for installed apps (needed for the Android app, and on iPhone), draft
privacy, "Delete my account", removing the passcode code once everyone has switched, and the Android real-phone checks of Google in an installed web app (the plan's Spike S).

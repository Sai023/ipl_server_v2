# Google sign-in (Phase 3, step 1; 2026-11-21, app 2.22.0)

`logic/google_identity.py` (the server's check of Google's ID token), the `identities` table and six routes in `routes.py` (section 1.6), and `Static/auth_google.js`
(the page's half). It replaces the 4-digit passcode over time (SA20 readiness plan, Phase 3). This is **step 1**: the three current members switch from the
passcode to Google. Joining with Google, the devices list, the hand-off sign-in for installed apps and removing the passcode code are the next releases.

## What it does (business view)

A member taps Google's own **Continue with Google** button, picks their Google account, and is signed in. A member who still has a passcode is offered, once
per launch, to **switch**: they sign in once with the passcode, tap Continue with Google, and from then on the passcode is retired. Nothing is shown at all until
the server says Google sign-in is set up (the environment variable `GOOGLE_CLIENT_ID` is set in Render; it is public by design, not a secret).

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
| `POST /api/auth/google` | Sign in. A linked member gets a session exactly as `/api/login` gives one. A good Google account nobody has linked is NOT an error: `{linked: false, email_masked}`. |
| `POST /api/auth/link` | A signed-in member switches: links their Google account and **retires their passcode in the same transaction** (`db.link_identity`). 409 `google_account_in_use` (it belongs to another member) or 409 `already_linked` (they have a different one: an admin must unlink first). Linking the same account again is a harmless repeat. |
| `POST /api/auth/logout` | Ends THIS device's session on the server (the page used to just forget its token). Safe to repeat. |
| `POST /api/admin/auth/unlink` | Recovery (admin only): removes a member's link, gives them the temporary passcode `1234` (must be changed at the next sign-in) and ends their sessions. |

Also changed: `/api/login` answers a switched member with 401 `pin_retired` ("This account signs in with Google."); `/api/passcode/change` refuses them (403
`pin_retired`); the admin passcode reset refuses them (409 `member_signs_in_with_google`: unlink first); `/api/whoami` carries `linked`, `email_masked` and
`pin_enabled`; `/api/admin/members` carries `linked` and `email_masked`. **No address ever appears except masked** (`s***@gmail.com`), not even to the admin.

## Data (db_manager.py v6.9)

`identities(provider, subject, member, email, created_at, last_login_at)`, primary key `(provider, subject)`, unique `(provider, member)`, `member` references
`members(username)` (cascade). Additive and created at boot. A member without a passcode has `members.passcode_hash = NO_PIN` (`"-"`): it can never equal a real
passcode hash, so the passcode check can never pass for them. DAO: `get_identity`, `touch_identity`, `link_identity`, `unlink_identity`, `count_pin_members`,
`delete_session`; `get_member_auth` and `list_members_admin_view` also return `google_email`.

## The page (Static/auth_google.js, `window.IplGoogle`)

`config()` (cached; a failure is not), `embeddedBrowser()` (Google refuses sign-in inside Instagram, Facebook, the old Android app ... so the member gets an "open it
in Chrome" notice with Copy link instead of a Google error), `mount(host, "signin" | "link", done)` (config, then Google's library, then a nonce, then
`google.accounts.id.initialize({client_id, nonce})` and `renderButton`; after every attempt the button is armed again with a NEW nonce; a button left on screen renews
its nonce before the server forgets it), `openSwitch()`, `offerSwitch()` (once per launch, never when a lock is within 24 hours or the member must set a passcode),
`signInAgain(name)` (a signed-out Google member, or a retired passcode). It reports through DOM events: `ipl:google-signed-in`, `ipl:google-linked`,
`ipl:google-config`; the page (templates/index.html) decides what they mean. The DOM part is a replaceable `view`, which is how the tests drive the logic.
`Static/ipl_glue.js` gained `authConfig`, `authNonce`, `authGoogle`, `authLink`, `authLogout`, `adminUnlinkGoogle`, and its 401 rule changed (see ipl_glue.md).

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

## Tests

`tests/test_google_identity.py` (23: real signatures, a local certs server, rotation, outage), `tests/test_google_signin.py` + `tests/helpers/google_scenario.py` (24 on the
real routes, including two members racing for one account), `tests/js/google_signin.test.js` (36: the glue, the module, the page functions), the error-reason contract
(`tests/test_error_reasons.py`: every new `reason` needs a sentence in `Static/ui.js` or an entry in the pass-through list).

## Known limits (harmless for three members; settle them BEFORE the link is opened to everybody)

1. **One process.** The nonce store lives in the memory of the one server process (Render runs a single `python server.py` with threads). With several worker processes a
   nonce issued by one would be unknown to another and sign-ins would fail at random: the store would first have to move into the database.
2. **"Per address" is really "per proxy".** The server does not read `X-Forwarded-For` (no `ProxyFix`), so behind Render's load balancer `request.remote_addr` is the proxy, and
   the two limiters (writes 30/min, Google 60/min) are each ONE bucket shared by everybody. Three members never notice; a stranger hammering `/api/auth/nonce` could use up the
   Google bucket and make everyone's Google sign-in answer 429 for a minute (the passcode door is not affected). The join release adds a trusted-proxy setting
   (`ProxyFix`, one proxy, rightmost address) and tests for it.
3. **First sign-in after a start fetches Google's keys** (one extra request, cached afterwards). If Google is unreachable at that moment the answer is 503 `google_unreachable`
   and the nonce is not spent.

## Open questions / next releases

Join with Google (display name, a joining switch, a member cap, an alert to the admin), the devices list and "sign out other devices", the browser-tab hand-off for
installed apps (needed on iPhone), draft privacy, "Delete my account", removing `/api/register` and the passcode code once everyone has switched, and the Android
real-phone checks of Google in an installed web app (the plan's Spike S).

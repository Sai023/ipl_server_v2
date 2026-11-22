# origin.py — The App's Address, and the "We've Moved" Switch

## What it does (business view)

The league lives on a free Render address (Sai's decision, 2026-11-19: no domain is bought). An address that may one day change must not be written into the code, because three things hang on it: the Android app (it has the address baked in), the Google sign-in setting ("Authorized JavaScript origins"), and, once they exist, the links in Telegram alerts and the sender identity of push reminders.

`origin.py` is the **one place that knows the address**. Code that needs it asks this module, and the module reads **one setting**. It also holds the switch that turns an **old** address into a "We've moved" page after a move (the written plan is [move_plan.md](move_plan.md)).

### The settings (environment variables, all optional)

| Setting | Where | Meaning |
|---|---|---|
| `PUBLIC_BASE_URL` | the live service | The app's own address, e.g. `https://league.example.org`: an **origin** (https, host, optional port; no path). Wins over everything. |
| `RENDER_EXTERNAL_URL` | set by Render itself | The address Render gave the service. Used when `PUBLIC_BASE_URL` is not set. It does **not** follow a custom domain (Render's own documentation: it is the service's onrender.com URL), which is why `PUBLIC_BASE_URL` wins. |
| `MOVED_TO_URL` | an **old** address only, after a move | The new address. Every link on the old address shows the "We've moved" page; every API call except `/api/ping`, `/api/health` and `/api/version` answers **410** `{reason: "moved", moved_to}`. |

Nothing has to be set today: with no setting the app behaves exactly as before, and the address is taken from the request when something asks for it.

## Where it sits in the flow

- `base.py` — `_moved_gate()`, an app-wide `before_request`. While `MOVED_TO_URL` is not set it costs one dictionary lookup and returns. When it is set, the decision comes from `moved_decision()`; the usual security headers (HSTS etc.) still go out with its answers because `_security_headers` is an `after_request`.
- `routes.py` — `GET /api/version` carries `public_base_url`, `public_base_source` (`env` / `platform` / `request` / `none`), `moved_to` and `origin_problems`, and lists the module (`modules.origin`).
- `server.py` — the boot log: `Address: <url> [<source>]; moved page: off|ON -> <url>`, and a warning for every setting that is set but unusable.
- **Future callers** (this is what the module is for): the Telegram alert text (Phase 2: no request, so `resolve_public_base(os.environ)` and cope with `None`), push sender identity and share-preview tags (Phase 4: `resolve_public_base(os.environ, request.host)`).
- The pages ask the **browser** (`location.origin`), never a server value: `Static/auth_google.js` builds its app-sign-in and hand-off links that way.

## Inputs / Outputs

### `normalize_base_url(raw) → str | None`
A clean origin (`https://host` or `https://host:port`, lower case, no trailing slash, default port dropped), or `None`. Refused: empty or non-string, no scheme, any scheme but https (plain **http only for the developer's own machine**: `localhost`, `127.0.0.1`, `[::1]`, `*.localhost`), a path, a query, a fragment, user info (`user@host`), a space or a backslash, a non-ASCII or malformed host (underscore, leading or trailing hyphen, empty label, trailing dot, label over 63 characters), an IPv6 address other than the loopback, a port outside 1–65535.

### `origin_of_host(host_header) → str | None`
The origin a request's `Host` header implies: https for a real name, http for the developer's own machine. `None` for anything that is not a clean host (a path, user info, a space, a scheme).

### `resolve_public_base(env, request_host=None) → (url | None, source)`
`PUBLIC_BASE_URL` → `RENDER_EXTERNAL_URL` → the request's own origin → `(None, "none")`. An unusable setting is **skipped, not trusted**.

### `moved_target(env) → str | None`, `config_problems(env) → [str]`
The clean `MOVED_TO_URL` (or `None`), and plain sentences about a setting that is set but unusable (it is ignored, never fatal): logged at boot and shown by `/api/version`.

### `moved_decision(method, path, moved_to, own_origins=()) → "pass" | "page" | "gone"`
What an **old** address does with a request:
- `pass` — answer as usual: `/static/…`, `/manifest.json`, `/offline`, `/api/ping`, `/api/health`, `/api/version`, and anything that is not a person opening a link (a bot's `/robots.txt`, a POST to `/`).
- `page` — the "We've moved" page: a GET or HEAD for a path that opens the app (the same rule the app uses to decide who gets the app page: no file name in the last part of the path, or `index.html`).
- `gone` — any other `/api/` call: 410.
- Always `pass` when `moved_to` is empty or is one of `own_origins` (the origins this request came to).

### `moved_page_html(moved_to) → str`, `moved_gone_body(moved_to) → dict`
The page: self-contained (one inline style, no script, the logo from `/static`), the new address as a button and written out, three plain steps, "about five minutes". The address is HTML-escaped inside the function whatever the caller did. The 410 body: `{error, ok: false, code: 410, reason: "moved", moved_to}`.

## Key business rules it enforces

1. **Nothing in the code names the address.** A guard test ([tests/test_origin.py](../tests/test_origin.py), `NoSourceFileNamesTheLiveAddress`) scans every source file (py, js, html, css, json, yml, ps1, sh, …) and fails on the live hostname; its scanner is itself proved on a planted file. The one tolerated place is a *comment* in the old Monday workflow that shows what the `HOSTED_URL` secret looks like.
2. **An unusable setting never takes the app down.** It is ignored, reported, and the next source is used.
3. **The "is this the new address?" test is the request's own Host header, never `PUBLIC_BASE_URL`.** So (a) a new service that inherited `MOVED_TO_URL` does not tell everyone it has moved to itself, and (b) **one service that answers both an old and a new hostname** (`PUBLIC_BASE_URL` and `MOVED_TO_URL` both the new address) shows the page on the old hostname only.
4. **The old address stays observable.** `/api/ping` (Render's health check), `/api/health` (the page's banner probe) and `/api/version` keep answering, so the service is not killed by Render and an operator can ask it what it believes.
5. **410, not a redirect.** An installed app, a Google sign-in setting and the stored sign-in token all belong to the old address; a silent redirect would load the new address inside an old shell and leave the member confused. The page tells them what to do.
6. **The decision is per request and stateless.** Turning the switch on or off is an environment variable and a restart; nothing is stored.

## Called by / Calls into

- Called by `base.py` (the gate), `routes.py` (`/api/version`), `server.py` (boot log).
- Calls nothing: stdlib only (`html`, `re`, `urllib.parse`), so it is tested without Flask or a database. Tests: [tests/test_origin.py](../tests/test_origin.py) — 32 cases: the pure module, the guard, and (on a scratch schema) the real routes through `tests/helpers/origin_scenario.py`: version, the moved page, the 410, what keeps working, the page rule against the app's own for 18 method/path pairs, the inherited variable, one service with two hostnames, an unusable setting.

## Known limits

1. **The Android app has the address baked in** (`APP_URL` in `MainActivity.java`, in the separate app project). A move needs a new build, signed with the same keystore so it installs over the old one. See [move_plan.md](move_plan.md).
2. **Google's "Authorized JavaScript origins" are set by hand** in the Google Cloud console; a new origin must be added there or sign-in fails at that origin.
3. A **bookmark or home-screen shortcut** on an old address shows the moved page; members re-add it.
4. The page's wording is generic English and mentions reminders in the conditional ("if you had turned reminders on"), because reminders do not exist yet (Phase 4).
5. Not covered by a test: the boot log lines in `server.py` (they run only under `__main__`).

## Dead Code Audit

None. `resolve_public_base` and `config_problems` are used by `/api/version` and the boot log; `origin_of_host` by the gate and by `resolve_public_base`. The planned callers (Telegram alert text, push sender, preview tags) are the reason `resolve_public_base` takes an optional request host.

## Open Questions

None for this release. Share-preview tags (gap G17 of the plan) are not built here: when they are, they must take their absolute links from `resolve_public_base`.

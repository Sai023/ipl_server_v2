# Moving the app to a new address — the written plan (plan item 1.10)

The league runs on a free Render address and no domain is bought. That is a choice, and this page is what makes it safe: if the address ever has to change, a move is a checklist, not a scramble. Nothing here is needed today.

**What a move never touches:** the database (the same Neon database serves both addresses), the members, their teams and points, and the Google links (a link belongs to the Google account, not to the address).

**What every member has to redo (about five minutes):** open the new link (or install the new app), tap **Continue with Google**, and turn reminders on again if they had them. Their sign-in token lives in the phone's storage *for the old address*, so one Google tap is needed at the new one.

## Why an address might change

A custom domain, a renamed or replaced Render service, a move to another host, or Render changing what the free tier offers. The app does not care which: it reads one setting ([origin.md](origin.md)).

## Where the address lives (inventory checked 2026-11-22)

| Place | What to change | Notes |
|---|---|---|
| **Render → the service → Environment** | `PUBLIC_BASE_URL` = the new address (on the new service); `MOVED_TO_URL` = the new address (on the **old** one) | Optional today: with nothing set the app uses the address Render gave the service (`RENDER_EXTERNAL_URL`). `/api/version` shows what it believes: `public_base_url`, `public_base_source`, `moved_to`, `origin_problems`. |
| **Google Cloud console → Credentials → the OAuth web client → Authorized JavaScript origins** | add the new origin (`https://new-address`); keep the old one until the grace period ends | Only origins are needed (no redirect addresses). Google says a change can take a few minutes to a few hours. Until it is in, sign-in at the new address fails with an origin error. |
| **The Android app** (project `ipl_fantasy_app`, constant `APP_URL` in `MainActivity.java`) | the new address; build, sign with the **same keystore**, raise the version code | Same package and same key: it installs over the old app, nobody has to uninstall. The old app, until updated, shows the moved page. |
| **GitHub → the repo's Actions secrets** | `HOSTED_URL` | Used by the Monday rollover workflow. |
| Telegram alerts (Phase 2, once built) | nothing but `PUBLIC_BASE_URL` | Their links come from the setting. |
| Push reminders (Phase 4, once built) | every phone turns reminders on again | A push subscription belongs to the address it was made at. The sender identity comes from the setting. |
| Members' bookmarks and home-screen shortcuts | each member re-adds them | The old address shows the moved page. |
| The WhatsApp group's pinned link | pin the new one | |
| The scheduled score runner on the PC | nothing | It talks to the database directly. |

Nothing in the web app's code names the address: it asks the browser (`location.origin`) or the setting, and a test fails if anyone writes the live hostname into a source file.

## Two ways a move can happen

**A. A custom domain on the same Render service** (the easy one). Both addresses answer at once; nothing breaks.
1. Add the domain in Render and wait for its certificate.
2. Set `PUBLIC_BASE_URL` to the new address; add the new origin in the Google Cloud console.
3. Build the new Android app (see the table) and send it to the members.
4. When everyone has moved, optionally set `MOVED_TO_URL` to the new address **on the same service**: the old hostname then shows the moved page and the new one keeps working (the test is the request's own hostname, so this is safe).

**B. A new Render service (or another host).** The new service shares the same database.
1. Create the service from the same repository and branch. Copy the environment variables (the database address, `GOOGLE_CLIENT_ID`, `ROLLOVER_TOKEN`, the rest) and set `PUBLIC_BASE_URL` to its own new address. Deploy.
2. Check `https://<new>/api/version`: `public_base_url` is the new address, `origin_problems` is `[]`.
3. Add the new origin in the Google Cloud console; sign in yourself at the new address.
4. Build, test and send the new Android app.
5. On the **old** service set `MOVED_TO_URL` to the new address and let it restart. Check: opening the old address shows "We've moved"; `https://<old>/api/version` shows `moved_to`; `/api/ping` still answers (Render's health check keeps the service alive).
6. Tell the members: the new link and the new app. Update the GitHub secret `HOSTED_URL`.
7. After a month, or when everyone has moved: delete the old service and remove the old origin from the Google Cloud console.

While both services run they share one database. Anything that writes is a normal database transaction, so that is safe; do not leave the old one running for months.

## Trying the moved page before you need it

On your own PC, in the repository folder, with `DATABASE_URL` pointing at a **test** database (never the live one), in PowerShell:

```powershell
$env:MOVED_TO_URL = "https://example.org"; py -3.14 server.py
```

Open `http://localhost:5060/`: the page says "We've moved" and points at the address. `http://localhost:5060/api/version` shows `moved_to`; any other `/api/` call answers 410. Remove the variable and restart to go back. The same checks run in the automated tests on every release ([tests/test_origin.py](../tests/test_origin.py)).

## Undoing a mistaken `MOVED_TO_URL`

Delete the variable in Render and let the service restart: the app is back at once. If the app ever looks "moved" when it should not, open `/api/version` and read `moved_to`. A value that is not a clean address (for example `new.example.org` without `https://`) is ignored and reported in `origin_problems`, so a typo cannot lock the league out.

## What is not covered

Adding a *second* domain permanently, iPhone home-screen installs (no iPhone is available to test on), and moving the database (a separate job, the INF-1 notes).

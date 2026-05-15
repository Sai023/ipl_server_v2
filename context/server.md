# server.py — The Thin Boot Wrapper

> **Post-cleanup note (2026-05-15).** This document is the snapshot that
> drove the cleanup. The Dead Code Audit / Open Questions below describe
> what was **found**. The items listed there have been actioned across
> Waves A–E — see [dead_code_register.md](dead_code_register.md) for the
> current `fixed` / `kept` / `guarded` status of each.


## What it does (business view)

`server.py` is the **thin Flask launcher**. Its job is to start the
web server with everything else already wired up, in the right order:

1. **Cold-start hydrate** — if the database has no matches but
   `data/matches/*.json` files exist (e.g. after a `git pull` on a
   fresh box), ingest them into the DB.
2. **Auto-seed** — call `init_db.run_all_sync(db)` to seed players,
   matches, and history if missing.
3. **Clear ephemeral state** — wipe `match_scores`,
   `player_match_points`, `user_match_points` and `data/matches/*.json`,
   so the scraper rebuilds them from scratch. **Never** wipes
   `week_pts` or `season_pts` (that was the v13.2 fix).
4. **Audit player IDs** — log any "ghost" IDs (in `user_selections`
   but not in `players`).
5. **Start the daily APScheduler cron** at 23:55 IST (via
   `tasks.start_daily_discovery_scheduler()`).
6. **Prevent Windows sleep** (via `SetThreadExecutionState`).
7. **Optionally open a public tunnel** (Cloudflare → ngrok → Pinggy →
   localhost.run, in that order of preference).
8. **Run Flask** — either directly (`app.run`) or in a background
   thread while the main thread babysits the tunnel.

The file is **pure plumbing**. Every API handler lives in `routes.py`;
every piece of shared state (the Flask app, the DB singleton, the
logger) lives in `base.py`; this file just orchestrates startup and
shutdown.

## Where it sits in the flow

Top of the dependency stack:

```
config.py  →  base.py  →  routes.py  →  server.py
                                          │
                                          ├── imports: init_db, tasks
                                          ├── registers: routes.bp Blueprint
                                          ├── runs:    cold-hydrate, auto-seed,
                                          │            audit, scheduler, tunnel
                                          └── starts:  Flask
```

Triggered only by `python server.py`. There is no `if __name__ ==
"__main__": main()`-with-a-helper-`main()` pattern — the whole launch
sequence is **inline at the bottom of the file**.

## Inputs / Outputs

- **Inputs:**
  - CLI flags: `--port`, `--host`, `--tunnel [PROVIDER]`, `--debug`.
  - The on-disk database, `data/matches/*.json`, and `cloudflared.exe`
    (or `cloudflared` on PATH).
- **Outputs:**
  - A running Flask server, optionally fronted by a public tunnel.
  - A banner on stdout summarising local / network / public URLs.
  - The mutable `base.CURRENT_PUBLIC_URL` is set when a tunnel comes up.
  - Side effect at boot: the ephemeral score tables are cleared.

## Key business rules it enforces

### 1. The startup sequence has a specific order
1. `_cold_start_hydrate()` (module-level, runs at import time —
   side-effect line 172).
2. `init_db.run_all_sync(db)` (line 348).
3. `_rebuild_scores_and_points()` (line 349).
4. `_audit_player_id_coverage()` (line 350).
5. `tasks.start_daily_discovery_scheduler()` (line 356).
6. `atexit.register(tasks.stop_scheduler)` (line 357).
7. Tunnel (optional, line 366) **before** the banner so the URL is
   known by `print_banner`.
8. `app.run(...)` either inline or in a daemon thread.

Each step is **idempotent** so a crash during boot can be recovered by
restarting.

### 2. Ephemeral vs persistent — the v13.2 fix
`_rebuild_scores_and_points()` is the file's most important business
rule, documented at length in the docstring ([lines 16-32](../server.py:16)):

> Tables NOT cleared (source of truth — survive restarts):
> - `user_selections.week_pts` — leaderboard totals
> - `user_selections.points_per_match` — history detail
> - `players.season_pts` — scouting badges
> - `players.points` — cap/vc-weighted display
>
> Tables cleared (ephemeral derived — repopulated by scraper):
> - `match_scores`, `player_match_points`, `user_match_points`
> - `data/matches/*.json`

Pre-v13.2, the function also ran `UPDATE user_selections SET week_pts
= 0` and `UPDATE players SET season_pts = 0` on every boot — wiping
weeks of historical leaderboard data every time the server restarted.
The current implementation is the safety net against that regression.

### 3. Tunnel preference order
`start_tunnel(port, "auto")` tries in order:
1. **Cloudflare** — persistent, requires `cloudflared.exe` installed
   (or in the project root).
2. **ngrok** — persistent if logged in; ephemeral free tier expires
   after ~2h.
3. **Pinggy** — ephemeral SSH tunnel, ~30 min lifetime.
4. **localhost.run** — ephemeral SSH tunnel.

The banner warns explicitly if the chosen tunnel is ephemeral.

### 4. Tunnel self-healing in `--tunnel` mode
The main thread polls the tunnel process every 5 seconds. If
`tunnel.proc.poll() is not None` (i.e. it died), the script:
- Counts the failure (up to `MAX_TF = 5`).
- Backs off exponentially (`min(5 * tunnel_failures, 30)` seconds).
- Restarts the tunnel.
After 5 failures, it gives up and runs without a tunnel.

It also auto-restarts the Flask thread if it dies.

### 5. Windows sleep prevention
`_prevent_windows_sleep()` calls `SetThreadExecutionState(0x80000001)`
— `ES_CONTINUOUS | ES_SYSTEM_REQUIRED`. This means the server keeps
the box awake **even when nobody clicks the mouse**, so the daily
23:55 IST cron actually fires. The `atexit` hook restores normal sleep
behaviour on shutdown.

### 6. Banner is 64 chars wide
Hardcoded `WIDE = 64`. Every line is padded to that width — purely
cosmetic, but worth knowing because long Cloudflare URLs wrap across
multiple banner lines via the `range(0, len(url), WIDE-4)` chunker.

## Called by / Calls into

- **Called by:** an operator running `python server.py [args]`. There
  is no other caller. The GitHub Actions workflow never runs this file
  — it runs the seeders and `scraper.py` directly.
- **Calls into:**
  - `base.*` (`app`, `db`, `_log`, `_base.CURRENT_PUBLIC_URL`, league
    constants).
  - `routes.bp` (Blueprint registration).
  - `init_db.run_all_sync`.
  - `tasks.start_daily_discovery_scheduler`, `tasks.stop_scheduler`.
  - `config.DB_PATH`.
  - stdlib: `os`, `re`, `shutil`, `socket`, `subprocess`, `sys`,
    `threading`, `time`, `argparse`, `atexit`.
  - **External executables:** `cloudflared`, `ngrok`, `ssh` (for
    Pinggy / localhost.run), `ctypes` for `SetThreadExecutionState`.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **Every capability** — `server.py` is what makes any of them
  reachable. No specific feature is implemented here; the file is
  pure infrastructure.
- **§10.2 Auto-rollover at Monday 14:00 UTC** is in the browser, but
  the server has to be *running* for the cron in `tasks.py` to fire —
  Windows sleep prevention is what keeps it running overnight.
- **§9.1 Refresh button** — the daily APScheduler job is started here
  but executed in `tasks.py`. Manual refresh (the button) still goes
  through `/api/sync-now` in `routes.py`.

## Dead Code Audit

| Item | Lines | Verdict |
|------|-------|---------|
| `from pathlib import Path` | 49 | **DEAD.** `Path` is never referenced in this file. `BASE_DIR` and `DATA_DIR` come from `base`. Added to register as **D22**. |
| `from flask import render_template` | 51 | **DEAD.** Never used in this file. `render_template` is called only inside `base.py` (404 handler) and `routes.py` (the `/` handler). Added as **D23**. |
| `_audit_player_id_coverage()` | 117–156 (~40 lines) | **Live.** Called at line 350. Logs ghost IDs at startup. |
| `_cold_start_hydrate()` | 159–170 | **Live.** Called at line 172 as a module-level side effect. |
| `TunnelResult` class | 183–189 | **Live.** Instantiated by every `try_*` function and consumed by the main loop. |
| `_run_bg(cmd)` | 191–193 | **Live.** Used by all four `try_*` tunnel functions. |
| `try_cloudflare/try_ngrok/try_pinggy/try_localhost_run` | 195–272 | **Live.** All four reachable via `start_tunnel`'s explicit-provider branches and the auto-fallback chain. |
| `start_tunnel`, `get_lan_ip`, `banner_line`, `print_banner`, `_prevent_windows_sleep`, `_restore_windows_sleep`, `WIDE` | — | **Live.** All used in `__main__`. |
| The `__main__` block (337–408) | 337–408 | **Live.** The entire reason the file exists. |

**Total dead code in `server.py`:** 2 unused imports.

## Open Questions

1. **`_cold_start_hydrate()` runs at module import**, before
   `if __name__ == "__main__"`. That means **importing `server` for
   any reason** (e.g. a test, an admin script) would also re-hydrate
   the DB. Worth moving inside the `__main__` block, or wrapping in
   `if __name__ == "__main__"`. Today nothing imports `server.py`, so
   the behaviour is fine — but the implicit contract is fragile.
2. **The `__main__` block is 75 lines of inline logic.** Refactoring
   into a `main()` function (with an early `if not __name__ ==
   "__main__": return` guard) would let an operator import `server`
   to inspect the tunnel functions without booting Flask.
3. **Tunnel polling sleeps 5 seconds.** A Cloudflare process that
   dies during a 5-second window is silently down until the next
   poll. Worth either reducing the poll interval, or using
   `proc.wait(timeout=5)` so death is detected immediately.
4. **The exponential backoff caps at 30s after 6 failures.** With
   `MAX_TF = 5`, the maximum total back-off before giving up is
   `5+10+15+20+25 = 75s`. Worth confirming this matches the
   tolerance for a Cloudflare/ngrok restart.
5. **Windows sleep prevention is unconditional.** Even running
   `python server.py` *without* `--tunnel` calls
   `SetThreadExecutionState`. For local-only development this is
   harmless but unnecessary. Worth gating behind `--tunnel`.
6. **`_audit_player_id_coverage()` is duplicate logic.** It does
   the same sweep as `/api/audit-player-ids`
   ([routes.py:472](../routes.py:472)). Two implementations of the
   same audit can drift. Worth refactoring to share a helper in
   `db_manager.py` or a new `logic/audit.py`.
7. **`get_lan_ip()` connects to `8.8.8.8:80`.** Works on any box with
   internet, fails over to `127.0.0.1` when offline. Worth documenting
   that a no-internet startup will print a misleading LAN URL.

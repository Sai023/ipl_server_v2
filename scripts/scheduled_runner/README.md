# Scheduled SA20 scoring runner (Windows Task Scheduler)

Links new Cricbuzz fixtures and scores finished SA20 matches automatically, from the owner's PC (Cricbuzz blocks cloud
servers, so this cannot run on Render). It is `score_competition.py` on a timer; see `context/score_competition.md` for
what a run does.

## What runs, and when

| Task (folder `\BCLFantasy\`) | When | What |
|---|---|---|
| `SA20 full run` | 09:00 and 13:15 every day, until 1 Mar 2027 | finds new Cricbuzz links (by team pair, or by match number with the teams confirmed from Cricbuzz's own header) + a scoring pass |
| `SA20 scoring pass` | every 30 minutes from 14:00 to 23:00, 17 Jan to 22 Feb 2027 | scores finished matches only (no discovery) |

Both run as the current user **while logged on** (no admin rights, no stored password), one run at a time, with a
15-minute limit, started with `pythonw.exe` so no console window appears. They run on battery, do not wake the PC and
catch up after a sleep. **The PC must be on, awake, online and logged in during matches.** Before 17 Jan the scoring pass
is dormant; the full run just checks for new fixtures (a no-op once they are all linked).

## Install / refresh / remove

```powershell
powershell -ExecutionPolicy Bypass -File scripts\scheduled_runner\install_tasks.ps1 -DsnFile "<file holding the production connection string>"
```

It freezes a copy of `origin/main` into the runner folder (so later work in a checkout cannot change what the schedule
runs), writes a machine-local `config.json` (the *path* of the connection file, never its content) and registers the
two tasks. It never connects to the database. Re-run it to refresh the code after a release (`-Ref <commit>` to pin
one). `-SkipTasks` installs the files only; `-Uninstall` removes the tasks (the folder and its logs stay).

## Files (default `C:\Users\<you>\BCLFantasyRunner`)

`runner.py` (the unattended entry point) · `config.json` · `app\` (frozen code, `VERSION.txt` says which commit) ·
`logs\runner-YYYY-MM.log` (one file a month, connection string redacted) · `state\last_run.json` (result of the latest
real run) · `state\ATTENTION.txt` (exists only while the latest run needs a look; it names the lines).

## Check on it

```powershell
Get-ScheduledTask -TaskPath '\BCLFantasy\' | Get-ScheduledTaskInfo | Select TaskName, LastRunTime, LastTaskResult, NextRunTime
Get-Content "$env:USERPROFILE\BCLFantasyRunner\state\last_run.json"
Get-Content "$env:USERPROFILE\BCLFantasyRunner\logs\runner-$(Get-Date -f yyyy-MM).log" -Tail 30
```

`LastTaskResult` 0 = fine, 2 = needs attention (a SKIPPED/FAILED match or a refused link), 1 = error, 3 = hit the
time limit, 4 = another run was in progress. **Nothing alerts you**: after the first real match, and whenever points look
wrong, read `ATTENTION.txt` and the log. A read-only self-check: `pythonw runner.py --check` (log line `check OK ...`);
`--dry-run` writes nothing.

## Pause / resume

```powershell
Disable-ScheduledTask -TaskPath '\BCLFantasy\' -TaskName 'SA20 scoring pass'     # and 'SA20 full run'
Enable-ScheduledTask  -TaskPath '\BCLFantasy\' -TaskName 'SA20 scoring pass'
```

## Deliberate differences from `py score_competition.py`

No schema-init step (the app on Render owns the schema, this job issues no DDL); only links whose two teams Cricbuzz's
header confirmed are stored; one run at a time (lock file); a 10-minute internal time limit; the connection string is
never printed or logged. A scorecard that does not match its fixture is skipped, never forced.

## Gotchas (learned 2026-11-18)

* **Not under `AppData`.** The Claude desktop app is a packaged app: Windows redirects its writes under `AppData\Local` to a
  private per-package copy that the Task Scheduler cannot see, so the task fails at once with `0x8007010B` "The directory
  name is invalid". The installer refuses `AppData` paths.
* **Not inside OneDrive.** `Documents` is often synced (here it is `OneDrive\Documents`); syncing would upload the
  logs and code copy and can lock or dehydrate files under an unattended job. The installer refuses OneDrive paths.
* **A standard (non-admin) user** cannot register a run-whether-logged-on task without a password, hence the
  "only while logged on" logon type. The connection file must stay where `config.json` points (it is read on every run).
* Unreachable Cricbuzz, a missing connection file or a database error end the run with exit 1 and an `ATTENTION.txt`; the
  next scheduled run simply tries again.

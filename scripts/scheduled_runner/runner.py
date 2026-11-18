#!/usr/bin/env python3
"""
BCL Fantasy - unattended scoring runner for Windows Task Scheduler                    runner v1.0.0
=================================================================================================
One run = score_competition.score() for the configured competition against the database named in the connection
file, then exit. It is started by pythonw.exe (no console window), so everything goes to a log file.

  pythonw runner.py --mode full     discovery (new Cricbuzz links) + a scoring pass        (the daily task)
  pythonw runner.py --mode score    a scoring pass only, no discovery                       (the match-day task)
  options: --dry-run (writes nothing) | --check (read-only self-check) | --config <path>

How it differs from `py score_competition.py ...` (deliberately, for a job that runs unattended):
  - no schema-init step (the app on Render owns the schema; this job issues no DDL)
  - only links whose two teams Cricbuzz's own scorecard header confirmed are stored
  - one run at a time (lock file), a hard time limit, and the connection string is never printed or logged
Files next to this script: config.json (machine-local), app\\ (frozen copy of the code), logs\\, state\\.
Exit codes: 0 ok | 1 error | 2 needs attention (a SKIPPED/FAILED match or a refused link) | 3 time limit | 4 another run busy
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
EXIT_OK, EXIT_ERROR, EXIT_ATTENTION, EXIT_TIMEOUT, EXIT_BUSY = 0, 1, 2, 3, 4
ATTENTION_RE = re.compile(r"\b(?:SKIPPED|FAILED)\b|NOT linked|NOT stored")
DSN_RE = re.compile(r"postgres(?:ql)?://\S+", re.I)


class Log:
    """Appends redacted lines to the monthly log file and keeps them in memory. Also usable as sys.stdout/sys.stderr
    (pythonw.exe has neither), so prints from the libraries land in the log too."""

    def __init__(self, directory, tag):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, f"runner-{dt.datetime.now():%Y-%m}.log")
        self.tag, self.lines, self.secrets, self._buf = tag, [], [], ""

    def hide(self, secret):
        if secret and len(secret) >= 4:
            self.secrets.append(secret)

    def _clean(self, line):
        for s in self.secrets:
            line = line.replace(s, "***")
        return DSN_RE.sub("postgres://***", line)

    def __call__(self, msg=""):
        for line in str(msg).splitlines() or [""]:
            line = self._clean(line.rstrip())
            self.lines.append(line)
            with open(self.path, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [{self.tag}] {line}\n")

    def write(self, s):                                   # file-like
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self(line)
        return len(s)

    def flush(self):
        pass


def take_lock(path):
    """Non-blocking exclusive lock on a file, held until the process exits. None when another run holds it."""
    import msvcrt
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return None
    return fd


def make_dao(dsn):
    """The app's DatabaseManager WITHOUT its __init__ (which would run CREATE TABLE IF NOT EXISTS on every run).
    score_competition only needs the connection pool."""
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    import db_manager
    dao = db_manager.DatabaseManager.__new__(db_manager.DatabaseManager)
    dao._dsn = dsn
    dao._pool = ConnectionPool(dsn, min_size=1, max_size=2, open=True, timeout=45,
                               kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": 30})
    return dao


def run(dao, cfg, args, log):
    import score_competition as sc
    original = sc.confirm_by_number

    def strict(fills, teams_of, fetch, lg, sleep=None):      # keep only links whose teams Cricbuzz's header confirmed
        out = []
        for mid, cid, how in original(fills, teams_of, fetch, lg, sleep):
            if how == "number":
                lg(f"    {mid} <- Cricbuzz #{cid}: its header names no teams -> NOT stored (this job stores teams-confirmed links only)")
                continue
            out.append((mid, cid, how))
        return out

    sc.confirm_by_number = strict
    log(f"score_competition v{sc.SCORER_VER}: {cfg['competition']} ({args.mode}{', dry run' if args.dry_run else ''})")
    return sc.score(dao, cfg["competition"], dry=args.dry_run, do_discover=(args.mode == "full"),
                    series_id=cfg["series_id"], series_slug=cfg["series_slug"], log=log)


def check(dao, cfg, log):
    """Read-only: the competition is there and the database answers."""
    import score_competition as sc
    slug = cfg["competition"]
    with dao._read() as con:
        c = con.execute("SELECT slug, status FROM competitions WHERE slug=%s", (slug,)).fetchone()
        if not c:
            raise RuntimeError(f"competition {slug!r} not found in this database")
        n_p = con.execute("SELECT count(*) AS n FROM players WHERE competition_id=%s", (slug,)).fetchone()["n"]
        n_m = con.execute("SELECT count(*) AS n FROM matches WHERE competition_id=%s", (slug,)).fetchone()["n"]
        n_l = con.execute("SELECT count(*) AS n FROM matches WHERE competition_id=%s AND scorecard_url NOT LIKE '%%/00000'", (slug,)).fetchone()["n"]
    log(f"check OK: {slug} is {c['status']}; {n_p} players, {n_m} matches, {n_l} with a Cricbuzz link; score_competition v{sc.SCORER_VER}")
    return {"competition": slug, "status": c["status"], "players": n_p, "matches": n_m, "linked": n_l}


def write_state(state_dir, code, mode, seconds, summary, attention, version):
    rec = {"finished_local": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode, "exit_code": code,
           "seconds": round(seconds, 1), "summary": summary, "attention": attention[:10], "code_version": version}
    tmp = os.path.join(state_dir, "last_run.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, os.path.join(state_dir, "last_run.json"))
    flag = os.path.join(state_dir, "ATTENTION.txt")
    if code in (EXIT_ATTENTION, EXIT_ERROR):
        with open(flag, "w", encoding="utf-8") as f:
            f.write(f"The last {mode} run ({rec['finished_local']}) needs a look (exit code {code}).\n"
                    f"Read logs\\runner-YYYY-MM.log. This file disappears after the next clean run.\n\n" + "\n".join(attention[:10]) + "\n")
    elif os.path.exists(flag):
        os.remove(flag)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Unattended SA20 scoring runner (Windows Task Scheduler).")
    ap.add_argument("--mode", choices=["full", "score"], default="full")
    ap.add_argument("--dry-run", action="store_true", help="read and parse only, write nothing")
    ap.add_argument("--check", action="store_true", help="read-only self-check: config, code copy, database")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    args = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(args.config))
    state_dir = os.path.join(here, "state")
    os.makedirs(state_dir, exist_ok=True)
    tag = "check" if args.check else args.mode + ("/dry" if args.dry_run else "")
    log = Log(os.path.join(here, "logs"), tag)
    sys.stdout = sys.stderr = log                              # pythonw.exe has no console
    lock = take_lock(os.path.join(state_dir, "runner.lock"))   # held for the length of this run
    if lock is None:
        log("skipped: another run is in progress")
        return EXIT_BUSY
    try:
        return run_locked(args, here, state_dir, log)
    finally:
        os.close(lock)


def run_locked(args, here, state_dir, log):
    """One run, entered holding the lock."""
    started = time.time()
    app = os.path.join(here, "app")
    vfile = os.path.join(app, "VERSION.txt")
    version = open(vfile, encoding="utf-8").read().strip() if os.path.exists(vfile) else "unknown"
    log(f"==== run start: runner {VERSION}, code {version}{', DRY RUN' if args.dry_run else ''}, pid {os.getpid()} ====")
    summary, code, timer = {}, EXIT_ERROR, None
    try:
        cfg = json.load(open(args.config, encoding="utf-8"))
        limit = int(cfg.get("time_limit_s", 600))

        def _timeout():
            log(f"time limit of {limit}s reached: aborting this run")
            os._exit(EXIT_TIMEOUT)
        timer = threading.Timer(limit, _timeout)
        timer.daemon = True
        timer.start()

        if not os.path.exists(cfg["dsn_file"]):
            raise RuntimeError("the connection file named in config.json was not found")
        dsn = open(cfg["dsn_file"], encoding="utf-8").read().strip()
        log.hide(dsn)
        log.hide(urlsplit(dsn).password or "")
        os.environ["DATABASE_URL"] = dsn
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        os.chdir(app)
        sys.path.insert(0, app)
        dao = make_dao(dsn)
        try:
            summary = check(dao, cfg, log) if args.check else run(dao, cfg, args, log)
        finally:
            dao._pool.close()
        attention = [ln.strip() for ln in log.lines if ATTENTION_RE.search(ln)]
        code = EXIT_ATTENTION if attention else EXIT_OK
    except Exception:                                           # never let a failure pass silently
        log(traceback.format_exc())
        attention, code = [ln.strip() for ln in log.lines[-6:] if ln.strip()], EXIT_ERROR
    if timer:
        timer.cancel()                                           # the watchdog must not outlive the run
    seconds = time.time() - started
    log(f"==== run end: exit {code}{' (needs attention)' if code == EXIT_ATTENTION else ''}, {seconds:.1f}s ====")
    if not (args.dry_run or args.check):
        write_state(state_dir, code, args.mode, seconds, summary, attention, version)
    return code


if __name__ == "__main__":
    sys.exit(main())

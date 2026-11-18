"""Tests for scripts/scheduled_runner/runner.py - the unattended runner started by Windows Task Scheduler.
No database and no network: log redaction, attention detection, the state files, the lock and the failure path."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("sched_runner", os.path.join(ROOT, "scripts", "scheduled_runner", "runner.py"))
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


DSN = "postgresql://bcl_user:s3cretPassw0rd@ep-test-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require"


class LogRedaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = runner.Log(self.tmp.name, "full")

    def tearDown(self):
        self.tmp.cleanup()

    def text(self):
        return read(self.log.path)

    def test_connection_string_and_password_never_reach_the_log(self):
        self.log.hide(DSN)
        self.log.hide("s3cretPassw0rd")
        self.log(f"connecting to {DSN}")
        self.log("error: password s3cretPassw0rd rejected")
        self.log("fallback postgres://other:pw@host/db is caught by the pattern")
        for secret in (DSN, "s3cretPassw0rd", "other:pw"):
            self.assertNotIn(secret, self.text())
            self.assertNotIn(secret, "\n".join(self.log.lines))
        self.assertIn("postgres://***", self.text())

    def test_prints_land_in_the_log_line_by_line(self):
        print("first", file=self.log)
        print("par", end="", file=self.log)
        print("tial", file=self.log)
        self.assertEqual(self.log.lines, ["first", "partial"])
        self.assertEqual(len(self.text().splitlines()), 2)


class AttentionDetection(unittest.TestCase):
    def test_real_problems_are_flagged(self):
        for line in ("  SKIPPED sa20_2027_m07: Cricbuzz #1 shows ['RCB'] but the fixture is ['PC', 'SEC'] - link NOT changed",
                     "  FAILED  sa20_2027_m07 (Cricbuzz #1): no scorecard data",
                     "    sa20_2027_m08: Cricbuzz #2 matches by number but its scorecard shows ['PR'] and our fixture is ['PC'] - NOT linked (check)",
                     "    sa20_2027_m09 <- Cricbuzz #3: its header names no teams -> NOT stored (this job stores teams-confirmed links only)"):
            self.assertTrue(runner.ATTENTION_RE.search(line), line)

    def test_normal_lines_are_not(self):
        for line in ("--- done: {'scored': 0, 'live': 0, 'skipped': 0, 'failed': 0, 'discovered': 0} ---",
                     "  WOULD SCORE  sa20_2027_m01: 22 players",
                     "  SCORED  sa20_2027_m01: 22 players, teams ['PC', 'SEC']",
                     "  LIVE    sa20_2027_m01",
                     "  discovery: 57 fixtures on the Cricbuzz series page, 0 matched to our unfilled matches",
                     "    sa20_2027_m01 <- Cricbuzz #168411 (by match number; both teams confirmed from Cricbuzz's scorecard header)"):
            self.assertFalse(runner.ATTENTION_RE.search(line), line)


class StateFiles(unittest.TestCase):
    def test_attention_flag_follows_the_latest_run(self):
        with tempfile.TemporaryDirectory() as d:
            flag = os.path.join(d, "ATTENTION.txt")
            runner.write_state(d, runner.EXIT_ATTENTION, "score", 3.21, {"scored": 0}, ["SKIPPED x"], "abc")
            self.assertIn("SKIPPED x", read(flag))
            rec = read_json(os.path.join(d, "last_run.json"))
            self.assertEqual((rec["exit_code"], rec["mode"], rec["code_version"], rec["seconds"]), (2, "score", "abc", 3.2))
            runner.write_state(d, runner.EXIT_OK, "score", 1.0, {}, [], "abc")          # the next run is clean: the flag goes away
            self.assertFalse(os.path.exists(flag))
            runner.write_state(d, runner.EXIT_ERROR, "full", 1.0, {}, ["boom"], "abc")  # an error flags too
            self.assertTrue(os.path.exists(flag))


@unittest.skipUnless(sys.platform == "win32", "the lock uses msvcrt")
class Lock(unittest.TestCase):
    def test_one_run_at_a_time(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runner.lock")
            fd = runner.take_lock(path)
            self.assertIsNotNone(fd)
            self.assertIsNone(runner.take_lock(path))
            os.close(fd)
            fd2 = runner.take_lock(path)
            self.assertIsNotNone(fd2)
            os.close(fd2)


class MainFailurePath(unittest.TestCase):
    """main() with a config whose connection file does not exist: no database is ever contacted."""

    def run_main(self, argv):
        out, err = sys.stdout, sys.stderr                       # main() points them at the log
        try:
            return runner.main(argv)
        finally:
            sys.stdout, sys.stderr = out, err

    def make_config(self, d):
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"dsn_file": os.path.join(d, "missing.txt"), "competition": "x", "series_id": "1", "series_slug": "x",
                       "time_limit_s": 30}, f)
        return cfg

    def test_missing_connection_file_fails_cleanly_and_flags_attention(self):
        with tempfile.TemporaryDirectory() as d:
            code = self.run_main(["--config", self.make_config(d), "--mode", "score"])
            self.assertEqual(code, runner.EXIT_ERROR)
            self.assertTrue(os.path.exists(os.path.join(d, "state", "ATTENTION.txt")))
            self.assertEqual(read_json(os.path.join(d, "state", "last_run.json"))["exit_code"], 1)
            logs = os.listdir(os.path.join(d, "logs"))
            self.assertIn("connection file", read(os.path.join(d, "logs", logs[0])))

    def test_dry_run_and_check_leave_the_state_files_alone(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.make_config(d)
            for extra in (["--dry-run"], ["--check"]):
                self.assertEqual(self.run_main(["--config", cfg] + extra), runner.EXIT_ERROR)
            self.assertFalse(os.path.exists(os.path.join(d, "state", "last_run.json")))
            self.assertFalse(os.path.exists(os.path.join(d, "state", "ATTENTION.txt")))

    @unittest.skipUnless(sys.platform == "win32", "the lock uses msvcrt")
    def test_a_busy_lock_skips_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.make_config(d)
            os.makedirs(os.path.join(d, "state"))
            fd = runner.take_lock(os.path.join(d, "state", "runner.lock"))
            try:
                self.assertEqual(self.run_main(["--config", cfg]), runner.EXIT_BUSY)
            finally:
                os.close(fd)
            self.assertFalse(os.path.exists(os.path.join(d, "state", "last_run.json")))


if __name__ == "__main__":
    unittest.main()

"""Runs the browser-side regression tests (plain Node scripts in tests/js/) as part of the normal unittest run.
Skipped when Node is not installed. Each script exits 1 on any failure and prints one PASS/FAIL line per case."""
import pathlib
import shutil
import subprocess
import unittest

NODE = shutil.which("node")
JS_DIR = pathlib.Path(__file__).parent / "js"


@unittest.skipUnless(NODE, "node is not installed")
class BrowserSideRegressions(unittest.TestCase):
    def run_script(self, name):
        r = subprocess.run([NODE, str(JS_DIR / name)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"{name} failed:\n{r.stdout}\n{r.stderr}")

    def test_match_centre_cache_follows_competition_and_member(self):
        """After a competition switch the Matches tab must never show the previous competition's data (seen on a phone, 2026-11-15)."""
        self.run_script("mc_hub_cache.test.js")

    def test_picker_filters_several_teams_and_takes_a_picked_player_out(self):
        """The Add-players picker (asked for by the league, 2026-11-18): several teams can be switched on at once, and tapping a player who is
        already in the XI takes him out (with Undo) instead of sending you back to the squad screen."""
        self.run_script("picker_team_chips.test.js")

    def test_edit_team_flow_keeps_picks_locks_cleanly_and_stays_usable(self):
        """The Pick tab (UX pass, 2026-11-18): unsaved picks survive a reload, a locked week is read-only and says why, Save cannot be
        double-fired or fail silently (offline / 'too late' / slow server), the hint line says what is left to do, and keyboard focus
        survives a redraw."""
        self.run_script("team_edit_ux.test.js")

    def test_failed_requests_become_plain_sentences_using_the_servers_reason(self):
        """The server tags many errors with a machine `reason` (2026-11-20); the page owns the sentence for the ones it knows and otherwise
        shows the server's own text, then the HTTP status. Nothing that worked before changes."""
        self.run_script("error_reasons.test.js")

    def test_the_lock_countdown_follows_the_servers_clock_not_the_phones(self):
        """A lock is decided by the server's clock (2026-11-20): a phone set two hours wrong used to show the wrong time left and a wrong
        locked / open state. The page keeps the offset to the server's time and every lock function applies it."""
        self.run_script("server_clock.test.js")

    def test_a_sign_in_that_ends_is_handled_in_the_open_and_a_network_blip_never_signs_anyone_out(self):
        """Launch: only a real 401 signs a member out; no network, a slow or failing server keeps them signed in and re-checks quietly (it used to
        sign them out and give up at 8 s). Mid-session: a refused token shows the blocking 'You've been signed out' sheet and signing in again
        carries on where they were (2026-11-20)."""
        self.run_script("session_flow.test.js")


if __name__ == "__main__":
    unittest.main()

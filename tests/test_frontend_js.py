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


if __name__ == "__main__":
    unittest.main()

"""Plan item 1.10 (2026-11-22, release 2.27.0): the app's address lives in ONE setting, nothing in the code names it, and an old address can say "We've moved".

1. logic/origin.py on its own (no database, no Flask): what counts as a clean address, which setting wins, what an OLD address does with each kind of request,
   and the page and the 410 it answers with.
2. A guard that needs no database: no source file names the live address (the scan itself is proved on a planted file, so it cannot pass by scanning nothing).
3. The real app on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess (tests/helpers/origin_scenario.py): /api/version,
   the moved page and the 410 on the real routes, what an old address still answers, and the page rule agreeing with the app's own.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from logic import origin  # noqa: E402

LIVE = "https://ipl-fantasy-2026-h0m9.onrender.com"
NEW = "https://new.example.org"


class NormalizeBaseUrl(unittest.TestCase):
    def test_a_clean_origin_comes_back_in_one_shape(self):
        for raw, want in [
            (LIVE, LIVE),
            (LIVE + "/", LIVE),
            ("  HTTPS://IPL-Fantasy.Example.COM/  ", "https://ipl-fantasy.example.com"),
            ("https://example.com:443", "https://example.com"),
            ("https://example.com:8443", "https://example.com:8443"),
            ("http://localhost:5060", "http://localhost:5060"),
            ("http://localhost:80", "http://localhost"),
            ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
            ("http://[::1]:5000", "http://[::1]:5000"),
            ("http://app.localhost:3000", "http://app.localhost:3000"),
            ("https://a.b-c.example.org", "https://a.b-c.example.org"),
        ]:
            self.assertEqual(origin.normalize_base_url(raw), want, raw)
            self.assertEqual(origin.normalize_base_url(want), want, "normalising twice must change nothing: " + raw)

    def test_everything_else_is_refused(self):
        bad = [None, "", "   ", 12345, "ipl.example.com", "//ipl.example.com", "ftp://example.com", "javascript:alert(1)", "https://", "https:///x", "https:example.com",
               "http://example.com", "http://192.168.1.5", "http://localhost.example.com",                  # plain http is for the developer's own machine only
               "https://example.com/app", "https://example.com//", "https://example.com/?x=1", "https://example.com?x=1", "https://example.com#top",
               "https://user@example.com", "https://user:pw@example.com", "https://example.com@evil.com", "https://example.com\\@evil.com",
               "https://exa mple.com", "https://exa\nmple.com", "https://münchen.example", "https://ex_ample.com", "https://-bad.com", "https://bad-.com",
               "https://bad..com", "https://bad.com.", "https://" + "a" * 64 + ".com", "https://%65xample.com",
               "https://example.com:0", "https://example.com:65536", "https://example.com:abc", "https://[::2]", "https://[2001:db8::1]", "https://[::1",
               "https://" + "a" * 300 + ".com"]
        for raw in bad:
            self.assertIsNone(origin.normalize_base_url(raw), repr(raw))


class ResolvePublicBase(unittest.TestCase):
    def test_the_setting_wins_then_the_platform_then_the_request(self):
        env = {"PUBLIC_BASE_URL": NEW, "RENDER_EXTERNAL_URL": LIVE}
        self.assertEqual(origin.resolve_public_base(env, "other.example.net"), (NEW, "env"))
        self.assertEqual(origin.resolve_public_base({"RENDER_EXTERNAL_URL": LIVE}, "other.example.net"), (LIVE, "platform"))
        self.assertEqual(origin.resolve_public_base({}, "other.example.net"), ("https://other.example.net", "request"))
        self.assertEqual(origin.resolve_public_base({}, None), (None, "none"))
        self.assertEqual(origin.resolve_public_base(None, None), (None, "none"))

    def test_an_unusable_setting_is_skipped_not_trusted(self):
        env = {"PUBLIC_BASE_URL": "https://example.com/app", "RENDER_EXTERNAL_URL": LIVE}
        self.assertEqual(origin.resolve_public_base(env), (LIVE, "platform"))
        self.assertEqual(origin.resolve_public_base({"PUBLIC_BASE_URL": "  ", "RENDER_EXTERNAL_URL": "nonsense"}, "x.example.net"), ("https://x.example.net", "request"))

    def test_the_request_host_gives_https_for_a_real_name_and_http_for_the_developers_machine(self):
        self.assertEqual(origin.resolve_public_base({}, "localhost:5060"), ("http://localhost:5060", "request"))
        self.assertEqual(origin.resolve_public_base({}, "127.0.0.1:5000"), ("http://127.0.0.1:5000", "request"))
        self.assertEqual(origin.resolve_public_base({}, "[::1]:5000"), ("http://[::1]:5000", "request"))
        self.assertEqual(origin.resolve_public_base({}, " IPL.Example.ORG "), ("https://ipl.example.org", "request"))

    def test_a_host_header_that_is_not_a_clean_host_is_never_turned_into_an_address(self):
        for host in ("evil.com/x", "evil.com\\@good.com", "user@evil.com", "a b", "evil.com?x=1", "evil.com#x", "", "   ", "[::1", "bad_host.com"):
            self.assertEqual(origin.resolve_public_base({}, host), (None, "none"), repr(host))


class MovedTargetAndProblems(unittest.TestCase):
    def test_moved_target(self):
        self.assertIsNone(origin.moved_target({}))
        self.assertIsNone(origin.moved_target(None))
        self.assertEqual(origin.moved_target({"MOVED_TO_URL": NEW + "/"}), NEW)
        self.assertIsNone(origin.moved_target({"MOVED_TO_URL": "new.example.org"}))

    def test_a_clean_setup_has_no_problems(self):
        self.assertEqual(origin.config_problems({}), [])
        self.assertEqual(origin.config_problems(None), [])
        self.assertEqual(origin.config_problems({"PUBLIC_BASE_URL": LIVE, "RENDER_EXTERNAL_URL": LIVE}), [])
        self.assertEqual(origin.config_problems({"PUBLIC_BASE_URL": LIVE, "MOVED_TO_URL": NEW}), [])

    def test_a_setting_that_is_set_but_unusable_is_named(self):
        p = origin.config_problems({"PUBLIC_BASE_URL": "https://example.com/app"})
        self.assertEqual(len(p), 1)
        self.assertIn("PUBLIC_BASE_URL", p[0])
        self.assertIn("ignored", p[0])
        p = origin.config_problems({"MOVED_TO_URL": "new.example.org"})
        self.assertEqual(len(p), 1)
        self.assertIn("MOVED_TO_URL", p[0])
        self.assertIn("NOT showing the moved page", p[0])
        self.assertEqual(len(origin.config_problems({"PUBLIC_BASE_URL": "x", "MOVED_TO_URL": "y"})), 2)

    def test_a_moved_address_that_equals_the_public_address_is_a_normal_setup(self):
        """One service may answer both an old and a new hostname (PUBLIC_BASE_URL and MOVED_TO_URL both the new one): the decision is made per request from its Host
        header, so this is not a mistake and nothing is reported."""
        for env in ({"PUBLIC_BASE_URL": NEW, "MOVED_TO_URL": NEW}, {"RENDER_EXTERNAL_URL": NEW, "MOVED_TO_URL": NEW + "/"}):
            self.assertEqual(origin.config_problems(env), [], env)


class OriginOfHost(unittest.TestCase):
    def test_a_clean_host_gives_an_origin(self):
        for host, want in [("ipl.example.org", "https://ipl.example.org"), (" IPL.Example.ORG ", "https://ipl.example.org"), ("ipl.example.org:8443", "https://ipl.example.org:8443"),
                           ("localhost", "http://localhost"), ("localhost:5060", "http://localhost:5060"), ("127.0.0.1:5000", "http://127.0.0.1:5000"), ("[::1]:5000", "http://[::1]:5000"),
                           ("app.localhost:3000", "http://app.localhost:3000")]:
            self.assertEqual(origin.origin_of_host(host), want, host)

    def test_anything_else_gives_none(self):
        for host in (None, "", "   ", "evil.com/x", "evil.com\\@good.com", "user@evil.com", "a b", "evil.com?x=1", "evil.com#x", "[::1", "bad_host.com", "https://evil.com", "-bad.com", "ex..ample.com"):
            self.assertIsNone(origin.origin_of_host(host), repr(host))


class MovedDecision(unittest.TestCase):
    def d(self, method, path, moved_to=NEW, own=()):
        return origin.moved_decision(method, path, moved_to, own)

    def test_nothing_happens_unless_a_new_address_is_set(self):
        for path in ("/", "/api/state", "/some/link", "/static/ui.js"):
            self.assertEqual(self.d("GET", path, moved_to=None), "pass", path)
            self.assertEqual(self.d("GET", path, moved_to=""), "pass", path)

    def test_a_person_opening_a_link_gets_the_page(self):
        for path in ("/", "/index.html", "/some/link", "/league/week-3", "/a/"):
            self.assertEqual(self.d("GET", path), "page", path)
            self.assertEqual(self.d("HEAD", path), "page", path)
            self.assertEqual(self.d("get", path), "page", path)

    def test_files_and_other_methods_are_answered_as_usual(self):
        for path in ("/robots.txt", "/favicon.ico", "/wp-login.php", "/some/file.json"):
            self.assertEqual(self.d("GET", path), "pass", path)         # a bot asking for a file gets the normal 404, as before
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(self.d(method, "/"), "pass", method)
            self.assertEqual(self.d(method, "/some/link"), "pass", method)

    def test_what_keeps_working_on_an_old_address(self):
        for path in ("/static/ui.js", "/static/brand/mark-paper.svg", "/static/", "/static/brand", "/manifest.json", "/offline", "/api/ping", "/api/health", "/api/version"):
            self.assertEqual(self.d("GET", path), "pass", path)            # (a folder name under /static/ has no dot: it must not be mistaken for a link to the app)

    def test_every_other_api_call_is_gone(self):
        for method, path in (("GET", "/api/state"), ("POST", "/api/login"), ("GET", "/api/whoami"), ("POST", "/api/save-next-week/Sai"), ("GET", "/api"),
                             ("GET", "/api/ping/"), ("GET", "/api/versions"), ("GET", "/api/auth/config"), ("POST", "/api/auth/google")):
            self.assertEqual(self.d(method, path), "gone", method + " " + path)

    def test_a_service_that_inherited_the_variable_does_not_say_it_has_moved_to_itself(self):
        self.assertEqual(self.d("GET", "/", own=(NEW,)), "pass")
        self.assertEqual(self.d("GET", "/api/state", own=(None, NEW)), "pass")
        self.assertEqual(self.d("GET", "/", own=(LIVE, None)), "page")            # an address that is NOT the target still gets the page


class MovedPage(unittest.TestCase):
    def test_the_page_points_at_the_new_address_and_says_what_to_do(self):
        page = origin.moved_page_html(NEW)
        self.assertEqual(page.count(NEW), 2, "the link and the address written out")
        self.assertIn('href="' + NEW + '/"', page)
        for needle in ("We've moved", "<title>BCL Fantasy has moved</title>", '<html lang="en">', 'name="viewport"', 'content="noindex"', "Continue with Google", "about five minutes"):
            self.assertIn(needle, page)
        self.assertNotIn("<script", page.lower(), "the page must work with no script at all")
        self.assertNotIn("@NEW@", page)

    def test_the_address_is_escaped_whatever_the_caller_did(self):
        page = origin.moved_page_html('https://a.example"><script>alert(1)</script>')
        self.assertNotIn("<script>", page)
        self.assertIn("&quot;&gt;&lt;script&gt;", page)

    def test_the_410_body(self):
        self.assertEqual(origin.moved_gone_body(NEW), {"error": "This app has moved to a new address.", "ok": False, "code": 410, "reason": "moved", "moved_to": NEW})


# ── the guard: no source file names the live address ─────────────────────────────────────────────────────────────────────────────────────
ADDRESS = re.compile(r"onrender\.com|ipl-fantasy-2026-h0m9", re.I)
SCANNED = (".py", ".js", ".html", ".css", ".json", ".yml", ".yaml", ".toml", ".ps1", ".sh", ".cfg", ".ini", ".txt", ".webmanifest")
SKIPPED_DIRS = {".git", "tests", "context", "data", "__pycache__", "node_modules"}
# the one place an address-shaped text may appear: a COMMENT in the old Monday workflow that shows what the HOSTED_URL secret looks like (the value itself is a secret)
COMMENT_ONLY = {".github/workflows/monday_rollover.yml"}


def scan_for_the_address(root):
    """[(relative path, line number, line)] for every line of a source file under `root` that names the live address."""
    found = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIPPED_DIRS]
        for fn in fns:
            if not fn.lower().endswith(SCANNED):
                continue
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                if ADDRESS.search(line) and not (rel in COMMENT_ONLY and line.lstrip().startswith("#")):
                    found.append((rel, n, line.strip()[:120]))
    return found


class NoSourceFileNamesTheLiveAddress(unittest.TestCase):
    def test_the_repository(self):
        """The address is a SETTING (PUBLIC_BASE_URL), never text in the code: an installed app, a Google sign-in setting and a reminder's sender identity hang on it,
        so a hard-coded copy is a bug that only shows on the day the address changes. The pages ask the browser (location.origin), the server asks logic/origin.py."""
        self.assertEqual(scan_for_the_address(ROOT), [], "a source file names the live address: use PUBLIC_BASE_URL (logic/origin.py) or location.origin instead")

    def test_the_scan_really_looks(self):
        with tempfile.TemporaryDirectory() as d:
            def put(rel, text):
                p = os.path.join(d, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
            put("Static/app.js", "var u = 'https://ipl-fantasy-2026-h0m9.onrender.com/x';\n")
            put("routes.py", "# fine\nURL = 'https://something.onrender.com'\n")
            put(".github/workflows/monday_rollover.yml", "# HOSTED_URL e.g. https://ipl-fantasy.onrender.com\nenv:\n  URL: https://x.onrender.com\n")
            put("context/doc.md", "the address is https://ipl-fantasy-2026-h0m9.onrender.com\n")
            put("tests/test_x.py", "X = 'https://ipl-fantasy-2026-h0m9.onrender.com'\n")
            put("data/blob.json", '{"u": "https://x.onrender.com"}\n')
            got = {(rel, n) for rel, n, _ in scan_for_the_address(d)}
            self.assertEqual(got, {("Static/app.js", 1), ("routes.py", 2), (".github/workflows/monday_rollover.yml", 3)})


# ── the real app on scratch data ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _flask_available():
    try:
        import click  # noqa: F401
        import flask  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL") and _flask_available(), "needs Flask and TEST_DATABASE_URL (run with `py -3.14`)")
class TheRealAppOnScratchData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        raw = os.environ["TEST_DATABASE_URL"]
        cls.schema = "t_orig_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        for k in ("HOSTED", "PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL", "MOVED_TO_URL"):
            env.pop(k, None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "origin_scenario.py")], env=env, capture_output=True, text=True, timeout=900)
        marker = "@@RESULT@@"
        cls.err = (p.stderr or "")[-2500:]
        cls.out = json.loads(p.stdout.split(marker)[1]) if marker in p.stdout else None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')
        finally:
            cls.admin.close()

    def setUp(self):
        self.assertIsNotNone(self.out, "the scenario script did not finish:\n" + self.err)

    def r(self, key):
        return self.out[key]

    # A  /api/version tells what the server believes its address is
    def test_a_the_version_says_where_the_address_came_from(self):
        v = self.r("a_unset")
        self.assertEqual(v["http"], 200)
        self.assertEqual(v["body"]["modules"]["origin"], "1.0.0")
        self.assertEqual((v["body"]["public_base_url"], v["body"]["public_base_source"]), ("http://localhost", "request"))     # the test client's own host
        self.assertIsNone(v["body"]["moved_to"])
        self.assertEqual(v["body"]["origin_problems"], [])
        self.assertEqual((self.r("a_platform")["body"]["public_base_url"], self.r("a_platform")["body"]["public_base_source"]), (LIVE, "platform"))
        self.assertEqual((self.r("a_env")["body"]["public_base_url"], self.r("a_env")["body"]["public_base_source"]), (NEW, "env"))
        bad = self.r("a_bad_env")["body"]
        self.assertEqual((bad["public_base_url"], bad["public_base_source"]), (LIVE, "platform"), "an unusable setting is skipped, the platform's address is used")
        self.assertEqual(len(bad["origin_problems"]), 1)
        self.assertIn("PUBLIC_BASE_URL", bad["origin_problems"][0])

    def test_a_nothing_changes_while_no_new_address_is_set(self):
        r = self.r("a_page_unset")
        self.assertEqual(r["http"], 200)
        self.assertTrue(r["app_page"], "the app page is served as before")
        self.assertFalse(r["moved_page"])
        self.assertEqual(self.r("a_state_unset")["http"], 200)

    # B  an old address
    def test_b_every_link_opens_the_moved_page(self):
        for key in ("b_root", "b_link", "b_index"):
            r = self.r(key)
            self.assertEqual(r["http"], 200, key)
            self.assertTrue(r["moved_page"], key)
            self.assertFalse(r["app_page"], key + ": the app must not open on an old address")
            self.assertEqual(r["text_all"], origin.moved_page_html(NEW), key + ": exactly the page the module builds")
            self.assertIn("text/html", r["ct"], key)
            self.assertEqual(r["headers"].get("Strict-Transport-Security"), "max-age=31536000", key + ": the usual security headers still go out")
            self.assertEqual(r["headers"].get("X-Content-Type-Options"), "nosniff", key)
            # never kept by a browser: the app's own header rule covers / and *.html, the gate's covers the rest
            self.assertEqual(r["headers"].get("Cache-Control"), "no-store" if key == "b_link" else "no-cache, must-revalidate", key)
        h = self.r("b_head")
        self.assertEqual(h["http"], 200)
        self.assertIn("text/html", h["ct"])
        self.assertEqual(h["body_len"], 0, "a HEAD request gets no body")
        self.assertEqual(h["headers"].get("Content-Length"), str(len(origin.moved_page_html(NEW).encode("utf-8"))), "but it is the moved page that HEAD describes, not the app page")

    def test_b_the_api_is_gone_with_the_new_address_in_it(self):
        for key in ("b_state", "b_login", "b_auth_config", "b_whoami"):
            r = self.r(key)
            self.assertEqual(r["http"], 410, key)
            self.assertEqual(r["body"], {"error": "This app has moved to a new address.", "ok": False, "code": 410, "reason": "moved", "moved_to": NEW}, key)
            self.assertFalse(r["is_html"], key)
            self.assertEqual(r["headers"].get("Cache-Control"), "no-store", key)

    def test_b_what_keeps_working(self):
        self.assertEqual(self.r("b_ping")["http"], 200)
        self.assertTrue(self.r("b_ping")["body"]["ok"])
        self.assertEqual(self.r("b_health")["http"], 200)
        v = self.r("b_version")
        self.assertEqual(v["http"], 200)
        self.assertEqual(v["body"]["moved_to"], NEW)
        for key in ("b_static", "b_logo", "b_manifest", "b_offline"):
            self.assertEqual(self.r(key)["http"], 200, key)
        self.assertIn("javascript", self.r("b_static")["ct"])
        self.assertEqual(self.r("b_robots")["http"], 404, "a file that does not exist is still a plain 404")
        self.assertFalse(self.r("b_robots")["moved_page"])
        self.assertEqual(self.r("b_post_root")["http"], self.r("a_post_root")["http"], "a POST to the root is answered exactly as before")

    def test_b_the_page_rule_is_the_apps_own(self):
        """The old address must give the moved page to exactly the paths where the app itself would have opened (a person opening a link), and to no others."""
        rows = self.r("b_parity")
        self.assertGreaterEqual(len(rows), 12)
        for row in rows:
            self.assertEqual(row["moved_page"], row["app_page_when_unset"], "%s %s: moved page %s but the app page was %s" % (row["method"], row["path"], row["moved_page"], row["app_page_when_unset"]))

    # C  a request that came to the NEW address is never turned away; an unusable MOVED_TO_URL changes nothing
    def test_c_a_service_that_inherited_the_variable_stays_open(self):
        r = self.r("c_same_host")
        self.assertTrue(r["app_page"], "MOVED_TO_URL naming the address the request came to must not turn the app into a moved page")
        self.assertFalse(r["moved_page"])
        self.assertEqual(self.r("c_same_host_api")["http"], 200)
        self.assertEqual(self.r("c_same_host_version")["body"]["origin_problems"], [], "setting both to the same address is a normal setup, not a mistake")
        only = self.r("c_request_only")
        self.assertTrue(only["app_page"], "with no address configured at all, the address the request came to is enough to know this is not the old one")
        self.assertFalse(only["moved_page"])

    def test_c_one_service_with_two_hostnames_shows_the_page_on_the_old_one_only(self):
        """PUBLIC_BASE_URL and MOVED_TO_URL are both the new address, and the same service also still answers its old hostname."""
        old, new = self.r("c_two_hosts_old"), self.r("c_two_hosts_new")
        self.assertTrue(old["moved_page"], "the old hostname gets the moved page")
        self.assertFalse(old["app_page"])
        self.assertEqual(self.r("c_two_hosts_old_api")["http"], 410)
        self.assertEqual(self.r("c_two_hosts_old_api")["body"]["moved_to"], NEW)
        self.assertTrue(new["app_page"], "the new hostname opens the app")
        self.assertFalse(new["moved_page"])
        self.assertEqual(self.r("c_two_hosts_new_api")["http"], 200)

    def test_c_an_unusable_moved_address_is_ignored(self):
        bad = self.r("c_bad")["body"]
        self.assertIsNone(bad["moved_to"])
        self.assertEqual(len(bad["origin_problems"]), 1)
        self.assertIn("MOVED_TO_URL", bad["origin_problems"][0])
        self.assertTrue(self.r("c_bad_page")["app_page"], "the app stays open")


if __name__ == "__main__":
    unittest.main()

"""The hardening release (2026-11-21, 2.21.0): the server answers every failure as what it is, and says nothing it should not.

1. Guards that need no database (they read the source): no route answers an UNEXPECTED failure with the exception's own text; the page only calls
   addresses that are real routes (an unknown /api/ path used to answer 200 with the whole HTML page, so a typo looked like success); every /static/ file
   the app names exists, in exactly that spelling (Render's disk is case-sensitive, Windows is not).
2. The real app on scratch data (needs Flask and TEST_DATABASE_URL; skipped otherwise), run in a subprocess:
   A HTTP errors, B unexpected failures + request ids, C /api/health, D the /api/state tag, E the player resolver, F the removed routes."""
import ast
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERIC = "The server had a problem. Please try again in a moment."


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def read_path(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _walk_files(folder, ext):
    for dp, _dn, fn in os.walk(os.path.join(ROOT, folder)):
        for f in fn:
            if f.endswith(ext):
                yield os.path.join(dp, f)


class NothingUnexpectedLeaksToAClient(unittest.TestCase):
    def test_no_route_answers_an_unexpected_failure_itself(self):
        """Only failures a route EXPECTS (a missing competition, a refused value, an oversize upload) are answered by the route, with its own sentence. Anything
        else is left to the one handler in base.py, which logs it under a request id and sends a fixed sentence. A handler that returns something must name one of
        the expected types: never `Exception`, a bare except, or a database error."""
        tree = ast.parse(read("routes.py"))
        bad = []
        for node in ast.walk(tree):
            # a handler that answers with a JSON reply (a helper that just returns a value, like the date formatter, is not a reply)
            if isinstance(node, ast.ExceptHandler) and any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "jsonify" for n in ast.walk(node)):
                name = ast.unparse(node.type) if node.type is not None else "bare"
                if name not in ("LookupError", "ValueError", "RequestEntityTooLarge"):
                    bad.append((node.lineno, name))
        self.assertEqual(bad, [], "a route catches an unexpected failure and answers it itself (raw text can reach the page): " + str(bad))

    def test_the_central_handlers_are_exactly_two_and_never_echo_the_exception(self):
        src = read("base.py")
        self.assertEqual(re.findall(r"@app\.errorhandler\(([^)]*)\)", src), ["HTTPException", "Exception"])
        start, end = src.index("# ── Flask error handling"), src.index("@app.after_request")
        block = src[start:end]
        for banned in ("str(e)", '"detail"', "{e}"):
            self.assertFalse(banned in block, "the error handlers must not put the exception's own text in a reply (found %s)" % banned)

    def test_hsts_is_sent(self):
        self.assertIn('response.headers["Strict-Transport-Security"]="max-age=31536000"', read("base.py"))

    def test_no_header_can_stop_googles_sign_in(self):
        """Google's documentation (developers.google.com/identity/gsi/web/guides/get-google-api-clientid): the page that loads its library should send
        Referrer-Policy strict-origin-when-cross-origin (same-origin and no-referrer send Google no referrer at all); a Cross-Origin-Opener-Policy of plain
        same-origin breaks the sign-in popup (it must include same-origin-allow-popups); and a Content-Security-Policy must allow accounts.google.com/gsi/.
        The server sends no COOP and no CSP today: whoever adds one has to read this."""
        src = read("base.py")
        self.assertTrue('response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"' in src,
                        "base.py must send Referrer-Policy strict-origin-when-cross-origin (the value Google documents for its sign-in library)")
        self.assertFalse('"Referrer-Policy"]="same-origin"' in src, "same-origin sends Google no referrer at all")
        if "Cross-Origin-Opener-Policy" in src:
            self.assertTrue("same-origin-allow-popups" in src, "a COOP of plain same-origin blocks the Google sign-in popup")
        if "Content-Security-Policy" in src:
            for need in ("https://accounts.google.com/gsi/client", "https://accounts.google.com/gsi/"):
                self.assertTrue(need in src, "a CSP must allow Google's sign-in library and frames (%s)" % need)


def _route_patterns():
    pats = []
    for fn in ast.parse(read("routes.py")).body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "route" and d.args and isinstance(d.args[0], ast.Constant):
                parts = re.split(r"(<[^>]+>)", d.args[0].value)
                rx = "".join((".+" if p.startswith("<path:") else "[^/]+") if p.startswith("<") else re.escape(p) for p in parts)
                pats.append((d.args[0].value, re.compile("^" + rx + "$")))
    return pats


class ThePageOnlyCallsRealThings(unittest.TestCase):
    def test_every_api_address_the_page_uses_is_a_real_route(self):
        pats = _route_patterns()
        self.assertGreater(len(pats), 25, "could not read the routes")
        found = {}
        files = [os.path.join(ROOT, "templates", "index.html")] + list(_walk_files("Static", ".js"))
        for path in files:
            for m in re.finditer(r"""["'`](/api/[A-Za-z0-9_\-/]*)""", read_path(path)):
                found.setdefault(m.group(1), os.path.basename(path))
        found.pop("/api/", None)                      # the bare prefix, used to recognise API calls (url.indexOf("/api/") === 0)
        self.assertGreater(len(found), 15, "found suspiciously few API addresses: " + str(sorted(found)))
        missing = []
        for addr, where in sorted(found.items()):
            probe = addr + "x" if addr.endswith("/") else addr     # "/api/history/" is a prefix: the name is added after it
            if not any(rx.match(probe) for _p, rx in pats):
                missing.append("%s (in %s)" % (addr, where))
        self.assertEqual(missing, [], "the page calls addresses that no route serves (before this release they answered 200 with the whole page)")

    def test_every_static_file_the_app_names_exists_in_exactly_that_spelling(self):
        names = set()
        files = [os.path.join(ROOT, "templates", "index.html"), os.path.join(ROOT, "routes.py")] + list(_walk_files("Static", ".js")) + list(_walk_files("Static", ".css"))
        for path in files:
            for m in re.finditer(r"""/static/([A-Za-z0-9_\-./]*[A-Za-z0-9_\-])""", read_path(path)):
                names.add(m.group(1))
        self.assertGreater(len(names), 10, "found suspiciously few static references")
        missing = []
        for name in sorted(names):
            here = os.path.join(ROOT, "Static")
            for part in name.split("/"):
                listing = os.listdir(here) if os.path.isdir(here) else []
                if part not in listing:         # exact case: os.path.exists would pass on Windows for a wrong-case name that Render would 404
                    missing.append(name)
                    break
                here = os.path.join(here, part)
        self.assertEqual(missing, [], "files the app names that are not in Static/ (a wrong-case spelling counts: Render's disk is case-sensitive)")


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
        cls.schema = "t_hard_" + uuid.uuid4().hex[:8]
        cls.admin = psycopg.connect(raw, autocommit=True)
        cls.admin.execute(f'CREATE SCHEMA "{cls.schema}"')
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_URL"] = raw + ("&" if "?" in raw else "?") + "options=" + quote(f"-c search_path={cls.schema}")
        env.pop("HOSTED", None)
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests", "helpers", "hardening_scenario.py")], env=env, capture_output=True, text=True, timeout=900)
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

    # ── helpers ──
    def r(self, key):
        return self.out[key]

    def assertJsonError(self, key, http, reason, error):
        r = self.r(key)
        self.assertEqual(r["http"], http, key)
        self.assertIn("json", r["ct"], key)
        self.assertFalse(r["is_html"], key + ": an error must never be the app page")
        self.assertEqual(r["body"], {"error": error, "code": http, "reason": reason}, key)

    def assertPlain404(self, key):
        r = self.r(key)
        self.assertEqual(r["http"], 404, key)
        self.assertTrue(r["ct"].startswith("text/plain"), key + ": " + r["ct"])
        self.assertFalse(r["is_html"], key)

    # ── A. HTTP errors ──
    def test_an_unknown_api_address_is_a_json_404_not_the_page(self):
        for key in ("a_unknown_get", "a_unknown_post", "a_api_root", "a_api_slash"):
            self.assertJsonError(key, 404, "not_found", "Not found")

    def test_a_wrong_method_is_a_json_405_that_says_which_methods_work(self):
        self.assertJsonError("a_wrong_method", 405, "method_not_allowed", "Method not allowed")
        self.assertIn("GET", self.r("a_wrong_method")["headers"].get("Allow", ""))

    def test_an_oversize_body_is_a_json_413(self):
        self.assertJsonError("a_too_large", 413, "too_large", "That is too large to send")

    def test_a_missing_static_file_or_a_file_like_path_is_a_real_404(self):
        for key in ("a_static_missing", "a_static_missing_deep", "a_favicon", "a_robots", "a_php", "a_post_deep_link"):
            self.assertPlain404(key)
        ok = self.r("a_static_present")
        self.assertEqual(ok["http"], 200)
        self.assertIn("javascript", ok["ct"])

    def test_only_a_person_opening_a_link_gets_the_page(self):
        for key in ("a_root", "a_deep_link", "a_index_html"):
            r = self.r(key)
            self.assertEqual(r["http"], 200, key)
            self.assertIn("text/html", r["ct"], key)
            self.assertTrue(r["is_html"], key)
        self.assertEqual(self.r("a_head_deep_link")["http"], 200)
        self.assertEqual(self.r("a_manifest")["http"], 200)

    def test_hsts_is_on_every_kind_of_answer(self):
        for key in ("a_root", "a_static_present", "a_unknown_get", "a_static_missing", "c_health", "b_runtime"):
            self.assertEqual(self.r(key)["headers"].get("Strict-Transport-Security"), "max-age=31536000", key)

    def test_the_referrer_policy_google_documents_is_on_every_kind_of_answer_and_no_coop_or_csp_is_sent(self):
        for key in ("a_root", "a_static_present", "a_unknown_get", "a_static_missing", "c_health", "b_runtime"):
            h = self.r(key)["headers"]
            self.assertEqual(h.get("Referrer-Policy"), "strict-origin-when-cross-origin", key)
            self.assertIsNone(h.get("Cross-Origin-Opener-Policy"), key)
            self.assertIsNone(h.get("Content-Security-Policy"), key)

    # ── B. unexpected failures ──
    FAILURES = {"b_runtime": ("boom", "RuntimeError"), "b_operational": ("connection to server", "OperationalError"),
                "b_integrity": ("members_pkey", "UniqueViolation"), "b_second": ("second boom", "RuntimeError")}
    FORBIDDEN = ("boom", "neon", "hunter2", "connection to server", "members_pkey", "UniqueViolation", "Traceback", "password")

    def test_an_unexpected_failure_is_a_fixed_sentence_with_a_request_id_and_nothing_else(self):
        for key in self.FAILURES:
            r = self.r(key)
            self.assertEqual(r["http"], 500, key)
            self.assertIn("json", r["ct"], key)
            b = r["body"]
            self.assertEqual(sorted(b), ["code", "error", "ok", "reason", "request_id"], key)
            self.assertEqual((b["error"], b["ok"], b["code"], b["reason"]), (GENERIC, False, 500, "server_error"), key)
            self.assertRegex(b["request_id"], r"^[0-9a-f]{8}$", key)
            self.assertEqual(r["headers"].get("X-Request-Id"), b["request_id"], key)
            for word in self.FORBIDDEN:
                self.assertNotIn(word, r["text"], key + " leaked " + word)

    def test_two_failures_get_two_ids(self):
        self.assertNotEqual(self.r("b_runtime")["body"]["request_id"], self.r("b_second")["body"]["request_id"])

    def test_the_request_id_finds_the_real_error_in_the_log(self):
        """The id on the screen is the id in the log, next to the traceback and the real cause (which never reached the caller)."""
        log = self.r("b_log")
        for key, (cause, kind) in self.FAILURES.items():
            rid = self.r(key)["body"]["request_id"]
            hits = [x for x in log if rid in x["msg"]]
            self.assertEqual(len(hits), 1, "%s: expected one log record carrying %s, got %d" % (key, rid, len(hits)))
            self.assertIn(cause, hits[0]["tb"] + hits[0]["msg"], key)
            self.assertIn(kind, hits[0]["tb"], key + ": the traceback is logged")
            self.assertIn("Traceback", hits[0]["tb"], key)

    def test_a_failure_on_a_page_address_is_plain_text_with_the_id(self):
        r = self.r("b_page")
        self.assertEqual(r["http"], 500)
        self.assertTrue(r["ct"].startswith("text/plain"), r["ct"])
        self.assertFalse(r["is_html"])
        self.assertRegex(r["text"], r"\(ref [0-9a-f]{8}\)")
        self.assertNotIn("template exploded", r["text"])
        self.assertNotIn("neon", r["text"])
        rid = re.search(r"ref ([0-9a-f]{8})", r["text"]).group(1)
        self.assertEqual(r["headers"].get("X-Request-Id"), rid)
        self.assertTrue(any(rid in x["msg"] and "template exploded" in x["tb"] for x in self.r("b_log")), "the page failure is in the log under its id")

    def test_an_expected_refusal_keeps_its_own_sentence(self):
        r = self.r("b_expected_still_expected")
        self.assertEqual((r["http"], r["body"]["reason"], r["body"]["error"]), (401, "bad_credentials", "wrong name or passcode"))
        self.assertNotIn("request_id", r["body"])

    # ── C. /api/health ──
    def test_health_is_one_statement_and_fails_when_the_database_does(self):
        self.assertEqual(self.r("c_health")["http"], 200)
        self.assertEqual(self.r("c_health")["body"], {"ok": True})
        self.assertEqual(self.r("c_health_statements"), 1, "the banner probes this repeatedly while the server is in trouble: one SELECT 1, nothing more")
        self.assertGreaterEqual(self.r("c_ping_statements"), 3, "(for contrast: /api/ping does several, which is why the probe moved)")
        down = self.r("c_health_db_down")
        self.assertEqual(down["http"], 500, "a probe must see a database that is gone as trouble")
        self.assertEqual(down["body"]["reason"], "server_error")
        self.assertNotIn("neon", down["text"])

    # ── D. /api/state ──
    def test_the_state_tag_is_in_the_header_and_the_body(self):
        d = self.r("d_first")
        self.assertEqual(d["http"], 200)
        self.assertTrue(self.r("d_etag"))
        self.assertNotEqual(self.r("d_etag"), "never", "the scenario saved teams first, so the tag is a real timestamp")
        self.assertEqual(self.r("d_body_saved"), self.r("d_etag"))

    def test_a_304_is_decided_before_any_heavy_read(self):
        ok = self.r("d_304")
        self.assertEqual(ok["http"], 304, "if the heavy read had run, the patched get_state would have failed this request")
        self.assertEqual(ok["text"], "")
        self.assertEqual(ok["headers"].get("ETag"), self.r("d_etag"))
        self.assertEqual(self.r("d_200_needs_the_heavy_read")["http"], 500, "control: with a different tag the heavy read IS reached (and the patch makes it fail)")
        self.assertLessEqual(self.r("d_304_statements"), 3)
        self.assertGreaterEqual(self.r("d_200_statements"), self.r("d_304_statements") + 5)

    def test_a_save_in_the_middle_of_the_read_can_only_make_the_tag_older_than_the_data(self):
        race = self.r("d_race")
        self.assertTrue(self.r("d_race_fired"), "the scenario's save did not land during the read, so this test would prove nothing")
        self.assertEqual(race["http"], 200)
        self.assertEqual(race["headers"].get("ETag"), self.r("d_race_before_tag"), "the tag was read BEFORE the data")
        self.assertEqual(race["body"]["_saved"], self.r("d_race_before_tag"))
        self.assertEqual(self.r("d_race_tag_now"), "2099-01-01T00:00:00+00:00", "(the scenario's save did land)")
        self.assertEqual(self.r("d_race_next_poll")["http"], 200, "a page holding the older tag must be given the newer state, not told 'unchanged'")

    # ── E. the resolver ──
    CANON = [f"p{i}" for i in range(1, 12)]

    def test_a_refused_save_changes_nothing_anywhere(self):
        self.assertEqual(self.r("e_canonical_save")["http"], 200)
        self.assertEqual(self.r("e_draft_after_canonical"), self.CANON)
        r = self.r("e_refused")
        self.assertEqual((r["http"], r["body"]["reason"]), (422, "invalid_team"))
        self.assertEqual(self.r("e_draft_after_refused"), self.CANON, "the refused save must not touch the stored draft (it used to patch it before validating)")
        self.assertEqual(self.r("e_marker_after_refused"), self.r("e_marker_before"), "nor the same member's draft for the same week number in another competition")

    def test_a_name_resolves_inside_the_competition_being_saved(self):
        r = self.r("e_by_name")
        self.assertEqual(r["http"], 200, r["body"])
        self.assertEqual(self.r("e_draft_after_by_name"), ["p2"] + [f"p{i}" for i in range(3, 13)],
                         "in h_b 'Zed Alpha' is p2; h_a's p1 (also 'Zed Alpha') is a different player there")
        corrected = [x for x in r["body"]["resolution_log"] if x["action"] == "corrected"]
        self.assertEqual([(x["input"], x["output"]) for x in corrected], [("Zed Alpha", "p2")])
        self.assertEqual(self.r("e_marker_after_by_name"), self.r("e_marker_before"), "saving in h_b never touches h_a")
        self.assertEqual(self.r("e_resolve_b")["body"]["resolved"]["id"], "p2")
        self.assertEqual(self.r("e_resolve_a")["body"]["resolved"]["id"], "p1")

    def test_a_normal_save_never_reads_the_player_list(self):
        r = self.r("e_fast_path")
        self.assertEqual(r["http"], 200, "the player list was read during a save with canonical ids: " + str(r["body"]))
        self.assertEqual({x["action"] for x in r["body"]["resolution_log"]}, {"passthrough"})
        self.assertTrue(self.r("e_no_db_con"), "the resolver's own unpooled connection is gone")
        self.assertEqual(self.r("e_fast_path_resolver_got"), ["no connection"], "a save with canonical ids must not even check out a connection to resolve")
        self.assertEqual(self.r("e_by_name_resolver_got"), ["a connection"], "(control: a name does need one)")

    def test_what_counts_as_needing_the_player_list(self):
        self.assertEqual(self.r("e_needs_resolving"), {"canonical_ids": False, "only_empty_values": False, "a_name": True, "an_id_of_another_shape": True, "a_name_as_captain": True})

    # ── F. removed routes ──
    def test_the_unused_diagnostic_routes_are_gone(self):
        for key in ("f_audit_scores", "f_audit_ids", "f_audit_blobs", "f_clean", "f_snapshot"):
            self.assertJsonError(key, 404, "not_found", "Not found")
        guarded = self.r("f_recalc_still_guarded")
        self.assertEqual((guarded["http"], guarded["body"]["reason"]), (401, "auth_required"), "the repair tool that stays is still admin-only")


if __name__ == "__main__":
    unittest.main()

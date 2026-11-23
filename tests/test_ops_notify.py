"""Phase 2 (2026-11-22): ops_notify.py, the only code that talks to Telegram. No real Telegram: a small fake server on this machine plays its part.
  - dormant without a bot token and a chat id (nothing is sent, and it says so)
  - one message goes to /bot<token>/sendMessage with the chat id and the text
  - every failure (an error answer, a refusal, no connection, a timeout, a garbled answer) comes back as a short sentence that NEVER contains the token
"""
import http.server
import json
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ops_notify as notify  # noqa: E402

TOKEN = "123456789:AAH-fake_test_token_0123"
CHAT = "-1001234567890"


class FakeTelegram:
    """Answers every POST with what `mode` says; remembers what it was sent."""

    def __init__(self):
        self.mode = "ok"
        self.seen = []
        self.updates = {"ok": True, "result": []}                 # what getUpdates answers
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.seen.append({"path": self.path, "body": None, "ctype": None, "method": "GET"})
                if outer.mode == "401":
                    return self._send(401, json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"}).encode())
                if outer.mode == "garbled":
                    return self._send(200, b"<html>not json</html>")
                return self._send(200, json.dumps(outer.updates).encode())

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8")
                outer.seen.append({"path": self.path, "body": json.loads(body) if body else None, "ctype": self.headers.get("Content-Type")})
                m = outer.mode
                if m == "slow":
                    time.sleep(1.5)
                if m == "garbled":
                    return self._send(200, b"<html>not json</html>")
                if m == "401":
                    return self._send(401, json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"}).encode())
                if m == "echo_token":                                 # a server that repeats the address it was called on in its error
                    return self._send(400, json.dumps({"ok": False, "description": "bad request to " + self.path}).encode())
                if m == "refuse":
                    return self._send(200, json.dumps({"ok": False, "description": "chat not found"}).encode())
                if m == "html_error":
                    return self._send(502, b"<html>bad gateway</html>")
                return self._send(200, json.dumps({"ok": True, "result": {"message_id": 7}}).encode())

            def _send(self, code, data):
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def env(self, **kw):
        e = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT, "TELEGRAM_API_BASE": "http://127.0.0.1:%d" % self.port}
        e.update(kw)
        return e

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Dormant(unittest.TestCase):
    def test_without_both_settings_nothing_is_sent(self):
        fake = FakeTelegram()
        self.addCleanup(fake.close)
        for env in ({}, {"TELEGRAM_BOT_TOKEN": TOKEN}, {"TELEGRAM_CHAT_ID": CHAT}, {"TELEGRAM_BOT_TOKEN": "  ", "TELEGRAM_CHAT_ID": CHAT},
                    {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "", "TELEGRAM_API_BASE": "http://127.0.0.1:%d" % fake.port}):
            self.assertFalse(notify.configured(env), env)
            ok, detail = notify.send("hello", env)
            self.assertFalse(ok)
            self.assertIn("not set up", detail)
        self.assertEqual(fake.seen, [], "nothing may reach the server when the module is not set up")

    def test_configured_needs_both(self):
        self.assertTrue(notify.configured({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}))
        self.assertFalse(notify.configured({}))


class Sending(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)

    def test_one_message_goes_out_the_way_telegram_wants_it(self):
        ok, detail = notify.send("RED: SA20 2027 week 1 handover is 3 min late.", self.fake.env())
        self.assertEqual((ok, detail), (True, "sent"))
        self.assertEqual(len(self.fake.seen), 1)
        got = self.fake.seen[0]
        self.assertEqual(got["path"], "/bot" + TOKEN + "/sendMessage")
        self.assertEqual(got["body"], {"chat_id": CHAT, "text": "RED: SA20 2027 week 1 handover is 3 min late.", "disable_web_page_preview": True})
        self.assertEqual(got["ctype"], "application/json")

    def test_a_long_message_is_cut(self):
        notify.send("x" * 9000, self.fake.env())
        self.assertEqual(len(self.fake.seen[0]["body"]["text"]), notify.MAX_TEXT)

    def test_a_non_text_message_is_sent_as_text(self):
        self.assertTrue(notify.send(12345, self.fake.env())[0])
        self.assertEqual(self.fake.seen[0]["body"]["text"], "12345")

    def test_an_error_answer_is_a_short_sentence(self):
        self.fake.mode = "401"
        ok, detail = notify.send("hi", self.fake.env())
        self.assertEqual((ok, detail), (False, "Telegram answered 401: Unauthorized"))

    def test_an_error_answer_without_a_reason(self):
        self.fake.mode = "html_error"
        ok, detail = notify.send("hi", self.fake.env())
        self.assertEqual((ok, detail), (False, "Telegram answered 502"))

    def test_a_refusal_with_a_good_status_is_still_a_failure(self):
        self.fake.mode = "refuse"
        ok, detail = notify.send("hi", self.fake.env())
        self.assertEqual((ok, detail), (False, "Telegram refused the message: chat not found"))

    def test_a_garbled_answer_is_a_failure(self):
        self.fake.mode = "garbled"
        ok, detail = notify.send("hi", self.fake.env())
        self.assertEqual((ok, detail), (False, "Telegram's answer could not be read"))

    def test_no_connection_is_a_failure(self):
        self.fake.close()
        ok, detail = notify.send("hi", self.fake.env())
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("could not reach Telegram ("), detail)

    def test_a_timeout_is_a_failure_and_does_not_hang(self):
        self.fake.mode = "slow"
        t0 = time.time()
        ok, detail = notify.send("hi", self.fake.env(), timeout=0.3)
        self.assertFalse(ok)
        self.assertLess(time.time() - t0, 1.2)
        self.assertTrue(detail.startswith("could not reach Telegram ("), detail)

    def test_the_token_never_appears_in_what_comes_back(self):
        for mode in ("401", "echo_token", "refuse", "html_error", "garbled"):
            self.fake.mode = mode
            ok, detail = notify.send("hi", self.fake.env())
            self.assertFalse(ok, mode)
            self.assertNotIn(TOKEN, detail, mode)
            self.assertNotIn("AAH-fake", detail, mode)
        self.fake.mode = "echo_token"
        self.assertEqual(notify.send("hi", self.fake.env()), (False, "Telegram answered 400: bad request to /bot<token>/sendMessage"))

    def test_an_unusable_api_base_sends_nothing(self):
        for bad in ("http://example.com", "example.com", "https://example.com/path", "ftp://x.example"):
            ok, detail = notify.send("hi", self.fake.env(TELEGRAM_API_BASE=bad))
            self.assertFalse(ok, bad)
            self.assertIn("not a clean address", detail)
        self.assertEqual(self.fake.seen, [])


UPDATES = {"ok": True, "result": [
    {"update_id": 1, "message": {"chat": {"id": 111, "type": "private", "first_name": "Sam", "last_name": "Example"}, "text": "hi"}},
    {"update_id": 2, "message": {"chat": {"id": -222, "type": "group", "title": "League Admins"}}},
    {"update_id": 3, "message": {"chat": {"id": 111, "type": "private", "first_name": "Sam", "last_name": "Example"}}},
    {"update_id": 4, "channel_post": {"chat": {"id": -333, "type": "channel", "title": "News"}}},
    {"update_id": 5, "edited_message": {"chat": {"id": 444, "type": "private", "username": "someone"}}},
    {"update_id": 6, "something_else": {}}, {"update_id": 7}]}


class ChatsSeen(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)

    def test_each_chat_is_listed_once_oldest_first_with_a_name(self):
        self.fake.updates = UPDATES
        ok, detail, chats = notify.chats_seen(self.fake.env())
        self.assertEqual((ok, detail), (True, "4 chat(s)"))
        self.assertEqual(chats, [{"id": 111, "type": "private", "name": "Sam Example"}, {"id": -222, "type": "group", "name": "League Admins"},
                                 {"id": -333, "type": "channel", "name": "News"}, {"id": 444, "type": "private", "name": "someone"}])
        self.assertEqual([(m["method"], m["path"]) for m in self.fake.seen], [("GET", "/bot" + TOKEN + "/getUpdates")])

    def test_nobody_has_written_yet(self):
        self.assertEqual(notify.chats_seen(self.fake.env()), (True, "0 chat(s)", []))

    def test_only_the_token_is_needed(self):
        env = self.fake.env()
        env.pop("TELEGRAM_CHAT_ID")
        self.assertTrue(notify.chats_seen(env)[0])
        ok, detail, chats = notify.chats_seen({})
        self.assertEqual((ok, detail, chats), (False, "no bot token", []))
        self.assertEqual(self.fake.seen[-1]["path"], "/bot" + TOKEN + "/getUpdates")

    def test_a_failure_is_a_short_sentence_without_the_token(self):
        self.fake.mode = "401"
        self.assertEqual(notify.chats_seen(self.fake.env()), (False, "Telegram answered 401: Unauthorized", []))
        self.fake.mode = "garbled"
        self.assertEqual(notify.chats_seen(self.fake.env()), (False, "Telegram's answer could not be read", []))
        self.fake.updates = {"ok": False, "description": "bad " + TOKEN}
        self.fake.mode = "ok"
        ok, detail, chats = notify.chats_seen(self.fake.env())
        self.assertEqual((ok, chats), (False, []))
        self.assertNotIn(TOKEN, detail)
        self.assertIn("<token>", detail)
        self.fake.close()
        ok, detail, chats = notify.chats_seen(self.fake.env())
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("could not reach Telegram ("), detail)

    def test_an_unusable_api_base_asks_nothing(self):
        before = len(self.fake.seen)
        ok, detail, chats = notify.chats_seen(self.fake.env(TELEGRAM_API_BASE="http://example.com"))
        self.assertEqual((ok, chats), (False, []))
        self.assertEqual(len(self.fake.seen), before)


class TheFindChatTool(unittest.TestCase):
    """tools/telegram_find_chat.py as the owner runs it: the token comes from a FILE and is never printed."""

    def setUp(self):
        import tempfile
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.close)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.token_file = os.path.join(self.dir.name, "token.txt")
        with open(self.token_file, "w", encoding="utf-8") as f:
            f.write(TOKEN + "\n")

    def run_tool(self, *args):
        import subprocess
        env = dict(os.environ, TELEGRAM_API_BASE="http://127.0.0.1:%d" % self.fake.port, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "telegram_find_chat.py"), *args], capture_output=True, text=True, env=env, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_it_prints_the_chats_and_never_the_token(self):
        self.fake.updates = UPDATES
        code, text = self.run_tool(self.token_file)
        self.assertEqual(code, 0, text)
        self.assertIn("chat id 111", text)
        self.assertIn("Sam Example", text)
        self.assertIn("chat id -222", text)
        self.assertIn("TELEGRAM_CHAT_ID", text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("AAH-fake", text)

    def test_nobody_has_written_yet_says_what_to_do(self):
        code, text = self.run_tool(self.token_file)
        self.assertEqual(code, 1)
        self.assertIn("send it any message", text)

    def test_a_refusing_telegram_is_reported_without_the_token(self):
        self.fake.mode = "401"
        code, text = self.run_tool(self.token_file)
        self.assertEqual(code, 1)
        self.assertIn("Telegram answered 401: Unauthorized", text)
        self.assertNotIn(TOKEN, text)

    def test_a_missing_empty_or_absent_file_is_a_plain_message(self):
        code, text = self.run_tool(os.path.join(self.dir.name, "nope.txt"))
        self.assertEqual(code, 2)
        self.assertIn("Could not read the token file", text)
        empty = os.path.join(self.dir.name, "empty.txt")
        open(empty, "w").close()
        self.assertEqual(self.run_tool(empty)[0], 2)
        code, text = self.run_tool()
        self.assertEqual(code, 2)
        self.assertIn("telegram_find_chat.py", text)


class Scrub(unittest.TestCase):
    def test_the_token_and_anything_shaped_like_one_is_replaced(self):
        env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}
        self.assertEqual(notify.scrub("GET /bot%s/sendMessage failed" % TOKEN, env), "GET /bot<token>/sendMessage failed")
        self.assertEqual(notify.scrub("token " + TOKEN, env), "token <token>")
        other = "bot987654321:ZZZ_other_token_abcdef"
        self.assertEqual(notify.scrub("see /%s/x" % other, env), "see /bot<token>/x")
        self.assertEqual(notify.scrub("nothing to hide", env), "nothing to hide")
        self.assertEqual(notify.scrub(None, env), "None")

    def test_the_default_api_is_telegrams_own(self):
        self.assertEqual(notify.DEFAULT_API, "https://api.telegram.org")


if __name__ == "__main__":
    unittest.main()

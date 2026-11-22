"""
BCL Fantasy - Admin alerts to Telegram                          ops_notify v1.0.0
===========================================================================
Phase 2 of the SA20 readiness plan (Sai's decision, 2026-11-19: "Telegram for admin alerts"). The ONLY code that talks to Telegram.

Dormant until TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in the environment (the Render dashboard; a file on the PC): with either missing, configured() is False
and send() does nothing and says so. The bot token is a SECRET: it lives in the environment only, is never logged, never returned by an endpoint and never part of an
error text. Every text that leaves this module is scrubbed of it, because Telegram's address contains it.

Public API
----------
configured(env=None) -> bool                  both settings present
send(text, env=None, timeout=10) -> (ok, detail)
    One message to the configured chat. `detail` is a short, scrubbed sentence: "sent", "Telegram answered 401: Unauthorized", "could not reach Telegram (URLError)", ...
    It never raises. A message is cut to 3500 characters (Telegram's limit is 4096).
chats_seen(env=None, timeout=10) -> (ok, detail, [{"id", "type", "name"}])
    Who has written to the bot (Telegram's getUpdates), so the owner can find the TELEGRAM_CHAT_ID to set. Needs only the token.
scrub(text, env=None) -> str                  the text with the token (and anything shaped like one) replaced

TELEGRAM_API_BASE (optional) points the module at another server, for the tests only: it must be a clean origin (https, or http on the developer's own machine), or nothing is sent.
`env` is any mapping (default: os.environ), so a test never touches the real environment.
"""

import json
import os
import re
import urllib.error
import urllib.request

from logic import origin as _origin

TOKEN_ENV, CHAT_ENV, API_ENV = "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_API_BASE"
DEFAULT_API = "https://api.telegram.org"
MAX_TEXT = 3500
TIMEOUT_S = 10
_TOKEN_SHAPE = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")


def _credentials(env):
    e = os.environ if env is None else env
    return (e.get(TOKEN_ENV) or "").strip(), (e.get(CHAT_ENV) or "").strip()


def configured(env=None):
    token, chat = _credentials(env)
    return bool(token and chat)


def scrub(text, env=None):
    """`text` with the bot token, and anything shaped like a Telegram bot address, replaced."""
    token, _chat = _credentials(env)
    out = str(text)
    if token:
        out = out.replace(token, "<token>")
    return _TOKEN_SHAPE.sub("bot<token>", out)


def _api_base(env):
    e = os.environ if env is None else env
    raw = (e.get(API_ENV) or "").strip()
    return DEFAULT_API if not raw else _origin.normalize_base_url(raw)


def _call(req, env, timeout):
    """(payload, None) for a good JSON answer from Telegram, else (None, a short scrubbed sentence)."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}"), None
    except urllib.error.HTTPError as e:
        why = ""
        try:
            why = str((json.loads(e.read().decode("utf-8", "replace")) or {}).get("description") or "")
        except Exception:
            pass
        finally:
            e.close()                                        # the error answer holds the connection until it is closed
        return None, scrub("Telegram answered %d%s" % (e.code, (": " + why) if why else ""), env)
    except urllib.error.URLError as e:
        return None, "could not reach Telegram (%s)" % type(e.reason).__name__
    except ValueError:
        return None, "Telegram's answer could not be read"
    except OSError as e:                                     # a timeout, a reset
        return None, "could not reach Telegram (%s)" % type(e).__name__


def send(text, env=None, timeout=TIMEOUT_S):
    """Send one message. Returns (ok, detail); never raises."""
    token, chat = _credentials(env)
    if not (token and chat):
        return False, "Telegram is not set up (no bot token or chat id)"
    base = _api_base(env)
    if not base:
        return False, "TELEGRAM_API_BASE is not a clean address, so nothing was sent"
    body = json.dumps({"chat_id": chat, "text": str(text)[:MAX_TEXT], "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(base + "/bot" + token + "/sendMessage", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "bcl-ops-alert"})
    payload, problem = _call(req, env, timeout)
    if problem:
        return False, problem
    if isinstance(payload, dict) and payload.get("ok") is True:
        return True, "sent"
    why = str(payload.get("description") if isinstance(payload, dict) else "") or "no reason given"
    return False, scrub("Telegram refused the message: " + why, env)


def chats_seen(env=None, timeout=TIMEOUT_S):
    """(ok, detail, chats): the chats that have written to the bot, oldest first, from Telegram's getUpdates. Never raises."""
    token, _chat = _credentials(env)
    if not token:
        return False, "no bot token", []
    base = _api_base(env)
    if not base:
        return False, "TELEGRAM_API_BASE is not a clean address", []
    payload, problem = _call(urllib.request.Request(base + "/bot" + token + "/getUpdates", headers={"User-Agent": "bcl-ops-alert"}), env, timeout)
    if problem:
        return False, problem, []
    if not (isinstance(payload, dict) and payload.get("ok") is True):
        why = str(payload.get("description") if isinstance(payload, dict) else "") or "no reason given"
        return False, scrub("Telegram refused the request: " + why, env), []
    chats, seen = [], set()
    for update in payload.get("result") or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = ((update or {}).get(key) or {}).get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None and chat["id"] not in seen:
                seen.add(chat["id"])
                name = chat.get("title") or " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p) or chat.get("username") or ""
                chats.append({"id": chat["id"], "type": chat.get("type") or "", "name": name})
    return True, "%d chat(s)" % len(chats), chats

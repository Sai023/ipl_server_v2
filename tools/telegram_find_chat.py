"""Find the chat id for the Ops alerts (Phase 2 of the SA20 readiness plan).

1. Create your bot (talk to @BotFather in Telegram: /newbot) and save the token it gives you in a FILE on your PC (one line, nothing else).
2. Open Telegram, find your new bot and send it any message (for example "hi").
3. Run, in PowerShell, from the repository folder:

       py -3.14 tools\\telegram_find_chat.py C:\\path\\to\\your_token_file.txt

It asks Telegram which chats have written to the bot and prints their ids and names. Set the id of YOUR chat in Render as TELEGRAM_CHAT_ID (a private chat is a plain number; a group
id starts with a minus sign). The token is read from the file only: it is never printed, never taken from the command line, never put in an error message.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ops_notify  # noqa: E402


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as e:
        print("Could not read the token file (%s)." % type(e).__name__)
        return 2
    if not token:
        print("The token file is empty.")
        return 2
    env = {"TELEGRAM_BOT_TOKEN": token}
    if os.environ.get("TELEGRAM_API_BASE"):                   # for the tests only
        env["TELEGRAM_API_BASE"] = os.environ["TELEGRAM_API_BASE"]
    ok, detail, chats = ops_notify.chats_seen(env)
    if not ok:
        print("Telegram said: " + detail)
        return 1
    if not chats:
        print("No chat has written to the bot yet. Open Telegram, find your bot, send it any message (for example hi), then run this again.")
        return 1
    print("Chats that have written to the bot:")
    for c in chats:
        print("  chat id %-16s %-9s %s" % (c["id"], c["type"], c["name"]))
    print("Put the id of YOUR chat in Render as TELEGRAM_CHAT_ID (the bot token goes in as TELEGRAM_BOT_TOKEN).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

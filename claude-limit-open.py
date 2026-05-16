#!/usr/bin/env python3
"""
Called by at-job exactly when the Claude Code usage window resets.
Sends Telegram notification with retry on transient network failures.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"
LOG_FILE = os.path.expanduser("~/.claude/.limit-notifier.log")

TEXT = "✅ <b>Claude Code: окно открылось!</b>\nМожно возвращаться к работе."


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")


def send_telegram(text, retries=5, delay=10):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    log(f"open: sent on attempt {attempt}")
                    return True
                log(f"open: API error attempt {attempt}: {result}")
        except Exception as e:
            log(f"open: attempt {attempt} failed: {e}")
        if attempt < retries:
            time.sleep(delay)
    return False


if __name__ == "__main__":
    import os
    log("open script started")
    ok = send_telegram(TEXT)
    if not ok:
        log("open: ERROR all attempts failed")
        sys.exit(1)

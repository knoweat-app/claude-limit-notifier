#!/usr/bin/env python3
"""Watches Claude Code usage limits and schedules at-job for reset notification."""

import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

BOT_TOKEN = "8253128219:AAE5xCqXtoE9bnzSLlwAgRZCupGUswNLgYk"
CHAT_ID = "8230181230"
CREDS_FILE = os.path.expanduser("~/.claude/.credentials.json")
CC_CACHE_FILE = os.path.expanduser("~/.claude/.usage-cache.json")  # written by statusline.sh
API_CACHE_FILE = "/root/scripts/claude-limit-api-cache.json"       # our own cache
API_CACHE_TTL = 300      # 5 minutes — prevents 429
API_ATTEMPT_TTL = 270    # wait 4.5 min after any failed attempt before retrying
CC_CACHE_MAX_AGE = 180   # 3 minutes — trust statusline cache if fresh
API_ATTEMPT_FILE = "/root/scripts/claude-limit-last-attempt"
STATE_FILE = "/root/scripts/claude-limit-notifier-state.json"
OPEN_SCRIPT = "/root/scripts/claude-limit-open.py"
LOG_FILE = "/root/scripts/claude-limit-notifier.log"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")


def send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                log(f"send failed: {result}")
    except Exception as e:
        log(f"send error: {e}")


def fetch_usage():
    """Return usage data: use fresh CC cache → our API cache → live API call."""
    now = time.time()

    # 1. statusline.sh cache (written when CC is active on this server)
    try:
        if now - os.path.getmtime(CC_CACHE_FILE) < CC_CACHE_MAX_AGE:
            with open(CC_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass

    # 2. Our own API cache (prevents 429 — max one real call per 5 min)
    try:
        if now - os.path.getmtime(API_CACHE_FILE) < API_CACHE_TTL:
            with open(API_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass

    # 2b. Cooldown after a failed attempt (prevents 429 cascade on repeated DNS/auth errors)
    try:
        if now - os.path.getmtime(API_ATTEMPT_FILE) < API_ATTEMPT_TTL:
            return None
    except Exception:
        pass

    # 3. Live API call — stamp attempt time first so failure also triggers cooldown
    try:
        open(API_ATTEMPT_FILE, "w").close()
    except Exception:
        pass

    try:
        with open(CREDS_FILE) as f:
            token = json.load(f)["claudeAiOauth"]["accessToken"]
    except Exception:
        return None

    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        with open(API_CACHE_FILE, "w") as f:
            json.dump(data, f)
        return data
    except Exception as e:
        log(f"fetch_usage failed: {e}")
        return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)


def fmt_moscow(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    mins = max(0, int((dt - now).total_seconds() / 60))
    msk = dt + timedelta(hours=3)
    suffix = f" (через {mins} мин)" if mins > 0 else ""
    return f"{msk.strftime('%H:%M')} МСК{suffix}"


def schedule_at(resets_at_str):
    """Create one-time at job for the reset moment (server local time = UTC)."""
    dt = datetime.fromisoformat(resets_at_str.replace("Z", "+00:00"))
    local_dt = dt.astimezone()
    local_time = local_dt.strftime("%H:%M %Y-%m-%d")
    cmd = f"echo '/usr/bin/python3 {OPEN_SCRIPT}' | at {local_time}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        log(f"at job scheduled for {local_time}")
        return True
    log(f"at schedule failed: {result.stderr}")
    return False


def main():
    data = fetch_usage()
    if not data:
        return

    state = load_state()

    for key, label in [("five_hour", "5-часовой"), ("seven_day", "недельный")]:
        entry = data.get(key)
        if not entry:
            continue
        util = entry.get("utilization", 0)
        resets_at_str = entry.get("resets_at")
        if not resets_at_str:
            continue

        s = state.setdefault(key, {})

        # Detect new window by minute (API milliseconds drift on each call)
        same_window = s.get("resets_at", "")[:16] == resets_at_str[:16]
        if not same_window:
            s.update({"warned_80": False, "warned_90": False, "at_scheduled": False})
        # Always store latest resets_at (needed for at-job scheduling accuracy)
        s["resets_at"] = resets_at_str

        # At 80%: schedule at-job + first warning (once)
        if util >= 80 and not s.get("at_scheduled"):
            log(f"{key} hit 80% (util={util}), scheduling at-job")
            ok = schedule_at(resets_at_str)
            s["at_scheduled"] = ok
            time_str = fmt_moscow(resets_at_str)
            send(
                f"⚠️ <b>Claude Code {label}: 80% использовано</b>\n"
                f"Окно сбросится в {time_str}\n"
                f"Уведомление придёт автоматически."
            )
            s["warned_80"] = True

        # At 90%: second warning (once)
        if util >= 90 and not s.get("warned_90"):
            send(
                f"🔴 <b>Claude Code {label}: 90% использовано</b>\n"
                f"Осталось совсем немного. Уведомление придёт когда окно откроется."
            )
            s["warned_90"] = True

    save_state(state)


if __name__ == "__main__":
    main()

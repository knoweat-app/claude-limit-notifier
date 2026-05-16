#!/usr/bin/env python3
"""
Claude Code usage limit watcher.
Runs every minute via cron. Reads local CC cache first, falls back to API.
Sends Telegram warning at 80% and 90%, schedules at-job for window-open notification.
"""

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone, timedelta

BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"
CREDS_FILE = os.path.expanduser("~/.claude/.credentials.json")
CC_CACHE_FILE = os.path.expanduser("~/.claude/.usage-cache.json")
API_CACHE_FILE = os.path.expanduser("~/.claude/.limit-notifier-api-cache.json")
STATE_FILE = os.path.expanduser("~/.claude/.limit-notifier-state.json")
OPEN_SCRIPT = os.path.expanduser("~/claude-limit-open.py")
LOG_FILE = os.path.expanduser("~/.claude/.limit-notifier.log")

CC_CACHE_MAX_AGE = 180   # trust statusline cache if fresher than 3 min
API_CACHE_TTL = 300      # call live API at most once per 5 min


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
    now = time.time()

    # 1. Claude Code statusline cache (written every 2 min when CC is active)
    try:
        if now - os.path.getmtime(CC_CACHE_FILE) < CC_CACHE_MAX_AGE:
            with open(CC_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass

    # 2. Our own API response cache (avoids rate-limiting)
    try:
        if now - os.path.getmtime(API_CACHE_FILE) < API_CACHE_TTL:
            with open(API_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass

    # 3. Live API call (with retry for transient DNS failures)
    try:
        with open(CREDS_FILE) as f:
            token = json.load(f)["claudeAiOauth"]["accessToken"]
    except Exception:
        return None

    for attempt in range(3):
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
            log(f"fetch_usage attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(5)

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

        same_window = s.get("resets_at", "")[:16] == resets_at_str[:16]
        if not same_window:
            s.update({"warned_80": False, "warned_90": False, "at_scheduled": False})
        s["resets_at"] = resets_at_str

        if util >= 80 and not s.get("at_scheduled"):
            log(f"{key} hit {util}%, scheduling at-job")
            ok = schedule_at(resets_at_str)
            s["at_scheduled"] = ok
            time_str = fmt_moscow(resets_at_str)
            send(
                f"⚠️ <b>Claude Code {label}: 80% использовано</b>\n"
                f"Окно сбросится в {time_str}\n"
                f"Уведомление придёт автоматически."
            )
            s["warned_80"] = True

        if util >= 90 and not s.get("warned_90"):
            send(
                f"🔴 <b>Claude Code {label}: 90% использовано</b>\n"
                f"Осталось совсем немного."
            )
            s["warned_90"] = True

    save_state(state)


if __name__ == "__main__":
    main()

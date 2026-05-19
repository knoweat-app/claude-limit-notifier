#!/usr/bin/env python3
"""Watches Claude Code usage limits and schedules at-job for reset notification."""

import json
import os
import re
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
AUTH_FAIL_TTL = 3600     # 60 min cooldown after 401 — expired token won't help sooner
CC_CACHE_MAX_AGE = 180   # 3 minutes — trust statusline cache if fresh
API_ATTEMPT_FILE = "/root/scripts/claude-limit-last-attempt"
AUTH_FAIL_FILE = "/root/scripts/claude-limit-auth-fail"
STATE_FILE = "/root/scripts/claude-limit-notifier-state.json"
OPEN_SCRIPT = "/root/scripts/claude-limit-open.py"
LOG_FILE = "/root/scripts/claude-limit-notifier.log"

# Tolerance for same-window detection: resets_at drifts a few seconds between API calls.
# Use 30-minute window so drift never triggers a false "new window" reset.
WINDOW_TOLERANCE_SEC = 1800


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


def parse_ts(iso_str):
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()


def same_reset_window(stored, new_str):
    """True if stored and new resets_at refer to the same window (within tolerance)."""
    if not stored:
        return False
    try:
        return abs(parse_ts(stored) - parse_ts(new_str)) < WINDOW_TOLERANCE_SEC
    except Exception:
        return False


def fetch_usage():
    """Return usage data: fresh CC cache → our API cache → live API call."""
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

    # 2b. Long cooldown after 401 — expired token won't recover without user action
    try:
        if now - os.path.getmtime(AUTH_FAIL_FILE) < AUTH_FAIL_TTL:
            return None
    except Exception:
        pass

    # 2c. Short cooldown after any other failed attempt
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
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Token expired — long cooldown, no point retrying every 5 min
            try:
                open(AUTH_FAIL_FILE, "w").close()
            except Exception:
                pass
            log(f"fetch_usage failed: 401 Unauthorized (token expired, cooling down {AUTH_FAIL_TTL//60} min)")
        elif e.code == 429:
            log(f"fetch_usage failed: 429 Too Many Requests")
        else:
            log(f"fetch_usage failed: {e}")
        return None
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


def cancel_open_at_jobs():
    """Cancel any pending at-jobs that run the open script."""
    try:
        result = subprocess.run("atq", capture_output=True, text=True)
        for line in result.stdout.splitlines():
            m = re.match(r"^\s*(\d+)", line)
            if not m:
                continue
            job_id = m.group(1)
            detail = subprocess.run(
                f"at -c {job_id}", shell=True, capture_output=True, text=True
            )
            if OPEN_SCRIPT in detail.stdout:
                subprocess.run(f"atrm {job_id}", shell=True, capture_output=True)
                log(f"cancelled stale at-job {job_id}")
    except Exception:
        pass


def schedule_at(resets_at_str):
    """Create one-time at job for the reset moment (server local time = UTC)."""
    cancel_open_at_jobs()
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

        # Detect new window using timestamp tolerance (30 min) instead of string [:16].
        # The API's resets_at drifts a few seconds per call — minute-level comparison
        # caused false "new window" resets and duplicate notifications.
        if not same_reset_window(s.get("resets_at", ""), resets_at_str):
            s.update({"warned_80": False, "warned_90": False, "at_scheduled": False})
        s["resets_at"] = resets_at_str

        # At 80%: send warning once, schedule at-job once (independently)
        if util >= 80 and not s.get("warned_80"):
            time_str = fmt_moscow(resets_at_str)
            send(
                f"⚠️ <b>Claude Code {label}: 80% использовано</b>\n"
                f"Окно сбросится в {time_str}\n"
                f"Уведомление придёт автоматически."
            )
            s["warned_80"] = True

        if util >= 80 and not s.get("at_scheduled"):
            log(f"{key} at 80%+ (util={util}), scheduling at-job")
            ok = schedule_at(resets_at_str)
            s["at_scheduled"] = ok

        # At 90%: second warning once
        if util >= 90 and not s.get("warned_90"):
            send(
                f"🔴 <b>Claude Code {label}: 90% использовано</b>\n"
                f"Осталось совсем немного. Уведомление придёт когда окно откроется."
            )
            s["warned_90"] = True

    save_state(state)


if __name__ == "__main__":
    main()

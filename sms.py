#!/usr/bin/env python3
"""Twilio SMS on the laptop.

  sms.py watch              show incoming SMS as they arrive (desktop notification + terminal)
  sms.py send <to> <text>   send an SMS from the Twilio number
"""
import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).with_name("twilio.env")
INBOX_LOG = Path(__file__).with_name("inbox.log")
POLL_SECONDS = 5


def load_env():
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    missing = [k for k in ("ACCOUNT_SID", "TWILIO_NUMBER") if not env.get(k)]
    if not env.get("AUTH_TOKEN") and not (env.get("API_KEY_SID") and env.get("API_KEY_SECRET")):
        missing.append("AUTH_TOKEN (or API_KEY_SID + API_KEY_SECRET)")
    if missing:
        sys.exit(f"Fill in {', '.join(missing)} in {ENV_FILE}")
    return env


def api(env, method, params=None):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{env['ACCOUNT_SID']}/Messages.json"
    data = None
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params)
    elif params:
        data = urllib.parse.urlencode(params).encode()
    # API key pair if its secret is set, otherwise the account's auth token
    if env.get("API_KEY_SECRET"):
        user, password = env["API_KEY_SID"], env["API_KEY_SECRET"]
    else:
        user, password = env["ACCOUNT_SID"], env["AUTH_TOKEN"]
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Twilio error {e.code}: {e.read().decode()}")


def show(msg, notify):
    line = f"[{msg['date_sent'] or msg['date_created']}] {msg['from']} -> {msg['to']}: {msg['body']}"
    print(line, flush=True)
    if not notify:
        return
    with INBOX_LOG.open("a") as f:
        f.write(line + "\n")
    if shutil.which("wl-copy"):
        # new SMS lands on the clipboard so it can be pasted straight away
        subprocess.run(["wl-copy", msg["body"]], check=False)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", f"SMS from {msg['from']} to {msg['to']} (copied)", msg["body"]], check=False)


def watch(env):
    seen = None
    print("Watching every number on the account for incoming SMS (Ctrl+C to stop)")
    while True:
        messages = api(env, "GET", {"PageSize": 30})["messages"]
        inbound = [m for m in reversed(messages) if m["direction"] == "inbound"]
        if seen is None:
            # first pass: print recent history without notifications
            for m in inbound[-5:]:
                show(m, notify=False)
            seen = {m["sid"] for m in inbound}
        else:
            for m in inbound:
                if m["sid"] not in seen:
                    seen.add(m["sid"])
                    show(m, notify=True)
        time.sleep(POLL_SECONDS)


def send(env, to, text):
    msg = api(env, "POST", {"From": env["TWILIO_NUMBER"], "To": to, "Body": text})
    print(f"Sent {msg['sid']} to {msg['to']} (status: {msg['status']})")


def main():
    args = sys.argv[1:]
    if args[:1] == ["watch"]:
        try:
            watch(load_env())
        except KeyboardInterrupt:
            pass
    elif args[:1] == ["send"] and len(args) >= 3:
        send(load_env(), args[1], " ".join(args[2:]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""SMS for the phone app (Linphone) through the Twilio number.

  text arrives on the Twilio number  -> shows up as a chat message in the app
  chat message typed in the app      -> sent as an SMS from the Twilio number

Talks to Asterisk on this laptop over its manager port (AMI) and to Twilio over its API.
Run by the twilio-sms-bridge user service; settings come from linphone.env and ../twilio.env.
"""
import asyncio
import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "sms_state.json"
POLL_SECONDS = 5
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("sms-bridge")


def read_env(path):
    return dict(line.strip().split("=", 1) for line in path.read_text().splitlines()
                if "=" in line and not line.startswith("#"))


TW = read_env(HERE.parent / "twilio.env")
LP = read_env(HERE / "linphone.env")
NUMBER, APP_USER, APP_DOMAIN = LP["NUMBER"], LP["APP_USERNAME"], LP["APP_DOMAIN"]
MESSAGES_URL = f"https://api.twilio.com/2010-04-01/Accounts/{TW['ACCOUNT_SID']}/Messages.json"
AUTH = "Basic " + base64.b64encode(f"{TW['ACCOUNT_SID']}:{TW['AUTH_TOKEN']}".encode()).decode()


def twilio(params=None, query=None):
    url = MESSAGES_URL + ("?" + urllib.parse.urlencode(query) if query else "")
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(url, data=data, headers={"Authorization": AUTH})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.loads(e.read().decode() or "{}").get("message", f"HTTP {e.code}")) from None


def to_e164(user):
    number = re.sub(r"[^\d+]", "", urllib.parse.unquote(user))
    if number.startswith("00"):
        number = "+" + number[2:]
    return number if number.startswith("+") else "+" + number


class Ami:
    """Minimal Asterisk Manager Interface client: actions with replies, plus an event callback."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.writer = None
        self.waiting = {}
        self.counter = 0

    async def connect(self):
        reader, self.writer = await asyncio.open_connection("127.0.0.1", 5038)
        await reader.readline()                      # "Asterisk Call Manager/x.y" banner
        self.reader_task = asyncio.create_task(self._read(reader))
        reply = await self.action({"Action": "Login", "Username": LP["AMI_USER"], "Secret": LP["AMI_SECRET"]})
        if reply.get("Response") != "Success":
            raise RuntimeError(f"AMI login failed: {reply.get('Message')}")

    async def _read(self, reader):
        block = {}
        while line := await reader.readline():
            line = line.decode("utf-8", "replace").rstrip("\r\n")
            if line:
                key, _, value = line.partition(":")
                block[key.strip()] = value.strip()
                continue
            if "Response" in block and block.get("ActionID") in self.waiting:
                self.waiting.pop(block["ActionID"]).set_result(block)
            elif "Event" in block:
                asyncio.create_task(self.on_event(block))
            block = {}
        for fut in self.waiting.values():
            fut.set_exception(ConnectionError("AMI connection closed"))
        raise ConnectionError("AMI connection closed")

    async def action(self, fields):
        self.counter += 1
        action_id = str(self.counter)
        fut = asyncio.get_running_loop().create_future()
        self.waiting[action_id] = fut
        lines = [f"{k}: {v}" for k, v in {**fields, "ActionID": action_id}.items()]
        self.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await self.writer.drain()
        return await asyncio.wait_for(fut, 15)


class Bridge:
    def __init__(self):
        self.ami = Ami(self.on_event)
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        self.seen = state.get("seen")                # None on the very first run
        self.pending = state.get("pending", [])      # texts the app has not received yet

    def save(self):
        STATE.write_text(json.dumps({"seen": (self.seen or [])[-500:], "pending": self.pending}))

    async def to_app(self, sender, body):
        """Deliver one chat message to the app. Returns False if no device is logged in."""
        reply = await self.ami.action({
            "Action": "MessageSend", "Destination": f"pjsip:{APP_USER}",
            "From": f'"{sender}" <sip:{sender}@{APP_DOMAIN}>',
            "Base64Body": base64.b64encode(body.encode()).decode()})
        return reply.get("Response") == "Success"

    async def on_event(self, event):
        if event.get("UserEvent") != "LinphoneSms":
            return
        to = to_e164(event.get("To", ""))
        body = base64.b64decode(event.get("Body64", "")).decode("utf-8", "replace")
        try:
            msg = await asyncio.to_thread(twilio, {"From": NUMBER, "To": to, "Body": body})
            log.info("app -> %s: %r (%s)", to, body, msg["status"])
        except Exception as e:  # noqa: BLE001
            log.info("app -> %s FAILED: %s", to, e)
            await self.to_app(to, f"[not sent] {e}")

    async def poll(self):
        while True:
            try:
                messages = (await asyncio.to_thread(twilio, None, {"To": NUMBER, "PageSize": 30}))["messages"]
                inbound = [m for m in reversed(messages) if m["direction"] == "inbound"]
                if self.seen is None:                # first run: don't replay old texts
                    self.seen = [m["sid"] for m in inbound]
                for m in inbound:
                    if m["sid"] not in self.seen:
                        self.seen.append(m["sid"])
                        self.pending.append({"from": m["from"], "body": m["body"]})
                        log.info("%s -> app: %r", m["from"], m["body"])
                while self.pending:
                    if not await self.to_app(self.pending[0]["from"], self.pending[0]["body"]):
                        break                        # app is offline; try again next round
                    self.pending.pop(0)
                self.save()
            except ConnectionError:
                raise
            except Exception as e:  # noqa: BLE001
                log.info("poll failed: %s", e)
            await asyncio.sleep(POLL_SECONDS)

    async def run(self):
        await self.ami.connect()
        log.info("bridging SMS for %s <-> %s@%s", NUMBER, APP_USER, APP_DOMAIN)
        await asyncio.gather(self.poll(), self.ami.reader_task)


if __name__ == "__main__":
    asyncio.run(Bridge().run())

# Twilio SMS Bridge for Linphone

This project connects a Twilio phone number to the Linphone softphone app so that the number behaves like a normal mobile line: calls to the number ring Linphone, calls dialled in Linphone go out through Twilio, SMS sent to the number appear as chat messages in Linphone, and chat messages typed in Linphone are sent as SMS. The glue is a small Asterisk PBX on the laptop plus two Python scripts: `sms.py`, a command-line tool to watch and send SMS, and `linphone/sms_bridge.py`, a long-running service that shuttles messages between Twilio's REST API and Asterisk's Manager Interface (AMI). A Twilio Function (`linphone/voice.js`) and an Asterisk dialplan snippet (`linphone/extensions_twilio.conf`) handle the voice side.

## Where it is used

In Abrar's setup this gives a phone a real, dialable number without a SIM: Linphone on the phone registers to the home Asterisk over the private network, and the bridge service runs under systemd on the laptop. Incoming verification codes and other texts pop up as desktop notifications (via `sms.py watch`) and as Linphone chats (via the bridge), and the same number is used for outgoing calls and texts.

## How it works

### Voice path

```
Linphone --SIP--> Asterisk --PJSIP trunk--> Twilio SIP Domain --Function--> PSTN
PSTN --> Twilio number --Function (voice.js)--> SIP to the Twilio SIP domain user --> Asterisk --> every Linphone device
```

- `extensions_twilio.conf` defines three contexts: `linphone-out` dials whatever number Linphone asked for through the `twilio` PJSIP endpoint (90 s timeout); `twilio-in` rings all registered contacts of the app user for 30 s; `linphone-sms` catches SIP MESSAGE requests from Linphone and raises an Asterisk `UserEvent(LinphoneSms, To: ..., Body64: <base64 body>)` instead of delivering them anywhere.
- `voice.js` is deployed as a Twilio Function. If the `To` is a `sip:` address (the call came from Linphone through the SIP domain), it extracts the digits, normalises `00` to `+`, and `<Dial>`s the number with the Twilio number as caller id. Otherwise the call came from the PSTN and it `<Dial>`s the configured SIP user with a 30 s timeout.

### SMS path (`linphone/sms_bridge.py`)

- Reads `../twilio.env` (account credentials, Twilio number) and `linphone.env` (SIP/app user, AMI credentials).
- `Ami` is a minimal asyncio AMI client: connects to `127.0.0.1:5038`, logs in, parses response blocks keyed by `ActionID`, and dispatches events to a callback.
- **Inbound (Twilio -> app).** `poll()` fetches the last 30 messages to the number every 5 s, keeps a `seen` list of message SIDs (the first run only records history and does not replay it), and appends new inbound texts to a `pending` queue. Each pending item is delivered with the AMI `MessageSend` action to `pjsip:<APP_USERNAME>` with the sender's number as the From address. If Asterisk reports failure (no device registered) the queue stops and retries on the next poll. State (`seen`, trimmed to 500, and `pending`) is persisted in `sms_state.json`.
- **Outbound (app -> Twilio).** When the `LinphoneSms` UserEvent arrives, the destination is normalised to E.164 and the decoded body is POSTed to Twilio's Messages API. On failure a `[not sent] <reason>` chat line is pushed back to the app.

### CLI (`sms.py`)

- `sms.py watch` polls the account's messages every 5 s, prints inbound ones, appends them to `inbox.log`, copies the body to the Wayland clipboard with `wl-copy` and shows a `notify-send` desktop notification. The first pass prints the last five without notifying.
- `sms.py send <to> <text>` posts a message from the Twilio number.
- Authentication uses an API key pair when `API_KEY_SECRET` is set, otherwise the account auth token; the script exits with a clear message listing any missing settings.

### Files

| Path | Role |
|---|---|
| `sms.py` | Watch/send CLI, stdlib only |
| `linphone/sms_bridge.py` | AMI <-> Twilio bridge service |
| `linphone/extensions_twilio.conf` | Asterisk dialplan contexts to include |
| `linphone/voice.js` | Twilio Function for call routing |
| `linphone/provisioning-qr.png` | QR code pointing Linphone at the remote-provisioning XML |
| `linphone/sms_state.json` | Runtime state (seen SIDs, pending texts) |
| `twilio.env`, `linphone/linphone.env` | Settings (not committed) |
| `inbox.log` | Runtime log written by `sms.py watch` |

## Setup

Requirements: Python 3.10+ (no third-party packages), Asterisk with PJSIP and the AMI enabled on the laptop, a Twilio account with a phone number, a Twilio SIP Domain with a credential list, and Linphone on the phone. Optional for `sms.py watch`: `wl-copy` and `notify-send`.

`twilio.env` (next to `sms.py`):

```ini
ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AUTH_TOKEN=your-auth-token
# or an API key pair instead of AUTH_TOKEN
API_KEY_SID=SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
API_KEY_SECRET=your-api-key-secret
TWILIO_NUMBER=++10000000000
```

`linphone/linphone.env`:

```ini
NUMBER=++10000000000               # the Twilio number
SIP_USERNAME=linphone             # credential-list user on the Twilio SIP domain
SIP_PASSWORD=change-me
SIP_DOMAIN=example.sip.twilio.com
FUNCTION_URL=https://example.twil.io/voice
SERVICE_SID=ZSxxxxxxxx
SIP_DOMAIN_SID=SDxxxxxxxx
CREDENTIAL_LIST_SID=CLxxxxxxxx
APP_DOMAIN=pbx.example.lan        # Asterisk domain Linphone registers to
APP_USERNAME=USER                 # PJSIP endpoint used by the phone
APP_PASSWORD=change-me
AMI_USER=bridge
AMI_SECRET=change-me
PROVISIONING_URL=https://example.com/TOKEN/linphone.xml
CARDDAV_URL=https://example.com/dav/USER/contacts/
CARDDAV_USERNAME=USER
CARDDAV_PASSWORD=change-me
```

Asterisk: include `linphone/extensions_twilio.conf` from `extensions.conf`, define a PJSIP endpoint named `twilio` pointing at the SIP domain, give the app user's endpoint the `linphone-out` context and `message_context=linphone-sms`, and route the Twilio inbound calls to `twilio-in`. Enable the AMI user in `manager.conf` with `read=user` (for UserEvents) and `write=message`.

Twilio: deploy `voice.js` as a Function with `CALLER_ID`, `SIP_USER` and `SIP_DOMAIN` environment variables, set it as the voice URL of the number and of the SIP domain.

Run the bridge as a user service:

```ini
# ~/.config/systemd/user/twilio-sms-bridge.service
[Unit]
Description=Twilio <-> Linphone SMS bridge
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 %h/Workspace/twilio/linphone/sms_bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

## Usage

```bash
# desktop: watch incoming texts (notification + clipboard)
~/Workspace/twilio/sms.py watch

# send one
~/Workspace/twilio/sms.py send ++10000000000 "On my way"

# bridge service
systemctl --user enable --now twilio-sms-bridge
journalctl --user -u twilio-sms-bridge -f
```

In Linphone: dial any number to call through Twilio; open a chat with `+<number>` and type to send an SMS; incoming texts arrive as chats from the sender's number.

## Project structure

```
twilio/
├── sms.py
├── twilio.env                 # secrets, not committed
├── inbox.log                  # runtime
└── linphone/
    ├── sms_bridge.py
    ├── extensions_twilio.conf
    ├── voice.js
    ├── linphone.env           # secrets, not committed
    ├── provisioning-qr.png
    └── sms_state.json         # runtime
```

## Limitations and notes

- Inbound SMS are discovered by polling (every 5 s), not by webhook, so there is up to a few seconds of delay and the laptop must be online.
- Only inbound texts to `NUMBER` are bridged; MMS attachments are ignored (only `body` is forwarded).
- If no Linphone device is registered, texts wait in `sms_state.json` and are delivered when one connects.
- `sms_bridge.py` uses the account auth token only; `sms.py` can use an API key pair instead.
- The bridge assumes Asterisk's AMI on `127.0.0.1:5038`; change the constant in `Ami.connect` for another host.
- The Asterisk, Twilio SIP domain and provisioning setup live outside this folder and must be configured by hand.

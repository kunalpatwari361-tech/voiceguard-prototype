# Castle security and the Admin Console

VoiceGuard's server is protected by seven layers, like the walls of a castle. Every request passes all of them.
A breach at any layer is recorded, the admin team is alerted, and the attacker is stopped automatically.

| # | Layer | Guards against | Automatic stop |
|---|---|---|---|
| 1 | Moat – network | request floods, scanners probing for pages, blocked addresses | address blocked (30–60 min) |
| 2 | Outer wall – sign-in | OTP code guessing, fake or stolen login tokens | address blocked; the targeted number's sign-in paused |
| 3 | Gatehouse – access | reaching other people's data, probing admin pages | account signed out on all devices and paused |
| 4 | Inner wall – input | SQL / script / command injection, path traversal, oversized uploads | request rejected; repeat attacker blocked |
| 5 | Keep – data | bulk scraping of numbers, server errors (bugs), database damage | account paused; database damage → lockdown; backups |
| 6 | Vault – integrity | server code, AI weights or settings changed while running | **lockdown** – all user traffic stops until an admin clears it |
| 7 | Royal guard – app | modified or re-signed copies of the app | recorded (`monitor`) or blocked (`enforce`) |

**Watchtower** (always on): records every breach (`sec_event`), groups serious ones into incidents (`sec_incident`),
pushes an alert to every admin's VoiceGuard app, and sends WhatsApp for *high* and *critical* incidents. It also checks
the server files every minute and the database every 30 minutes.

**Lockdown**: users get "VoiceGuard is in security lockdown" (503); health, sign-in and the Admin Console keep working so
admins can investigate and end it.

## Admin Console – `http://<server>:8000/admin/console`

| Tab | What you can do |
|---|---|
| Security | castle view of the 7 layers (green / amber / red), open incidents, **Lockdown now**, **Send test alert** |
| Incidents | every incident with what was done automatically; acknowledge, resolve, reopen |
| Blocked | blocked addresses, numbers and paused accounts; block or unblock by hand |
| Data | rows in every table, database size, integrity check, **Back up now** (last 10 backups kept) |
| Logs | server log (filter by text) and the security event log |
| Reports | users, families, scam reports, evidence, most-reported numbers, alerts and voice checks of the last 7 days |
| Updates & bugs | server / AI / app versions, which files changed since the approved version (**approve** after an update), server errors |

Access is checked by the server for every request (non-admins get 403, and the attempt raises a Layer 3 alert).

## Set-up (`backend/.env`)

```
VG_ADMIN_PHONES=+91XXXXXXXXXX            # admin accounts (comma-separated) – they get the alerts and the console
VG_APP_CERTS=<sha256 of the APK signing certificate>   # set to the current APK's certificate
VG_APP_TRUST_MODE=monitor                # enforce = only the genuine app may use the API
```
Optional thresholds: `VG_SEC_FLOOD` (600/min), `VG_SEC_SCAN` (30 / 5 min), `VG_SEC_BAD_TOKEN` (40 / 10 min),
`VG_SEC_OTP_IP` (10 / 15 min), `VG_SEC_OTP_PHONE` (8), `VG_SEC_FORBIDDEN_USER` (8 / 10 min), `VG_SEC_FORBIDDEN_IP` (25 / 5 min),
`VG_SEC_ATTACK_IP` (5 / 10 min), `VG_SEC_SCRAPE` (300 lookups / 10 min), `VG_SEC_BAN_MINUTES` (60), `VG_SEC_MAX_BODY_MB` (30),
`VG_SEC_TAMPER_LOCKDOWN` (1), `VG_TRUST_PROXY` (1 when behind a reverse proxy).

**After installing an update**: restart the server, open Updates & bugs, check the listed files and click *approve*.
Editing server files while it runs triggers a lockdown on purpose.

## Tests

- `backend\scripts\security_test.py` – 35 checks: attacks every layer (scanner, flood, fake tokens, OTP guessing,
  admin probing, SQL / script / path injection, oversized upload, scraping, a server bug, a modified app, live
  tampering) and checks each is stopped, the admin alerted, the lockdown works and the console tools work.
- Regression with all layers on: `smoke_test.py` 93/93, `caller_identity_test.py` 41/41.

## Files

New: `backend/app/security.py`, `backend/app/routers/admin_console.py`, `backend/app/static/admin_console.html`,
`backend/scripts/security_test.py`, `android/.../data/AppIdentity.kt`.
Changed: `backend/app/main.py` (gate, watchtower start, console router), `android/.../data/Api.kt` (app identity header).

## Limits (prototype)

- The server runs plain HTTP on USB / local Wi-Fi; use HTTPS (reverse proxy) for real users.
- Counters and blocks of one server process; a multi-server deployment needs a shared store (e.g. Redis).
- Not a replacement for a hosted firewall / DDoS protection in front of a public server.
- WhatsApp alerts reach admins who joined the Twilio sandbox (a paid WhatsApp sender removes this).

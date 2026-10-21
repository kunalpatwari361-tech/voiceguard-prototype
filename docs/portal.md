# VoiceGuard Team Portal (confidential)

One web portal where the VoiceGuard team runs the service: the AI, security, evidence, data from telecom operators,
and everything about fraud and scam calls. Only team members can sign in.

**Address:** `http://localhost:8000/portal` on the server computer. From other computers it only works over HTTPS
(see *Using it from other computers*).

## Signing in: two steps

1. Enter your phone number and tap **Send SMS code**. A code goes out only if the number belongs to a team member.
   Everyone gets the same reply, so the page never reveals who is on the team.
2. Enter the **SMS code** and the **6-digit code from your authenticator app** (Google Authenticator,
   Microsoft Authenticator or any TOTP app).

**First sign-in:** leave the authenticator code empty. The portal shows a QR code: scan it in the authenticator app
and type the code it shows. Until that is done, the session can't open any data.

**Lost phone:** an owner or admin opens Team → **Reset authenticator**. All of that member's sessions end, and they
set up the app again at their next sign-in.

## Who can open what

| Role | Sections | Sees users' phone numbers |
|---|---|---|
| Owner | everything; can add owners and admins | yes |
| Admin | everything; can add any role below admin | yes |
| Fraud analyst | Dashboard, Fraud & scams, Evidence, Telecom data, Network, Daily reports, Logs (evidence) | yes |
| Security | Dashboard, Security, Telecom data, Network, Audit log, Daily reports, Logs (server, security) | no (hidden) |
| AI engineer | Dashboard, AI models, Network, Daily reports, Logs (AI checks) | no |
| Viewer | Dashboard, Daily reports (headline numbers only) | no |

Each part of a daily report (security, evidence, AI, fraud, network, team) is shown only to roles that may open that section.

Only owners and admins can create or revoke operator API keys and switch the live AI model. The server checks the
role on every request. Opening a section outside your role is refused, written to the audit log, and alerts the admins.

## Sections

| Section | What the team can do |
|---|---|
| Dashboard | danger calls, scam reports, telecom flags, evidence, security incidents, phones, AI accuracy; 14-day chart |
| Fraud & scams | numbers reported by people, operators and the team: search, **flag** a number, **clear** a false alarm (a reason is required), export CSV; risky calls checked by the AI; Scam Voice ID list (voice fingerprints only, never audio) |
| Evidence | every saved scam call: transcript, AI report, recording (each listen is logged); case status *New → Reviewing → Sent to police → Closed*, assign to yourself, notes |
| Telecom data | operator API keys: create (shown **once**, stored only as a hash), revoke; numbers flagged by operators; every operator call (flag, lookup, rejected key) |
| AI models | live detector and Live Call Check detector with accuracy per phone condition; trained candidates and backups; **test a clip** (nothing is stored); **Make live**: the old model is kept as a backup and the switch is approved as trusted at once, so it never triggers the tamper lockdown; the admins are alerted |
| Network | how the app works on every operator (Jio, Airtel, Vi, BSNL, MTNL…): phones and how many were active today, % of server calls that worked, average response time, connection drops per phone per day, 5G/4G/3G/Wi-Fi mix, signal bars, download speed, AI checks and their speed, calls screened, spam warned/blocked, the operator's API key activity; a list of phones with their SIMs. Health per operator: good / fair / poor |
| Security | **the castle**: a drawing of the 7 layers (moat, outer wall, gatehouse, inner wall, keep, vault, royal guard). A layer under attack turns red and pulses, one that needs a look turns amber. Click a part to see its events. Also **lockdown** on/off (a reason is required), open incidents (acknowledge / resolve), blocked addresses, server-file integrity, phones at risk |
| Daily reports | one report per day (India time): security (events per layer, incidents, blocks, lockdowns), evidence (new, case updates, sent to police), AI (checks, results, AI voices found, average and 95% time, problems, model switches), fraud & telecom, phones per operator, team activity. Today's report is live. **Download** (a plain page to share) or **Print / save as PDF**. Just after midnight the server stores yesterday's report and sends the admins a summary |
| Logs | **Server log** (filter by text), **Security events** (by layer, severity, search), **AI checks** (every check: type, result, score, AI-voice %, how the voice was made, seconds, phone-quality audio, problems only), **Evidence activity** (saved by phones + everything the team did with it) |
| Team | add members by phone and role, change role, switch an account off, unlock, reset the authenticator, sign a member out everywhere |
| Audit log | everything team members did: sign-ins, views of evidence, exports, flags, key changes, AI switches, lockdowns, refused access |
| My sessions | your signed-in browsers (managers see the whole team's); end any of them |

## How it keeps data confidential

- **Two-factor sign-in:** the SMS code proves the phone, the authenticator code proves the person. Codes work only
  once. Authenticator keys are stored sealed with the server secret.
- **Lock-out:** 5 wrong codes lock the account for 30 minutes and alert the admins. Repeated failures from one address
  block that address (castle Layer 2).
- **Short sessions:** they end after 30 minutes without activity and always after 8 hours. They are stored only as
  a hash, live only in that browser tab, and end on sign-out or when the tab closes.
- **The page holds no data:** it is a strict single page with no outside scripts, fonts or images
  (`Content-Security-Policy`), it can't be framed, and it is never cached (`no-store`). Everything from the server is
  shown as text, never as HTML.
- **Least privilege:** each role sees only its sections. Users' phone numbers are hidden from roles that don't need them.
- **Everything is recorded:** the portal audit log, plus castle security events. Team changes, new operator keys,
  AI switches and lockdowns alert the admins.
- **Integrity:** the portal's page, script and style are part of the castle's file check. A changed file means lockdown.
- **Works during lockdown,** so the team can investigate.
- **Not listed** in the public API documentation (`/docs`).

## Set-up (`backend/.env`)

```
VG_PORTAL_OWNERS=+91XXXXXXXXXX     # first owner(s); default: VG_ADMIN_PHONES. Owners then add everyone else in Team.
VG_PORTAL_IDLE_MIN=30              # sign out after this many idle minutes
VG_PORTAL_MAX_HOURS=8              # longest session
VG_PORTAL_ALLOW_IPS=               # optional: only these addresses/networks, e.g. 203.0.113.10,10.20.0.0/16
VG_PORTAL_ALLOW_HTTP=0             # keep 0: off the server computer the portal needs HTTPS
VG_TELECOM_DEMO_KEY=demo-telecom-key   # the shared demo key; set it empty in production (operators use their own keys)
VG_DAILY_REPORT=1                  # build yesterday's report just after midnight and tell the admins (0 = off)
VG_REPORT_TZ_MIN=330               # report day in minutes from UTC (330 = India)
```

## Using it from other computers

The portal refuses plain `http://` from any address other than the server computer, because anyone on the network
could read a sniffed session. Put the server behind an HTTPS reverse proxy (Caddy, nginx, Cloudflare Tunnel), set
`VG_TRUST_PROXY=1`, and ideally limit it to the team's addresses with `VG_PORTAL_ALLOW_IPS`.
Example `Caddyfile`:

```
portal.example.in {
    reverse_proxy 127.0.0.1:8000
}
```

## Telecom operators

An owner creates a key in **Telecom data → New operator key** and gives it to the operator. The operator calls:

```
POST /api/v1/telecom/flag            X-API-Key: <key>    {"number": "+9190000...", "reason": "SIM box"}
GET  /api/v1/telecom/lookup/<number> X-API-Key: <key>
```

The operator's name on each flag comes from its key, not from the request. Revoking a key stops it at once.

## What the phones send for the Network page

With each app report (on start, on reconnect, every 6 hours) the phone adds: the operator names of its SIMs
(and which SIM has mobile data), the network type (5G / 4G / 3G / 2G / Wi-Fi), signal bars (0–4), roaming, the
estimated download speed, and counts of how its server calls went (worked, server error, could not reach,
average response time, family-link drops). **No phone numbers, contacts, messages or locations.**
Phones need the app version from 21 Oct 2026 or later.

## Tests

`backend\.venv\Scripts\python backend\scripts\portal_test.py`: 94 checks covering sign-in and set-up, no member-list
leak, outsiders, roles, every section, Network page per operator, logs, daily reports (parts per role, escaping, stored once, admin summary), operator keys, AI switch without lockdown, lockdown, lock-out and unlock,
address blocking, idle time-out and audit. It uses its own temporary database and weights folder.

# Caller Identity & Verification

An additive, isolated module: who is calling, where the name came from, and how far it can be trusted. It is kept
separate from the voice AI, from the existing scam scoring and from every existing API.

## 1. Architecture assessment (done before any change)

| Area | What exists | Consequence for this feature |
|---|---|---|
| Auth | OTP sign-in → bearer token (`auth.py`, `current_user`) | Reused unchanged |
| Admin | **No admin role and no admin dashboard.** Only fixed partner API keys for demo endpoints | **Conflict, documented.** Isolated admin permission added: accounts listed in `VG_CALLER_ID_ADMIN_PHONES` (signed in with the normal OTP login), checked on the server for every admin request |
| Database | SQLite + SQLModel, tables created at start-up (`create_all`), no migrations | Only **new** `cid_*` tables; no existing table altered |
| Server routes | One router list in `main.py` | **One line added** to register the new router (no existing route changed) |
| Spam data | `GET /api/numbers/{n}` (existing number lookup) | Called **read-only** to show spam risk next to the identity |
| Android telecom | `ScreeningService` (CallScreeningService), `CallManager` (InCallService), RoleManager prompts, `Contacts.nameFor` | Small hooks only; logic lives in the new `callerid` package |
| CNAP / KYC | No public Android API returns CNAP names; no authorised provider configured | Telecom source built as a **disabled adapter** that reports "not available" |

## 2. Files

New:

| File | Why |
|---|---|
| `backend/app/caller_identity.py` | Tables, input checks, rate limiter, identity resolution, telecom adapter, retention, deletion |
| `backend/app/routers/caller_identity.py` | `/api/caller-identity/*` (users) and `/api/admin/caller-identity/*` (admins), dashboard route |
| `backend/app/static/caller_identity_admin.html` | Admin dashboard (holds no data; uses the admin API after sign-in) |
| `backend/scripts/caller_identity_test.py` | 41 unit / API / security tests on a temporary database |
| `android/.../callerid/CallerIdentity.kt` | Decision rules (pure, unit-tested) + offline matching + online lookup + carrier check |
| `android/.../callerid/CallerIdentityUi.kt` | Identity card, report dialogs, the Caller Identity & Verification screen |
| `android/app/src/test/.../CallerIdResolverTest.kt` | 10 JUnit tests |
| `docs/caller-identity.md` | This document |

Existing files changed (minimal, additive):

| File | Change | Why |
|---|---|---|
| `backend/app/main.py` | register `caller_identity.router` | Only way to expose new endpoints |
| `android/.../telecom/ScreeningService.kt` | 1 line: record the carrier verification status | Only the screening service receives `Call.Details` for incoming calls |
| `android/.../ui/InCallActivity.kt` | show the identity card while ringing | Required call-screen UI |
| `android/.../ui/HomeScreens.kt` | identity card on the Number details screen | Report wrong name / spam after a call |
| `android/.../ui/SetupScreens.kt` | Settings entry "Caller Identity & Verification" | Required controls |
| `android/.../ui/MainActivity.kt` | route `callerid` | New screen |
| `android/.../data/Prefs.kt` | 3 settings | Enable / contacts / online |
| `android/app/build.gradle.kts` | `testImplementation junit` | Unit tests |
| `backend/.env` (git-ignored) | `VG_CALLER_ID_ADMIN_PHONES=` (empty) | Admins are off until you add a number |

## 3. How an identity is decided

1. **On the phone, offline:** family member, then saved contact (only if "Match my contacts" is on). Contacts are never uploaded.
2. **Online, only for numbers you have not saved** (if "Online identity lookups" is on), strongest first:
   - `verified_business` – an admin verified it and recorded how → **Verified**, high confidence
   - `telecom` – an authorised provider (not configured) → never used in this build
   - `voiceguard_user` – the number was proven by OTP to a VoiceGuard account → **Number confirmed, name not verified**
   - `community` – names users suggested; shown only when ≥2 users agree or an admin approved → **Not verified** / **Names disagree**
3. Otherwise **Unknown caller** – never blocked for that alone. Lookups time out after 3.5 s and fail to "unknown".
4. Shown separately: the **carrier number check** (STIR/SHAKEN, Android 11+: passed / failed / none / not available) and the **spam risk** (existing data, read only).

A community or VoiceGuard-member name is never labelled verified – enforced on the server and again in the app (tested).
A name is not proof of who is speaking; the card says so.

## 4. API

User (signed in; rate limited):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/caller-identity/lookup/{number}` | `{number, identity: {name, source, status, confidence, detail} \| null, telecom, spam, notice}`; 400 malformed; 120/h |
| POST | `/api/caller-identity/report-name` | `{number, name, note}` → moderation queue; 20/day |
| POST | `/api/caller-identity/report-spam` | `{number, reason}` → moderation queue (not fed to other systems); 20/day |
| GET | `/api/caller-identity/my-submissions` | own submissions only |
| POST | `/api/caller-identity/my-submissions/delete` | deletes own submissions; legal-hold items are kept without the user id |
| GET | `/api/caller-identity/sources` | what each source means |

Admin (account phone in `VG_CALLER_ID_ADMIN_PHONES`; 403 otherwise, logged):
`/me`, `/summary`, `/records?q=`, `/submissions?status=&kind=`, `POST /submissions/{id}/review` (approve / reject / flag / correct, legal hold),
`/businesses`, `POST /businesses`, `POST /businesses/{id}/verify` (note of how it was verified required), `POST /businesses/{id}/unverify`,
`/conflicts`, `/events?kind=`, `/deletions`, `POST /retention/purge`, `/export?what=submissions|businesses|audit` (CSV, no account ids), `/audit` –
all under `/api/admin/caller-identity`. Every admin action is written to `cid_audit`.

Dashboard: `http://<server>:8000/admin/caller-identity`.

## 5. Data (new tables only)

`cid_business`, `cid_submission`, `cid_event` (activity/errors, no names), `cid_audit`, `cid_deletion`. Created automatically at
start-up; no existing table or data is touched. Retention: submissions `VG_CALLER_ID_RETENTION_DAYS` (default 365, except legal hold),
events 90 days; runs at most daily and on demand from the dashboard.

## 6. Android permissions

No new permissions. Uses the existing `READ_CONTACTS`, `READ_PHONE_STATE`, the call-screening role and (optionally) the default-dialer
role. Works on Android 10+ (app minimum); the carrier number check needs Android 11+ and carrier support.

## 7. Admin set-up

1. Add your number to `backend/.env`: `VG_CALLER_ID_ADMIN_PHONES=+91XXXXXXXXXX` (comma-separate several). Use a dedicated admin number:
   signing in on the dashboard sets that account's display name, like the app does.
2. Restart the server, open `/admin/caller-identity`, sign in with the OTP.

## 8. Tests

- `backend\.venv\Scripts\python backend\scripts\caller_identity_test.py` – **41 checks**: contacts/unknown/VoiceGuard-member/verified-business
  identities, community names never verified, conflicting names, malformed numbers, telecom unavailable, provider timeout (fails safely in ~1 s),
  ordinary user vs admin (403), unauthenticated (401), audit trail, export without account ids, deletion + legal hold, retention, rate limiting.
- `cd android && gradlew :app:testReleaseUnitTest` – **10 JUnit tests** of the decision rules (offline, unknown, sources, carrier mapping, malformed replies).
- Regression: `backend\scripts\smoke_test.py` – **93/93 existing checks still pass**.
- Not automated (manual on a device): default-dialer role accept / decline prompts; the Android 5-second screening deadline is met because the
  screening response is sent before any lookup starts (existing behaviour).

## 9. Build and install

```
cd android && gradlew assembleRelease
adb install -r app\build\outputs\apk\release\app-release.apk
```
Restart the server (`start_server.bat`) so the new endpoints load.

## 10. Left unchanged

Voice AI (WavLM, AI-voice detector and room judge, Source Tracer, Voice Print, Scam Voice ID, Whisper, fingerprints), risk scoring, the AI
weights, `/api/analyze` and every other existing endpoint and response format, existing tables, auth behaviour, alerts, live location, and the
existing caller-ID overlay. Turning the feature off in the app (or never opening the dashboard) changes nothing else.

## 11. External dependencies

- An **authorised telecom identity provider** (e.g. a licensed CNAP / caller-identity service through an operator) – needed for the
  `telecom` source. Configure `VG_TELECOM_ID_URL` + `VG_TELECOM_ID_KEY` on the server only; the adapter expects `{"name", "verified"}`.
- Carrier support for STIR/SHAKEN-style number verification (rare in India today; shown as "not available" otherwise).

## 12. Not available until a legitimate telecom source is secured

- Official subscriber (CNAP) names inside VoiceGuard – the phone's own call screen shows CNAP where the operator supports it.
- "Telecom verified" identities for numbers that are not verified businesses or VoiceGuard members.

## Production notes

- Serve the API over **HTTPS** (reverse proxy with TLS); the prototype runs plain HTTP over USB / local Wi-Fi.
- SQLite is not encrypted at rest – use an encrypted volume or SQLCipher in production.
- The rate limiter is in memory (per server process); use a shared store if several servers run.

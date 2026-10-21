# Family Safety & Live Location Sharing

An add-on module: live family map, continuous location sharing with explicit consent, SOS, arrival alerts,
safe-zone entry/exit alerts, low-battery alerts, secure invitations and membership control.
Switch it off with `VG_FAMILY_SAFETY=0`; caller identification, fraud detection and every other feature keep working.

## 1. Existing architecture found (before the change)

| Part | What is there |
|---|---|
| Android app | Kotlin + Jetpack Compose, one `MainActivity` with Navigation Compose routes; minSdk 29, targetSdk/compileSdk 35; OkHttp REST + one WebSocket "live link" per phone (`data/Live.kt`); osmdroid (OpenStreetMap) maps; `GuardService` foreground service (`specialUse|location|microphone`) shares the old 10-minute family location |
| Location permissions already declared | `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`, `FOREGROUND_SERVICE_LOCATION`, `POST_NOTIFICATIONS`, `RECEIVE_BOOT_COMPLETED` (no background-location permission) |
| Backend | Python FastAPI (`backend/app/main.py`), routers registered in one loop; SQLModel on SQLite; tables created at start by `SQLModel.metadata.create_all` (no Alembic) |
| Sign-in | Phone OTP → bearer token (`auth.current_user`); the same token is used here |
| Family | `Family` table + `User.family_id`; 6-digit join code (does not expire) and phone-number invites; leave only (no remove) |
| Realtime | `hub.py`: WebSocket per phone + Firebase Cloud Messaging push for closed apps (`push.py`) |
| Firebase | FCM only (Android gets settings from the server). Firebase Realtime Database / Firestore: **not used** |
| Deployment | No AWS, EC2, Docker or CI configuration in the repository. The server runs on the laptop (`start_server.bat`); optional Cloudflare quick tunnel |
| Tests | `backend/scripts/*_test.py` (TestClient, temporary databases) and Android JUnit tests in `android/app/src/test` |

Baseline before any change (all executed): smoke 93/93, security 48/48, caller identity 41/41, portal 96/96 - no
pre-existing failures.

## 2. Design decisions

- **The family circle is the group.** A second, separate group system would duplicate families. Added on top:
  secure expiring invitations, owner "remove member", leave with ownership hand-over.
- **No Firebase Realtime Database.** A realtime system already exists (WebSocket hub + FCM), so it is reused
  (spec §6). Location writes go through the backend, which stays the authority for membership and consent.
  There are no client-side Firebase reads or writes, so there are no Firebase security rules to deploy.
- **Namespace `/api/family-safety/`.** It follows the app's naming (`/api/caller-identity/`). `/api/v1/` is used by
  telecom/bank servers and skips the app-identity check, so it was not used for phone endpoints.
- **Only the latest location is stored** (no movement history).
- **No new Android permissions or libraries.** Continuous sharing uses a new foreground service of type `location`
  started from the app while in use, so `ACCESS_BACKGROUND_LOCATION` is not needed. The existing osmdroid map is
  reused (no map API key, no cost).

## 3. New files

| File | What |
|---|---|
| `backend/app/family_safety.py` | Tables, consent/visibility rules, geofences with hysteresis, battery threshold logic, delivery with retries, retention, background worker |
| `backend/app/routers/family_safety.py` | The API (`/api/family-safety/*`) |
| `backend/scripts/family_safety_test.py` | 106 checks (see §9) |
| `backend/scripts/family_safety_migrate.py` | `status` / `up` / `down` (backup, then drop only `fs_*` tables) |
| `android/.../familysafety/FamilySafety.kt` | Phone side: status, SOS with idempotency key + retries, alert receipts, battery reading, notifications |
| `android/.../familysafety/FamilySafetyService.kt` | Foreground service (type location) with always-visible notification, Pause / Stop |
| `android/.../familysafety/FamilySafetyReceiver.kt` | Notification Pause / Stop; reminder after reboot (no automatic tracking) |
| `android/.../familysafety/FamilySafetyScreen.kt` | Consent + permissions, sharing status, SOS, live map, arrivals, safe zones, invites, members |
| `android/.../familysafety/FamilySafetyLogic.kt` | Pure rules (permission order, send interval, freshness, battery) |
| `android/app/src/test/.../familysafety/FamilySafetyLogicTest.kt` | 6 JUnit tests |
| `docs/family-safety.md` | This document |

## 4. Existing files changed (small, additive)

| File | Change | Why |
|---|---|---|
| `backend/app/main.py` | import + register the router; `family_safety.start()` in startup | route registration and the retry/retention worker |
| `backend/app/portal.py` | new portal permission `family` (owner, admin, security roles) | access to the monitoring page |
| `backend/app/routers/portal.py` | `GET /api/portal/family-safety` (counts only) | operations monitoring (spec §8) |
| `backend/app/static/portal.js` | "Family safety" page | shows the monitoring numbers |
| `backend/scripts/portal_test.py` | 2 checks | the new portal page |
| `android/.../ui/MainActivity.kt` | route `familysafety` + opening it from its notification | navigation |
| `android/.../ui/FamilyScreens.kt` | one "Family Safety" tile | entry point |
| `android/.../service/Inbox.kt` | two lines: confirm received safety alerts; handle removal / SOS-closed messages | delivery confirmation |
| `android/app/src/main/AndroidManifest.xml` | the new service (type location) and receiver; **no new permissions** | Android requires declaring them |

No existing endpoint, table, column, environment variable, permission, AI model or fraud/caller-ID logic changed.

## 5. Database (additive) and rollback

New tables, all prefixed `fs_`: `fs_consent` (consent history: version, scope, granted_at, revoked_at, reason),
`fs_sharing` (pause, battery threshold), `fs_location` (latest position only, one row per member), `fs_invite`
(hashed token, expiry, uses, revoked), `fs_zone`, `fs_zone_state`, `fs_arrival`, `fs_battery`, `fs_event` (unique
`(user_id, client_key)` for SOS idempotency), `fs_delivery` (unique `(event_id, recipient_id)`), `fs_audit`.
Foreign keys point to `user.id` / `family.id` (SQLite does not enforce them unless enabled; the server checks
membership on every request). Safety alerts are also written to the existing `alert` table as kinds `family_*`.

- Migrate: automatic at server start, or `backend\.venv\Scripts\python backend\scripts\family_safety_migrate.py up`
- Status: `... family_safety_migrate.py status`
- Switch off without deleting anything: `VG_FAMILY_SAFETY=0` in `backend/.env`, restart the server.
- Roll back (removes Family Safety data only): stop the server, then
  `... family_safety_migrate.py down`. A backup copy goes to `backend/data/backups/` first. Add `--purge-alerts` to
  also delete `family_*` rows from the alert inbox. Then set `VG_FAMILY_SAFETY=0`, or the tables are recreated
  empty at the next start.

Retention: latest location deleted on pause-forever/stop/leave/removal, or after `VG_FAMILY_LOCATION_RETENTION_H`
(168 h) without updates. Safety events are kept `VG_FAMILY_EVENT_RETENTION_D` (90 days); their coordinates are
removed after 30 days. Expired invitations are deleted after 30 days.

## 6. API (`/api/family-safety`, bearer token from phone sign-in)

The acting member always comes from the token. `403` = not allowed (counted by the castle security), `409` = sharing
not switched on / not in a circle (the phone stops sending), `404` = not found or the feature is off, `429` = too many.

| Method & path | What |
|---|---|
| `GET /status` | feature on/off, my sharing state, scope, family |
| `POST /group` `{name}` | create a family circle |
| `GET /group` | members and their sharing state (no locations) |
| `POST /group/invites` `{expires_hours 1-168, max_uses 1-10}` | invitation code (shown once, only its hash stored) |
| `GET /group/invites` · `DELETE /group/invites/{id}` | list / cancel |
| `POST /invites/preview` `{token}` · `POST /invites/accept` `{token}` | see the circle, then explicitly join |
| `POST /group/members/{user_id}/remove` | owner removes a member (their consent ends, location deleted) |
| `POST /group/leave` | leave (ownership passes to the longest member) |
| `GET /consent` | the explanation (what, why, who, when, how long) + my consent history |
| `POST /consent` `{version, accept, share_location, share_battery, zone_alerts, battery_threshold}` | only the member themself |
| `POST /sharing/pause` `{minutes or null}` · `POST /sharing/resume` · `POST /sharing/settings` `{battery_threshold}` | |
| `POST /consent/revoke` | stop: consent ends, latest location deleted, family loses access at once |
| `POST /location` `{lat, lon, accuracy, recorded_at (epoch ms), battery?, charging?}` | validated: range, accuracy ≤ 2000 m, ≤ 2 min in the future, ≤ 15 min old, newer than the last fix, ≤ 1 per 5 s |
| `GET /map` | members I may see: status live (≤ 6 min) / stale (≤ 60 min) / offline / paused / off, age, accuracy, battery if shared |
| `POST /map/watch` · `POST /map/unwatch` | realtime updates over the live link for 10 minutes (renewed by the open map) |
| `POST /sos` `{client_key, message, lat?, lon?, accuracy?, recorded_at?}` | idempotent; a second press within 2 min returns the same SOS |
| `GET /events?active=` · `GET /events/{id}` | events I sent (with per-person delivery status) or received |
| `POST /events/{id}/ack` · `/cancel` (sender) · `/resolve` (sender or recipient) | |
| `POST /arrivals` `{name, lat, lon, radius_m 50-2000, recipients[], expires_hours}` · `GET` · `DELETE /arrivals/{id}` | |
| `POST /zones` `{name, lat, lon, radius_m 50-5000, notify_entry, notify_exit, subjects[], recipients[]}` · `GET /zones` | watched members see every zone that watches them |
| `PATCH /zones/{id}` or `POST /zones/{id}/edit` · `DELETE /zones/{id}` (owner) · `POST /zones/{id}/leave` (watched member) | |

Realtime messages on the live link: `family_safety_member`, `family_safety_event`, `family_safety_removed`.
Alerts use the existing `alert` message (kinds `family_sos`, `family_arrival`, `family_zone_enter`,
`family_zone_exit`, `family_battery_low`), so older app versions still show them.

**Rules that are enforced:**
- **Geofences:** fixes vaguer than 250 m are ignored. Hysteresis: a member enters a zone only when clearly inside,
  and leaves only when clearly outside the radius plus a margin of max(30 m, 20 %). The first fix never alerts.
  The same alert type is not repeated within 10 minutes. An arrival alerts once.
- **Battery:** an alert fires only when the level crosses the threshold from above while not charging. It re-arms
  5 % above the threshold, with a 6-hour cool-down. An unknown level changes nothing.
- **SOS delivery:** retried at 20, 40, 80, 160 and 320 s until the phone confirms. It then shows `unconfirmed` or
  `failed`. Retries stop when the SOS is resolved or cancelled. The text always says VoiceGuard has **not**
  contacted police or 112.

## 7. Android permissions and consent

1. The member opens **Family → Family Safety → Turn on location sharing**. A full-screen explanation appears first
   (prominent disclosure): what is shared, why, who sees it, when (also in the background), and how long it is
   kept. Battery sharing and zone alerts are optional ticks.
2. Only after "I agree" does Android ask for location permission. The member may choose approximate location,
   which works with lower accuracy.
3. Then Android asks for notification permission (Android 13+). If it is refused, sharing stays off, because the
   member must always be able to see that it runs.
4. The server records the consent. The service then starts with an always-visible notification ("Sharing your
   location with …", Pause 1 h, Stop sharing).

The service sends at most one fix a minute while moving (25 m), and at least one every 5 minutes. It stops when:
- the location permission is removed;
- the server answers 409 (sharing off), 403 or 404;
- the member taps Stop, or is removed from the circle.

It is not restarted automatically after a reboot or when Android ends the app. A notification asks the member to
reopen VoiceGuard instead. Continuous tracking is therefore **not guaranteed**: Doze, battery savers and phone
makers' app killers can delay or stop it, and the map then shows the location as stale or offline rather than
current.

Google Play: the app does not request `ACCESS_BACKGROUND_LOCATION`. It still needs the Play Console
foreground-service declaration (location) and a prominent-disclosure review before release.

## 8. Deployment and configuration

| Variable | Default | |
|---|---|---|
| `VG_FAMILY_SAFETY` | `1` | `0` switches the whole module off (API answers 404, app hides it, worker not started) |
| `VG_FAMILY_LOCATION_RETENTION_H` | `168` | delete a latest location after this many hours without updates |
| `VG_FAMILY_EVENT_RETENTION_D` | `90` | delete safety events after this many days |

No new secrets, services, queues or workers outside the existing server process. A background task in the
server retries alerts and applies retention.

**AWS:** the repository has no AWS setup today. To run it on EC2 later:
- **Instance and OS:** one instance is enough (the AI models need about 4 GB RAM). Use Ubuntu with Python 3.12+.
- **Run it:** `uvicorn app.main:app` behind nginx or Caddy with HTTPS. Set `VG_TRUST_PROXY=1`.
- **Secrets:** keep `backend/.env` and `data/` (including `secret.key`) outside the image, for example in AWS
  Secrets Manager or SSM Parameter Store, with an IAM role allowing only `ssm:GetParameter` on those parameters.
- **Database:** SQLite on the instance's EBS volume, with snapshots for backup.
- **Monitoring:** CloudWatch is optional; the Team Portal already shows Family Safety health.
- **Cost:** Family Safety itself adds no AWS cost beyond the instance. Push uses FCM, which is free.
- **Staging:** run a second instance or port with its own `.env` and `VG_DB`.
- **Firebase:** keep separate Firebase projects for development and production, if push is used in both.

## 9. Tests executed (real results)

| Suite | Result |
|---|---|
| `backend/scripts/family_safety_test.py` (new) | **106 / 106 passed** |
| smoke / security / caller identity (existing) | 93/93, 48/48, 41/41 passed, before and after |
| portal (existing + 2 new) | 96/96 before, 98/98 after |
| Android JUnit (`testDebugUnitTest`) | caller ID 10/10 (existing), Family Safety 6/6 (new); `compileDebugKotlin` succeeded |

`family_safety_test.py` covers:
- feature flag off and on;
- create, invite (hash only, preview, accept, used, wrong, expired, cancelled), join, remove (owner only), leave,
  including leaving through the old family screen;
- outsider access to the map, events, zones and recipients (IDOR);
- consent version, explicit yes, scope and history; pause, resume and revoke; nobody can switch on someone else's
  sharing;
- invalid, future, stale, duplicate, out-of-order and rate-limited fixes; stale and offline display; latest
  location only;
- realtime delivery only to members with the map open; the 409-not-punished-by-castle check;
- SOS: unique id, recipients, location, "not police" text, idempotent retry, double press, delivery status,
  confirmation, retry with back-off, cancel and resolve rules, retries stopping;
- unreachable phones retried, then marked failed;
- arrival: far away, GPS drift, arrival once, chosen recipients only;
- zones: first fix, entry, hysteresis band, exit, cool-down, owner-only edits, disabled zone, watched member
  leaving, delete;
- battery: crossing, no repeat, unknown level, re-arm with cool-down, charging, alert after the cool-down;
- audit entries without coordinates; retention; portal health with no names or coordinates; migration down
  (only `fs_` tables) and up.

**Not covered by automated tests:**
- Android background behaviour on a real phone: Doze, OEM battery killers, reboot, the permission dialogs. This
  needs manual testing on the Galaxy phones.
- Firebase security rules: not applicable, since no client-side Firebase database is used.
- Delivery through real FCM: push is currently off on this server because the service-account file is missing.

## 10. Known limitations and open risks

- **The old family join code:** the existing 6-digit code does not expire and remains valid. The new invitations
  are secure, but the old code is a weaker way in. Consider turning it off; that would change existing behaviour,
  so it was not done.
- **Rate limits and realtime watchers are kept in memory.** They reset when the server restarts, and a multi-server
  setup would need shared storage such as Redis.
- **Operational counters** on the portal page count from the last server start.
- **SQLite foreign keys are declared but not enforced** by SQLite. The server checks membership on every request.
- **The old 10-minute family location** (`User.lat/lon`, Settings → Share my location) is a separate, existing
  feature with its own switch. Family Safety does not change it.
- **Most of the new screen's text is in English and Hindi only.** The consent text comes from the server, in
  English.
- **Before a public release:**
  - real HTTPS hosting, not the laptop or a quick tunnel;
  - FCM switched on, so SOS reaches phones whose app is closed;
  - Play Store policy review;
  - a legal review of the consent text.

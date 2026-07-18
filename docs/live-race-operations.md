<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Live race operations

Apex Arena can consume live OpenF1 MQTT data. It does not simulate missing telemetry or present a
scheduled session as connected before the provider publishes it.

## Race-day path

```text
OpenF1 MQTT -> authenticated backend -> raw + normalized events -> race state
            -> grounded agent debate -> Redis/SSE -> browser Race Room
```

The backend connects automatically when `OPENF1_LIVE_AUTO_CONNECT=true` and credentials are
present. Every 60 seconds the catalog reconciler checks for newly published OpenF1 sessions. Once
the provider session is matched confidently, the scheduled row becomes a live room and incoming
MQTT records use its `session_key`.

The default live topics cover sessions, drivers, positions, intervals, laps, pits, stints,
race-control, and weather. High-frequency car/location telemetry is intentionally excluded to
keep provider load and storage bounded.

## Before lights out

1. Confirm `GET /api/v1/live/status` reports credentials present and `CONNECTED`.
2. Confirm `GET /api/v1/engine/status` reports the expected live session key after OpenF1 publishes
   it.
3. Open `GET /api/v1/race-rooms/events` and confirm the Race session changes from
   `future_read_only`/`provider_pending` to `eligible_live` or `already_exists`.
4. Keep PostgreSQL and Redis volumes mounted; use `docker compose down`, never `down -v`.
5. Watch `last_event_at`. A connected socket with no recent event means there is no current feed,
   not that Apex Arena should manufacture updates.

## Missing qualifying data

The previous Qualifying session was not captured because live auto-connect was off. Apex Arena can
backfill it through the historical REST pipeline if OpenF1 publishes a matching session. If the
provider does not publish that session or the subscription cannot access it, the UI remains
`Provider data not published yet`; the system will not fabricate laps, classifications, or debate.

## Live limitations

- Live availability and latency depend on the OpenF1 subscription and provider publication.
- MQTT reconnects with bounded backoff, but records omitted upstream cannot be recovered from the
  live connection.
- Historical REST can fill a gap only after the provider exposes the relevant endpoint data.
- Catalog matching is confidence-scored. Ambiguous sessions stay pending instead of attaching
  another event's telemetry.
- Browser updates use SSE; the MQTT connection and credentials remain backend-only.

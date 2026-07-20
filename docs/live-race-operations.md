<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Live race operations

Apex Arena can consume live OpenF1 MQTT data. It does not simulate missing telemetry or present a
scheduled session as connected before the provider publishes it.

## Race-day path

```text
OpenF1 MQTT -> authenticated backend -> raw + normalized events -> race state
            -> grounded agent debate -> Redis/SSE -> browser Race Room
```

The backend connects to MQTT only when `OPENF1_INGESTION_MODE` is not `rest`,
`OPENF1_LIVE_AUTO_CONNECT=true`, and credentials are present. Recent-session reconciliation is a
separate REST worker: when enabled in an `ingestor` or `combined` process, it checks completed
competitive sessions after a provider grace period and upgrades stale `provider_pending` rooms
after OpenF1 publishes real metadata and endpoint data.

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

The previous Qualifying session may not be available immediately after the flag. Apex Arena keeps
that room in `provider_pending`, retries within the configured recent-session horizon, and
backfills it through historical REST only after OpenF1 publishes a confident session match and
usable endpoint data. If the provider does not publish enough data, the UI remains
`Provider data not published yet`; the system will not fabricate laps, classifications, or debate.

For the resumable, production-safe recovery procedure, endpoint checkpoints, advisory locking,
and room finalization rules, see [`openf1-rest-backfill.md`](./openf1-rest-backfill.md).

## Live limitations

- Live availability and latency depend on the OpenF1 subscription and provider publication.
- MQTT reconnects with bounded backoff, but records omitted upstream cannot be recovered from the
  live connection.
- Historical REST can fill a gap only after the provider exposes the relevant endpoint data.
- Catalog matching is confidence-scored. Ambiguous sessions stay pending instead of attaching
  another event's telemetry.
- Browser updates use SSE; the MQTT connection and credentials remain backend-only.

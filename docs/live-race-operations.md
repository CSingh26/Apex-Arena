<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Live race operations

This runbook describes the implementation committed through
`35666c0b0653701fcf9ea0f91e46f989f73dfadf` on `sprints`. Those commits have
been pushed to `origin/sprints`; they have **not** been promoted to `main` or
deployed to production. Complete the release gates in
[`2026-09-10-foundation-and-release-safety.md`](superpowers/plans/2026-09-10-foundation-and-release-safety.md)
before using this as a production rollout procedure.

Apex Arena does not simulate missing telemetry. A calendar-live session can
open a waiting room before OpenF1 publishes a confident provider identity, but
provider availability, ingestion state, and replay readiness remain separate.

## Runtime path

```text
Jolpica calendar (UTC) -> canonical OpenF1 match -> one leased ingestor
  -> OpenF1 REST and/or MQTT -> durable raw + normalized PostgreSQL events
  -> race state + grounded discussion -> Redis Streams -> resumable SSE
  -> browser timing, discussion, and time-indexed GPS
```

Use one of these deployed process roles:

- `APP_PROCESS_ROLE=api`: HTTP/SSE only. It never starts live ingestion or
  recent-session reconciliation.
- `APP_PROCESS_ROLE=ingestor`: dedicated worker entry point selected by
  `python -m app.runtime`; it exposes only `/health/live` and the diagnostic
  `/health/provider`. It does not expose `/health/ready` or the API routes.
- `APP_PROCESS_ROLE=combined`: API and worker in one process.

`all` is retained for non-production compatibility and is rejected when
`APP_ENV=production`; when used, it serves the API and runs the worker like
`combined`. An ingesting `ingestor`, `combined`, or legacy `all` process must
have `DATABASE_MIGRATION_URL` set to the direct, non-pooler PostgreSQL endpoint
in staging and production. The process holds a session-scoped advisory lease;
failure to acquire it is fatal. Do not run multiple independent live workers.

## Live transport modes

`LIVE_MODE_ENABLED` and the OpenF1 mode settings have exact, separate effects:

| Configuration | Committed behavior |
| --- | --- |
| `LIVE_MODE_ENABLED=false` | Disables the live worker. It does not by itself disable recent completed-session reconciliation. |
| `OPENF1_INGESTION_MODE=rest` | Starts the live worker when live mode is enabled, regardless of `OPENF1_LIVE_AUTO_CONNECT`; does not connect MQTT; polls REST. |
| `OPENF1_INGESTION_MODE=mqtt` | Requires `OPENF1_LIVE_AUTO_CONNECT=true` to start; connects MQTT and does not poll REST endpoints when MQTT is unavailable. |
| `OPENF1_INGESTION_MODE=auto` | Requires `OPENF1_LIVE_AUTO_CONNECT=true` to start; connects MQTT and polls REST only while MQTT is not connected and fresh for the same session key. |

The local `docker-compose.yml` deliberately uses `combined`, `rest`, and
`OPENF1_LIVE_AUTO_CONNECT=false`; it enables both recent-session settings. The
checked-in `.env.example` instead demonstrates `auto` with MQTT auto-connect
enabled and leaves recent recovery disabled. Environment variables override
the `Settings` defaults, so inspect the effective deployment configuration
rather than assuming either example is active.

The REST worker runs every `OPENF1_LIVE_POLL_SECONDS` (default 5 seconds),
refreshes the live-window catalog every 60 seconds, and retries catalog or
endpoint failures after 5, 15, 30, then 60 seconds. Endpoint schedules range
from 15 to 120 seconds. `position`, `car_data`, and `location` polling uses
bounded overlapping windows; the initial position poll retains each driver's
latest available row. GPS is downsampled according to
`LOCATION_SAMPLE_INTERVAL_MS` before durable insertion.

## Credentials and diagnostics

Keep `OPENF1_USERNAME`, `OPENF1_PASSWORD`, `INTERNAL_API_KEY`, database URLs,
and Redis URLs in the deployment secret store. Never place credential values
in commands, URLs, tickets, logs, or committed files.

- REST requests start without authentication. After a 401, the client obtains
  and caches an OAuth token and retries once when OpenF1 credentials are
  configured. If OpenF1 requires authentication and credentials are missing or
  invalid, the request fails; it is not treated as an empty provider response.
- MQTT always requires the OpenF1 username/password pair. Token state and
  credential presence are reported as booleans/counters, never as values.
- `GET /api/v1/internal/openf1/backfill-status` requires
  `X-Internal-API-Key`. The public health endpoints do not accept or reveal
  that key.

Production API roles now fail closed if proxy enforcement lacks
`APEX_ARENA_PROXY_TOKEN`, or if public replay controls lack
`ADMIN_DASHBOARD_PASSWORD`. The proxy token authenticates only the deployment
hop. Replay start, restart, resume, pause, speed, and seek additionally require
the operator-entered `X-Apex-Replay-Password`; public room reads and streams do
not. The header carries canonical Base64 of the exact UTF-8 password so Unicode
and surrounding spaces survive the HTTP header boundary; Base64 is transport
encoding, not authorization. The browser keeps the original credential in
component memory for the current room and clears it after a 401 or room change.

## Health interpretation

Use the endpoints according to the process entry point and what they actually
prove:

| Process | Probe behavior |
| --- | --- |
| API, combined, or legacy `all` served by the main API app | `/health/live` is process-only HTTP 200. `/health/ready` checks PostgreSQL and Redis and returns 200 or 503. `/health/provider` evaluates provider state and returns 200 or 503 when live transport is configured. |
| Dedicated ingestor | `/health/live` is process-only HTTP 200. `/health/provider` always returns HTTP 200 when its handler completes; its JSON `status`, `current_session_key`, `last_event_at`, reconnect count, and reconciliation fields are diagnostic and must be inspected. `/health/ready`, `/health`, `/api/v1/live/status`, and `/api/v1/engine/status` are not registered. |

On an API-only process, provider state is read from the shared Redis status
stream and a report older than 120 seconds becomes `STALE`; `combined` and
legacy `all` read their process-local worker state. `/api/v1/live/status` on
the main API app returns that role-aware state with more detail.
`/api/v1/engine/status` adds durable counts and database/Redis health, but its
top-level readiness still reflects database and Redis, not provider freshness.

The main API app's `/health` route is a legacy aggregate whose OpenF1-live
component is based on configuration and credential presence. Do not use it as
proof that the REST/MQTT worker is running.

Useful live states are `WAITING_FOR_SESSION_KEY`, `WAITING_FOR_PROVIDER`,
`LIVE`, `STALE`, `PROVIDER_UNAVAILABLE`, and `SESSION_COMPLETE`. A browser SSE
connection or an HTTP 200 from liveness/readiness does not prove fresh provider
telemetry. SSE client counts are local to the API process, not cluster-wide.

## Race-day checks

Before a session:

1. Confirm the intended role and transport settings without printing secret
   values.
2. Confirm the database is at the single Alembic head
   `20260911_0015`; see the migration checks below.
3. Confirm exactly one ingestor owns the advisory lease.
4. Against the API, combined, or legacy `all` service, require HTTP 200 from
   `/health/ready`, use `/health/provider` as the 200/503 provider gate, and
   inspect `/api/v1/live/status` for the expected room/session identity.
5. Against a dedicated ingestor, require HTTP 200 from `/health/live`, then
   parse `/health/provider` and require an operational JSON `status` and the
   expected session/freshness fields. Do not probe its absent `/health/ready`
   route or treat the diagnostic endpoint's HTTP 200 alone as readiness. Check
   PostgreSQL plus Redis readiness through the main API service's
   `/health/ready`. The `database_status` CLI is only a PostgreSQL/schema check
   and cannot substitute for the Redis part of readiness.
6. Open `/api/v1/race-rooms/events` on the API service. A calendar-live item may
   link to a waiting room with no session key; an upcoming item remains
   schedule-only.

During a session, watch the provider state, endpoint error classes and retry
times, `last_event_at`, durable raw/normalized counts, Redis diagnostics, and
room identity together. HTTP failures and successful empty responses are
different conditions. Missing or ambiguous provider data must remain pending.

At provider completion, the live finalizer preserves captured data, marks the
capture partial when appropriate, and transitions usable rooms to completed
archives. Recent reconciliation can then recover earlier or late-published
rows. It includes Practice 1/2/3, Sprint Qualifying, Sprint, Qualifying, and
Race; candidates are ordered by durable `reconciliation_attempted_at` so a
newer unavailable session cannot starve older work.

## Replay, stream, and GPS recovery

Session-event and room-message SSE reconnects page through durable PostgreSQL
backlogs and repair bounded Redis gaps without advancing resume IDs for
non-event frames. API processes load shared race state rather than relying only
on a process-local snapshot.

Replay start/resume rebuilds race state through the persisted sequence before
continuing. Seek operations rebuild replay state and emit replay-marked copies;
they do not rewrite captured source events. Startup reconciliation pauses
orphaned replay rows and marks historical ingestion runs without a fresh
heartbeat as retryable failures. Follow `docs/ingestion-run-recovery.md` for
the first rollout's legacy-worker drain.

Live and replay GPS share the same rendering path but use different cache
semantics. Live windows remain mutable and accept streamed fixes; replay and
archive windows become immutable after a successful fetch. The browser scopes
requests and caches to the session, rejects late responses from replaced
sessions, retains bounded look-behind/look-ahead data, and refreshes missing
track geometry when live fixes arrive. The map clock follows replay event time
for replay and current state/event time for live; a paused replay clock does not
freeze a live room.

## Recent recovery

Automatic recovery requires both settings:

```dotenv
RECENT_SESSION_RECONCILIATION_ENABLED=true
RECENT_SESSION_AUTO_BACKFILL_ENABLED=true
```

The defaults are disabled, a 14-day lookback, a 15-minute provider grace
period, a 900-second pass interval, and one selected room per pass. It runs
in `ingestor`, `combined`, or legacy non-production `all`, processes selected
rooms sequentially, excludes high-frequency `car_data` and `location`, resumes
non-empty endpoint checkpoints, retries empty endpoints, and leaves incomplete
provider data pending. `RECENT_SESSION_AUTO_BACKFILL_MAX_CONCURRENT` is
declared and validated but is not consumed by the current sequential
reconciler. Migration `20260911_0015` supplies the durable fairness marker.

The candidate query is age/status based rather than a strict `status != live`
filter. A sufficiently overdue `RoomStatus.LIVE` row can therefore be selected
and have `reconciliation_attempted_at` advanced. The reconciler may resolve or
bind provider identity, but `OpenF1HistoricalBackfillService` rejects an active
provider session whose `date_end` is missing or in the future, so historical
endpoint ingestion does not run. That failure is reported as retryable, and
the row remains eligible for a later pass while it continues to match the
candidate filters; the attempt marker changes fair ordering, not eligibility.
The manual completed-room batch is different: its repository query explicitly
excludes `RoomStatus.LIVE` rows before invoking backfill.

For explicit recovery, use
[`openf1-rest-backfill.md`](openf1-rest-backfill.md).

## Migration and rollout boundary

The repository currently has one Alembic head, `20260911_0015`, defined by
`backend/migrations/versions/20260911_0015_recent_reconciliation_attempts.py`.
Inspect before applying:

```bash
cd backend
./.venv/bin/alembic heads
python -m app.cli.database_status --json-summary
cd ..
scripts/run-production-migrations.sh --check
```

`database_status` checks the selected PostgreSQL connection, its schema
revision, and database row counts; it does not connect to or test Redis. The
API-app `/health/ready` endpoint is the operational readiness check that covers
both PostgreSQL and Redis. The status and `--check` commands apply no
migration. Before a production upgrade, create a recoverable database
backup/branch and run the reviewed one-shot migration process against
`DATABASE_MIGRATION_URL`. Do not downgrade or delete provider facts as a
rollback shortcut.

No production rollout is recorded by this document. Tasks 20–27 still contain
security, restart reconciliation, CI seed, release-graph, formatting, and full
phase verification work. After those gates pass, deploy one compatible
application/schema release, validate API and worker health, then run a
single-room backfill canary before broader recovery.

To stop live/recovery activity without deleting data, set
`LIVE_MODE_ENABLED=false`, `RECENT_SESSION_RECONCILIATION_ENABLED=false`, and
`RECENT_SESSION_AUTO_BACKFILL_ENABLED=false`, restart the worker, and inspect
durable jobs. Preserve PostgreSQL and Redis volumes and resume later.

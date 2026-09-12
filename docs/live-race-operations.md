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
  `python -m app.runtime`; it exposes only worker health routes.
- `APP_PROCESS_ROLE=combined`: API and worker in one process.

`all` is retained for non-production compatibility and is rejected when
`APP_ENV=production`. An ingesting `ingestor` or `combined` process must have
`DATABASE_MIGRATION_URL` set to the direct, non-pooler PostgreSQL endpoint in
staging and production. The process holds a session-scoped advisory lease;
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

At this foundation point, Tasks 20 and 21 are still pending: production proxy
configuration does not yet fail closed when `APEX_ARENA_PROXY_TOKEN` is absent,
and replay mutation routes are not yet protected by the trusted-proxy
dependency. Do not treat the current `sprints` artifact as approved for public
production exposure.

## Health interpretation

Use the endpoints according to what they actually prove:

1. `/health/live` proves only that the current process can answer HTTP.
2. `/health/ready` checks PostgreSQL and Redis readiness; it does not prove that
   OpenF1 is fresh.
3. `/health/provider` is the transport-oriented readiness check. An API role
   reads the shared status from Redis and marks a report older than 120 seconds
   `STALE`; worker roles report local state. `PROVIDER_UNAVAILABLE`, `STALE`,
   and other non-ready states return HTTP 503 when the live worker is enabled.
4. `/api/v1/live/status` returns the same role-aware provider state with more
   detail. `/api/v1/engine/status` adds durable counts and database/Redis
   health, but its top-level readiness still reflects database and Redis, not
   provider freshness.
5. `/health` is a legacy aggregate whose OpenF1-live component is based on
   configuration and credential presence. Do not use it as proof that the
   REST/MQTT worker is running.

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
4. Confirm `/health/ready` and `/health/provider`, then inspect
   `/api/v1/live/status` for the expected room/session identity.
5. Open `/api/v1/race-rooms/events`. A calendar-live item may link to a waiting
   room with no session key; an upcoming item remains schedule-only.

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
they do not rewrite captured source events. Startup reconciliation for an
orphaned `running` replay row is still pending Task 22, and stale ingestion-run
repair is still pending Task 23.

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
only in `ingestor` or `combined`, processes selected rooms sequentially, excludes
high-frequency `car_data` and `location`, resumes non-empty endpoint
checkpoints, retries empty endpoints, and leaves incomplete provider data
pending. `RECENT_SESSION_AUTO_BACKFILL_MAX_CONCURRENT` is declared and
validated but is not consumed by the current sequential reconciler. Migration
`20260911_0015` supplies the durable fairness marker.

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

The status and `--check` commands apply no migration. Before a production
upgrade, create a recoverable database backup/branch and run the reviewed
one-shot migration process against `DATABASE_MIGRATION_URL`. Do not downgrade
or delete provider facts as a rollback shortcut.

No production rollout is recorded by this document. Tasks 20–27 still contain
security, restart reconciliation, CI seed, release-graph, formatting, and full
phase verification work. After those gates pass, deploy one compatible
application/schema release, validate API and worker health, then run a
single-room backfill canary before broader recovery.

To stop live/recovery activity without deleting data, set
`LIVE_MODE_ENABLED=false`, `RECENT_SESSION_RECONCILIATION_ENABLED=false`, and
`RECENT_SESSION_AUTO_BACKFILL_ENABLED=false`, restart the worker, and inspect
durable jobs. Preserve PostgreSQL and Redis volumes and resume later.

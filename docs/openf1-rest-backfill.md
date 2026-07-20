# OpenF1 historical REST backfill

Apex Arena uses OpenF1 REST to recover completed sessions when live MQTT is unavailable or when
OpenF1 publishes data after a delay. Full-season historical recovery is explicit and resumable;
application startup never launches a full-season backfill. Recent-session reconciliation may, when
enabled, run a single narrow backfill for a recently completed competitive session.

## Data flow

Every provider row follows the same path as live ingestion:

```text
OpenF1 REST payload
  -> RawEventInput
  -> RawProviderEventService (durable deterministic deduplication)
  -> RaceEventProcessor
  -> OpenF1EventNormalizer
  -> ordered normalized_race_events
  -> race-state snapshots + Redis event publication + grounded room discussion
  -> stored-data room finalization
  -> public Race Rooms API
```

No REST response is inserted directly into replay or discussion tables. A room becomes replayable
only when persisted session metadata, drivers, timing data, and a non-empty normalized sequence
exist.

## Production settings

`main` is the canonical deployment source branch. The intended single-service backend role is:

```dotenv
APP_ENV=production
APP_PROCESS_ROLE=combined
OPENF1_LIVE_AUTO_CONNECT=false
OPENF1_INGESTION_MODE=rest
OPENF1_REST_BACKFILL_ENABLED=false
OPENF1_REST_BACKFILL_SEASON=2026
OPENF1_REST_BACKFILL_MAX_SESSIONS=1
OPENF1_REST_MAX_CONCURRENT_REQUESTS=2
OPENF1_REST_CURSOR_OVERLAP_SECONDS=2
OPENF1_REST_INCLUDE_HIGH_FREQUENCY=false
RECENT_SESSION_RECONCILIATION_ENABLED=true
RECENT_SESSION_AUTO_BACKFILL_ENABLED=true
RECENT_SESSION_RECONCILIATION_LOOKBACK_DAYS=14
RECENT_SESSION_PROVIDER_GRACE_MINUTES=15
RECENT_SESSION_RECONCILIATION_INTERVAL_SECONDS=900
RECENT_SESSION_AUTO_BACKFILL_MAX_SESSIONS=1
RECENT_SESSION_AUTO_BACKFILL_MAX_CONCURRENT=1
```

`OPENF1_REST_BACKFILL_ENABLED=false` is intentional: the full historical CLI is explicit and does
not run during startup. Recent-session reconciliation is disabled by default and limited by the
separate `RECENT_SESSION_*` settings. The API role cannot enable or execute worker duties. Keep
both Neon URLs configured; worker roles use the direct URL for session advisory locks.

Recent automatic recovery:

- starts only in `ingestor` or `combined`;
- never touches future or practice sessions;
- examines completed Qualifying, Sprint Qualifying, Sprint, and Race rooms only;
- defaults to a 14-day lookback and 15-minute provider grace period;
- queues at most one session per pass by default;
- excludes high-frequency `car_data` and `location`;
- preserves endpoint checkpoints and normalized-event deduplication;
- leaves the room `provider_pending` when provider metadata or core data is still missing.

MQTT is disabled operationally because the provider broker currently refuses both native TLS and
WebSocket connections. OAuth and historical REST remain independent and functional. Re-enable
MQTT only after a broker connectivity probe succeeds, then set `OPENF1_INGESTION_MODE=auto` and
`OPENF1_LIVE_AUTO_CONNECT=true` on the single ingestor replica.

## One-session rollout: Spa Qualifying or Race

Run from a Railway ingestor shell after migrations reach head.

Dry run (no database writes):

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-belgian-grand-prix-qualifying \
  --dry-run \
  --json-summary
```

Core endpoint backfill:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-belgian-grand-prix-qualifying \
  --endpoints drivers,laps,position,race_control,weather,session_result,starting_grid \
  --json-summary
```

For a race room, use the race endpoint allowlist:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-belgian-grand-prix-race \
  --endpoints drivers,laps,position,intervals,pit,stints,race_control,weather,session_result,starting_grid \
  --json-summary
```

Resume a failed job without re-fetching completed endpoints:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-belgian-grand-prix-qualifying \
  --resume \
  --force-retry-failed \
  --json-summary
```

The command accepts exactly one `--room-slug` or `--session-key`; `--max-sessions` must remain `1`.
It rejects future, unresolved, and ambiguous sessions. A second worker for the same season/session
exits cleanly when it cannot acquire the advisory lock.

## Verification

Inspect durable progress:

```sql
SELECT season, meeting_key, session_key, room_slug, status,
       requested_endpoints, completed_endpoints, failed_endpoint,
       rows_fetched, rows_processed, rows_inserted, rows_deduplicated,
       last_error_code, updated_at, completed_at
FROM openf1_backfill_jobs
WHERE room_slug = '2026-belgian-grand-prix-qualifying';
```

Verify the public room state:

```sql
SELECT slug, meeting_key, session_key, status, mode, ingestion_status,
       source_availability, replay_available, results_available,
       eligibility_status, is_development, last_event_at
FROM race_rooms
WHERE slug = '2026-belgian-grand-prix-qualifying';
```

Verify real normalized events:

```sql
SELECT count(*) AS normalized_event_count
FROM normalized_race_events
WHERE session_key = (
  SELECT session_key FROM race_rooms
  WHERE slug = '2026-belgian-grand-prix-qualifying'
);
```

The internal `GET /api/v1/internal/openf1/backfill-status` endpoint requires
`X-Internal-API-Key` and returns only safe state and counters.

Then call the production weekends API and confirm the recovered session has a non-null room slug,
`already_exists` or `eligible_historical`, `limited_telemetry` or `telemetry`, and
`replay_available=true`. Future sessions must remain `future_read_only` with a null room slug.

Run the same CLI command again. Completed endpoints are skipped and cumulative deduplication/job
counters remain stable; normalized events, messages, and evidence must not increase from duplicates.

## Availability and rollback

- `telemetry`: meaningful high-frequency `car_data`/`location` plus replay timing context.
- `limited_telemetry`: metadata, drivers, timing, and a non-empty normalized replay sequence.
- `timing_only`: timing exists but the replay threshold is incomplete.
- `results_only`: real classification/grid data without replay timing.
- `unavailable`: insufficient stored provider data; the room remains non-openable.

High-frequency endpoints are always opt-in:

```bash
python -m app.cli.backfill_openf1 ... \
  --include-high-frequency \
  --endpoints car_data,location
```

Use them for one session first and monitor Neon storage and request volume. Do not start a
full-season batch automatically.

Rollback is operational: stop the CLI, set `OPENF1_LIVE_AUTO_CONNECT=false`, leave
`OPENF1_REST_BACKFILL_ENABLED=false`, and inspect the durable job. Resume later. Do not downgrade
the migration or delete provider events; finalization never replaces a better availability state
with a worse one.

## Production race-room chat build

Historical chat generation is now an explicit database job. The frontend reads persisted
`room_messages` only; normal page requests do not generate conversations. This keeps production
traffic predictable and makes every replay resumable.

Railway deployment is repository-driven now. The API service uses
`/backend/deploy/railway/api.toml`; the finite historical job uses
`/backend/deploy/railway/chat-build.toml`. See [Railway deployment](railway-deployment.md) before
running the production job.

Recommended operator sequence:

```bash
python -m app.cli.database_status --json-summary
python -m app.cli.build_race_rooms --season 2026 --completed-only --json-summary --force-refresh
python -m app.cli.generate_room_chats \
  --season 2026 \
  --room-slug 2026-australian-grand-prix-race \
  --completed-only \
  --dry-run \
  --json-summary
```

If the single-room dry run looks correct and the room already has normalized OpenF1 events, run it
for real:

```bash
python -m app.cli.generate_room_chats \
  --season 2026 \
  --room-slug 2026-australian-grand-prix-race \
  --completed-only \
  --json-summary
```

Only after verifying that room through the public API should the full-season script be used:

```bash
backend/scripts/build_2026_rooms_and_chats.sh
```

Safety properties:

- requires `APP_ENV=production`, `DATABASE_URL`, and `DATABASE_MIGRATION_URL`;
- refuses obvious local database URLs;
- runs migrations before generating chats;
- creates rooms only through the reviewed room-catalog service;
- selects completed competitive sessions only when `--completed-only` is present;
- generates from persisted `normalized_race_events`, not from frontend page views;
- stores a deterministic `generation_key` per room/event/agent/version so reruns are idempotent;
- soft-archives generated messages for the selected version when `--force-regenerate` is used,
  preserving evidence and non-generated content.

Useful status query:

```sql
SELECT slug, session_type, status, ingestion_status, replay_available,
       chat_generation_status, generated_message_count,
       last_generated_sequence, generation_version, generation_error
FROM race_rooms
WHERE season = 2026
ORDER BY scheduled_start, session_type;
```

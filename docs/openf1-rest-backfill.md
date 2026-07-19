# OpenF1 historical REST backfill

Apex Arena uses OpenF1 REST to recover completed sessions when live MQTT is unavailable. The
historical path is explicit and resumable; application startup never launches a backfill.

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

The Railway ingestor should temporarily use:

```dotenv
APP_ENV=production
APP_PROCESS_ROLE=ingestor
OPENF1_LIVE_AUTO_CONNECT=false
OPENF1_INGESTION_MODE=rest
OPENF1_REST_BACKFILL_ENABLED=false
OPENF1_REST_BACKFILL_SEASON=2026
OPENF1_REST_BACKFILL_MAX_SESSIONS=1
OPENF1_REST_MAX_CONCURRENT_REQUESTS=2
OPENF1_REST_CURSOR_OVERLAP_SECONDS=2
OPENF1_REST_INCLUDE_HIGH_FREQUENCY=false
```

`OPENF1_REST_BACKFILL_ENABLED=false` is intentional: the CLI is explicit and does not run during
startup. The API role cannot enable or execute historical backfill. Keep both Neon URLs configured;
the ingestor and CLI use the direct URL for session advisory locks.

MQTT is disabled operationally because the provider broker currently refuses both native TLS and
WebSocket connections. OAuth and historical REST remain independent and functional. Re-enable
MQTT only after a broker connectivity probe succeeds, then set `OPENF1_INGESTION_MODE=auto` and
`OPENF1_LIVE_AUTO_CONNECT=true` on the single ingestor replica.

## One-session rollout: Australia Qualifying

Run from a Railway ingestor shell after migrations reach head.

Dry run (no database writes):

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-australian-grand-prix-qualifying \
  --dry-run \
  --json-summary
```

Core endpoint backfill:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-australian-grand-prix-qualifying \
  --endpoints drivers,laps,position,intervals,pit,stints,race_control,weather,session_result,starting_grid \
  --json-summary
```

Resume a failed job without re-fetching completed endpoints:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-australian-grand-prix-qualifying \
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
WHERE room_slug = '2026-australian-grand-prix-qualifying';
```

Verify the public room state:

```sql
SELECT slug, meeting_key, session_key, status, mode, ingestion_status,
       source_availability, replay_available, results_available,
       eligibility_status, is_development, last_event_at
FROM race_rooms
WHERE slug = '2026-australian-grand-prix-qualifying';
```

Verify real normalized events:

```sql
SELECT count(*) AS normalized_event_count
FROM normalized_race_events
WHERE session_key = (
  SELECT session_key FROM race_rooms
  WHERE slug = '2026-australian-grand-prix-qualifying'
);
```

The internal `GET /api/v1/internal/openf1/backfill-status` endpoint requires
`X-Internal-API-Key` and returns only safe state and counters.

Then call the production weekends API and confirm Australia Qualifying has a non-null room slug,
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

<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# OpenF1 historical REST backfill

This runbook describes the implementation committed through
`35666c0b0653701fcf9ea0f91e46f989f73dfadf` on `sprints`. It is tested as part
of the Task 1–18 foundation artifacts and pushed to `origin/sprints`, but it is
not a record of a production deployment.

Apex Arena uses OpenF1 REST to recover completed Practice 1/2/3, Sprint
Qualifying, Sprint, Qualifying, and Race sessions. Startup never launches a
full-season backfill. There are three separate paths:

- `app.cli.backfill_openf1` handles exactly one completed room or provider
  session.
- `app.cli.backfill_completed_rooms` handles a bounded or full set of
  incomplete completed-room candidates, practice included.
- recent-session reconciliation optionally selects at most the configured
  number of recently completed rooms per pass and invokes resumable backfill.

`OPENF1_REST_BACKFILL_ENABLED` is currently a configuration/status guard; it
does not schedule either CLI. Keep it `false` for normal services and invoke
historical work explicitly.

## Data and finalization path

```text
OpenF1 REST payload
  -> RawEventInput -> durable raw provider event
  -> canonical normalizer -> ordered normalized_race_events
  -> persisted race state / replay source facts
  -> room finalization from stored endpoint counts
  -> public Race Rooms API
```

The CLI disables live consumers while rebuilding history, so it does not fan
thousands of archived rows into Redis or generate conversation synchronously.
Historical chat generation is a separate explicit job over persisted
normalized events.

A room is replayable only when the finalizer finds stored timing context and a
non-empty normalized sequence. Results availability comes from persisted
classification rows. Optional endpoint failures produce a `partial` result
without deleting successful endpoint work or downgrading a better existing
availability state.

## Preconditions

Do not run these commands against production from the current Task 19 artifact.
The production proxy/replay protections, restart reconciliation, release seed,
release dependency graph, formatting gate, and full foundation verification
remain pending in Tasks 20–27.

For a later approved rollout:

1. Use an ingesting execution context. Both CLIs construct
   `APP_PROCESS_ROLE=ingestor` and therefore select the direct database DSN.
2. Configure `DATABASE_URL`, `DATABASE_MIGRATION_URL`, and `REDIS_URL` only in
   the secret store. Staging/production ingesting roles require the direct,
   non-pooler `DATABASE_MIGRATION_URL`.
3. Configure `OPENF1_USERNAME` and `OPENF1_PASSWORD` when the provider requires
   OAuth. REST starts public, retries a 401 through OAuth, and fails safely when
   required credentials are absent or invalid. MQTT credentials are irrelevant
   to the CLI unless the same worker also runs MQTT.
4. Confirm the single Alembic head is `20260911_0015` and that the target
   database reports the same revision. Migration 0015 adds the durable recent
   reconciliation-attempt marker used for fair candidate ordering.
5. Take a recoverable database backup/branch and stop competing recovery work.

Read-only checks:

```bash
cd backend
./.venv/bin/alembic heads
python -m app.cli.database_status --json-summary
cd ..
scripts/run-production-migrations.sh --check
```

Never paste DSNs, passwords, OAuth tokens, or internal API keys into command
history or reports.

## One-session canary

Run from the backend container/working directory after an approved compatible
application and schema rollout. Substitute a real completed room slug; the
examples use a repository-format placeholder and do not assert that provider
data is currently published.

Dry run (provider resolution only, no database writes):

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-example-grand-prix-qualifying \
  --dry-run \
  --json-summary
```

Qualifying core endpoints:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-example-grand-prix-qualifying \
  --endpoints drivers,laps,position,race_control,weather,session_result,starting_grid \
  --json-summary
```

Race/practice/sprint core endpoints:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-example-grand-prix-race \
  --endpoints drivers,laps,position,intervals,pit,stints,race_control,weather,session_result,starting_grid \
  --json-summary
```

Resume skips only endpoints with durable non-empty completion checkpoints.
Empty responses remain retryable:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-example-grand-prix-qualifying \
  --resume \
  --force-retry-failed \
  --json-summary
```

The command requires exactly one of `--room-slug` or `--session-key` and
requires `--max-sessions=1` (the default). It rejects round ranges, unresolved
or ambiguous matches, and provider sessions whose `date_end` is missing or in
the future. A worker that cannot acquire the per-season/session advisory lock
returns `locked` without writing.

## Completed-room batch

Inspect and bound the candidate set before broad recovery. The batch covers all
seven session types, not just competitive sessions:

```bash
python -m app.cli.backfill_completed_rooms \
  --season 2026 \
  --max-rooms 1 \
  --dry-run \
  --json-summary
```

Then remove `--dry-run` for the approved canary. Use `--room-slug` to pin one
room, `--resume` to preserve successful non-empty checkpoints,
`--force-retry-failed` to retry failed endpoints, and `--fail-fast` when an
operator wants the batch to stop at the first error. The default is to continue
and return a nonzero exit code if any room remains failed or partial.

The checked-in `backend/scripts/build_2026_rooms_and_chats.sh` is a broader,
production-only orchestration: it runs migrations, reports database status,
syncs the catalog, runs completed-room backfill, and then generates chats. Do
not use it as the initial backfill canary.

## Automatic recent recovery

Automatic recovery is off by default and requires both:

```dotenv
RECENT_SESSION_RECONCILIATION_ENABLED=true
RECENT_SESSION_AUTO_BACKFILL_ENABLED=true
```

It runs only in `ingestor` or `combined`, never selects a future/live session,
and covers Practice 1/2/3, Sprint Qualifying, Sprint, Qualifying, and Race. The
defaults are a 14-day lookback, 15-minute provider grace, 900-second interval,
and one selected room per pass. Selected rooms are processed sequentially.
`RECENT_SESSION_AUTO_BACKFILL_MAX_CONCURRENT` is declared and validated but is
not consumed by this reconciler. Durable least-recently-attempted ordering
prevents permanent starvation across restarts.

The reconciler inspects provider endpoint availability first. It binds a
confident provider identity but leaves the room pending when drivers/timing are
not yet usable. Automatic work excludes high-frequency `car_data` and
`location`, resumes completed non-empty checkpoints, and retries empty or
failed endpoints.

## Durable verification

Inspect job progress without exposing `last_error_message` in routine reports:

```sql
SELECT season, meeting_key, session_key, room_slug, status,
       requested_endpoints, completed_endpoints, failed_endpoint,
       rows_fetched, rows_processed, rows_inserted, rows_deduplicated,
       last_error_code, updated_at, completed_at
FROM openf1_backfill_jobs
WHERE room_slug = '2026-example-grand-prix-qualifying';
```

Inspect public room facts:

```sql
SELECT slug, meeting_key, session_key, session_type, status, mode,
       ingestion_status, source_availability, replay_available,
       results_available, eligibility_status,
       reconciliation_attempted_at, last_event_at
FROM race_rooms
WHERE slug = '2026-example-grand-prix-qualifying';
```

Verify that normalized facts exist:

```sql
SELECT count(*) AS normalized_event_count
FROM normalized_race_events
WHERE session_key = (
  SELECT session_key FROM race_rooms
  WHERE slug = '2026-example-grand-prix-qualifying'
);
```

`GET /api/v1/internal/openf1/backfill-status` requires
`X-Internal-API-Key` and reports only safe state, counters, role-aware provider
status, recent-reconciliation state, lease ownership, and Redis diagnostics.
Do not confuse an API process's local SSE client count with a cluster total.

After a successful canary, verify the public room/session response has the
expected slug, confident identity, availability, and replay/result flags. Run
the same command with `--resume`; completed non-empty endpoints should be
skipped and durable deduplication should prevent duplicate normalized events.

## Availability and high-frequency data

- `telemetry`: at least 100 stored `car_data`/`location` facts plus replay
  timing context.
- `limited_telemetry`: drivers, timing, and a non-empty normalized replay
  sequence.
- `timing_only`: timing exists but the fuller replay threshold is incomplete.
- `results_only`: classification data exists without replay timing.
- `unavailable`: insufficient stored provider data; the room remains pending or
  unavailable rather than fabricating values.

High-frequency endpoints are opt-in and require both the endpoint selection and
the guard flag:

```bash
python -m app.cli.backfill_openf1 \
  --season 2026 \
  --room-slug 2026-example-grand-prix-race \
  --include-high-frequency \
  --endpoints car_data,location \
  --json-summary
```

Test one session and monitor provider request volume and PostgreSQL growth
before expanding. GPS is stored through the dedicated time-indexed pipeline and
downsampled according to `LOCATION_SAMPLE_INTERVAL_MS`; do not claim complete
historical high-frequency telemetry unless the provider and stored counts prove
it.

## Stop and recover

Stop the CLI or worker, disable both recent-session settings, and set
`LIVE_MODE_ENABLED=false` if live intake must also stop. Leave durable jobs and
provider facts intact, inspect the last safe error category/status, and resume
later. Do not downgrade migrations, delete events, or clear endpoint
checkpoints as a routine rollback.

Production rollout remains pending. The historical Italian GP evidence and the
current tested/deployed boundary are recorded in
[`italian-gp-live-room-repair-report.md`](italian-gp-live-room-repair-report.md).

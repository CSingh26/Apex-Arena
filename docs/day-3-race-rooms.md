<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Day 3 Race Rooms

This guide describes the Race Rooms implementation as it exists on the Day 3 branch. It is both
an architecture reference and a local operator runbook. The provider ingestion, normalized event
sequence, race-state reducer, snapshots, and Redis boundary are the Day 1/Day 2 foundation; Day 3
adds a durable room catalog, replay coordinator, grounded specialist discussion, evidence APIs,
room-specific SSE, and the Race Rooms interface.

## System boundary and data flow

Race Rooms never reason directly over an OpenF1 or Jolpica response. The replay coordinator reads
persisted `NormalizedRaceEvent` rows in sequence, applies each event to the shared race-state
reducer, and then offers it to the discussion trigger evaluator.

```text
OpenF1 REST/MQTT records
        |
        v
raw event persistence -> normalization -> deduplication -> event-time order -> sequence
                                                                       |
                                                                       v
                                                        PostgreSQL normalized events
                                                                       |
                                    RoomReplayCoordinator reads one event at a time
                                                                       |
                                      +--------------------------------+-------------------+
                                      |                                                    |
                                      v                                                    v
                              race-state reducer                                  trigger evaluator
                                      |                                                    |
                              periodic snapshots                              grounded context builder
                                                                                           |
                                                                    primary -> optional reply
                                                                                  -> optional Nova summary
                                                                                           |
                                                                  validator -> messages + evidence
                                                                                           |
                                                            Redis room stream -> SSE -> browser
```

Important boundaries:

- Jolpica supplies the public 2026 calendar and lifecycle metadata. OpenF1 session metadata is
  used to associate a completed meeting with a historical race session. The REST client first
  attempts the session query without credentials and, when OpenF1 returns 401 and backend
  credentials exist, retries once with a cached OAuth bearer token.
- A catalog sync does **not** ingest detailed OpenF1 laps, positions, stints, or race-control data.
  A matched session says replay data may be obtainable; a replay still requires normalized events
  for that `session_key` to have been ingested already.
- Messages and message evidence are durable PostgreSQL records. Redis is the low-latency delivery
  path, not the source of truth for discussion recovery.
- Playback cursor, lap, speed, pause state, and start time are durable. The scheduler task itself,
  trigger cooldowns, recent-content fingerprints, and active race state are process-local.

The main implementation entry points are:

- [`backend/app/services/rooms.py`](../backend/app/services/rooms.py): agent seeding, fixture
  registration, Jolpica/OpenF1 catalog synchronization, and availability classification.
- [`backend/app/services/room_replay.py`](../backend/app/services/room_replay.py): replay lifecycle
  and normalized-event scheduling.
- [`backend/app/services/discussion_triggers.py`](../backend/app/services/discussion_triggers.py):
  significance, priority, deduplication, and cooldown decisions.
- [`backend/app/services/discussion.py`](../backend/app/services/discussion.py): context building,
  deterministic specialist templates, claim validation, evidence persistence, and publication.
- [`backend/app/api/room_routes.py`](../backend/app/api/room_routes.py) and
  [`backend/app/api/room_streaming.py`](../backend/app/api/room_streaming.py): public REST/SSE
  contract and debug-key-protected operations.
- [`frontend/src/components/race-rooms`](../frontend/src/components/race-rooms): archive, roster,
  timeline, replay controls, evidence drawer, context, and Pipeline Diagnostics.

## Five specialist agents

The profiles are typed `AgentProfile` values in
[`backend/app/services/room_agents.py`](../backend/app/services/room_agents.py), then upserted into
`agent_profiles` and associated with rooms idempotently. The frontend renders the API response; it
does not define five separate hard-coded profiles.

| Agent | Responsibility | Guardrail |
| --- | --- | --- |
| Mira Vale — Race Strategist | Pit windows, tyre life, traffic, undercuts, neutralisation opportunities | Explains trade-offs and does not infer an outcome without supplied strategy evidence |
| Theo Voss — Telemetry Engineer | Lap deltas, representative laps, sector/pace trends, consistency, degradation, data quality | Uses numbers only when present and calls out noisy or incomplete samples |
| Lena Cross — Racecraft Analyst | Overtakes, position changes, incidents, starts, defence, and track position | Separates an observed position update from an unproven on-track pass |
| Arjun Reyes — Championship Historian | Supplied season, circuit, result, and championship context | Makes no historical comparison unless that context was provided to the current trigger |
| Nova — Room Host | Moderation, evidence checks, phase changes, uncertainty, and summaries | Keeps chains bounded, avoids repetition, and summarizes only major events or disagreement |

Profiles include display/role copy, avatar key, specialties, personality rules, speaking style,
supported topics, active state, sort order, and a semantic UI accent key. A new profile should be
added to `DEFAULT_ROOM_AGENTS`; changing its persistent shape also requires a migration.

## Durable model

Race Rooms use six PostgreSQL tables:

| Table | Durable responsibility |
| --- | --- |
| `agent_profiles` | Typed specialist identity, behavior metadata, supported topics, ordering, and UI accent |
| `race_rooms` | Race/session metadata, mode/status, honest source availability, progress, counts, and featured/development flags |
| `race_room_agents` | Room membership, active state, join/leave timestamps, and display order |
| `room_messages` | Ordered specialist message, lap/session context, topic/type, confidence, evidence state, reply link, trigger references, and generation metadata |
| `message_evidence` | Evidence key, normalized source reference/provider, optional metric value/unit, quality context, and event/lap sequence |
| `room_playback_states` | Current event/message sequence, lap, speed, pause state, start time, and last update |

The original room tables were introduced by `20260716_0003`, JSON was aligned with PostgreSQL by
`20260716_0004`, and
[`20260717_0005_day3_room_contracts.py`](../backend/migrations/versions/20260717_0005_day3_room_contracts.py)
adds the Day 3 field names, `country_code`, `telemetry_quality`, evidence keys, the expanded
playback cursor, and compound query indexes.

The schema enforces unique room slugs, one membership per room/agent, one message sequence per
room, and one generated message per room/trigger/agent. Indexed access paths cover room slug,
season/round, status/mode/date/session, room message sequence, room/lap, room/agent, topic,
trigger-event ID, evidence message ID, and message creation time. Message insertion locks the room
row while allocating the next sequence so concurrent inserts in one database serialize safely.

Run all migrations before starting a new backend:

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

## Catalog synchronization and honest availability

`RaceRoomService.ensure_catalog()` runs lazily before room reads. It:

1. Upserts the five active agent profiles.
2. Outside production, upserts and seeds the isolated Day 3 validation room.
3. Loads the configured season calendar from Jolpica.
4. Requests OpenF1 `Race` session metadata for that year when the client is available, with the
   public-first/OAuth-on-401 behavior described above.
5. Matches sessions by year, race date within two days, and country or circuit identity.
6. Upserts one slug-stable room per calendar meeting and associates all five agents.

Repeated calls do not duplicate agents, memberships, fixture events, or rooms. Catalog refreshes
preserve generated progress: once a room has messages, its status, mode, availability,
telemetry-quality label, message count, current lap, and last activity are not overwritten by a
metadata refresh.

Current availability rules are deliberately conservative:

| Calendar/session state | Room classification | Meaning |
| --- | --- | --- |
| Completed meeting plus matched OpenF1 Race session | `limited_telemetry` | Historical source may be used after its records are normalized; this is not a claim of complete telemetry |
| Completed meeting without a match | `results_only` | Calendar/results metadata only; no telemetry discussion is fabricated |
| Live meeting | `limited_telemetry` | A room can be shown, but actual detail depends on live ingestion and credentials |
| Upcoming meeting | `unavailable` | Metadata exists; replay data and discussion do not yet exist |
| Day 3 fixture outside production | `limited_telemetry` + `deterministic_fixture` | Explicitly synthetic validation data, never a real race result |

During the Day 3 live-provider check on 17 July 2026, this matcher associated 21 entries in the
2026 calendar with OpenF1 Race sessions. That is an observed validation result, not a fixed seed
count; it can change as the provider and calendar evolve.

To force a catalog refresh through the API, configure a non-empty `INTERNAL_API_KEY` in the
private `.env`, then run:

```bash
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/sync \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}"
```

The endpoint returns only `rooms_synchronized`. It is not public and never returns provider
credentials. `ENABLE_AUTO_ROOM_CREATION`, `ENABLE_HISTORICAL_REPLAY`, and
`ENABLE_PUBLIC_REPLAYS` currently appear in safe debug metadata but do not gate these Race Room
routes; the catalog is still ensured on Race Room reads.

## Deterministic validation room

When `APP_ENV` is not `production`, startup services expose `day3-validation-room` and idempotently
seed 14 normalized events under session key `day3-validation`. The room is named **Day 3
Validation Room**, uses the synthetic circuit/country labels, and is visibly marked as deterministic
development data in both the archive and room view.

The fixture spans lap/session markers 0 through 12 and includes three drivers (4, 81, and 63), a
start, position change, explicit overtake, representative laps, fastest lap, pit stop, tyre change,
yellow flag, incomplete weather update, pace trend, retirement, safety car, and finish. Its
`season_context` explicitly says that no championship points apply.

This room is the repeatable local replay target. It is not loaded in production, and it must not
be used as evidence about a real driver, circuit, race, or championship.

## Triggering, grounding, and deterministic generation

### Significance and chain bounds

The trigger evaluator recognizes session/race starts, meaningful lap completions, position
changes, overtakes, pit and tyre events, fastest laps, safety-car/VSC/red/yellow flags, penalties,
race-control events, weather changes/updates, retirements, and session finish.

It applies:

- event deduplication by normalized `dedup_key` with bounded in-memory storage;
- event-time topic and agent cooldowns;
- a room-level trigger-per-minute limit;
- priority rules where critical events bypass normal throttles;
- representative lap gating (lap 1, every tenth lap, or a supplied pace trend); and
- a maximum chain of one primary message, one eligible reply, and one Nova summary for a critical
  phase change.

The evaluator selects specialists by topic. High-priority triggers may get a reply. Critical
triggers may also request a host summary. This avoids five unrelated comments or an autonomous
conversation loop for every telemetry sample.

### Context and validation

For one trigger, `GroundingContextBuilder` supplies only the normalized event type/sequence/lap,
involved driver numbers, the event payload, current race status/lap, and state for relevant
drivers. Each generated structured message contains one or more claims and the evidence keys used
by each claim.

Before persistence, `GroundingValidator` rejects output when:

- a claim cites an evidence key absent from the supplied context;
- content mentions a `Driver N` not present in the triggering event;
- high confidence is used with explicitly incomplete data;
- content is empty, has no structured claim, or uses prohibited ungrounded radio/tyre phrasing; or
- normalized message content exactly repeats a recent room message.

Every accepted message gets evidence rows that point back to the normalized event ID and provider,
carry event/lap sequence and data-quality context, and store scalar metrics where possible. Reply
messages persist `reply_to_message_id`. Provider payloads are never presented as hidden prompts,
and the evidence API is scoped to the requested room before it reveals a message trace.

### Current generation mode

The Day 3 runtime always uses `DeterministicRoomGenerator`. It contains factual templates for
starts, lap/pace observations, positions/overtakes, pits, tyres, fastest laps, control events,
weather uncertainty, retirement, finish, specialist replies/disagreement, and Nova summaries.
Messages are stored with `generated_by="deterministic"` and `prompt_version="rooms-v2"`.

This means Race Rooms work with an empty `OPENAI_API_KEY`; no current Race Room code calls an LLM.
The `generated_by`, `model_name`, and prompt-version fields preserve an audit boundary for a future
validated LLM generator. Until one is wired through the same structured claim validator, the
deterministic generator is not merely a fallback path—it is the only active generation path.

## Replay lifecycle

Replay is server-driven and does not require MQTT. `RoomReplayCoordinator` processes one persisted
normalized event per `ROOM_REPLAY_INTERVAL_SECONDS / playback_speed`, applies race state, asks the
discussion engine to consume the event, advances durable playback, updates the room, and publishes
playback/status changes.

Actions have these semantics:

- **Start** requires a linked session and at least one normalized event, preserves the existing
  cursor, marks the room replaying, and creates an application-local scheduler task.
- **Resume** unpauses the durable cursor and recreates the task when none is active.
- **Pause** stops advancement without deleting messages or state.
- **Set speed** accepts only `0.5`, `1`, `2`, `4`, or `8`.
- **Seek to sequence** validates the target against the session's maximum event sequence, then
  resets and deterministically reapplies race state and discussion triggers from sequence zero
  through that target.
- **Seek to lap** resolves the first normalized event at or after that lap, then rebuilds through
  the immediately preceding sequence so normal replay resumes at the requested lap.
- **Restart** cancels the active task, deletes the room's messages/evidence, resets playback,
  in-memory trigger state, race state, and snapshots for that session, then replays from sequence
  zero at 1x.
- **Completion** pauses playback, marks the room completed, and emits both room status and
  `replay_complete` status notifications.

Pause, resume, speed, seek, and normal event advancement are serialized by one per-room lock, so a
seek cannot interleave with the replay task. Rebuild replays normalized events in batches through
the target and recalculates the playback message cursor, but it deliberately retains persisted
messages/evidence. Existing uniqueness constraints prevent duplicate trigger/agent messages.

Playback is shared by every viewer of a room. Use Restart when the persisted conversation itself
must be deleted and regenerated from a clean timeline.

## REST and SSE contract

All endpoints use the existing `/api/v1` FastAPI conventions.

| Method and path | Contract |
| --- | --- |
| `GET /race-rooms` | `season`, `status`, `mode`, `search`, `sort=race_date_desc|race_date_asc|latest_activity`, `limit`, and `offset` |
| `POST /race-rooms/sync` | Internal-key-protected Jolpica/OpenF1 metadata refresh |
| `GET /race-rooms/{slug}` | Room, active agents, playback, availability notice, and diagnostics availability |
| `GET /race-rooms/{slug}/messages` | Forward cursor plus `agent_id`, `topic`, `message_type`, `lap_from`, `lap_to`, `sequence_from`, `sequence_to`, and bounded `limit` filters |
| `GET /race-rooms/{slug}/messages/{message_id}/evidence` | Evidence rows, trigger event summary, optional snapshot reference, quality flags, generation mode, and confidence; never prompts or secrets |
| `POST /race-rooms/{slug}/replay` | Body action `start`, `restart`, or `resume` |
| `POST /race-rooms/{slug}/playback` | `pause`, `resume`, `set_speed`, `seek_to_lap`, or `seek_to_sequence` with the associated validated value |
| `GET /race-rooms/{slug}/stream` | Room SSE with optional `after_sequence` and numeric `Last-Event-ID` recovery |
| `GET /race-rooms/{slug}/diagnostics` | Safe development/debug diagnostics; hidden in production unless explicitly enabled |
| `POST /race-rooms/{slug}/generate` | Internal-key-protected deterministic generation over already-normalized events |

The full paths begin with `/api/v1`, for example
`GET /api/v1/race-rooms/day3-validation-room`.

Missing rooms or room-scoped messages return 404. A replay with no linked/normalized session, or a
seek outside the available range, returns 409. Malformed actions and unsafe playback speeds return
FastAPI's 422 validation response. Protected sync/generation operations return 401 for a bad key
and 503 when no internal key is configured.

Example replay controls:

```bash
# Start or continue the current cursor.
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/day3-validation-room/replay \
  -H 'Content-Type: application/json' \
  --data '{"action":"start"}'

# Pause and then move to the first event associated with lap 9.
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/day3-validation-room/playback \
  -H 'Content-Type: application/json' \
  --data '{"action":"pause"}'
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/day3-validation-room/playback \
  -H 'Content-Type: application/json' \
  --data '{"action":"seek_to_lap","lap_number":9}'

# Resume at 4x.
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/day3-validation-room/playback \
  -H 'Content-Type: application/json' \
  --data '{"action":"set_speed","playback_speed":4}'
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/day3-validation-room/playback \
  -H 'Content-Type: application/json' \
  --data '{"action":"resume"}'
```

### Stream recovery

The SSE handoff records the current Redis stream ID, then returns persisted PostgreSQL messages
after the client's last message sequence, followed by the current playback state. It subsequently
reads newer Redis records. Message sequences at or below the recovery cursor are discarded, which
handles the PostgreSQL/Redis overlap without duplicate timeline entries.

The stream emits `connection_status`, `room_message`, `playback_state`, and `room_status` events,
plus comment heartbeats. A numeric `Last-Event-ID` takes precedence when it is newer than the
`after_sequence` query. Browser `EventSource` reconnects use `after_sequence` because custom
headers cannot be set; the client retains its maximum message sequence, deduplicates by both
message ID and room sequence, sorts by sequence, and bounds local history to 600 messages. The API
recovery page is capped by `ROOM_STREAM_BACKLOG_LIMIT` (250 by default).

## Frontend behavior

`/race-rooms` provides featured/open/archive sections with season, status, mode, search, and sort
controls. Cards show session metadata, status/mode, date, coverage, agent/message counts, progress,
and an unmistakable fixture label where relevant.

`/race-rooms/[slug]` provides:

- a sticky room header and replay controls;
- a collapsible API-driven “In this room” roster;
- an editorial message timeline with compact mobile controls and agent/topic/type/exact-lap
  filters mirrored into the URL;
- reply labels and traceable evidence buttons;
- a focus-contained, Escape-dismissable evidence dialog with trigger, confidence, quality,
  provider, sequence, and metric context;
- a desktop race-context column, mobile context sheet, and honest limited-data notice;
- bounded 100-message API pages, a 600-message client cache, and a 300-message render window;
- SSE reconnect with duplicate/out-of-order merge handling; and
- persisted light/dark theme selection and responsive single-column layouts.

Filter changes update the URL without a route reset; initial state is still local and is not yet
hydrated from a pre-existing query string. “Jump to latest” scrolls to the current end of the
locally loaded timeline.

## Pipeline Diagnostics and secret safety

The former Race Signal operational view is available as the lazy-loaded, collapsible **Pipeline
Diagnostics** panel. The room detail response advertises it when `APP_ENV != production` or
`ROOM_DIAGNOSTICS_ENABLED=true`. The diagnostics endpoint returns 404 in production when the flag
is false, which is the default.

The response contains raw/normalized/snapshot counts, latest normalized sequence/events, ordering
buffer depth, room stream status, provider mode, safe live connection state, current race state,
playback, and discussion trigger/generated/rejected/deterministic counts. It does not query or
return raw provider payload records, API keys, OAuth tokens, Redis/database URLs, hidden prompts,
or exception traces. Keep `ROOM_DIAGNOSTICS_ENABLED=false` in production unless this intentionally
safe debug surface is required.

## Local Docker workflow

Create a private environment, make the two PostgreSQL password values agree, and keep `.env` out
of version control:

```bash
cp .env.example .env
# Edit POSTGRES_PASSWORD and the password embedded in DATABASE_URL.
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

The backend container waits for PostgreSQL/Redis health and applies Alembic migrations before the
API starts. The frontend waits for backend health. With the unmodified `.env.example`, open
`http://localhost:3000/race-rooms` and use `http://localhost:8000/docs` for the API schema. If you
change `FRONTEND_PORT`/`BACKEND_PORT`, update the matching public URLs and CORS origin before
building the frontend image because `NEXT_PUBLIC_API_URL` is a build argument.

Useful local checks:

```bash
docker compose logs -f backend frontend
curl --fail http://localhost:8000/health
curl --fail 'http://localhost:8000/api/v1/race-rooms?season=2026&limit=100'
curl --fail http://localhost:8000/api/v1/race-rooms/day3-validation-room
curl --no-buffer 'http://localhost:8000/api/v1/race-rooms/day3-validation-room/stream?after_sequence=0'
docker compose down
```

`docker compose down` preserves named PostgreSQL and Redis volumes. Use `docker compose down -v`
only when intentionally discarding the local database and stream state.

## Test and quality commands

Run the backend suite with the project virtual environment and a migrated PostgreSQL test target:

```bash
cd backend
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/alembic check
```

Run frontend static checks, component/unit tests, production compilation, and dependency audit:

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
npm audit
```

Validate the infrastructure and the executable replay against the full stack:

```bash
docker compose config --quiet
docker compose up -d --build --wait
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/api/v1/race-rooms/day3-validation-room
curl --fail -X POST http://localhost:8000/api/v1/race-rooms/day3-validation-room/replay \
  -H 'Content-Type: application/json' --data '{"action":"restart"}'

cd frontend
E2E_BASE_URL=http://localhost:3000 \
E2E_API_URL=http://localhost:8000 \
npm run test:e2e
```

Set the two E2E URLs to the active Compose frontend and backend origins when using alternate
published ports.

The automated backend coverage includes trigger/grounding chains, replay lifecycle, safe routes,
message filters/evidence, and stream recovery. The Day 3 frontend unit suite has 14 tests in seven
Vitest/Testing Library files covering the archive and filters, five-agent roster/collapse,
timeline replies and filters, evidence details/Escape dismissal, exact replay-control payloads,
hydration-safe theme restoration, and bounded duplicate/out-of-order message merging. The
Playwright suite has six Chromium tests:
one covers the primary archive-to-room replay, filtering, evidence, seek/resume/completion flow,
and five cover the required responsive width matrix. Those tests fail on browser console/page
errors and verify that Pipeline Diagnostics remains reachable at a 1280 × 720 viewport. Manual
browser validation remains a release gate for network inspection and fault scenarios outside that
scripted path.

For manual browser acceptance, inspect light and dark themes at 1440, 1280, 1024, 768, and 390
CSS pixels. Exercise start/restart, pause/resume, every speed, lap seek, agent/topic/type/lap
filters, evidence open/close by keyboard, reconnect, long content, and the diagnostics expansion.
Confirm the console/network panels have no hydration errors, failed calls, reconnect loops,
duplicate messages, overflow, or sticky-control overlap.

## Known limitations

- Replay scheduling, task ownership, trigger cooldown/dedup state, and recent-message repetition
  memory are process-local. Playback is durable, but a running replay is not automatically resumed
  after process restart and multiple API workers are not coordinated.
- Normalized event sequence allocation and bounded-lateness ordering inherit the Day 2
  single-process/distributed-scaling limitations described in the root README.
- The discussion runtime is deterministic-only. OpenAI environment variables and model metadata
  do not imply that an LLM generator is connected.
- The `ENABLE_AUTO_ROOM_CREATION`, `ENABLE_HISTORICAL_REPLAY`, and `ENABLE_PUBLIC_REPLAYS` flags
  are reported as configuration metadata but do not currently gate catalog or replay operations.
- OpenF1 session matching is a date/country/circuit heuristic. `limited_telemetry` means a matching
  historical source exists, not that every desired endpoint has been ingested or is complete.
- Catalog synchronization creates metadata; historical data ingestion is a separate protected
  Day 2 workflow. Results-only rooms intentionally have no invented telemetry discussion.
- Playback state and controls are shared per room, not per viewer. One viewer can pause, seek, or
  restart the replay seen by others.
- Seek serially reconstructs race state and in-memory discussion state through the target event,
  but intentionally retains the room's persisted messages/evidence, including messages generated
  later in an earlier run. Restart is the supported operation for a clean persisted timeline.
- The room UI fetches initial data from the browser rather than using server-rendered initial
  room data. Timeline filtering is local and exact-lap only; values are mirrored to the URL but a
  shared filter URL does not yet restore those values on initial load.
- The timeline bounds local and rendered messages but does not use measured-row virtualization.
  Very large archives will need a bidirectional cursor and a dedicated virtualization layer.
- Playwright covers the primary full-stack flow and responsive width matrix, but reconnect fault
  injection, long-content stress, console/network inspection, and broader browser coverage remain
  manual release checks.

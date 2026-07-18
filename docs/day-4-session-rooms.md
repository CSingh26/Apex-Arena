<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Day 4: event weekends and competitive session rooms

Day 4 keeps **Race Rooms** as the public product name while making the existing
`race_rooms` persistence model session-generic. Renaming the table would add migration risk to
messages, evidence, playback, and existing deep links without improving the public contract, so
the compatibility name is deliberate.

## Baseline findings

Before this work, browser inspection of the local stack found:

- 23 separate room cards instead of grouped race weekends;
- 11 visible `Archived` labels, including finished events that should simply say completed;
- the synthetic `Day 3 Validation Room` in the normal public list and direct route;
- an expanded agent column approximately 1,075 px tall before the conversation;
- full diagnostics, message metadata, filters, and biographies competing for the initial viewport;
- a room navbar constrained independently from the content and a sticky header that could leave
  a large disconnected strip while scrolling.

The Day 3 fixture remains useful for deterministic tests, but it is now opt-in. Normal local,
staging, and production catalog reads exclude development rooms. Test runs or an explicitly
enabled local fixture may still create and open it.

## Weekend grouping

`GET /api/v1/race-rooms/events` returns one object per event weekend. The backend, rather than
React, owns session normalization, eligibility, and category assignment.

The public categories are:

1. **Live This Weekend** — the official competitive weekend has begun (or is within the narrow
   pre-weekend window) and has not ended. Completed and future sessions from the same weekend stay
   together.
2. **Completed Events** — the weekend's final competitive session has finished. Events are sorted
   earliest to latest so the section reads in season order.
3. **Upcoming Events** — the weekend has not begun. Events are sorted nearest to furthest.

Sessions inside a weekend use their actual scheduled start order. A standard weekend exposes
Qualifying and Race. Provider-declared Sprint weekends expose Sprint Qualifying, Sprint,
Qualifying, and Race. Practice remains available as schedule context but is not given a public
conversation room in Day 4.

Search operates on event name, circuit, and country without breaking a weekend into disconnected
session results. The grouped endpoint also accepts season, category, competitive session type,
Sprint-format, limit, and offset filters.

## Session identity and Sprint detection

The stable internal session types are:

- `QUALIFYING`
- `SPRINT_QUALIFYING`
- `SPRINT`
- `RACE`

`Sprint Shootout`, `Sprint Qualification`, and related provider spellings normalize to
`SPRINT_QUALIFYING`; `Sprint Race` normalizes to `SPRINT`. The schedule format is derived from
Jolpica/OpenF1 session metadata, never from a manually maintained event list.

A room is unique for a season, round, and normalized competitive session. Its slug includes the
event and session type. Provider `meeting_key` and `session_key` remain persisted correlation
identifiers, while repeat synchronization upserts the same row and preserves discussion history.

## Authoritative eligibility

`RoomEligibilityService` is the single policy boundary used by synchronization, direct room
navigation, replay, playback, and generation. It returns one of:

- `eligible_live`
- `eligible_historical`
- `future_read_only`
- `provider_pending`
- `unavailable`
- `already_exists`

The decision considers current time, authoritative session start/status, provider-session
presence, data availability, replay/results readiness, existing state, and explicit fixture mode.

An upcoming event click only opens a schedule preview. A GET never creates rooms, participants,
messages, telemetry, or replay state. The backend rejects direct opening, replay, playback, and
generation for future placeholder rows, even if an old database already contains one. Only the
authenticated synchronization lifecycle may create an eligible row after a session starts or
historical provider data becomes available.

## Historical OpenF1 ingestion

The historical path is session-key scoped, idempotent, and staged:

1. **Metadata** — session and driver identity.
2. **Timing** — laps, position, and intervals.
3. **Strategy** — stints and pit stops.
4. **Context** — race control and weather.
5. **Classification** — session result and starting grid where the provider exposes them.
6. **Deep telemetry (opt-in)** — bounded car/location samples; never part of the default season
   backfill.

Each provider request has a timeout, minimum interval, bounded exponential retry, and in-process
historical cache. One endpoint or session failure is isolated rather than aborting the season.
Raw and normalized deduplication make retries safe. Ingestion runs retain safe stage/status data
but never credentials, response bodies, or exception traces.

Availability is derived from records actually fetched and normalized:

- `replay_ready`: session, driver, lap, and useful timing/event data exist;
- `partial`: some useful data exists but one or more expected datasets are absent;
- `results_only`: classification exists without sufficient timing events;
- `unavailable`: no usable provider data exists.

Session discovery by itself does **not** mean telemetry is available. After ingestion, the adapter
updates the matched room's ingestion status, data availability, result flag, and replay flag.

### Provider matching

Jolpica weekends and OpenF1 sessions are matched using season, race date, normalized country,
circuit similarity, event-name similarity, session type, scheduled time, and meeting key. Close
scores are marked ambiguous and left unresolved; telemetry is never silently attached to a
plausible-but-uncertain event.

OpenF1's current public contract exposes session results and starting grids, but availability
still varies by session. Intervals are race-only. High-frequency car/location data is deliberately
excluded from default backfills to control storage and provider load.

## Qualifying and Sprint semantics

Main Qualifying supports `Q1`, `Q2`, and `Q3`; Sprint Qualifying supports `SQ1`, `SQ2`, and `SQ3`.
Phases are recorded only from explicit provider phase metadata, phase-indexed result arrays, or
race-control records. Wall-clock guesses are not used. If the provider does not expose a reliable
boundary, the UI says the phase is unavailable instead of inventing one.

Qualifying state retains best laps by phase, phase results, deletions, final classification, gaps,
and grid data when available. Playback swaps race-only lap emphasis for phase, session-time, and
event-sequence navigation. Sprint sessions keep independent keys, rooms, messages, results, and
replay state from the Sunday Race.

## Audience-friendly conversation

Public messages are deterministically shaped to short, plain-language observations that explain
the consequence first. Driver metadata resolves numbers to names and verified teams; an unresolved
number uses a neutral car-number fallback and is logged internally. Qualifying triggers suppress
race-only pit-window commentary and focus on valid laps, phase progression, improvements, and
elimination risk.

The default message shows speaker, short role, plain-language copy, phase/lap, topic, and one
evidence action. Exact precision, confidence, provider source, event sequence, and data-quality
fields remain in the evidence drawer or diagnostics. Routine laps do not trigger noise unless the
event is meaningful.

## Information hierarchy and navigation

Weekend cards show event, circuit/location, date, status, sessions, one availability phrase, and
one clear action. Internal mode, provider IDs, repeated season labels, agent/message counts, and
pipeline timestamps are removed from the default card.

Inside a room, the conversation is primary. The five-agent roster defaults to a compact row and
expands on request. Diagnostics and race context are collapsed secondary content. Qualifying rooms
do not display meaningless `Lap 0 / 0` readouts.

The shared navigation uses one maximum-width/padding contract across the landing page, index, and
rooms. Desktop keeps brand, primary links, context, and controls aligned. At mobile widths an
accessible button opens a focus-contained drawer that supports Escape, backdrop dismissal, focus
restoration, and 320 px layouts.

## Safe operations and backfill

Mutating operations require `X-Internal-API-Key`. Public GET endpoints cannot trigger ingestion or
generation.

Recommended rollout:

1. upgrade the schema with `alembic upgrade head`;
2. call the authenticated catalog sync;
3. inspect grouped event/session matches and ambiguous diagnostics;
4. ingest one completed standard weekend, one session at a time;
5. ingest one completed Sprint weekend, one session at a time;
6. verify replay, evidence, results-only/partial states, and idempotent retry;
7. only then resume the remaining completed-session backfill;
8. generate discussions only for replay-ready sessions.

The existing internal endpoints remain the safe primitives:

```text
POST /api/v1/race-rooms/sync
POST /api/v1/debug/ingest-historical-session
POST /api/v1/race-rooms/{slug}/generate
GET  /api/v1/engine/status
GET  /api/v1/race-rooms/events
```

Run ingestion with an explicit session key and bounded endpoint list. A retry reuses deterministic
hashes and does not delete messages. Never use `docker compose down -v` during deployment; schema
upgrades and container replacement retain PostgreSQL/Redis volumes.

## Validated local Docker backfill (2026-07-18)

The local Docker validation covered 22 event weekends, including six Sprint weekends. All 26 real
completed competitive sessions became replay-ready. The backfill recorded 27 ingestion runs (26
sessions plus one idempotency retry), with zero failed runs, 110,599 raw inserts, 110,599 normalized
inserts, and 11,098 replay snapshots. The retry found 1,672 duplicate records and produced zero new
raw or normalized inserts.

The database contained 741 messages after validation: 722 public messages from real sessions and
19 messages belonging to the opt-in development fixture. Live and future sessions were left
untouched, no future replay was created, and repeatedly listing events or opening an upcoming
schedule preview did not create a room.

Some OpenF1 datasets remain session-dependent. Qualifying interval data may be absent, and race
starting-grid data may be absent. Those sessions correctly finish with a `partial` ingestion status
while still being replay-ready from the available timing, event, driver, and classification data.

## Failure recovery

- `provider_pending`: retry catalog discovery after the provider publishes a session.
- `partial`: retry only failed datasets; existing normalized events remain intact.
- `results_only`: show classification without generating unsupported analysis.
- `failed`: inspect the safe ingestion stage/error type, then retry the session.
- ambiguous match: correct provider metadata or mapping evidence; do not force a guessed key.

The local fixture can be enabled only for internal deterministic testing. It must stay disabled in
public staging and production, and public listing/detail APIs continue to exclude development
rows even if a legacy row remains in the database.

# Native client contracts (iOS, Android, web)

ApexArena's race domain is server-side. A native client renders state and
subscribes to events; it does not re-derive race meaning. Everything a Swift or
Kotlin client needs is below. No separate native application exists in this
repository, and none is required — this is the contract a future one consumes.

## 1. Base URL and versioning

All public reads live under `/api/v1/`. The `v1` prefix is the compatibility
boundary:

- Fields are **added** within `v1`. A client must ignore unknown fields rather
  than failing to decode, because new fields ship without a version bump.
- Fields are **never** repurposed within `v1`. A removal or a meaning change
  requires `/api/v2/`.
- Enum-like strings are open sets. Decode an unrecognised `status`,
  `availability`, `reason` or `event_type` as "unknown to this client version"
  and fall back to a neutral presentation. Do not crash and do not treat an
  unknown value as its nearest known neighbour.

In Swift terms: decode into structs with optional properties, and model open
enums with an `unknown(String)` case rather than a plain `RawRepresentable`.

## 2. Authentication

There are two independent boundaries. Neither is a user login — ApexArena has
no end-user accounts.

### Proxy hop (`X-Apex-Proxy-Token`)

Public traffic reaches the backend through a first-party proxy. When
`APEX_ARENA_PROXY_TOKEN` is configured, every path except `/health/live`
requires that header, and requests without it are rejected with `403` before
any Redis or database work happens.

A native client must **not** ship this token. Treat it as infrastructure
credentials for a first-party gateway the app talks to, exactly as the web
frontend does through its own same-origin route. A mobile binary is not a
trusted holder of a shared secret.

### Replay operator (`X-Apex-Replay-Password`)

Required only for replay *mutations* (start, restart, resume, pause, seek). It
is an operator credential, not a user credential. Public reads — rooms,
sessions, timing, telemetry, history, standings, streams — never require it.

Do not prompt a normal viewer for it and do not embed it in a shipped client.

## 3. Read endpoints

Season and weekend structure:

```
GET /api/v1/season/{year}
GET /api/v1/season/{season}/weekends
GET /api/v1/weekends/{event_slug}
GET /api/v1/weekends/{event_slug}/sessions
```

A weekend's session list is authoritative: sprint and standard weekends differ,
and a client must not assume a fixed session set per event.

Session state:

```
GET /api/v1/sessions/{session_id}
GET /api/v1/sessions/{session_id}/capabilities
GET /api/v1/sessions/{session_key}/state
GET /api/v1/sessions/{session_key}/timing
GET /api/v1/sessions/{session_key}/events
GET /api/v1/sessions/{session_key}/track
GET /api/v1/sessions/{session_key}/locations
GET /api/v1/sessions/{session_key}/locations/samples
GET /api/v1/sessions/{session_key}/drivers/{driver_number}/telemetry
```

`capabilities` says what this session can actually support. Query it before
offering a telemetry or track-map view, rather than discovering emptiness by
rendering a blank chart.

`/drivers/{driver_number}/telemetry` is the **latest sample** for one driver.
It is a gauge feed, not a trace.

Rooms:

```
GET /api/v1/race-rooms
GET /api/v1/race-rooms/{room_slug}
GET /api/v1/race-rooms/{room_slug}/messages
GET /api/v1/race-rooms/{room_slug}/messages/{message_id}/evidence
```

Bounded detail reads:

```
GET /api/v1/sessions/{session_key}/intelligence-detail?driver=4&family=laps
GET /api/v1/race-rooms/{room_slug}/intelligence-detail?driver=4&family=laps
GET /api/v1/sessions/{session_key}/telemetry-history?driver=4&driver=16&lap=12
```

`telemetry-history` returns a bounded **series** for up to two drivers, with a
`channels` array per driver listing the channels the provider actually
published. A channel absent from `channels` has no data: render it as
unavailable. Never substitute zero — a flat zero brake trace is a fabricated
fact, not a quiet car.

Championship:

```
GET /api/v1/championship/drivers
GET /api/v1/championship/constructors
GET /api/v1/championship/summary
```

## 4. Live event streams (SSE)

Two Server-Sent Events endpoints:

```
GET /api/v1/stream/sessions/{session_key}?after_sequence_number=<n>
GET /api/v1/race-rooms/{room_slug}/stream
```

Both are `text/event-stream`. Comment frames (`: heartbeat`) arrive during idle
periods; a client must tolerate them and must not treat one as data.

### Session stream events

| `event:` | Meaning |
| --- | --- |
| `state` | Full reduced race state at a sequence number. |
| `event` | One normalized race event. Carries an `id:` equal to its sequence number. |
| `stream_status` | `{"status": "degraded", ...}` when Redis is temporarily unavailable. Facts continue; freshness is reduced. |

### Room stream events

| `event:` | Meaning |
| --- | --- |
| `connection_status` | `connected` on open, `degraded` when the backing stream falters. |
| `room_message` | One agent message. Carries an `id:` of `"<generation>:<sequence>"`. |
| `playback_state` | Replay cursor, speed and pause state. |
| `room_status` | Room lifecycle transition. |
| `discussion_generation` | The discussion was reset; see below. |

### Resumption

Session stream: pass `after_sequence_number` with the highest `event` sequence
you have. `Last-Event-ID` carries the same value.

Room stream: the event id is `"<generation>:<sequence>"`. **Both halves
matter.** A `discussion_generation` change means the conversation was reset;
the client must discard its message list and restart from that generation's
sequence 0. Resuming a new generation at an old sequence mixes two
conversations.

Reconnect with bounded backoff. The server replays missed events from a durable
cursor, so a gap is recoverable; a reconnect storm is not.

## 5. Error contract

| Status | Meaning | Client action |
| --- | --- | --- |
| `403` | Proxy token missing or wrong. | Configuration fault. Do not retry. |
| `404` | Unknown session, room or slug. | Do not retry. |
| `409` | The room view changed under a detail read. | Refresh compact state, then retry once. |
| `422` | Selection outside supported bounds. | Fix the request. Do not retry unchanged. |
| `429` | Read budget exceeded. | Honour `Retry-After`. |
| `503` | Temporarily unavailable. | Honour `Retry-After` (usually `1`). |

A `200` response may still carry `availability: "unavailable"` with a `reason`.
That is a successful read reporting that the data genuinely does not exist — it
is not an error, and it must be shown as an explanatory state rather than as a
failure or an empty view.

`Retry-After` is always seconds.

## 6. Data honesty rules a client must preserve

These are contract semantics, not styling preferences. A client that ignores
them will assert things the data does not support.

- `null` means unknown. It is never zero, false, or "none".
- `availability` of `partial` means some of what you asked for is missing;
  `reason` says which. Do not present partial data as complete.
- Literal `"unknown"` and `"unavailable"` string values mean *the system cannot
  determine this*. They do not mean the thing did not happen. In particular
  `attempt_evidence` and `championship_context` on battle context are currently
  always `"unavailable"`.
- Proximity is not DRS usage. `within_one_second` and `drs_permission` describe
  position and track state; only `observed_wing_open` is evidence of use.
- `history_truncated` on the control projection means clean-lap inference is
  uncertain for that view. Surface it.
- `projection.status` of anything other than `current` or `replay` means
  strategy reasoning is withheld for that cursor. The frame still carries its
  identity, but its situations are not published.

## 7. Timing formats

Values arrive as numeric seconds. Format them to F1 convention on the client:

| Value | Format | Example |
| --- | --- | --- |
| Lap time | `m:ss.SSS` | `1:32.481` |
| Gap / interval | `+s.SSS` | `+1.284` |
| Pit stop | `s.SSs` | `2.31s` |

Never display raw milliseconds where a timing convention is expected.

## 8. Optional AI commentary

Agent messages are an enhancement. When language generation is disabled,
unconfigured, over budget or failing, rooms publish deterministic wording and
every other feature is unaffected. A client must never gate timing, telemetry,
standings or events on message availability.

## 9. Deprecation

Within `v1`, a field being retired is documented here first and continues to be
served. Clients should not depend on field ordering, on the absence of a field,
or on `additionalProperties` being closed.

The machine-readable schema is served at `/openapi.json` and is the source of
truth for request and response shapes. Generate client models from it rather
than hand-transcribing this document.

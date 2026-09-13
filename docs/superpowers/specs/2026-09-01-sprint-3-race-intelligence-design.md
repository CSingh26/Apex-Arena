# ApexArena Sprint 3 Race Intelligence Design

**Date:** 2026-09-01  
**Branch:** `codex/sprint-3-intelligence`  
**Starting commit:** `d3fb4cb`  
**Target branch:** `sprints`

## Purpose

Sprint 3 adds a deterministic interpretation layer between normalized provider facts and the Race Room. It identifies position changes, defensible overtakes, meaningful race battles, qualifying cutoff changes, and event importance. The same structured outputs power live sessions, historical replay, Fan and Analyst presentation, the compact event feed, and grounded agent reactions.

The sprint does not implement strategy simulation, Race Rewind, championship prediction, or LLM-based event discovery.

## Verified Starting State

The repository already has the foundations Sprint 3 should extend:

- `NormalizedRaceEvent` and `RaceEventType` provide provider-neutral events with deduplication, event-time ordering, sequence numbers, importance, confidence, and replay identity.
- `normalized_race_events` persists normalized events, while `RaceEventProcessor` feeds both live MQTT and historical REST data through the same path.
- `RaceStateEngine` is a deterministic reducer with bounded race-control history and periodic persisted snapshots.
- `SessionCapabilities` represents timing, telemetry, location, weather, race control, pit, stint, and result availability without guessing.
- `SessionTimingState`, `DriverTimingState`, `DriverTelemetryState`, and `DriverLocationState` expose provider-neutral Race Room views.
- Redis Streams already publish typed session event and state streams. SSE exposes those streams to the frontend.
- Historical room replay applies persisted normalized events through `RaceStateEngine`; there is no separate browser-only replay model.
- Driver locations use their own time-indexed store and bounded browser windows. Classification remains timing-authoritative.
- `RoomExperience` owns the selected-driver state shared by the timing tower, map, and selected telemetry view.
- Agent discussion already links messages to trigger events and evidence, and has topic cooldowns.
- The isolated baseline passes 390 backend tests and 98 frontend tests.

The audit also found the primary Sprint 3 gaps:

- Position movement is currently calculated from two driver samples without a confirmation model or cause classification.
- Existing `OVERTAKE` events are normalized/provider-shaped or fixture-driven; there is no conservative ApexArena overtake inference engine.
- No battle lifecycle, trend history, intensity, train deduplication, or current-battle snapshot exists.
- Agent triggers can still be driven by low-level normalized events instead of only meaningful structured events.
- The Race Room has timing, map, telemetry, weather, and conversation, but no structured battle rail, event feed, recent-change summary, or Fan/Analyst mode.

The missing intelligence is a new subsystem, not a reason to replace the established session, replay, location, or selection architecture.

## Architectural Decision

Use a backend-owned deterministic intelligence pipeline within the existing normalized event flow:

```text
OpenF1 source data
    -> normalization and ordering
    -> persisted source-fact RaceEvent
    -> authoritative RaceState reduction
    -> deterministic intelligence coordinator
         -> position classification
         -> overtake confirmation
         -> battle lifecycle
         -> qualifying intelligence
         -> importance, cooldown, and deduplication
    -> persisted derived RaceEvents and resolved battle summaries
    -> existing Redis event/state streams
    -> SSE and lightweight session snapshots
    -> Race Room and grounded agent reactions
```

The coordinator guarantees this order for each source fact:

1. Apply the source fact to authoritative session state.
2. Evaluate intelligence using the updated state and bounded prior intelligence state.
3. Persist any derived events with global session sequence numbers.
4. Apply derived events to the session's recent-event view.
5. Publish the source fact, derived events, and enriched state in monotonic sequence order.
6. Offer only eligible important events to the discussion engine.

Derived events never re-enter the derivation step. This prevents recursion and duplicate inference. During replay, persisted derived events are consumed as facts from ApexArena and are not re-derived. A development/rebuild command can deliberately delete and regenerate derived intelligence from source facts using the same coordinator.

## Data Authority

Each fact has one declared authority:

| Concern | Authority | Use |
| --- | --- | --- |
| Classification and order | normalized timing/position facts | official positions and adjacency |
| Gaps and intervals | normalized interval facts | battle proximity and trend |
| Pit state | pit and stint facts | exclude pit-cycle overtakes |
| Driver status | timing, race control, and results | running, stopped, retired |
| Track location | location samples | supporting proximity evidence only |
| Race control | normalized race-control facts | flags, penalties, DRS restrictions |
| Overtake | ApexArena derivation | confirmed classification pass |
| Battle | ApexArena derivation | current meaningful contest |
| Agent prose | no authority | explanation of supplied structured facts |

Map geometry never changes classification. AI output never becomes an input to session state or intelligence.

## RaceEvent Model

`RaceEventType` is expanded to cover the controlled Sprint 3 vocabulary while retaining existing provider-sample types required by ingestion:

- Session: `SESSION_START`, `SESSION_PHASE_CHANGE`, `SESSION_END`, `LAP_COMPLETED`
- Position: `POSITION_GAIN`, `POSITION_LOSS`, `POSITION_CHANGE`, `OVERTAKE`
- Pit: `PIT_ENTRY`, `PIT_STOP`, `PIT_EXIT`
- Pace: `FASTEST_LAP`, `PERSONAL_BEST`
- Battle: `BATTLE_STARTED`, `BATTLE_INTENSIFIED`, `BATTLE_ENDED`, `DRS_RANGE_ENTERED`, `DRS_RANGE_EXITED`
- Control: `YELLOW_FLAG`, `RED_FLAG`, `SAFETY_CAR`, `VIRTUAL_SAFETY_CAR`, `PENALTY`, `INVESTIGATION`
- Status: `DRIVER_STOPPED`, `DRIVER_RETIRED`, `WEATHER_CHANGE`
- Qualifying: `QUALIFYING_CUTOFF_CHANGE`, `ELIMINATION_RISK`

Existing low-level types such as `POSITION_SAMPLE`, `INTERVAL_SAMPLE`, `CAR_DATA_SAMPLE`, and `LOCATION_SAMPLE` remain source facts and are not shown as meaningful Race Room events.

The normalized model gains explicit typed metadata:

```text
event_origin: SOURCE_FACT | DERIVED
primary_driver_number: int?
secondary_driver_number: int?
position_before: int?
position_after: int?
gap_seconds: float?
interval_seconds: float?
importance_level: LOW | NORMAL | IMPORTANT | MAJOR | CRITICAL
confidence_level: LOW | MEDIUM | HIGH
derivation: {
  algorithm: string,
  version: int,
  evidence: [{kind, event_id?, observed_at, value?}],
  exclusions_checked: [string]
}?
```

The existing numeric `importance` and `confidence` fields remain for thresholds and backward compatibility. Controlled levels are deterministic projections of those scores and are persisted for stable API presentation. `source`, `raw_event_id`, and `event_origin` make source facts and ApexArena derivations unambiguous. Evidence contains normalized references and small values, never copied provider payloads.

An Alembic migration adds the typed event columns and indexes needed for session/time, driver, event type, lap, and importance filtering. Existing rows receive conservative defaults based on whether `raw_event_id` is present and whether `source` identifies ApexArena.

## Intelligence State

The coordinator maintains one bounded `SessionIntelligenceState` per session:

```text
positions_by_driver
position_candidates
overtake_candidates
interval_history_by_driver
current_battles
recent_derived_events
cooldowns
qualifying_state
last_processed_sequence
```

It is protected by the existing per-session serialization boundary. Histories use deques with explicit maximum lengths. State needed after restart is stored in `RaceStateSnapshot.state`; it contains only current candidates, current battles, cooldown timestamps, and bounded recent samples.

All algorithms use event timestamps and sequence numbers, never wall-clock time, so historical reconstruction is deterministic.

## Configuration

Domain thresholds live in a single `RaceIntelligenceConfig` populated from validated settings. Initial defaults are deliberately conservative and will be checked against historical sessions:

| Setting | Initial default | Meaning |
| --- | ---: | --- |
| Battle start interval | 2.0 s | Maximum adjacent-driver interval for a candidate |
| Battle start samples | 3 | Sustained samples required to become active |
| Battle intense interval | 1.0 s | Approximate close-range threshold, not confirmed DRS eligibility |
| Battle end interval | 3.0 s | Hysteresis boundary for ending a battle |
| Battle end samples | 3 | Sustained separated samples required to resolve |
| Trend window | 5 samples | Bounded rolling interval trend |
| Trend minimum change | 0.15 s | Change required before declaring closing/falling back |
| Overtake confirmation | 2 samples and 2 s | New ordering persistence requirement |
| Position candidate expiry | 10 s | Reject stale asynchronous order changes |
| Proximity enter/exit | 1.0 s / 1.2 s | Hysteresis for within-one-second events |
| Repeated event cooldown | 20 s | Default informational-event cooldown |

These are domain defaults, not scattered literals. Tests can inject compact configurations without modifying application settings.

## Position State and Classification

Each driver position state records current observed position, previous confirmed position, start position, previous-lap position, status, pit transition, event timestamp, and source sequence. Updates older than the last accepted driver observation are ignored.

A single driver position sample does not immediately rewrite a confirmed classification relationship. The tracker forms a candidate order from all current driver observations, waits for the relevant adjacent updates or expiry window, and confirms only a coherent unique ordering. Duplicate positions, missing peers, and stale samples remain provisional.

Every confirmed movement emits a generic `POSITION_CHANGE` plus directional `POSITION_GAIN` or `POSITION_LOSS` semantics in its context. The change is classified as one of:

- `ON_TRACK_CANDIDATE`
- `PIT_CYCLE`
- `RETIREMENT_INHERITANCE`
- `PENALTY_OR_CLASSIFICATION`
- `TIMING_CORRECTION`
- `LAPPED_ORDERING`
- `UNKNOWN`

Batch cascades caused by a pit stop or retirement share one cause reference. They may update tower movement but do not generate a burst of overtakes. Large simultaneous reorderings without supporting context are treated as corrections or unknown changes and rendered without dramatic animation.

Qualifying and practice never route classification movement into race-overtake inference.

## Conservative Overtake Detection

An overtake candidate begins only when two adjacent running drivers reverse confirmed classification order during a Race or Sprint. Confirmation requires:

- persistent new ordering for the configured sample/time window;
- both drivers active and on track;
- no current pit entry, stop, or exit transition for either driver;
- no retirement/stopped transition explaining the change;
- no penalty/classification event explaining the change;
- no multi-driver correction cascade;
- timing proximity before the change when interval data is available;
- compatible lap/classification context that does not indicate lapping or unlapping.

Location progression is supporting evidence, not a requirement. A timing-only session can produce a medium- or high-confidence overtake when persistence, adjacency, interval, running status, and pit evidence are sufficient. Missing pit capability lowers confidence; if a position cascade or ambiguous context remains, inference is suppressed rather than labeled an overtake.

Confidence is assigned as follows:

- `HIGH`: persistent adjacent reversal, close timing, both running, and reliable pit/status exclusions; location may strengthen but is not required.
- `MEDIUM`: persistent adjacent reversal with strong timing evidence but one optional supporting source unavailable.
- `LOW`: unresolved exclusions or weak proximity. Low-confidence candidates are logged at debug level and never emitted as `OVERTAKE` UI events.

A confirmed overtake records the passed driver, previous and new positions, pre-change interval, battle duration when applicable, evidence references, exclusions checked, and `overtake_detector_v1`. A candidate that reverts, expires, or gains pit/retirement evidence is rejected without a public event.

## Battle Engine

Battle detection runs only for Race and Sprint sessions. Practice uses pace presentation, while Qualifying and Sprint Qualifying use cutoff intelligence.

### Start

The engine compares adjacent confirmed classification positions, making the work proportional to the number of drivers. A candidate requires both drivers running, neither in a pit transition, a usable interval at or below the configured start threshold, and sustained close samples. Location can increase confidence but cannot create a battle without timing.

### State

Each active battle has a stable ID, lead and chasing drivers, positions, current and closest interval, rolling trend, start/update timestamps, intensity, confidence, proximity/DRS wording, tyre context, lap context, and status:

```text
POTENTIAL -> ACTIVE -> INTENSE -> RESOLVED
```

`POTENTIAL` remains internal. Public snapshots contain active and intense battles plus recently resolved summaries.

### Trend

Trend uses bounded robust linear change across the recent interval window. Small movement inside the configured dead band is `STABLE`; a meaningful negative slope is `CLOSING`; a meaningful positive slope is `FALLING_BACK`. A single noisy sample cannot change the trend.

### Intensity

Intensity is an explainable score built from interval band, sustained closing trend, position significance, selected-driver relevance for ranking only, recent battle/overtake activity, tyre-age difference when available, and late-session context. It maps to `BUILDING`, `CLOSE`, or `INTENSE`. Selected-driver preference never changes the underlying intensity, only presentation ranking.

### DRS wording

The engine distinguishes `WITHIN_ONE_SECOND` from `CONFIRMED_DRS_ELIGIBLE`. The first is interval proximity. Confirmed eligibility is used only when session type, race-control/track status, activation context, and available provider facts support it. Otherwise UI copy says “within one second,” not “DRS available.” Enter/exit events use hysteresis and cooldowns.

### End

A battle resolves after a sustained interval above the end threshold, a pit transition, retirement/stopped status, session/control state that invalidates normal running, session end, or a confirmed pass that changes the pairing. A pass resolves the old relationship before evaluating the new adjacent order.

### Multi-car trains

The engine may track each adjacent edge in a close train internally. Presentation ranking builds connected train groups and ensures a driver appears in only one of the top cards. The strongest edge or the selected driver's edge represents the train, with a compact “three-car train” context when applicable. This avoids contradictory duplicate cards without introducing a graph-heavy public model.

Resolved battles are persisted as summaries, not micro-updates. A `battle_summaries` table stores deterministic ID/key, session, drivers, positions, start/end, closest interval, peak intensity, outcome, and compact context. Current battle state remains in the latest session snapshot and Redis state stream.

## Qualifying Intelligence

Qualifying and Sprint Qualifying have a separate deterministic state machine based on normalized phase (`Q1`, `Q2`, `Q3`), active participants, timing positions, personal bests, session best, and remaining time when available.

Cutoff size is derived from field size with ten Q3 places and an even split of earlier eliminations, then stored in phase state. This supports both 20- and 22-car fields without scattering fixed P15/P16 assumptions. Provider-supplied phase/result facts take precedence where available.

The engine emits:

- `SESSION_PHASE_CHANGE` for Q1/Q2/Q3 transitions;
- `QUALIFYING_CUTOFF_CHANGE` when a confirmed timing update moves a driver into or out of the current drop zone;
- `ELIMINATION_RISK` for a driver in or near the drop zone late in a phase, subject to cooldown;
- `PERSONAL_BEST` and `FASTEST_LAP` from valid completed laps;
- provisional-pole changes as structured qualifying context, not race overtakes.

“Safe” is not emitted while meaningful running remains. Practice sessions do not create race battles from track proximity.

## Importance, Deduplication, and Cooldowns

Importance is deterministic and separate from prose:

- `LOW`: raw/routine samples and ordinary laps; never agent-eligible.
- `NORMAL`: personal bests and routine pit context.
- `IMPORTANT`: battle within one second, meaningful cutoff movement, or confirmed overtake.
- `MAJOR`: lead change, high-value overtake, or major session turning point.
- `CRITICAL`: red flag and similarly session-defining control events.

Event deduplication continues to use deterministic keys. Stateful events additionally use hysteresis, persistence, and per-session/per-driver cooldown keys. Critical events and genuinely new overtakes are never suppressed by informational cooldowns.

Structured debug logs cover candidate creation, confirmation/rejection reason, battle state transition, importance assignment, and suppression reason. Per-sample interval logs remain debug-only.

## API, Persistence, and Streaming

The design extends current APIs instead of creating parallel data paths:

- `GET /api/v1/sessions/{session_key}/state` includes lightweight `current_battles`, `recent_events`, and qualifying intelligence.
- The existing session timing response remains authoritative and gains only normalized selected-driver context fields that cannot be correctly recomputed in the browser.
- The existing events endpoint gains filters for event type/category, driver, lap, importance, event origin, time cursor, and bounded limit.
- The room detail/bootstrap includes a lightweight initial intelligence response so the UI never waits for the first SSE state event. It contains current battles and at most five recent meaningful events, never full history.

No new Redis channels are added. Derived `race_event` records use the existing session event stream; current battle updates travel in the existing state stream. Complete event history is fetched with bounded REST pagination, not repeated in every SSE snapshot.

Database changes are made through Alembic. The event model migration and battle summary migration are reversible and preserve existing source facts.

## Agent Integration

The discussion engine receives an eligibility envelope produced after importance filtering. Routine telemetry, position samples, interval samples, location samples, and internal battle candidates never trigger agents.

Eligible context is concise and structured:

```text
event identity and type
session type, phase, lap, and timestamp
primary and secondary driver facts
confirmed positions
interval and trend
tyre/stint facts when available
battle duration/intensity when applicable
importance and confidence
normalized evidence references
```

Prompts explicitly separate authoritative facts from optional interpretation. Deterministic tests assert the envelope contents and absence of unsupported fields, not exact generated prose. Agents may disagree on strategy or interpretation but receive one authoritative classification state.

## Race Room Experience

The existing selected-driver state remains the only driver selection source. Battle card driver actions call the same selection handler used by timing and map markers.

### Mode control

The Race Room adds an accessible compact segmented control for `FAN` and `ANALYST`. The preference is stored in local storage and defaults to Fan for a new user. Switching modes changes presentation only and does not refetch timing, locations, telemetry, battles, or events.

### Fan mode

Fan mode prioritizes:

- timing positions, names, gaps, tyres, and restrained position movement;
- the selected driver's ahead/behind and battle meaning;
- one prominent selected-driver battle or highest-ranked battle, with up to three compact battles total;
- compact circuit map with subtle battle-marker emphasis;
- three to five recent meaningful events and a deterministic “What changed?” summary;
- agent conversation after structured race context.

Raw RPM, throttle/brake detail, detailed sectors, provider metadata, and excessive precision move behind disclosure or analyst presentation. Critical session and control information remains visible.

### Analyst mode

Analyst mode uses the same normalized data and adds interval trend values, recent lap comparison, richer tyre/stint context, telemetry, sectors where available, and a concise battle-evidence disclosure. Raw JSON and provider debug payloads are never exposed.

### Battle cards

Each card presents battle position, two driver identities, current interval, textual trend, tyre compound/age when available, intensity, and defensible proximity wording. Cards are keyboard-operable buttons/sections with stable dimensions. Status is textual as well as visual. Motion occurs only on start, intensification, confirmed pass, and confirmed position transition, and is disabled by `prefers-reduced-motion`.

### Map and selected-driver context

Active battle participants receive a subtle marker ring or emphasis. No large connector crosses the circuit. Timing remains authoritative if location is delayed or unavailable.

The selected-driver panel receives backend-normalized context:

```text
Ahead: driver and interval
Behind: driver and interval
Status: CLOSING | UNDER_PRESSURE | BATTLING | CLEAR_AIR | UNAVAILABLE
```

### Event feed and recent changes

The event feed is separate from conversation and defaults to meaningful events. Filters are `ALL`, `BATTLES`, `PITS`, `RACE CONTROL`, and `MY DRIVER`; pace events remain available inside `ALL` to avoid a sixth permanent tab. Feed entries use lap/phase, concise deterministic wording, and event semantics. AI messages may discuss the same development but are not copied into the feed.

“What changed?” shows the last three to five meaningful structured events. Initial wording is deterministic; an optional AI summary can be layered later without changing facts.

### Responsive hierarchy

Desktop keeps timing, map, and selected-driver context as the command surface, followed by the compact battle rail, important events, and conversation. On mobile, Fan mode orders selected-driver context, top battle, timing, compact map, events, and conversation. Analyst-only detail uses disclosures or tabs instead of horizontal timing-table panning.

The current oversized command-center implementation is split into focused timing, selected-driver, battle, event-feed, and mode components while preserving existing CSS variables and visual language. Cards use restrained borders and radii; there are no nested card stacks, glowing panels, or continuous interval animations.

## Failure and Recovery Behavior

- Missing interval data disables battle start rather than inferring from map dots.
- Missing locations only removes location evidence and map emphasis; timing-based battles can continue.
- Missing pit capability lowers overtake confidence and suppresses ambiguous cascades.
- Missing tyre/stint data omits tyre context without weakening a timing-supported battle.
- Stale or out-of-order source events cannot rewind confirmed intelligence state.
- Redis publication failure does not erase persisted events; existing explicit failure behavior remains.
- Intelligence state reconstructs deterministically from source facts or the latest snapshot after restart.
- Low-confidence inferred overtakes remain internal diagnostics and are not shown or offered to agents.

## Testing Strategy

All behavior changes follow test-driven development. New production behavior begins with a failing focused test.

Backend unit and integration coverage includes:

- position gain/loss, asynchronous updates, duplicates, stale events, corrections, pit cycles, retirements, penalties, and lapped-order ambiguity;
- confirmed overtake, reverted order, missing location, missing pit context, and evidence/confidence retention;
- battle start persistence, trend dead band, closing/falling back, intensity, pit/retirement/session end, pass reassignment, train deduplication, and selected-driver ranking;
- Q1/Q2/Q3 phase changes, dynamic cutoff, drop-zone entry/exit, provisional pole, elimination risk, and no qualifying overtakes;
- importance, critical-event bypass, hysteresis, cooldown, deterministic replay, repository filtering, migration upgrade/downgrade, and Redis/SSE ordering;
- agent eligibility and structured grounding without exact-prose assertions.

Frontend coverage includes:

- battle card meaning, selection synchronization, keyboard behavior, train context, and missing-data fallbacks;
- Fan/Analyst visibility, local preference, no mode-triggered refetch, and retained driver/battle state;
- event filtering, “What changed?”, selected-driver context, map highlighting, and screen-reader text;
- mobile viewport behavior and reduced motion.

The complete existing backend and frontend suites remain required. Final verification also runs Ruff, ESLint, TypeScript checking, production Next.js build, Alembic migration upgrade, and targeted Playwright Race Room checks.

## Historical and Manual Validation

Validation uses persisted provider data, not synthetic browser state:

1. A completed Race with timing, intervals, pit, race-control, and location data. The expected first target is the stored 2026 Belgian Grand Prix race (`session_key=11334`) documented by Sprint 2, provided it remains available in the local database.
2. A completed Qualifying session with phase timing and results.
3. A completed Sprint when a session with sufficient timing is locally available.
4. A timing-capable session without locations to verify graceful degradation.

For the race, capture actual battle starts, closest intervals, trends, confirmed/rejected overtake candidates, pit exclusions, and final feed events. For qualifying, capture phase and cutoff events and verify zero race-style overtakes. Exact session keys and detected examples are recorded in the final report after querying the actual database.

Browser validation covers desktop and mobile Fan/Analyst modes, selection synchronization, battle-marker emphasis, event filters, layout stability, and accessibility. The final product check must answer yes to both questions from the brief: a casual fan can identify the important fight and understand it, and an experienced fan can inspect enough evidence in Analyst mode to understand the classification.

## Performance Targets

- Position and battle evaluation is `O(number of drivers)` per relevant timing update, using adjacent comparisons only.
- No algorithm scans full race history during live updates.
- Interval, candidate, cooldown, and recent-event histories are bounded.
- SSE snapshots contain current battles and a small recent-event window, not complete history.
- Mode switching performs no network request.
- A historical rebuild records events processed per second and peak in-memory intelligence state.
- Frontend render checks compare command-center update behavior before and after the feature and ensure interval ticks do not animate or remount the entire Race Room.

## Delivery Boundaries

Implementation proceeds in logical commits:

1. `feat(events): add deterministic race event intelligence`
2. `feat(battles): add battle and overtake detection`
3. `feat(race-room): add battle cards and event context`
4. `feat(qualifying): add cutoff and session-phase intelligence`
5. `feat(ui): add fan and analyst race-room modes`
6. `feat(ai): route agent commentary through race events`
7. `test: cover battle overtake and event intelligence`

Commit boundaries may combine tightly coupled test and implementation files, but each commit must pass its focused tests. Nothing is pushed. After all verification passes, `codex/sprint-3-intelligence` is merged into local `sprints`, the merged result is verified again, and the feature worktree is removed.

## Acceptance Summary

Sprint 3 is complete when source facts and derived events are distinguishable; position changes are cause-classified; only defensible persistent passes become overtakes; meaningful race battles start, trend, intensify, resolve, rank, and replay deterministically; qualifying uses cutoff semantics; the Race Room provides concise battle and event context in both Fan and Analyst modes; agents react only to eligible structured events; partial data degrades conservatively; and all automated, migration, build, performance, historical, mobile, and manual validation described above passes.

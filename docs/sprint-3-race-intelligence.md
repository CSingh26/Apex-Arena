# Sprint 3 Race Intelligence

## Status

Sprint 3 adds deterministic race meaning between normalized provider facts and the Race
Room. The same coordinator is used by live ingestion and historical reconstruction. AI
agents receive only eligible structured events after classification and importance
filtering; they do not infer overtakes or battles from raw telemetry.

## Delivered Architecture

- Typed `RaceEvent` metadata distinguishes `SOURCE_FACT` from `DERIVED` events and stores
  controlled importance, confidence, driver, position, interval, and derivation evidence.
- Source facts are scored at normalization time: routine timing remains `LOW`, pit stops are
  `NORMAL`, race-control facts are `IMPORTANT` or above, and red flags are `CRITICAL`. Existing
  source rows receive the same policy through migration `20260902_0013`.
- Historical source facts without a primary driver are backfilled from their first recorded driver
  through migration `20260902_0014`, preserving readable pit and race-control context.
- The position tracker confirms coherent ordering changes and classifies pit, retirement,
  penalty/classification, lapped-order, and timing-correction causes.
- Overtakes require a race-like session, an adjacent order reversal, usable interval
  evidence, running drivers, no recent pit transition, persistence for two seconds, and a
  later position sample involving one of the two drivers. A later check verifies that the
  passing driver remains ahead.
- The battle engine requires three close samples, keeps five interval samples for trend,
  uses 1.0/1.2 second proximity hysteresis, and ends battles after sustained separation,
  pit/status/session events, overtakes, or changed adjacency. It tracks at most one battle
  edge per chaser and persists compact resolved summaries.
- Qualifying uses separate Q1/Q2/Q3 cutoff, drop-zone, risk, best-lap, and provisional-pole
  semantics. Practice does not create race battles or overtakes.
- Session state, REST filters, the existing Redis streams, room bootstrap, and SSE carry the
  same normalized intelligence. No mode-specific API or new Redis channel was added.
- Fan mode emphasizes positions, selected-driver meaning, top battles, map context, and
  important events. Analyst mode exposes richer telemetry, trends, tyres, and evidence from
  the same data. The preference is stored locally and switching modes does not refetch.
- The compact event feed and agent discussion both consume persisted RaceEvents, but remain
  separate presentation surfaces.

## Historical Validation

The local PostgreSQL dataset contains one completed session:

| Field | Value |
| --- | --- |
| Session key | `11334` |
| Session | 2026 Belgium Race |
| Circuit | Spa-Francorchamps |
| Start | 2026-07-19 13:00 UTC |
| Drivers | 22 |
| Source facts | 6,739 |
| Location samples | 0 |

The imported facts were originally stored endpoint-by-endpoint: position facts occupied source
sequences 899-1341 and interval facts 1342-6341 even though their event timestamps overlap.
Historical reconstruction sorts source facts by event timestamp, then original source sequence
and ID. Its replacement transaction preserves source IDs and payloads while assigning one
canonical monotonic timeline that interleaves each derived event immediately after its triggering
source fact. The Race Room, REST pagination, and replay all consume that same timeline. A replay
room's session clock uses the timestamp of its applied event, and its event feed is bounded to
the active replay sequence so it cannot reveal later race outcomes. Live processing continues to
use its normal monotonic ingestion sequence.

The final read-only validation and explicit local rebuild produced:

| Metric | Result |
| --- | ---: |
| Derived events | 1,773 |
| Confirmed overtakes | 106 |
| High-confidence overtakes | 71 |
| Medium-confidence overtakes | 35 |
| Resolved battle summaries | 86 |
| Position changes | 363 |
| Position gains / losses | 194 / 204 |
| Battle started / intensified / ended | 148 / 187 / 173 |
| Within-one-second entered / exited | 258 / 140 |
| Rejected overtake candidates | 94 |
| Pit-transition exclusions | 8 |
| Reverted-order exclusions | 26 |
| Timing-correction exclusions | 21 |
| End-of-session candidate closures | 7 |
| Rebuild throughput | 442.59 source events/second |

The source-fact importance backfill for this session yields 6,537 `LOW` routine samples, 145
`NORMAL` facts (including 28 pit stops), 50 `IMPORTANT` race-control/session facts, six `MAJOR`
safety-car facts, and one `CRITICAL` red flag. This keeps the compact feed and agent eligibility
useful without elevating high-frequency timing data. Agent prompts accept persisted source facts
only at `IMPORTANT` or higher, while the compact feed can retain normal pit context.

Representative persisted events from that rebuild:

- Lap 5 at 13:14:27.754 UTC: Bottas passed Hadjar for P19, supported by a 1.255
  second prior interval; confidence `HIGH`, importance `IMPORTANT`.
- Lap 6 at 13:16:19.283 UTC: Norris passed Bortoleto for P9, supported by a 0.117
  second prior interval; confidence `HIGH`, importance `IMPORTANT`.
- Lap 5 at 13:15:55.766 UTC: Hamilton's battle with Piastri intensified at 0.885
  seconds; importance `IMPORTANT`.
- Lap 6 at 13:18:11.202 UTC: Leclerc's battle with Verstappen intensified at 0.980
  seconds; importance `IMPORTANT`.

Repeated rebuilds use source facts only, generate stable derived IDs/deduplication keys, and
atomically replace derived rows, canonical replay sequences, battle summaries, and stale session
snapshots only when `--replace-derived` is explicit. Dry-run validation never changes stored data.

## Bounded State And Performance

The real session reached these maxima while processing 6,739 source facts:

- 22 position states for 22 drivers;
- 21 tracked/current battle edges, the adjacent-pair ceiling for 22 classified drivers;
- five interval samples in any battle trend;
- 14 pending overtake candidates, bounded by the driver field;
- zero buffered derived events after each drain;
- zero current battles and zero pending overtakes after session finish.

The automated stress test also processes 100 update cycles across every adjacent pair in a
20-driver field. It asserts 20 position states, at most 19 battle edges, five history samples
per edge, no pending overtakes, and no retained derived-event history.

## Operations

Validate a session without writes:

```bash
cd backend
python -m app.cli.validate_race_intelligence --session-key 11334 --json
```

Replace derived intelligence explicitly:

```bash
cd backend
python -m app.cli.rebuild_intelligence \
  --session-key 11334 \
  --replace-derived \
  --json
```

Both commands open PostgreSQL repositories only; they do not start OpenF1, Redis, live
ingestion, or agent services.

## Known Limitations

- The available local database has no Qualifying or Sprint session, so those state machines
  are covered by deterministic unit/integration streams rather than a local historical run.
- Session `11334` has no stored location samples. Location remains optional supporting
  evidence and never changes classification authority.
- OpenF1 pit rows do not provide separate normalized entry and exit events in this dataset.
  A recent pit fact excludes a candidate before confirmation, but a pit record received only
  after confirmation cannot retroactively retract an emitted event.
- Medium-confidence overtakes remain visibly labeled and can be filtered by agent eligibility;
  low-confidence overtake inference is suppressed entirely.
- The battle model intentionally represents adjacent pairs and collapses trains for card
  presentation. It is not a general multi-car graph or strategy model.

## Sprint 4 Readiness

Persisted, ordered, filterable RaceEvents and resolved battle summaries are ready to support
Race Story, What Just Happened, AI Commentary V2, Ask Apex, and deterministic session
summarization. Broader historical calibration should add at least one qualifying session,
one sprint, and one race with synchronized location plus explicit pit entry/exit data before
using the event stream for comparative analytics across weekends.

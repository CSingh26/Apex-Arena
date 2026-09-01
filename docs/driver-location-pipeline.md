# Driver track positions: root cause and pipeline

Why the circuit map was empty, what was actually broken, and how the location
pipeline works now.

## Summary

The map was not a rendering problem. Four independent defects sat between the
OpenF1 `/location` endpoint and the browser, and each one alone was enough to
leave the map blank or wrong.

| # | Defect | Layer |
|---|--------|-------|
| 1 | The whole location series was collapsed to one fix per car, written at the tail of the replay sequence | Ingestion |
| 2 | The map re-derived its own coordinate space every frame from whichever cars it had | Frontend |
| 3 | Location capability was reported from the coarse telemetry grade, never from real data | API |
| 4 | The replay had no monotonic session clock to select positions against | Replay |

A fifth section documents a provider constraint that is not a defect in the
shipped code but blocks any attempt to fetch the series: OpenF1 rejects
inclusive date filters.

---

## 1. Ingestion collapsed the series to a single frame

`location` was listed in the historical backfill's `deep_telemetry` stage and
went through the same normalize-into-`normalized_race_events` path as timing.
OpenF1 publishes roughly **four fixes per car per second** — a race is ~470,000
rows — so the per-endpoint record cap reduced it to one `LOCATION_SAMPLE` per
driver.

Observed for the 2026 Belgian GP (`session_key=11334`) before the fix:

```
 event_type      | count | first               | last                | minseq | maxseq
 LOCATION_SAMPLE |    22 | 2026-07-19 14:32:20 | 2026-07-19 14:32:20 |   6762 |   6783
```

Twenty-two rows, all stamped at the same instant — the closing seconds of the
session — occupying the last 22 slots of a 6,783-event replay sequence.

The map could therefore only ever show cars **after the entire replay had
finished playing**, frozen at their final positions. Nothing would move,
because a single fix per car is not a trajectory.

**Fix.** Location no longer enters the event sequence at all
([`historical.py`](../backend/app/services/historical.py)). It has its own
time-indexed store and its own ingestion path:

- `session_location_samples` — `(session_key, driver_number, sample_time, x, y, z)`
  with a unique constraint on the first three and an index on
  `(session_key, sample_time)`. Migration
  [`20260810_0011`](../backend/migrations/versions/20260810_0011_session_location_samples.py).
- `session_track_geometry` — the derived circuit outline and bounds per session.
- [`LocationIngestionService`](../backend/app/services/locations.py) walks the session
  in bounded provider windows, downsamples to one fix per driver per second
  (configurable; enough for a map that interpolates), and inserts idempotently.

Result for the same session: **108,178 samples across 23 cars**, continuous
1 Hz coverage with a maximum per-driver gap of 2 seconds.

## 2. The frontend invented a coordinate space per frame

`live-command-center.tsx` contained:

```ts
const minX = Math.min(...raw.map((p) => p.x));   // over the visible cars only
const maxX = Math.max(...raw.map((p) => p.x));
…
x: 12 + ((point.x - minX) / width) * 76,
```

Four consequences:

- **The scale changed every frame.** Bounds came from the cars currently
  plotted, so as the field spread out or bunched up the entire map rescaled
  underneath the markers. Nothing held still.
- **Fewer than two cars rendered nothing** (`if (raw.length < 2) return []`).
- **There was no circuit.** The backdrop was a rounded rectangle. Markers were
  placed in a normalized 12–88 box with no relationship to any track.
- **Y was never inverted.** Provider Y increases away from the viewer; SVG Y
  increases down the screen, so the layout was mirrored vertically.

**Fix.** One transform, used by the outline and every marker
([`track-projection.ts`](../frontend/src/lib/track-projection.ts)):
uniform scale on both axes from **fixed session bounds**, Y inverted exactly
once, centred with symmetric padding. Nothing downstream may apply its own
offset or scale — a per-circuit fudge would silently decouple the cars from
the track.

The circuit outline is traced from the drivers' own samples, so it is
guaranteed to live in the same coordinate space as the markers. Deriving it
turned out to need care: picking a car at an arbitrary time offset works for a
race but fails for practice and qualifying, where cars sit stationary in the
garage — the first attempt produced a **two-point** "circuit" for Belgian
qualifying. [`find_lap_window`](../backend/app/services/locations.py) instead searches
the stored series for a stretch where a driver actually closes a lap, then
re-fetches that exact range at native resolution and simplifies it
(Ramer–Douglas–Peucker, ~100 points for Spa).

## 3. Capability detection never looked at the data

```python
location=historical_detail,   # derived from source_availability
```

`historical_detail` is a coarse grade for the whole session. A timing-only room
advertised location as `partial` with zero samples stored; a telemetry-graded
room advertised `available` with zero samples. The flag carried no information
about locations.

**Fix.** [`capabilities_for`](../backend/app/services/rooms.py) takes the real stored
sample count and reports `available` / `unavailable` from it, or `unknown` when
the count genuinely cannot be read — never a guess derived from session type or
telemetry grade.

## 4. The replay had no session clock

Selecting "where was every car at time T" needs a T. The obvious candidate —
the event time of the most recently applied state — does not work here.

Persisted events are sequenced **per provider endpoint**, because the backfill
ingests one endpoint at a time for checkpoint/resume. So the sequence walks all
laps, then all positions, then all intervals, and the event time sawtooths
across the session instead of advancing. Rows from endpoints with no `date`
field (`/drivers`, `/stints`, `/session_result`) fall back to ingest time,
landing a full day past the session.

Observed live in the browser: the clock jumped from `2026-07-19T14:23` to
`2026-07-20T05:10`, and the map dutifully requested sample windows in a range
where no session existed.

**Fix.** [`RoomPlaybackState.session_clock`](../backend/app/domain/rooms.py) — replay
progress through the sequence mapped onto the session's own recorded time span.
It is monotonic with playback, freezes when playback pauses, and moves
backwards only on a deliberate seek. Null when a session has no recorded span,
in which case the map falls back to the reduced state's own time (correct for
live sessions, where event time *is* session time).

Re-architecting the sequence to be globally time-ordered would fix the
underlying oddity, but it changes resume semantics and invalidates every
sequence-keyed room message — out of scope here.

## 5. Provider constraint: OpenF1 rejects inclusive date filters

Not a defect in the shipped code — the old pipeline never attempted a windowed
fetch — but it blocks anyone who tries. The obvious implementation uses the
filter syntax the rest of the codebase assumes:

```
GET /v1/location?session_key=11334&date>=…&date<…   → 500
```

Every window failed. Probing each operator against the live API:

| filter | status |
|--------|--------|
| `date>` | 200 |
| `date<` | 200 |
| `date>=` | 404 |
| `date<=` | 404 |

OpenF1 implements only the **strict** comparisons; paired with `date<` the
inclusive form escalates to a 500. The timestamp must also be whole seconds
with no offset — the parser rejects both microseconds and a `+00:00` suffix.

**Fix.** [`_date_window`](../backend/app/services/locations.py) emits `date>`/`date<`
with second-precision naive UTC, and consecutive windows overlap by one second
so the strict bounds cannot drop a boundary sample. Re-fetched rows are
absorbed by the unique constraint.

The provider also returns intermittent 500s on high-frequency windows even
after the client's own retries (13 of 60 windows on one run). `ingest_session`
sweeps the failures again rather than accepting the first answer.

---

## How it fits together now

```
OpenF1 /location  ──► LocationIngestionService ──► session_location_samples
   (4 Hz/car)          windowed, retried,           (1 Hz/car, time-indexed)
                       downsampled                           │
                                                             ├──► session_track_geometry
OpenF1 MQTT ────► LiveLocationRecorder ─────────────────────►┘   (outline + bounds)
   (live)         (pipeline consumer)                        │
                                                             ▼
                                        GET /sessions/{key}/track      → outline, bounds
                                        GET /sessions/{key}/locations  → latest per driver at a clock
                                        GET …/locations/samples        → windowed series
                                                             │
                                                             ▼
                                   useDriverLocations ──► selectLocationsAt ──► projectPoint
                                   (30 s windows,          (at-or-before,        (one shared
                                    smoothed clock)         interpolated)         transform)
```

Live and replay converge on the same store and the same selection code, so the
map has one behaviour to reason about rather than two.

### Selection semantics

Positions are chosen **at or before** the clock, per driver — never by exact
timestamp match. Providers do not sample all cars on a shared clock, so exact
matching returns nothing. A car whose feed goes quiet holds its last position
and is marked stale rather than disappearing.

Between two fixes the marker interpolates, unless the pair is too far apart in
time (the feed stopped) or implies more than 200 m/s (a pit/garage transition
or a replay seek, not motion). Those snap.

### Operating it

```bash
python -m app.cli.backfill_locations --room-slug 2026-belgian-grand-prix-race
```

`--reset` drops the stored series first; `--rebuild-geometry-only` re-derives
just the outline; `--max-minutes` bounds a trial run.

Diagnostics are opt-in in the browser via `?debug=location` (add
`?debug=location-raw` to plot un-interpolated fixes alongside the animated
markers, which separates a projection bug from a motion bug).

### Failure behaviour

Location is an enhancement layer. The live recorder swallows and logs store
failures so timing, race control and the room conversation are never taken down
by the map; the API returns 503 for a genuine store outage and the map shows an
error state while the rest of the room keeps working; a session with no
provider positions says so plainly instead of rendering an empty grid.

---

## Verification

Against the real 2026 Belgian Grand Prix race (`session_key=11334`), no
synthetic data at any layer.

**Data.** 108,178 stored samples across 23 cars spanning
`12:59:59Z → 14:32:20Z`. Continuous coverage: maximum per-driver gap **2 s**,
zero gaps over 5 s. The derived outline is 104 points and is recognisably Spa —
La Source, Eau Rouge/Raidillon, the Kemmel straight, Les Combes, Pouhon,
Stavelot and the Bus Stop are all identifiable.

**In the browser** (`?debug=location`):

| Check | Result |
|-------|--------|
| Cars at playback start | 22 markers bunched on the pit straight, lap 1 — a starting grid, not a scatter |
| Cars during replay | 20 of 22 move per tick, median 53 SVG units per 3 s; 2 stationary cars are retired |
| Markers vs. outline | Every marker sits on the track line; both come from `projectPoint` |
| Pause | Clock freezes; 0 of 22 markers move over 3.5 s |
| Resume | Clock advances; 20 of 22 move again |
| Seek to lap 30 | Clock jumps `13:01:11 → 13:08:29`, all markers reposition, field spreads from bunched to strung out through Eau Rouge |
| Tower → map | Selecting Leclerc in the timing tower selects his marker and the telemetry panel |
| Map → tower | Clicking Verstappen's marker highlights his tower row and switches the telemetry panel |
| Capability, race | `location: available` (108,178 samples) |
| Capability, empty session | `location` not claimed; `/track` and `/locations` return `available: false` |

**Non-race sessions.** Belgian qualifying (`11330`, 66,329 samples, 22 cars)
derives a 100-point outline — the case that produced a two-point "circuit"
before `find_lap_window`, because the mid-session sample happened to catch a
car parked in the garage.

**Cross-session agreement.** The race and qualifying outlines are derived
independently, from different cars on different days, and land on top of each
other. Their bounds agree to within ~60 provider units (6 m) across a 20 km
extent:

```
11334 (race)        X -4333 → 8312   Y -15770 → 4545
11330 (qualifying)  X -4336 → 8299   Y -15769 → 4484
```

That is the strongest available evidence that the coordinate frame is stable
per circuit and that the derivation is reading it correctly, rather than
fitting a shape to whatever it happened to receive.

**Suites.** 390 backend tests, 98 frontend tests, `ruff check`/`format`,
`tsc --noEmit`, `eslint`, and `next build` all clean.

# Sprint 3 Backend Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic position, overtake, battle, qualifying, importance, persistence, API, and streaming intelligence to the existing normalized session pipeline.

**Architecture:** Source facts continue through `RaceEventProcessor` and `RaceStateEngine`. A `RaceIntelligenceCoordinator` consumes the updated state, persists non-recursive derived events and resolved battle summaries, and exposes bounded current intelligence through the existing state and Redis/SSE streams.

**Tech Stack:** Python 3.12+, Pydantic 2, SQLAlchemy 2, Alembic, FastAPI, Redis Streams, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-01-sprint-3-race-intelligence-design.md`

## Global Constraints

- Classification comes only from normalized timing state; location is supporting evidence.
- All state transitions use event timestamps and sequence numbers, never wall-clock time.
- Histories are bounded and update work is `O(number of drivers)`.
- Low-confidence overtakes are diagnostics only.
- Race/Sprint use battle semantics; Qualifying uses cutoff semantics; Practice creates neither race battles nor overtakes.
- Existing live and historical ingestion remain one normalized path.
- No new Redis channels and no full event history in state snapshots.
- Every production behavior starts with a failing test.

---

### Task 1: Typed Race Events and Persistence

**Files:**
- Modify: `backend/app/domain/models.py`
- Modify: `backend/app/storage/models.py`
- Modify: `backend/app/storage/repositories.py`
- Create: `backend/migrations/versions/20260901_0012_race_intelligence.py`
- Test: `backend/tests/test_event_intelligence_models.py`
- Test: `backend/tests/test_event_pipeline.py`

**Interfaces:**
- Produces `EventOrigin`, `EventImportance`, `EventConfidence`, `EventDerivation`, and expanded `NormalizedRaceEvent` fields.
- Extends `SqlNormalizedEventRepository.list_for_session(..., event_types, driver_number, minimum_importance, event_origin, before_time)` without changing current defaults.

- [ ] **Step 1: Write failing domain tests** proving a derived `OVERTAKE` requires `event_origin=DERIVED`, accepts typed evidence, exposes primary/secondary driver and positions, and rejects scores outside 0..1.

```python
event = NormalizedRaceEvent(
    session_key="11334", source="apexarena", event_origin=EventOrigin.DERIVED,
    event_time=now, received_at=now, event_type=RaceEventType.OVERTAKE,
    primary_driver_number=4, secondary_driver_number=16,
    position_before=5, position_after=4,
    importance=0.8, importance_level=EventImportance.IMPORTANT,
    confidence=0.9, confidence_level=EventConfidence.HIGH,
    derivation=EventDerivation(algorithm="overtake_detector_v1", version=1),
    dedup_key="overtake:11334:4:16:38",
)
assert event.driver_numbers == [4, 16]
```

- [ ] **Step 2: Run `pytest tests/test_event_intelligence_models.py -q`** and confirm missing types/fields fail.
- [ ] **Step 3: Add enums, typed derivation/evidence models, event fields, controlled defaults, and all Sprint 3 event types** while retaining source-sample event types.
- [ ] **Step 4: Add migration columns/indexes and repository mappings/filter predicates**; backfill origin from `raw_event_id/source` and levels from existing scores.
- [ ] **Step 5: Run focused model, repository, pipeline, and migration tests**, then Ruff these files.
- [ ] **Step 6: Commit** with `feat(events): add typed race event intelligence`.

### Task 2: Position Tracker and Change Classifier

**Files:**
- Create: `backend/app/domain/intelligence.py`
- Create: `backend/app/services/position_intelligence.py`
- Test: `backend/tests/test_position_intelligence.py`

**Interfaces:**
- Produces `PositionState`, `PositionChange`, `PositionChangeCause`, `PositionTracker.apply(event, state) -> list[PositionChange]`.
- Consumes `NormalizedRaceEvent`, `RaceState`, session capabilities, and injected `RaceIntelligenceConfig`.

- [ ] **Step 1: Write failing tests** for confirmed gain/loss, one-sample provisional movement, duplicate update, stale update, coherent asynchronous pair swap, pit cascade, retirement inheritance, penalty/classification change, correction cascade, and ambiguous lapped ordering.

```python
changes = tracker.apply(position_event(driver=4, position=4, at=t2), race_state)
assert changes == []
changes = tracker.apply(position_event(driver=16, position=5, at=t2), race_state)
assert changes[0].cause is PositionChangeCause.ON_TRACK_CANDIDATE
assert changes[0].primary_driver_number == 4
```

- [ ] **Step 2: Run `pytest tests/test_position_intelligence.py -q`** and confirm import/API failures.
- [ ] **Step 3: Implement bounded per-driver observations, coherent order confirmation, expiry, cause classification, and batch cause references.**
- [ ] **Step 4: Add deterministic conversion from changes to `POSITION_CHANGE`, `POSITION_GAIN`, and `POSITION_LOSS` events** with evidence references and dedup keys.
- [ ] **Step 5: Run focused tests and `ruff check app/domain/intelligence.py app/services/position_intelligence.py tests/test_position_intelligence.py`.**
- [ ] **Step 6: Commit** with `feat(events): classify confirmed position changes`.

### Task 3: Conservative Overtake Detection

**Files:**
- Create: `backend/app/services/overtake_intelligence.py`
- Modify: `backend/app/domain/intelligence.py`
- Test: `backend/tests/test_overtake_intelligence.py`

**Interfaces:**
- Produces `OvertakeCandidate` and `OvertakeDetector.apply(change, context) -> NormalizedRaceEvent | None`.
- Consumes confirmed `PositionChange`, bounded interval history, driver/pit/status context, and session capabilities.

- [ ] **Step 1: Write failing scenario tests** for a persistent adjacent pass, reversal that reverts, pit reshuffle, retirement reshuffle, penalty change, stale candidate, missing location, missing pit context, correction cascade, and lapped ambiguity.

```python
assert detector.apply(swap, context(at=t0)) is None
confirmed = detector.apply(swap, context(at=t0 + timedelta(seconds=2), persisted=True))
assert confirmed is not None
assert confirmed.event_type is RaceEventType.OVERTAKE
assert confirmed.confidence_level is EventConfidence.HIGH
```

- [ ] **Step 2: Run the focused test file** and confirm it fails because the detector is absent.
- [ ] **Step 3: Implement candidate persistence, exclusions, confidence grading, rejection reasons, and `overtake_detector_v1` evidence.**
- [ ] **Step 4: Verify only medium/high candidates emit events and every rejected case records a debug reason without a public event.**
- [ ] **Step 5: Run position and overtake suites together plus Ruff.**
- [ ] **Step 6: Commit** with `feat(battles): add conservative overtake detection`.

### Task 4: Deterministic Battle Engine

**Files:**
- Create: `backend/app/services/battle_intelligence.py`
- Modify: `backend/app/domain/intelligence.py`
- Modify: `backend/app/core/settings.py`
- Test: `backend/tests/test_battle_intelligence.py`
- Test: `backend/tests/test_settings.py`

**Interfaces:**
- Produces `BattleState`, `BattleStatus`, `BattleTrend`, `BattleIntensity`, `BattleUpdate`, and `BattleEngine.apply(event, race_state) -> list[BattleUpdate]`.
- Produces `rank_battles(battles, selected_driver) -> list[BattleState]` with train deduplication.

- [ ] **Step 1: Write failing tests** for sustained start, noisy non-start, closing/stable/falling trend, intensification, one-second hysteresis, pit/retirement/session end, overtake reassignment, missing interval, Race/Sprint eligibility, Practice/Qualifying exclusion, train deduplication, and selected-driver ranking.

```python
for gap in (1.8, 1.7, 1.6):
    updates = engine.apply(interval_event(chaser=4, gap=gap), state)
assert updates[-1].event_type is RaceEventType.BATTLE_STARTED
assert updates[-1].battle.trend is BattleTrend.CLOSING
```

- [ ] **Step 2: Run focused tests** and verify expected missing-engine failures.
- [ ] **Step 3: Add validated centralized settings and an injectable `RaceIntelligenceConfig`.**
- [ ] **Step 4: Implement adjacent comparison, bounded robust trend, explainable intensity, lifecycle transitions, DRS-safe wording, and event cooldowns.**
- [ ] **Step 5: Implement connected-train presentation deduplication without changing internal adjacent battle state.**
- [ ] **Step 6: Run battle, settings, race-state, and session-realtime tests plus Ruff.**
- [ ] **Step 7: Commit** with `feat(battles): add deterministic battle engine`.

### Task 5: Qualifying and Event Importance Intelligence

**Files:**
- Create: `backend/app/services/qualifying_intelligence.py`
- Create: `backend/app/services/event_importance.py`
- Modify: `backend/app/domain/intelligence.py`
- Test: `backend/tests/test_qualifying_intelligence.py`
- Test: `backend/tests/test_event_importance.py`

**Interfaces:**
- Produces `QualifyingState`, `QualifyingInsight`, `QualifyingEngine.apply(event, race_state)`.
- Produces `EventImportancePolicy.classify(event, context) -> tuple[EventImportance, float, bool]` where the boolean is agent eligibility.

- [ ] **Step 1: Write failing qualifying tests** for Q1/Q2/Q3 transitions, dynamic 20/22-driver cutoff, drop-zone entry/exit, provisional pole, personal/session best, late-phase elimination risk cooldown, eliminated drivers, and zero overtakes.
- [ ] **Step 2: Write failing importance tests** for routine suppression, battle proximity, confirmed overtake, lead change, red-flag critical bypass, and cooldown deduplication.
- [ ] **Step 3: Run both test files** and confirm failures identify missing policies.
- [ ] **Step 4: Implement phase/cutoff state and deterministic qualifying events.**
- [ ] **Step 5: Implement importance scores/levels, eligibility, hysteresis, cooldown keys, and critical bypass.**
- [ ] **Step 6: Run focused tests and all discussion-trigger tests plus Ruff.**
- [ ] **Step 7: Commit** with `feat(qualifying): add cutoff and importance intelligence`.

### Task 6: Coordinator, Battle Summaries, and Replay Determinism

**Files:**
- Create: `backend/app/services/race_intelligence.py`
- Create: `backend/app/storage/intelligence_repository.py`
- Modify: `backend/app/storage/models.py`
- Modify: `backend/migrations/versions/20260901_0012_race_intelligence.py`
- Modify: `backend/app/services/event_pipeline.py`
- Modify: `backend/app/services/race_state.py`
- Modify: `backend/app/services/container.py`
- Modify: `backend/app/services/room_replay.py`
- Test: `backend/tests/test_race_intelligence.py`
- Test: `backend/tests/test_room_replay.py`

**Interfaces:**
- Produces `RaceIntelligenceCoordinator.process(source_event, race_state) -> IntelligenceResult`.
- Produces `IntelligenceResult(derived_events, current_battles, recent_events, qualifying)`.
- Adds `SqlBattleSummaryRepository.upsert_resolved(battle)` and snapshot serialization.

- [ ] **Step 1: Write failing orchestration tests** proving source-before-derived ordering, no recursive derivation, monotonic sequence allocation, idempotent reprocessing, bounded snapshot state, resolved summary upsert, and identical live/replay outputs.
- [ ] **Step 2: Run orchestration and replay tests** and confirm failures are due to missing coordinator behavior.
- [ ] **Step 3: Implement coordinator composition and a derived-event sink using the existing sequence/repository abstractions.**
- [ ] **Step 4: Extend `RaceState` with bounded current battles, recent meaningful events, and qualifying state; serialize them in snapshots.**
- [ ] **Step 5: Wire container consumer order and replay behavior so persisted derived events are consumed but never re-derived.**
- [ ] **Step 6: Add battle summary persistence and migration table/indexes.**
- [ ] **Step 7: Run event pipeline, race state, replay, persistence, and complete backend tests.**
- [ ] **Step 8: Commit** with `feat(events): coordinate deterministic session intelligence`.

### Task 7: Session APIs, Filters, SSE, and Rebuild Tooling

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/routes.py`
- Modify: `backend/app/api/room_schemas.py`
- Modify: `backend/app/api/room_routes.py`
- Modify: `backend/app/api/streaming.py`
- Create: `backend/app/cli/rebuild_intelligence.py`
- Test: `backend/tests/test_session_api.py`
- Test: `backend/tests/test_room_routes.py`
- Test: `backend/tests/test_streaming.py`
- Test: `backend/tests/test_intelligence_rebuild.py`

**Interfaces:**
- Extends session state and room bootstrap with current battles, qualifying state, and at most five recent meaningful events.
- Extends `GET /events` with bounded typed filters.
- Rebuild CLI consumes source facts only and regenerates deterministic derived intelligence.

- [ ] **Step 1: Write failing API tests** for bootstrap intelligence, event category/driver/lap/origin/importance/time filters, bounded limits, and SSE derived-event/state ordering.
- [ ] **Step 2: Write failing rebuild tests** for source-only input, deterministic output, dry-run, session scoping, and safe replacement of derived records/summaries.
- [ ] **Step 3: Run focused API/CLI tests** and confirm expected contract failures.
- [ ] **Step 4: Implement schemas, filters, bootstrap mapping, and stream serialization without adding Redis channels.**
- [ ] **Step 5: Implement the explicit rebuild command with `--session-key`, `--dry-run`, and `--replace-derived`.**
- [ ] **Step 6: Run complete backend tests, `ruff check .`, and migration upgrade/downgrade/upgrade against the local Postgres service.**
- [ ] **Step 7: Commit** with `feat(events): expose session intelligence APIs`.


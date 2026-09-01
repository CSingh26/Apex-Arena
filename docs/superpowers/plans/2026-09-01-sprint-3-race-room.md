# Sprint 3 Race Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present backend race intelligence through concise Battle Cards, selected-driver context, structured event history, map emphasis, and persistent Fan/Analyst Race Room modes.

**Architecture:** The Race Room consumes the same timing, telemetry, location, and enriched session state in both modes. New focused components receive normalized data from `LiveCommandCenter`; they never infer classification, intervals, or battles independently.

**Tech Stack:** Next.js, React 19, TypeScript, CSS Modules, Vitest, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-01-sprint-3-race-intelligence-design.md`

## Global Constraints

- Fan and Analyst modes use identical normalized API data and do not refetch on switch.
- `RoomExperience` remains the sole selected-driver owner.
- Timing is authoritative; map location is supporting context.
- Show at most three ranked battles and avoid duplicate drivers in train cards.
- No nested cards, raw JSON, continuous interval animation, or horizontal mobile timing-table panning.
- Every status has textual meaning and every interaction is keyboard accessible.
- Respect `prefers-reduced-motion`.
- Every production behavior starts with a failing test.

---

### Task 1: Frontend Intelligence Contracts and Reducers

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/room-state.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/race-intelligence.ts`
- Test: `frontend/src/lib/race-intelligence.test.ts`
- Test: `frontend/src/lib/room-state.test.ts`

**Interfaces:**
- Adds `RaceEvent`, `BattleState`, `QualifyingIntelligence`, `DriverBattleContext`, `SessionIntelligenceState`, and event filter types matching backend schemas.
- Produces `mergeSessionIntelligence`, `rankBattleCards`, `filterRaceEvents`, and `describeRecentChanges`.

- [ ] **Step 1: Write failing reducer tests** proving newer sequence wins, stale state is ignored, event IDs deduplicate, histories remain bounded, selected-driver battle ranks first, train duplicates collapse, and filters preserve chronology.
- [ ] **Step 2: Run `npm test -- --run src/lib/race-intelligence.test.ts src/lib/room-state.test.ts`** and confirm missing contract failures.
- [ ] **Step 3: Add exact backend-aligned types and pure bounded reducers/formatters.**
- [ ] **Step 4: Extend API helpers for filtered event pagination without adding mode-specific endpoints.**
- [ ] **Step 5: Run focused tests, ESLint, and TypeScript check.**
- [ ] **Step 6: Commit** with `feat(race-room): add race intelligence client state`.

### Task 2: Battle Rail and Selected-Driver Context

**Files:**
- Create: `frontend/src/components/race-rooms/battle-rail.tsx`
- Create: `frontend/src/components/race-rooms/battle-rail.module.css`
- Create: `frontend/src/components/race-rooms/selected-driver-context.tsx`
- Test: `frontend/src/components/race-rooms/battle-rail.test.tsx`
- Test: `frontend/src/components/race-rooms/selected-driver-context.test.tsx`

**Interfaces:**
- `BattleRail({battles, selectedDriver, mode, onSelectDriver})` renders one to three cards.
- `SelectedDriverContext({driverNumber, timing, context, mode})` renders ahead/behind and meaning.

- [ ] **Step 1: Write failing component tests** for positions, driver identities, interval, textual trend, tyres, within-one-second wording, selected-driver priority, click/keyboard selection, train context, missing-data omission, and analyst evidence disclosure.
- [ ] **Step 2: Run focused tests** and confirm missing component failures.
- [ ] **Step 3: Implement stable compact cards using buttons for driver selection and semantic headings/lists.**
- [ ] **Step 4: Implement selected-driver `CLOSING`, `UNDER PRESSURE`, `BATTLING`, `CLEAR AIR`, and `UNAVAILABLE` presentation from backend context only.**
- [ ] **Step 5: Add restrained start/intensity transitions and reduced-motion overrides.**
- [ ] **Step 6: Run focused tests, accessibility assertions, ESLint, and typecheck.**
- [ ] **Step 7: Commit** with `feat(race-room): add battle cards and driver context`.

### Task 3: Structured Event Feed and What Changed

**Files:**
- Create: `frontend/src/components/race-rooms/race-event-feed.tsx`
- Create: `frontend/src/components/race-rooms/race-event-feed.module.css`
- Test: `frontend/src/components/race-rooms/race-event-feed.test.tsx`

**Interfaces:**
- `RaceEventFeed({events, selectedDriver, onLoadMore, hasMore})` owns `ALL | BATTLES | PITS | RACE_CONTROL | MY_DRIVER` presentation filtering.
- `RecentChanges({events})` renders three to five deterministic statements.

- [ ] **Step 1: Write failing tests** for each filter, lap/phase labels, source-vs-derived semantics, selected-driver filtering, chronological density, pagination action, empty state, and deterministic recent-change wording.
- [ ] **Step 2: Run focused tests** and confirm missing component failures.
- [ ] **Step 3: Implement the compact feed with accessible pressed-state filter controls and no AI-message duplication.**
- [ ] **Step 4: Implement `RecentChanges` from structured events only.**
- [ ] **Step 5: Run focused tests, ESLint, and typecheck.**
- [ ] **Step 6: Commit** with `feat(race-room): add event feed and recent changes`.

### Task 4: Fan and Analyst Modes

**Files:**
- Create: `frontend/src/components/race-rooms/race-room-mode-toggle.tsx`
- Test: `frontend/src/components/race-rooms/race-room-mode-toggle.test.tsx`
- Modify: `frontend/src/components/race-rooms/live-command-center.tsx`
- Modify: `frontend/src/components/race-rooms/live-command-center.module.css`
- Modify: `frontend/src/components/race-rooms/live-command-center.test.tsx`

**Interfaces:**
- `useRaceRoomMode()` returns `{mode, setMode}` and persists `apex-arena-race-room-mode`.
- `LiveCommandCenter` composes timing, map, driver context, and battle rail from one state stream.

- [ ] **Step 1: Write failing mode tests** for Fan default, saved Analyst preference, accessible segmented control, and persistence.
- [ ] **Step 2: Extend command-center tests** proving mode changes issue no fetch/EventSource recreation, retain selection/battle state, hide raw telemetry in Fan, and expose richer timing/telemetry/evidence in Analyst.
- [ ] **Step 3: Run focused tests** and confirm contract failures.
- [ ] **Step 4: Implement mode hook/control and split unwieldy command-center sections into focused internal components without duplicating state.**
- [ ] **Step 5: Apply responsive hierarchy: mobile Fan context/battle/timing/map, desktop timing/map/context followed by battles.**
- [ ] **Step 6: Run focused tests, full frontend tests, ESLint, and typecheck.**
- [ ] **Step 7: Commit** with `feat(ui): add fan and analyst race-room modes`.

### Task 5: Map Battle Emphasis and Room Composition

**Files:**
- Modify: `frontend/src/components/race-rooms/circuit-map.tsx`
- Modify: `frontend/src/components/race-rooms/circuit-map.module.css`
- Modify: `frontend/src/components/race-rooms/circuit-map.test.tsx`
- Modify: `frontend/src/components/race-rooms/room-experience.tsx`
- Modify: `frontend/src/components/race-rooms/race-rooms-revamp.module.css`
- Modify: `frontend/src/test/race-room-fixtures.ts`
- Test: `frontend/src/components/race-rooms/room-experience.test.tsx`

**Interfaces:**
- `CircuitMap` accepts `battleDrivers: Set<number>` and marks participants without changing coordinates.
- `RoomExperience` supplies bootstrap intelligence, owns event pagination, and preserves one selected-driver state.

- [ ] **Step 1: Write failing map tests** for subtle participant emphasis, selected-driver precedence, textual equivalent, missing locations, and no coordinate/classification mutation.
- [ ] **Step 2: Write failing room-composition tests** for bootstrap intelligence before SSE, event feed placement separate from conversation, selected-driver synchronization, and reconnect preservation.
- [ ] **Step 3: Run focused tests** and confirm expected failures.
- [ ] **Step 4: Implement map ring/emphasis and screen-reader battle summary without connector lines.**
- [ ] **Step 5: Integrate battle rail, recent changes, and event feed into the Race Room hierarchy.**
- [ ] **Step 6: Run full frontend tests, lint, typecheck, and production build.**
- [ ] **Step 7: Commit** with `feat(race-room): synchronize battles map and events`.

### Task 6: Responsive and Browser Validation

**Files:**
- Modify: `frontend/e2e/race-rooms.spec.ts`
- Modify: `frontend/src/components/race-rooms/*.module.css` only where screenshots reveal defects

**Interfaces:**
- Adds Playwright coverage for Fan/Analyst desktop and mobile behavior against deterministic room fixtures or local replay.

- [ ] **Step 1: Add failing Playwright assertions** for mode control, top battle, driver selection from a card, map emphasis, event filters, no horizontal overflow at 390px, and Analyst evidence.
- [ ] **Step 2: Run the targeted browser spec** and capture desktop/mobile screenshots.
- [ ] **Step 3: Fix only observed layout, focus, text-fit, or reduced-motion defects.**
- [ ] **Step 4: Re-run Playwright and inspect screenshots for overlaps, blank map, clipped controls, and inaccessible labels.**
- [ ] **Step 5: Run full frontend tests, lint, typecheck, and build.**
- [ ] **Step 6: Commit** with `test(race-room): validate sprint 3 experience`.


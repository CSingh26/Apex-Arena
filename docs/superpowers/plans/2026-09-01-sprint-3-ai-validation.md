# Sprint 3 AI and Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route agent reactions through eligible structured RaceEvents, validate deterministic historical intelligence and performance, document limitations, and complete the release-quality Sprint 3 verification.

**Architecture:** Discussion receives a compact immutable grounding envelope after importance filtering. Historical rebuild uses the same coordinator as live ingestion; validation records actual derived events and UI behavior without using LLM output as race state.

**Tech Stack:** Python, Pydantic, pytest, PostgreSQL, Redis, Docker Compose, Next.js, Playwright, structured logging.

**Spec:** `docs/superpowers/specs/2026-09-01-sprint-3-race-intelligence-design.md`

## Global Constraints

- Agents explain eligible events but never discover classification facts from raw telemetry.
- Objective facts are identical for every agent; opinions may differ.
- Unsupported tyre, pace, location, or DRS claims are absent from grounding.
- Historical output is deterministic for identical source facts.
- Validation names exact real sessions and actual event examples.
- No push. Merge to local `sprints` only after all checks pass.

---

### Task 1: Structured Agent Grounding and Eligibility

**Files:**
- Create: `backend/app/services/agent_grounding.py`
- Modify: `backend/app/services/discussion_triggers.py`
- Modify: `backend/app/services/discussion.py`
- Modify: `backend/app/services/room_agents.py`
- Modify: `backend/app/services/room_chat_generation.py`
- Test: `backend/tests/test_agent_grounding.py`
- Test: `backend/tests/test_race_room_discussion.py`
- Test: `backend/tests/test_room_chat_generation_accounting.py`

**Interfaces:**
- Produces `AgentEventEnvelope.from_event(event, race_state, battle) -> AgentEventEnvelope`.
- Produces `AgentEligibility.evaluate(event) -> bool` using event origin/type/importance/confidence.

- [ ] **Step 1: Write failing tests** proving raw telemetry/location/interval/position samples are ineligible; important overtake/battle/control events are eligible; stale/low-confidence events are suppressed; and envelopes contain exact session, drivers, positions, opponent, lap, interval, battle, tyre, and evidence fields only when supported.
- [ ] **Step 2: Add tests** proving two agents receive identical facts while their deterministic opinion templates may differ, and tests continue to avoid exact generated prose.
- [ ] **Step 3: Run focused discussion tests** and confirm current raw-event trigger behavior fails the new contract.
- [ ] **Step 4: Implement immutable grounding models, eligibility, concise serialization, and prompt/fallback integration.**
- [ ] **Step 5: Run all discussion/chat tests and Ruff.**
- [ ] **Step 6: Commit** with `feat(ai): route agent commentary through race events`.

### Task 2: Observability and Development Diagnostics

**Files:**
- Modify: `backend/app/services/position_intelligence.py`
- Modify: `backend/app/services/overtake_intelligence.py`
- Modify: `backend/app/services/battle_intelligence.py`
- Modify: `backend/app/api/room_routes.py`
- Modify: `backend/app/api/room_schemas.py`
- Test: `backend/tests/test_room_routes.py`
- Test: `backend/tests/test_race_intelligence.py`

**Interfaces:**
- Adds structured debug records for candidate, confirmation/rejection, battle transition, suppression, and importance.
- Extends development-only diagnostics with current battles and pending overtake candidates.

- [ ] **Step 1: Write failing caplog/API tests** for structured fields and diagnostics gating by `room_diagnostics_enabled`.
- [ ] **Step 2: Run focused tests** and confirm missing fields/response failures.
- [ ] **Step 3: Add debug/info logging at transition boundaries only and secret-safe diagnostics schemas.**
- [ ] **Step 4: Run focused tests and Ruff.**
- [ ] **Step 5: Commit** with `feat(events): add race intelligence diagnostics`.

### Task 3: Historical Reconstruction and Performance Measurement

**Files:**
- Create: `backend/app/cli/validate_race_intelligence.py`
- Create: `backend/tests/test_intelligence_performance.py`
- Modify: `docs/driver-location-pipeline.md` only if replay contracts require clarification
- Create: `docs/sprint-3-race-intelligence.md`

**Interfaces:**
- Validation CLI reports source count, derived count by type, battles, overtake confirmations/rejections, pit exclusions, elapsed time, events/second, and bounded-state maxima.

- [ ] **Step 1: Write failing CLI tests** using deterministic race/qualifying streams and asserting stable JSON summaries across two runs.
- [ ] **Step 2: Write a performance test** generating 20-driver adjacent interval updates and asserting bounded history sizes and no full-history growth.
- [ ] **Step 3: Run focused tests** and confirm missing CLI/metrics failures.
- [ ] **Step 4: Implement validation instrumentation and JSON/text reports without raw provider dumps.**
- [ ] **Step 5: Query local Postgres for suitable completed Race, Qualifying, Sprint, and timing-without-location sessions; run rebuild/validation against each available session.**
- [ ] **Step 6: Record exact session keys, event examples, timing, and limitations in `docs/sprint-3-race-intelligence.md`.**
- [ ] **Step 7: Commit** with `test: validate historical race intelligence`.

### Task 4: Full Verification and Branch Completion

**Files:**
- Modify tracked files only for defects reproduced by verification
- Update: `docs/sprint-3-race-intelligence.md`

**Interfaces:**
- Produces final verified feature branch ready for local merge.

- [ ] **Step 1: Run backend verification:** `pytest -q`, `ruff check .`, Alembic upgrade/downgrade/upgrade, and relevant CLI historical validations.
- [ ] **Step 2: Run frontend verification:** `npm test -- --run`, `npm run lint`, `npx tsc --noEmit`, `npm run build`, and targeted/full Playwright Race Room specs.
- [ ] **Step 3: Start the worktree application on unused ports and verify backend readiness, Postgres, Redis, frontend HTTP, Race Room desktop/mobile Fan mode, Analyst mode, selection sync, and event filters.**
- [ ] **Step 4: Inspect `git diff --check`, tracked-file status, ignored build output, secret patterns, migration head, and commit history.**
- [ ] **Step 5: Use `superpowers:requesting-code-review` for a final code-quality review and resolve every validated finding with failing regression tests.**
- [ ] **Step 6: Use `superpowers:verification-before-completion`, rerun affected and full checks, then commit final documentation/fixes with `docs: document sprint 3 race intelligence`.**
- [ ] **Step 7: Use `superpowers:finishing-a-development-branch`, merge `codex/sprint-3-intelligence` into local `sprints`, verify the merged tree again, and remove the feature worktree/branch only after success.**


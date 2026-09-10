# Foundation and Release Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the verified Live Rooms repair, remove immediate security and release blockers, and leave a clean foundation for the remaining ApexArena master build.

**Architecture:** Preserve the provider-neutral ingestion, PostgreSQL, Redis Streams, and SSE architecture. Partition the pre-existing repair by runtime responsibility, then add fail-closed proxy authentication, replay mutation protection, restart reconciliation, and current CI/release fixtures through test-first changes.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, Redis, pytest, Ruff, TypeScript, React, Next.js, Vitest, Playwright, GitHub Actions, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-10-apexarena-master-build-design.md`

## Global Constraints

- Work remains on `sprints` and targets `origin/sprints`.
- Perform one distinct successful `git push origin sprints` after every commit.
- Do not create empty commits, filler edits, reversals made only to increase counts, merge bubbles for counting, or artificial file churn.
- Do not rewrite history or force push.
- Never fabricate provider data or silently replace failures with mock data.
- Preserve provider-neutral domain contracts and one normalized live/replay event path.
- Every new production behavior starts with a failing test.
- Security-sensitive mutating endpoints fail closed.
- Do not overwrite unrelated existing working-tree changes.

---

### Task 1: Reconcile Live Configuration Contracts

**Files:**
- Modify: `.env.example`
- Modify: `backend/app/core/settings.py`
- Modify: `docker-compose.yml`
- Test: `backend/tests/test_ingestion_schema.py`
- Test: `backend/tests/test_process_roles.py`

**Interfaces:**
- Consumes: existing `Settings` and process-role configuration.
- Produces: validated REST live-ingestion settings and role-aware startup configuration.

- [ ] **Step 1: Run the focused configuration tests.**

```bash
cd backend
./.venv/bin/pytest tests/test_ingestion_schema.py tests/test_process_roles.py -q
```

- [ ] **Step 2: Review and stage only the coherent configuration contract.**

```bash
git diff -- .env.example backend/app/core/settings.py docker-compose.yml
git add .env.example backend/app/core/settings.py docker-compose.yml backend/tests/test_ingestion_schema.py backend/tests/test_process_roles.py
git diff --cached --check
```

- [ ] **Step 3: Commit and push.**

```bash
git commit -m "feat(live): configure REST session ingestion"
git push origin sprints
```

### Task 2: Reconcile OpenF1 Provider Retry Behavior

**Files:**
- Modify: `backend/app/providers/openf1.py`
- Test: `backend/tests/test_openf1.py`

**Interfaces:**
- Consumes: existing `OpenF1Client` REST and authentication behavior.
- Produces: bounded credential retry and explicit live REST requests.

- [ ] **Step 1: Run provider tests and stage the implementation with its regression tests.**

```bash
cd backend
./.venv/bin/pytest tests/test_openf1.py -q
cd ..
git add backend/app/providers/openf1.py backend/tests/test_openf1.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(provider): bound OpenF1 live retries"
git push origin sprints
```

### Task 3: Reconcile Canonical Session Matching

**Files:**
- Modify: `backend/app/services/provider_matching.py`
- Test: `backend/tests/test_openf1_backfill.py`

**Interfaces:**
- Consumes: `RaceRoom` metadata and OpenF1 session rows.
- Produces: one confidence-bearing matcher shared by catalog and backfill.

- [ ] **Step 1: Run matching tests and stage the canonical matcher.**

```bash
cd backend
./.venv/bin/pytest tests/test_openf1_backfill.py -q
cd ..
git add backend/app/services/provider_matching.py backend/tests/test_openf1_backfill.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(provider): unify OpenF1 session matching"
git push origin sprints
```

### Task 4: Reconcile Room Eligibility Semantics

**Files:**
- Modify: `backend/app/domain/rooms.py`
- Modify: `backend/app/services/room_eligibility.py`
- Test: `backend/tests/test_room_eligibility.py`

**Interfaces:**
- Consumes: public session lifecycle and provider availability.
- Produces: explicit waiting, live, historical, and provider-pending eligibility.

- [ ] **Step 1: Run lifecycle tests and stage the domain policy.**

```bash
cd backend
./.venv/bin/pytest tests/test_room_eligibility.py -q
cd ..
git add backend/app/domain/rooms.py backend/app/services/room_eligibility.py backend/tests/test_room_eligibility.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(rooms): model live waiting eligibility"
git push origin sprints
```

### Task 5: Reconcile the Authoritative Room Catalog

**Files:**
- Modify: `backend/app/services/rooms.py`
- Modify: `backend/app/storage/room_repository.py`
- Test: `backend/tests/test_live_catalog.py`
- Test: `backend/tests/test_race_rooms_service.py`
- Test: `backend/tests/test_live_room_repair.py`

**Interfaces:**
- Consumes: Jolpica meetings, OpenF1 sessions, and room eligibility.
- Produces: refreshable catalog state, provider diagnostics, stable identity, and current-weekend hydration.

- [ ] **Step 1: Run catalog tests and stage the coherent catalog slice.**

```bash
cd backend
./.venv/bin/pytest tests/test_live_catalog.py tests/test_race_rooms_service.py tests/test_live_room_repair.py -q
cd ..
git add backend/app/services/rooms.py backend/app/storage/room_repository.py backend/tests/test_live_catalog.py backend/tests/test_race_rooms_service.py backend/tests/test_live_room_repair.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(rooms): hydrate current session catalog"
git push origin sprints
```

### Task 6: Reconcile Live REST Ingestion

**Files:**
- Create: `backend/app/services/live_ingestion.py`
- Test: `backend/tests/test_live_ingestion.py`
- Test: `backend/tests/test_live_pipeline_integration.py`

**Interfaces:**
- Consumes: room catalog, OpenF1 REST, normalized processing, and location ingestion.
- Produces: `LiveSessionIngestionService.run_once(now=...)` with bounded discovery and polling.

- [ ] **Step 1: Run live-ingestion tests and stage the service.**

```bash
cd backend
./.venv/bin/pytest tests/test_live_ingestion.py tests/test_live_pipeline_integration.py -q
cd ..
git add backend/app/services/live_ingestion.py backend/tests/test_live_ingestion.py backend/tests/test_live_pipeline_integration.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "feat(live): add REST ingestion coordinator"
git push origin sprints
```

### Task 7: Reconcile Worker Lifecycle Integration

**Files:**
- Modify: `backend/app/services/container.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/ingestor.py`
- Test: `backend/tests/test_process_roles.py`

**Interfaces:**
- Consumes: `LiveSessionIngestionService` and singleton worker leases.
- Produces: role-correct startup, shutdown, and health status.

- [ ] **Step 1: Run lifecycle tests and stage service composition.**

```bash
cd backend
./.venv/bin/pytest tests/test_process_roles.py -q
cd ..
git add backend/app/services/container.py backend/app/main.py backend/app/ingestor.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "feat(live): run REST worker by process role"
git push origin sprints
```

### Task 8: Reconcile Cancellation-Safe Database Operations

**Files:**
- Modify: `backend/app/storage/database.py`
- Test: `backend/tests/test_live_pipeline_integration.py`

**Interfaces:**
- Consumes: asynchronous SQLAlchemy sessions and worker cancellation.
- Produces: shielded rollback/close semantics without connection leaks.

- [ ] **Step 1: Run the integration contract and stage database cleanup.**

```bash
cd backend
./.venv/bin/pytest tests/test_live_pipeline_integration.py -q
cd ..
git add backend/app/storage/database.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(storage): shield database cleanup on cancellation"
git push origin sprints
```

### Task 9: Reconcile Event Commit Ordering

**Files:**
- Modify: `backend/app/services/raw_events.py`
- Modify: `backend/app/services/event_pipeline.py`
- Modify: `backend/app/services/race_state.py`
- Modify: `backend/app/storage/repositories.py`
- Test: `backend/tests/test_race_state.py`
- Test: `backend/tests/test_live_pipeline_integration.py`

**Interfaces:**
- Consumes: raw provider facts and normalized writes.
- Produces: commit-before-publish ordering and database-backed state freshness.

- [ ] **Step 1: Run event-path tests and stage the transaction boundary.**

```bash
cd backend
./.venv/bin/pytest tests/test_race_state.py tests/test_live_pipeline_integration.py -q
cd ..
git add backend/app/services/raw_events.py backend/app/services/event_pipeline.py backend/app/services/race_state.py backend/app/storage/repositories.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(events): publish only committed race state"
git push origin sprints
```

### Task 10: Reconcile Redis Stream Recovery

**Files:**
- Modify: `backend/app/storage/redis.py`
- Modify: `backend/app/api/streaming.py`
- Test: `backend/tests/test_event_bus.py`
- Test: `backend/tests/test_streaming.py`

**Interfaces:**
- Consumes: Redis Streams and durable normalized-event pagination.
- Produces: bounded multi-page catch-up and honest outage behavior.

- [ ] **Step 1: Run stream tests and stage event recovery.**

```bash
cd backend
./.venv/bin/pytest tests/test_event_bus.py tests/test_streaming.py -q
cd ..
git add backend/app/storage/redis.py backend/app/api/streaming.py backend/tests/test_event_bus.py backend/tests/test_streaming.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(streaming): recover bounded Redis event gaps"
git push origin sprints
```

### Task 11: Reconcile Room Stream Recovery

**Files:**
- Modify: `backend/app/api/room_streaming.py`
- Test: `backend/tests/test_room_streaming.py`

**Interfaces:**
- Consumes: durable messages and Redis room streams.
- Produces: paginated reconnect recovery without duplicates.

- [ ] **Step 1: Run room-stream tests and stage recovery.**

```bash
cd backend
./.venv/bin/pytest tests/test_room_streaming.py -q
cd ..
git add backend/app/api/room_streaming.py backend/tests/test_room_streaming.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(streaming): recover room messages after reconnect"
git push origin sprints
```

### Task 12: Reconcile Historical Finalization

**Files:**
- Modify: `backend/app/services/historical.py`
- Modify: `backend/app/services/openf1_backfill.py`
- Modify: `backend/app/storage/backfill_repository.py`
- Modify: `backend/app/cli/backfill_completed_rooms.py`
- Modify: `backend/app/cli/build_race_rooms.py`
- Test: `backend/tests/test_historical.py`
- Test: `backend/tests/test_completed_backfill_session_types.py`

**Interfaces:**
- Consumes: canonical matching, endpoint checkpoints, and normalized session data.
- Produces: durable partial/complete outcomes and retryable empty endpoints.

- [ ] **Step 1: Run historical tests and stage the backfill slice.**

```bash
cd backend
./.venv/bin/pytest tests/test_historical.py tests/test_openf1_backfill.py tests/test_completed_backfill_session_types.py -q
cd ..
git add backend/app/services/historical.py backend/app/services/openf1_backfill.py backend/app/storage/backfill_repository.py backend/app/cli/backfill_completed_rooms.py backend/app/cli/build_race_rooms.py backend/tests/test_historical.py backend/tests/test_completed_backfill_session_types.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(replay): make historical backfill resumable"
git push origin sprints
```

### Task 13: Reconcile Recent Session Recovery

**Files:**
- Modify: `backend/app/services/recent_sessions.py`
- Test: `backend/tests/test_recent_session_reconciliation.py`

**Interfaces:**
- Consumes: completed room candidates and backfill job state.
- Produces: fair bounded recovery including practice sessions.

- [ ] **Step 1: Run reconciliation tests and stage the service.**

```bash
cd backend
./.venv/bin/pytest tests/test_recent_session_reconciliation.py -q
cd ..
git add backend/app/services/recent_sessions.py backend/tests/test_recent_session_reconciliation.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(backfill): fairly recover recent sessions"
git push origin sprints
```

### Task 14: Reconcile Replay State Refresh

**Files:**
- Modify: `backend/app/services/room_replay.py`
- Test: `backend/tests/test_room_replay.py`

**Interfaces:**
- Consumes: playback cursor and persisted snapshots.
- Produces: fresher replay state and bounded rehydration.

- [ ] **Step 1: Run replay tests and stage state refresh.**

```bash
cd backend
./.venv/bin/pytest tests/test_room_replay.py -q
cd ..
git add backend/app/services/room_replay.py backend/tests/test_room_replay.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(replay): refresh persisted race state"
git push origin sprints
```

### Task 15: Reconcile Live Diagnostics API

**Files:**
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: provider, ingestion, Redis, and database status.
- Produces: secret-safe live-ingestion health diagnostics.

- [ ] **Step 1: Run route tests and stage diagnostics.**

```bash
cd backend
./.venv/bin/pytest tests/test_routes.py -q
cd ..
git add backend/app/api/routes.py backend/tests/test_routes.py
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "feat(health): expose live ingestion status"
git push origin sprints
```

### Task 16: Reconcile Race Room Catalog UX

**Files:**
- Modify: `frontend/src/components/race-rooms/race-rooms-index.tsx`
- Modify: `frontend/src/components/race-rooms/race-rooms-index.test.tsx`
- Modify: `frontend/src/components/race-rooms/race-rooms-revamp.module.css`
- Modify: `frontend/e2e/race-rooms.spec.ts`

**Interfaces:**
- Consumes: public room lifecycle and provider status.
- Produces: completed wording, honest waiting states, and compact filters.

- [ ] **Step 1: Run catalog tests and stage the UX slice.**

```bash
cd frontend
npm test -- --run src/components/race-rooms/race-rooms-index.test.tsx
cd ..
git add frontend/src/components/race-rooms/race-rooms-index.tsx frontend/src/components/race-rooms/race-rooms-index.test.tsx frontend/src/components/race-rooms/race-rooms-revamp.module.css frontend/e2e/race-rooms.spec.ts
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(ui): clarify live room availability"
git push origin sprints
```

### Task 17: Reconcile Live Command Center Recovery

**Files:**
- Modify: `frontend/src/components/race-rooms/live-command-center.tsx`
- Modify: `frontend/src/components/race-rooms/live-command-center.test.tsx`
- Modify: `frontend/src/lib/types.ts`

**Interfaces:**
- Consumes: session events, snapshots, provider status, and capabilities.
- Produces: hydrated state, visible reconnect status, and safe transitions.

- [ ] **Step 1: Run command-center tests and stage recovery.**

```bash
cd frontend
npm test -- --run src/components/race-rooms/live-command-center.test.tsx
cd ..
git add frontend/src/components/race-rooms/live-command-center.tsx frontend/src/components/race-rooms/live-command-center.test.tsx frontend/src/lib/types.ts
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(ui): hydrate live command center state"
git push origin sprints
```

### Task 18: Reconcile GPS and Room Bootstrap

**Files:**
- Modify: `frontend/src/lib/use-driver-locations.ts`
- Create: `frontend/src/lib/use-driver-locations.test.tsx`
- Modify: `frontend/src/components/race-rooms/room-experience.tsx`
- Create: `frontend/src/components/race-rooms/room-experience.test.tsx`

**Interfaces:**
- Consumes: location windows, track geometry, replay clock, and room metadata.
- Produces: geometry refresh, reconnect-safe polling, and provider-key hydration.

- [ ] **Step 1: Run GPS/bootstrap tests and stage the changes.**

```bash
cd frontend
npm test -- --run src/lib/use-driver-locations.test.tsx src/components/race-rooms/room-experience.test.tsx
cd ..
git add frontend/src/lib/use-driver-locations.ts frontend/src/lib/use-driver-locations.test.tsx frontend/src/components/race-rooms/room-experience.tsx frontend/src/components/race-rooms/room-experience.test.tsx
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "fix(map): recover live driver locations"
git push origin sprints
```

### Task 19: Reconcile Live Operations Documentation

**Files:**
- Modify: `docs/live-race-operations.md`
- Modify: `docs/openf1-rest-backfill.md`
- Create: `docs/italian-gp-live-room-repair-report.md`
- Create: `docs/superpowers/plans/2026-09-06-live-room-repair.md`

**Interfaces:**
- Consumes: verified runtime behavior and provider limitations.
- Produces: accurate operational and rollout guidance.

- [ ] **Step 1: Validate and stage documentation.**

```bash
git diff --check -- docs/live-race-operations.md docs/openf1-rest-backfill.md docs/italian-gp-live-room-repair-report.md docs/superpowers/plans/2026-09-06-live-room-repair.md
git add docs/live-race-operations.md docs/openf1-rest-backfill.md docs/italian-gp-live-room-repair-report.md docs/superpowers/plans/2026-09-06-live-room-repair.md
git diff --cached --check
```

- [ ] **Step 2: Commit and push.**

```bash
git commit -m "docs(live): record room repair operations"
git push origin sprints
```

### Task 20: Enforce Production Proxy Authentication

**Files:**
- Modify: `backend/app/core/settings.py`
- Modify: `backend/app/api/proxy.py`
- Test: `backend/tests/test_proxy_security.py`

**Interfaces:**
- Consumes: `APP_ENV`, `PROXY_ENFORCEMENT_ENABLED`, and `APEX_ARENA_PROXY_TOKEN`.
- Produces: startup validation and fail-closed proxy enforcement.

- [ ] **Step 1: Add and run a failing test for production without a proxy token.**

```python
def test_production_proxy_enforcement_requires_token():
    with pytest.raises(ValidationError):
        Settings(app_env="production", proxy_enforcement_enabled=True, apex_arena_proxy_token=None)
```

```bash
cd backend
./.venv/bin/pytest tests/test_proxy_security.py -q
```

- [ ] **Step 2: Implement cross-field validation and constant-time request checking.**

```python
if self.app_env == "production" and self.proxy_enforcement_enabled and not self.apex_arena_proxy_token:
    raise ValueError("APEX_ARENA_PROXY_TOKEN is required when production proxy enforcement is enabled")
```

- [ ] **Step 3: Run focused tests and Ruff, then commit and push.**

```bash
cd backend
./.venv/bin/pytest tests/test_proxy_security.py -q
./.venv/bin/ruff check app tests
cd ..
git add backend/app/core/settings.py backend/app/api/proxy.py backend/tests/test_proxy_security.py
git commit -m "fix(security): fail closed without proxy token"
git push origin sprints
```

### Task 21: Protect Shared Replay Mutations

**Files:**
- Modify: `backend/app/api/room_routes.py`
- Modify: `backend/app/api/proxy.py`
- Test: `backend/tests/test_room_routes.py`

**Interfaces:**
- Consumes: trusted proxy authentication from Task 20.
- Produces: authenticated replay mutations and public read-only replay state.

- [ ] **Step 1: Add and run a failing anonymous mutation test.**

```python
response = client.post(f"/api/v1/race-rooms/{slug}/replay")
assert response.status_code == 401
```

```bash
cd backend
./.venv/bin/pytest tests/test_room_routes.py -q
```

- [ ] **Step 2: Reuse one trusted-proxy dependency on replay mutation routes.**

```python
dependencies=[Depends(require_trusted_proxy)]
```

- [ ] **Step 3: Run tests, commit, and push.**

```bash
cd backend
./.venv/bin/pytest tests/test_room_routes.py -q
cd ..
git add backend/app/api/room_routes.py backend/app/api/proxy.py backend/tests/test_room_routes.py
git commit -m "fix(security): authenticate replay controls"
git push origin sprints
```

### Task 22: Reconcile Interrupted Replay Workers

**Files:**
- Modify: `backend/app/services/room_replay.py`
- Modify: `backend/app/services/container.py`
- Modify: `backend/app/storage/room_repository.py`
- Test: `backend/tests/test_room_replay.py`

**Interfaces:**
- Consumes: persisted playback rows at startup.
- Produces: `reconcile_interrupted_replays()` that pauses orphaned rows while preserving cursors.

- [ ] **Step 1: Add and run a failing orphaned-running-row test.**

```python
await service.reconcile_interrupted_replays()
assert (await playback.get(room.id)).status == "paused"
```

```bash
cd backend
./.venv/bin/pytest tests/test_room_replay.py -q
```

- [ ] **Step 2: Implement idempotent startup reconciliation.**

```python
async def reconcile_interrupted_replays(self) -> int:
    return await self.playback.pause_orphaned_running_rows()
```

- [ ] **Step 3: Run tests, commit, and push.**

```bash
cd backend
./.venv/bin/pytest tests/test_room_replay.py -q
cd ..
git add backend/app/services/room_replay.py backend/app/services/container.py backend/app/storage/room_repository.py backend/tests/test_room_replay.py
git commit -m "fix(replay): reconcile interrupted workers"
git push origin sprints
```

### Task 23: Reconcile Interrupted Ingestion Runs

**Files:**
- Modify: `backend/app/services/historical.py`
- Modify: `backend/app/storage/repositories.py`
- Test: `backend/tests/test_historical.py`

**Interfaces:**
- Consumes: persisted ingestion runs left running after termination.
- Produces: age-bounded startup reconciliation to a retryable failed state.

- [ ] **Step 1: Add and run a failing stale-run test.**

```python
count = await service.reconcile_stale_runs(now=now, stale_after=timedelta(minutes=30))
assert count == 1
```

```bash
cd backend
./.venv/bin/pytest tests/test_historical.py -q
```

- [ ] **Step 2: Implement guarded stale-run reconciliation.**

```python
await repository.fail_running_before(cutoff, reason="worker interrupted")
```

- [ ] **Step 3: Run tests, commit, and push.**

```bash
cd backend
./.venv/bin/pytest tests/test_historical.py -q
cd ..
git add backend/app/services/historical.py backend/app/storage/repositories.py backend/tests/test_historical.py
git commit -m "fix(ingestion): reconcile interrupted runs"
git push origin sprints
```

### Task 24: Repair CI Development Seed

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `backend/app/cli/seed_e2e_room.py`
- Test: `backend/tests/test_seed_e2e_room.py`

**Interfaces:**
- Consumes: current room/session schemas and deterministic test-only fixtures.
- Produces: a guarded CLI-owned E2E seed.

- [ ] **Step 1: Add and run a failing production-refusal test.**

```python
with pytest.raises(RuntimeError, match="test environments"):
    await seed(settings=production_settings)
```

```bash
cd backend
./.venv/bin/pytest tests/test_seed_e2e_room.py -q
```

- [ ] **Step 2: Implement the CLI and replace the removed fixture flag/slug in CI.**

```bash
python -m app.cli.seed_e2e_room --scenario normal-race --slug e2e-normal-race
```

- [ ] **Step 3: Run tests, commit, and push.**

```bash
cd backend
./.venv/bin/pytest tests/test_seed_e2e_room.py -q
cd ..
git add backend/app/cli/seed_e2e_room.py backend/tests/test_seed_e2e_room.py .github/workflows/release.yml
git commit -m "fix(ci): seed current race room fixtures"
git push origin sprints
```

### Task 25: Restore Reachable Release Publishing

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `scripts/test-release-workflow.sh`

**Interfaces:**
- Consumes: successful quality and container jobs.
- Produces: a release graph where disabled optional deployment cannot block publication.

- [ ] **Step 1: Add a failing shell assertion for the dependency graph.**

```bash
./scripts/test-release-workflow.sh .github/workflows/release.yml
```

- [ ] **Step 2: Make verified publication depend only on mandatory jobs.**

```yaml
needs: [quality, build-images]
```

- [ ] **Step 3: Run checks, commit, and push.**

```bash
bash -n scripts/test-release-workflow.sh
./scripts/test-release-workflow.sh .github/workflows/release.yml
git add .github/workflows/release.yml scripts/test-release-workflow.sh
git commit -m "fix(release): make image publishing reachable"
git push origin sprints
```

### Task 26: Apply Backend Formatting Gate

**Files:**
- Modify: files selected by `ruff format app tests`.

**Interfaces:**
- Consumes: current Python source and tests.
- Produces: behavior-preserving code accepted by the formatting gate.

- [ ] **Step 1: Capture the current formatting failure, apply Ruff, and inspect the diff.**

```bash
cd backend
./.venv/bin/ruff format --check app tests
./.venv/bin/ruff format app tests
git diff --stat
```

- [ ] **Step 2: Verify format, lint, and backend tests.**

```bash
./.venv/bin/ruff format --check app tests
./.venv/bin/ruff check app tests
./.venv/bin/pytest -q
```

- [ ] **Step 3: Commit and push only formatter changes.**

```bash
cd ..
git add backend/app backend/tests
git commit -m "style(backend): satisfy Ruff formatting gate"
git push origin sprints
```

### Task 27: Verify the Foundation Phase

**Files:**
- Modify: `docs/italian-gp-live-room-repair-report.md`
- Modify: `docs/superpowers/plans/2026-09-10-foundation-and-release-safety.md`

**Interfaces:**
- Consumes: all completed foundation changes and fresh command output.
- Produces: exact verification evidence and a closed phase checklist.

- [ ] **Step 1: Run backend and frontend quality gates.**

```bash
cd backend
./.venv/bin/pytest -q
./.venv/bin/ruff check app tests migrations
./.venv/bin/ruff format --check app tests migrations
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

- [ ] **Step 2: Verify migrations and Compose.**

```bash
cd ../backend
./.venv/bin/alembic heads
cd ..
docker compose config --quiet
```

- [ ] **Step 3: Record exact results, stage documentation, commit, and push.**

```bash
git add docs/italian-gp-live-room-repair-report.md docs/superpowers/plans/2026-09-10-foundation-and-release-safety.md
git diff --cached --check
git commit -m "docs(verification): close foundation phase"
git push origin sprints
```

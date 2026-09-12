<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Historical live-room repair implementation plan

> **Superseded operational record.** This plan records the September 6 repair
> session and its original evidence. The repair was subsequently decomposed,
> reviewed, committed, and pushed by Tasks 1–18 of the
> [foundation and release safety plan](2026-09-10-foundation-and-release-safety.md),
> through exact commit `35666c0b0653701fcf9ea0f91e46f989f73dfadf` on
> `origin/sprints`. Nothing in this historical plan records a promotion to
> `main`, a production application deployment, or a production migration.

**Goal:** Activate real shared live rooms, recover all available weekend sessions, and retain honest provider diagnostics.
**Architecture:** Keep the canonical SessionType/internal UUID and OpenF1SessionMatcher. Add a live REST adapter to the existing singleton worker and RaceEventProcessor, using the existing Redis Streams and location store. Repair catalog and historical recovery in place.
**Spec:** User attachment `pasted-text.txt`, ApexArena — Fix Live Race Rooms + Backfill 2026 Italian Grand Prix.

## Constraints and execution rulings
- Historical constraint: no fabricated production data, provider keys, Monza branches, UI redesign, agent personality changes, push, or merge during the original repair session.
- Preserve pre-existing working-tree edits. Work on the existing `sprints` checkout because the repair includes those changes and configured local services.
- User explicitly requests diagnosis followed by implementation, so proceed through tests and fixes without an additional design approval.
- Use subagent-driven-development for the independent browser repair and scoped review; backend lifecycle changes stay together locally.

## Root cause evidence (before code changes)
- main.py/ingestor.py start live services only with MQTT auto-connect. Production settings use rest + auto-connect false. There is no live REST poller.
- rooms.py _availability always returns unavailable; _from_session consequently creates pending/replay even while calendar status is LIVE. room_eligibility.py then skips the pending row on future syncs.
- ensure_catalog caches provider sessions forever, including empty lists produced by swallowed discovery exceptions.
- recent_sessions.py excludes practice, scans only existing rows, and can repeatedly select a pending live row before older completed sessions. Empty successful endpoints become permanent backfill checkpoints.
- openf1_backfill.py has a second, weaker date/token matcher rather than the catalog matcher.
- Default MQTT topics omit location/car_data. LiveLocationRecorder stores positions but never derives live geometry; the browser fetches geometry once.
- SSE catches Redis errors, but reconnect backlog is one page; API race state is process-cached and can lag the ingestor.
- Actual 2026-09-06T13:36Z probe: OpenF1 sessions/meetings return HTTP 401 (global live restriction), configured OAuth returns 401. Jolpica schedule and qualifying return 200, race results empty. Production DB already contains qualifying 11357 with replay data; local DB has no round-13 rooms.

## Task 1: Canonical lifecycle and live adapter
- [x] Add failing catalog tests: LIVE with missing data opens a waiting room, provider key binds later, pending recovery, UTC Monza/Arizona, stale catalog refresh.
- [x] Add failing adapter tests using the real processor and in-memory repositories: keyed polling, retry deadlines, incremental windows, temporary errors, location event flow, completion.
- [x] Repair catalog/eligibility without claiming telemetry coverage from metadata.
- [x] Implement shared LiveSessionIngestionService.run_once(now=...) with 5/15/30/60-second resolution retries, per-endpoint safe retries and bounded windows. Reuse processor and location services; add worker startup/cleanup under existing lease.
- [x] Expose credential-free status through existing health/internal endpoint and Redis status stream.

## Task 2: Browser stream and GPS recovery
- [x] Reproduce missing geometry refresh, connection-state and reconnect issues in frontend tests.
- [x] Repair only existing connection/hooks, with cleanup and no duplicate EventSource per component. Preserve lap formatting and projection.
- [x] Run frontend tests, lint, typecheck, production build.

## Task 3: Historical recovery and transport
- [x] Regression tests for canonical historical matching, empty endpoint retries, partial finalization, completed practice recovery, and SSE multi-page/outage catch-up.
- [x] Reuse OpenF1SessionMatcher in backfill. Preserve durable successful data; retry empty endpoints. Include practice in bounded recent recovery and avoid live starvation.
- [x] Repair stream catch-up/state snapshots and track status/counts in the existing bus.
- [x] Run the reusable backfill and location ingestion for each actually published Italian GP session; record provider responses, row counts, idempotency and room state.

## Task 4: Verification and report
- [x] Full backend/frontend suites, Ruff, ESLint, TypeScript and build.
- [x] Real provider/database/Redis/browser smoke, scoped code review and resource/secret/timezone audit.
- [x] Produce requested report with exact commands/results, provider limitations, all changed files and Git state. Do not claim deployment or unavailable live observations.

## Runtime findings and scope

- Real OpenF1 access recovered at 14:17 UTC. Italian keys 11354/11355/11356/11357/11361 were resolved from the provider; no production constants were added.
- Local completed-session backfill and GPS recovery succeeded. The live browser exposed a paused replay-clock bug; a regression test now protects live GPS movement.
- Real SQL/Redis integration additionally verified advisory leases, fan-out to two SSE clients, grounded messages, completion, and recovery after raw commit / normalized write failure.
- Production backfill was attempted and stopped when the deployment observed on
  September 6 at schema `20260720_0009` proved incompatible with the repair
  workspace, which then required `20260902_0014`. No production migrations or
  code deployment were performed. That production revision has not been
  revalidated by Task 19.

## Verified runtime outcome

Local OpenF1 sessions 11354/11355/11356/11357/11361 resolved from real provider responses. Four archives were backfilled with 260,104 GPS samples. The browser verified 19 moving markers out of 22 located drivers; SQL/Redis integration and browser suites passed. On September 6, production was observed at schema 0009, behind the then-required 0014; attempted writes exposed the mismatch and production retries were stopped without applying migrations or deploying.

## Foundation reconciliation addendum (12 September 2026)

The current repository has one Alembic head, `20260911_0015`, defined by
`20260911_0015_recent_reconciliation_attempts.py`. Migration 0015 adds durable
recent-reconciliation attempt ordering. Practice 1/2/3, Sprint Qualifying,
Sprint, Qualifying, and Race are all included in manual completed-room backfill
and bounded recent recovery.

The committed live modes are explicit: `rest` polls REST without MQTT and runs
even when `OPENF1_LIVE_AUTO_CONNECT=false`; `mqtt` requires auto-connect and
does not fall back to REST; `auto` requires auto-connect and uses REST only
while MQTT is not connected and fresh for the same session. `api` processes do
not ingest. API-role provider status is read from Redis and becomes `STALE`
after 120 seconds; worker roles report local state.

Replay now rebuilds state through the durable playback cursor. Browser GPS uses
session-scoped bounded caches, mutable live windows, immutable replay/archive
windows, strict streamed-frame validation, authoritative room hydration, and
two-sided seek pruning that keeps quiet drivers visible. These statements
describe reviewed code, not a current production deployment.

REST begins with a public request and retries a 401 through OAuth when
`OPENF1_USERNAME` and `OPENF1_PASSWORD` are configured; MQTT requires those
credentials. The internal backfill-status endpoint separately requires
`INTERNAL_API_KEY`. No credential values belong in this plan or its evidence.

Production rollout remains pending. Tasks 20–27 still cover fail-closed proxy
configuration, replay mutation authentication, interrupted-worker/run
reconciliation, CI seed repair, release graph repair, formatting, and a fresh
full foundation gate. The current code is pushed to `origin/sprints`, but this
plan must not be cited as evidence of promotion, migration, or deployment.

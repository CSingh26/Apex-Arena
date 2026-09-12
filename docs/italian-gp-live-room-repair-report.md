<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Italian GP live-room repair report

## Status and provenance

This document contains two deliberately separate records:

- **Historical repair evidence (6 September 2026):** real OpenF1, local
  PostgreSQL/Redis/browser verification and a contained production compatibility
  incident from the original repair session.
- **Foundation integration (12 September 2026):** the repair was reviewed,
  corrected, committed, and pushed through exact commit
  `35666c0b0653701fcf9ea0f91e46f989f73dfadf` on `sprints` by Tasks 1–18.

The foundation commits are tested and present on `origin/sprints`. They have
not been promoted to `main` or deployed to production. This reconciliation did
not query production, run a provider smoke test, or apply a migration. Any
production state observed on September 6 is historical evidence, not a claim
about the current deployment.

## Historical root cause

Before the repair, calendar and telemetry lifecycles had become disconnected:

- Startup gated all live work on MQTT auto-connect even when the intended mode
  was REST, so no live REST worker ran.
- Calendar-live sessions could remain pending/replay-only and existing pending
  rooms could block later provider-key binding.
- Provider discovery cached successful and failed discovery too long. Backfill
  used a separate weaker matcher, permanently checkpointed successful empty
  responses, omitted practice from recovery, and could starve older candidates.
- Live GPS had no reliable geometry refresh, used a paused replay clock in live
  rooms, and did not hydrate a session key that appeared after page bootstrap.
- Initial position windows could omit quiet drivers. SSE recovery covered only
  one durable page, process-local race state could be stale, and Redis
  publication failures could leave gaps.
- Historical qualifying lost Q1/Q2/Q3 type context; historical car-data window
  calculation could fail on null lap timestamps.
- Replay reused final live state and live-marked events, skipping earlier
  replay facts. Request cancellation could interrupt SQL connection cleanup.

During the September 6 incident, the production database was observed at
`20260720_0009` while that repair workspace required migrations through
`20260902_0014`. Production writes failed with `ProgrammingError` because GPS
tables and normalized-event fields were absent. That observation has not been
revalidated. The current repository now has a later single Alembic head,
`20260911_0015`, which adds `race_rooms.reconciliation_attempted_at` for durable
fair recent-session recovery.

## Committed repair behavior

Tasks 1–18 retain one provider-neutral normalized event path and add or correct:

- live REST polling under the existing PostgreSQL advisory lease;
- explicit REST, MQTT, and auto behavior, with API-only processes excluded;
- UTC-aware canonical session matching and stable waiting-room identity;
- bounded provider retries, endpoint schedules, overlapping high-frequency
  windows, and latest-per-driver position warmup;
- commit-before-publish event ordering, database-backed state refresh, bounded
  Redis/SQL event recovery, and paginated room-message recovery;
- resumable partial historical backfill, non-empty endpoint checkpoints, and
  retryable empty/error endpoints;
- Practice 1/2/3, Sprint Qualifying, Sprint, Qualifying, and Race recovery with
  durable least-recently-attempted ordering;
- replay state rebuilding through the saved cursor with replay-marked event
  copies and bounded rehydration;
- role-aware provider health: API roles read the Redis status stream and reject
  reports older than 120 seconds as `STALE`; worker roles report local state;
- session-scoped browser streams, strict frame validation, authoritative room
  rehydration, bounded GPS caches, mutable live windows, immutable replay
  windows, and two-sided seek pruning that preserves quiet drivers.

The REST client starts public and retries a 401 with a cached OAuth token when
OpenF1 credentials are configured. MQTT requires credentials before connecting.
Operational output reports only safe error classes and credential/token state,
never secret values.

## Transport and role corrections

The committed modes are not interchangeable:

- `rest`: live mode starts REST polling even when
  `OPENF1_LIVE_AUTO_CONNECT=false`; MQTT is not connected.
- `mqtt`: live work starts only with auto-connect enabled; REST endpoint polling
  remains off even when MQTT is unavailable.
- `auto`: live work starts only with auto-connect enabled, connects MQTT, and
  falls back to REST while MQTT is not connected and fresh for the same
  session key.

`api` serves HTTP/SSE and reads shared provider state. `ingestor` runs the
dedicated worker app. `combined` performs both roles. Staging/production
ingesting roles require the direct `DATABASE_MIGRATION_URL`; production rejects
the legacy `all` role.

## Historical Italian GP evidence

The following figures come from the September 6 local application database and
the retained evidence files. They are not production counts and were not
re-measured during Task 19.

| Session | OpenF1 key | Normalized events | GPS samples | Historical outcome |
| --- | ---: | ---: | ---: | --- |
| Practice 1 | 11354 | 1,643 | 69,377 | Completed local archive; replay/results available |
| Practice 2 | 11355 | 1,557 | 67,033 | Completed local archive; replay/results available |
| Practice 3 | 11356 | 1,524 | 56,589 | Completed local archive; replay/results available |
| Qualifying | 11357 | 1,640 | 67,105 | Completed local archive; replay/results available |
| Race | 11361 | 41,519 | 155,323 | Completed local archive; replay/final results available |

The first four recovered sessions stored 260,104 GPS samples with zero failed
GPS windows. Qualifying included 22 real classifications with Q1/Q2/Q3 phase
structures. Optional practice starting-grid/interval and qualifying interval
requests were unavailable; no values were invented.

Historical race completion fetched 24,127 provider records. Sixty GPS windows
added 104,415 samples to the live capture for 155,323 stored race samples and
415,427 samples across all five sessions. A repeat GPS pass inserted zero
samples. OpenF1 later published 22 classification rows, raising the race to
41,519 normalized events and enabling results; the starting grid remained
unavailable.

### Historical live observation

At 14:41:57 UTC on September 6, the local worker observed race session `11361`
for meeting `1293` through authenticated REST. All 22 drivers had car telemetry
and classification state had reached lap 44. The browser rendered 22 markers;
19 moved across observations in agreement with backend GPS changes. Weather,
36 race-control entries, advancing Redis streams, SSE timing, and 42 grounded
room messages were observed. The room later completed at lap 53 and exposed an
archive/replay after historical completion.

Replay subsequently advanced from sequence 552 to 803 and showed replay lap 28
with timing and GPS. This was a local manual verification; it does not prove the
current production runtime.

## Historical production incident and recovery

The September 6 production catalog/backfill attempt created 29 practice rooms
and 145 agent rows against the older application. The old backend could not
deserialize the new practice session types and the public calendar returned
HTTP 500. Those exact new rows were copied to a `repair_20260906` backup schema
and removed from active tables; the affected rooms had no messages. Calendar
HTTP 200 was then observed. Existing history, including 1,372 qualifying
records, was retained, and retries stopped.

No production application migration or code deployment was performed during
that repair. Task 19 did not inspect the backup schema or current production
revision, so operators must re-establish both facts before any future rollout.

## Verification record

### Historical repair workspace (September 6)

These results are preserved as provenance. They predate the foundation commit
series and are not an exact-head Task 19 test run.

| Check | Historical result |
| --- | --- |
| `LIVE_REPAIR_INTEGRATION=1 backend/.venv/bin/python -m pytest backend/tests -q` | 529 passed; one Starlette deprecation warning |
| Frontend `npm test` | 129 passed in 28 files |
| Backend Ruff, frontend lint/typecheck/build | Passed |
| Isolated Playwright repair configuration | 11 passed at 320–1440 px |
| Docker backend/frontend builds and local health | Passed |
| Evidence scan against configured secret values | 39 artifacts checked; no configured-secret match |

### Foundation Tasks 1–18 (through September 12)

The controller ledger records focused/exact-artifact tests and reviews for each
committed slice. The latest Task 18 exact-head verification at `35666c0` ran 31
narrow GPS/seek regressions and received a clean review. Earlier Task 18
verification at `5be2f56` recorded 54 focused and 196 full frontend tests plus
lint, typecheck, build, and responsive Playwright checks. Backend tasks recorded
focused pytest/Ruff evidence per commit, including Alembic head
`20260911_0015` for Task 13.

Task 27, not this report, owns a fresh full backend/frontend/migration/Compose
phase gate. Therefore this document does not claim a fresh full-suite pass at
`35666c0`.

## Tested, deployed, and pending

| Boundary | Status at Task 19 start |
| --- | --- |
| Repair code Tasks 1–18 | Committed, pushed to `origin/sprints`, and reviewed with scoped/exact-artifact evidence |
| Historical Italian GP local data and browser smoke | Tested September 6; not re-run by Task 19 |
| Production application/schema state | Not inspected by Task 19; September 6 observation only |
| Promotion to `main` or production deploy | Not performed |
| Migration from the historical production revision to current head 0015 | Not performed or authorized here |
| Proxy fail-closed validation and replay mutation authentication | Pending Tasks 20–21 |
| Orphan replay and stale ingestion-run startup reconciliation | Pending Tasks 22–23 |
| Current CI E2E seed and reachable release publishing graph | Pending Tasks 24–25 |
| Backend formatting and complete foundation gate | Pending Tasks 26–27 |

The current `sprints` artifact must not be described as production-ready or
rolled out. A future rollout needs a fresh production read-only audit, a
recoverable database backup/branch, one-shot migrations through 0015 against
the direct DSN, completion of Tasks 20–27, compatible application deployment,
role-aware health checks, and a one-room backfill canary.

## Evidence retention

The original local evidence remains under the ignored `tmp/` directory in the
working copy (provider probes, backfill results, room/GPS audits, rollback
verification, and backend/frontend/browser logs). Those machine-local files are
not part of this committed documentation artifact and must not be treated as
durable repository evidence. Their filenames and the original plan chronology
are preserved in the historical plan:
[`2026-09-06-live-room-repair.md`](superpowers/plans/2026-09-06-live-room-repair.md).

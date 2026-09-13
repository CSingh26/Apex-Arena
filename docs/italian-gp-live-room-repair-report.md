<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Italian GP live-room repair report

## Status and provenance

This document contains two deliberately separate records:

- **Historical repair evidence (6 September 2026):** real OpenF1, local
  PostgreSQL/Redis/browser verification and a contained production compatibility
  incident from the original repair session.
- **Foundation integration (12 September 2026):** the repair was reviewed,
  corrected, committed, and pushed through Tasks 1–26. Task 27's fresh phase
  gate tested code revision `7f7513efaba6ffe93cc20292ecb3915e716e85c1` on
  `sprints`; the subsequent Task 27 commit changes documentation only.

The foundation commits are tested and present on `origin/sprints`. They have
not been promoted to `main` or deployed to production. This reconciliation did
not query production, run a provider smoke test, or apply a production migration. Any
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
revalidated. At Task 19, the repository head was `20260911_0015`, adding
`race_rooms.reconciliation_attempted_at` for durable fair recent recovery.
Task 27 now verifies the single head `20260912_0017`: migration 0016 adds replay
ownership/expiry, and 0017 adds ingestion-run heartbeats. This is a repository
head observation, not a production schema observation.

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
- role-aware provider health: the API-only role reads the Redis status stream
  and marks reports older than 120 seconds `STALE`; `combined` and legacy `all`
  use process-local worker state. Main-app provider probes can return HTTP 503,
  while the dedicated ingestor's diagnostic provider route always returns
  HTTP 200 and exposes state in JSON;
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
dedicated worker app. `combined` performs both roles; legacy non-production
`all` also uses the main API app and runs the worker. Staging/production
ingesting roles require the direct `DATABASE_MIGRATION_URL`; production rejects
`all`. The main API app used by `api`, `combined`, and `all` exposes
`/health/ready` and its 200/503 `/health/provider` gate. A dedicated ingestor
exposes `/health/live` and an always-200 `/health/provider` diagnostic, so
operators must inspect the JSON state. PostgreSQL plus Redis readiness must be
checked at the main API app's `/health/ready`; `database_status` checks only
PostgreSQL/schema state and does not test Redis.

Automatic recent recovery runs in `ingestor`, `combined`, and legacy
non-production `all`. Its age/status candidate predicate can select a
sufficiently overdue `RoomStatus.LIVE` row and records the attempt before
provider work. The historical backfill completion guard refuses an active
provider session with a missing/future `date_end`, so no historical endpoint
ingestion runs for it. The outcome is retryable and the row remains eligible
for later passes while it matches the candidate filters; the attempt marker
only affects fair ordering. The manual completed-room batch separately and
explicitly excludes `RoomStatus.LIVE` rows.

## Historical Italian GP evidence

The following figures come from the September 6 local application database and
the retained evidence files. They are not production counts and were not
re-measured during Task 19 or Task 27.

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
revision; Task 27 did not either. Operators must re-establish both facts before
any future rollout.

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

Those are historical integration results, not a fresh full-suite pass at
`35666c0`. The separate current Task 27 gate follows.

### Current foundation gate (Task 27, September 12)

Tested code: `7f7513efaba6ffe93cc20292ecb3915e716e85c1`. All tests include the
committed catalog correction `5274df4`; no user-dirty fixture or catalog
exclusion is required. The controller ledger records Tasks 1–26 independently
reviewed complete. This documentation records local verification, not hosted
Actions execution, image publication, production promotion, or a deployment.

| Check | Current evidence |
| --- | --- |
| Full backend, all four integration opt-ins enabled | 699 passed, zero skipped, one existing TestClient/httpx deprecation; 33.65s |
| Ruff lint: `app tests migrations` | Passed |
| Ruff format: `app tests migrations` | 161 files already formatted |
| Frontend `npm test -- --run` | 211 passed in 29 files; 4.38s |
| Frontend `npm run lint`, `npm run typecheck`, `npm run build` | Passed; Next.js 16.2.10 Turbopack production build |
| Alembic `heads` | `20260912_0017 (head)`; no upgrade executed by Task 27 |
| Synthetic E2E Compose config and normal Compose structure | Passed, explicit synthetic env/project; normal service env files deliberately not resolved |
| Release validator and its negative graph/YAML/status/Dockerfile mutations | Passed; failed/cancelled mandatory jobs still block publish |
| Actual managed-URL, unsafe-production, and role-validation workflow snippets | Passed under synthetic dotenv-disabled settings |

Backend command, from `backend` (public synthetic credentials only):

```sh
env -i PATH="$PATH" APP_ENV=test \
  DATABASE_URL=postgresql://apex:test-password@localhost:5432/apex_arena \
  POSTGRES_PASSWORD=test-password REDIS_URL=redis://localhost:6379/15 \
  TEST_REPLAY_POSTGRES_URL=postgresql+asyncpg://task27:task24-synthetic@127.0.0.1:62485/apex_e2e_task27 \
  TEST_INGESTION_POSTGRES_URL=postgresql+asyncpg://task27:task24-synthetic@127.0.0.1:62485/apex_e2e_task27 \
  TEST_E2E_DATABASE_URL=postgresql://task27:task24-synthetic@127.0.0.1:62485/apex_e2e_task27 \
  LIVE_REPAIR_INTEGRATION=1 \
  .venv/bin/python -c 'from app.core.settings import Settings; Settings.model_config["env_file"]=None; import pytest; raise SystemExit(pytest.main(["-q","-ra","--tb=short"]))'
```

The three SQL URL suites use UUID schemas in a Task 27-created database on the
parent's disposable PostgreSQL at loopback 62485. The live fixture uses only
the verified parent-owned `apex-foundation-live-postgres` at loopback 55433
(`apex_test` / `repair_test_only`, database `apex_live_repair`) and
`apex-foundation-live-redis` at loopback 16379. Its intentional `drop_all` and
`flushdb` never targeted user services. Task 27 removes only its own database
and role; parent containers remain for independent phase review.

Frontend gates ran against a `git archive` export of that exact commit with a
copy of existing installed dependencies, rather than reading the working
copy's `.env.local`. The stripped environment supplied only PATH, disabled
telemetry, and synthetic public app/API URLs. No real dotenv file was read or
changed. From repository root, the other reproducible gates were:

```sh
backend/.venv/bin/ruff check backend/app backend/tests backend/migrations
backend/.venv/bin/ruff format --check backend/app backend/tests backend/migrations
bash -n scripts/test-release-workflow.sh
env -i PATH="$PWD/backend/.venv/bin:$PATH" scripts/test-release-workflow.sh .github/workflows/release.yml
docker compose --env-file scripts/e2e.env.example --project-name apex-task27-config -f docker-compose.e2e.yml config --quiet
docker compose --env-file scripts/e2e.env.example --project-name apex-task27-local-config -f docker-compose.yml config --no-env-resolution --quiet
```

The last command validates the normal Compose model without resolving its
real `.env` service file; it does not validate actual deployment credentials.

### Reused exact-revision evidence

- Task 24 at `db2d2c8bc4abe8464862a23deb170a42419bb35f`: fresh isolated
  production-image stack, real migrations to 0017, guarded synthetic seed,
  **13 unchanged Playwright tests passed in 16.5s**, no retries/skips. The
  internal-network runner tested standard/sprint calendars, metadata-only
  upcoming schedules, five-agent discussion/evidence, authenticated lap-six
  battle/pit replay, and six viewport widths. This was synthetic local E2E,
  not real OpenF1 data or hosted CI.
- Task 25 at `2e3eb4abaceaebbf98b79806d9d8d6c0ef4a2a0c`: release validator
  RED/GREEN and negative mutations, actual Settings snippet checks, mandatory
  PostgreSQL fixture wiring, and 698 backend tests plus one intentional live
  opt-in skip. Task 27 now exercises that live test too.
- Task 26 at the tested `7f7513e`: all 12 formatter-only files, including the
  legacy 0012 migration, proved AST-equal to their parent with location
  attributes ignored. The previous 11 app/test formatting failures and one
  migration failure are resolved.

Task 25 changed non-E2E workflow/development-dependency validation, and Task 26
changed formatting only. A diff confirmed the Task 24 E2E job/fixtures,
browser assertions, Compose file, and Dockerfiles are unchanged, so Task 27
does not duplicate that browser run. Machine-local task reports retain the
exact commands, failed attempts, receipts and artifact paths under
`.superpowers/sdd/2026-09-10-foundation-and-release-safety/`; those ignored
files are not claimed as durable repository artifacts.

## Tested, deployed, and pending

| Boundary | Current Task 27 status |
| --- | --- |
| Repair code and operations Tasks 1–19 | Committed, pushed to `origin/sprints`, reviewed; historical record preserved |
| Historical Italian GP local data and browser smoke | Tested September 6; not re-run by Task 27 |
| Production application/schema state | Not inspected by Task 27; September 6 observation only |
| Promotion to `main` or production deploy | Not performed |
| Migration from the historical production revision to current head 0017 | Not performed or authorized here |
| Tasks 20–21 authentication | Complete: deployed API proxy fails closed; separate operator credential protects replay mutations, not the proxy hop alone |
| Tasks 22–23 interrupted-worker recovery | Complete: durable replay ownership/fencing and ingestion heartbeat recovery, with legacy-worker drain prerequisites |
| Tasks 24–25 CI/release | Complete: guarded synthetic E2E and mandatory release graph checks; no hosted execution/publication claimed |
| Task 26 formatting | Complete; AST-equivalent and combined Ruff gate clean |
| Task 27 phase verification | Local gates recorded above; documentation-only closure, independent whole-foundation review follows |

Tasks 20–21 use exact constant-time credential checks. Replay's canonical
Base64 UTF-8 operator header is forwarded, never minted by the public proxy;
public reads/SSE remain anonymous. Tasks 22–23 preserve stored facts/cursors
while making interrupted work explicitly retryable. Replay uses a 30-second
lease, 10-second heartbeat and one deferred startup sweep; healthy-peer
controls return retryable 409 rather than silently writing concurrently.
Ingestion uses a 30-minute heartbeat-age threshold and one startup sweep.

The first rollout must drain every legacy replay-capable API/combined/all
process before new lease-aware replay workers start, and drain legacy
historical ingestion workers before heartbeat recovery starts. An ownerless
legacy worker is not safely distinguishable from a crashed one. Legacy
ingestion rows retain null heartbeat and fall back to started_at; a recently
drained run may require waiting past the threshold or explicit reconciliation.
See [replay recovery](replay-worker-recovery.md) and
[ingestion recovery](ingestion-run-recovery.md) for the exact rollout/rollback
constraints. Cross-process replay command routing, independent per-viewer
playback, shared-reducer isolation and transactional exactly-once external
effects remain outside this phase.

Remaining issues: Task 24's lockfile installation reported 11 dependency audit
findings (4 moderate, 6 high, 1 critical); no audit remediation or fresh registry
audit is claimed here. The existing TestClient/httpx deprecation remains.
Previously exposed operator credentials must be rotated before deployment;
no real value is recorded here and no rotation was performed. Passing these
local gates does not resolve those issues or establish production GO.

The current `sprints` artifact must not be described as production-ready or
rolled out. A future rollout needs a fresh production read-only audit, a
recoverable database backup/branch, the legacy-worker drains above, approved
one-shot migrations through 0017 against the direct DSN, dependency/credential
triage, compatible application deployment, role-aware health checks, and a
one-room backfill canary. None is authorized or performed by this phase gate.
Foundation closure does not mean the broader master roadmap is complete or
that 150 separate pushes have been made. The controller ledger contains 54
pushes before this documentation commit across the sprint, not 54 today.

## Evidence retention

The original local evidence remains under the ignored `tmp/` directory in the
working copy (provider probes, backfill results, room/GPS audits, rollback
verification, and backend/frontend/browser logs). Those machine-local files are
not part of this committed documentation artifact and must not be treated as
durable repository evidence. Their filenames and the original plan chronology
are preserved in the historical plan:
[`2026-09-06-live-room-repair.md`](superpowers/plans/2026-09-06-live-room-repair.md).

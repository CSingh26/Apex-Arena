<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Apex Arena — Low-Cost Production Deployment Audit

_Audited 2026-07-19 on branch `deployment/low-cost-production`. Every claim below was checked against the actual repository, not assumed._

Target architecture: Vercel (portfolio domain + Apex Arena frontend origin) → Railway
(FastAPI API + OpenF1 ingestor) → Neon PostgreSQL + Upstash Redis.

## Classification legend

| Status | Meaning |
|---|---|
| ✅ Ready | Works as-is against the target platform |
| ⚙️ Config | No code change; requires environment configuration |
| 🔧 Code | Required a code change (made on this branch) |
| ⛔ Blocker | Must be resolved before production traffic |
| 💡 Future | Optional improvement, not required for launch |

## Frontend

| Component | Evidence | Status |
|---|---|---|
| Next.js 16.2.10, App Router, `output: "standalone"` | `frontend/next.config.ts`, `package.json` | ✅ Ready (Vercel) |
| Base path support | `next.config.ts` derives `basePath` from `NEXT_PUBLIC_APP_BASE_PATH` | ✅ Ready |
| Path helper (`withBasePath`, `apiPath`, `stripBasePath`) | `frontend/src/lib/app-paths.ts` | ✅ Ready |
| `publicUrl()` for canonical/OG/share links | Added; root emits no trailing slash | 🔧 Code |
| `NEXT_PUBLIC_API_BASE_PATH` honoured | Previously declared but unused; now drives `API_BASE_PATH` | 🔧 Code |
| Same-origin API client | `frontend/src/lib/api.ts` calls `apiPath()`; no backend host in bundle | ✅ Ready |
| Server-side API proxy | `frontend/src/app/api/[[...path]]/route.ts` | 🔧 Code (see below) |
| Backend origin resolution | Read the wrong variable names and fell back to `localhost` in production | 🔧 Code — now `BACKEND_INTERNAL_URL`/`BACKEND_PUBLIC_ORIGIN`, fails closed with 503 |
| Backend proxy token | Was stripped and never re-attached | 🔧 Code — minted server-side from `APEX_ARENA_BACKEND_PROXY_TOKEN` |
| Routes `/rooms`, `/rooms/[slug]` with legacy redirect | `next.config.ts` redirects `/race-rooms*` permanently | ✅ Ready |
| Production build under `/apex-arena` | Verified: `npm run build` succeeds, 7 routes emitted | ✅ Ready |
| SSE long-lived connections on Vercel | Function max duration caps stream lifetime | ⚙️ Config — mitigated by `Last-Event-ID` reconnect; see `apex-arena-vercel-deployment.md` |

## Backend (FastAPI on Railway)

| Component | Evidence | Status |
|---|---|---|
| Process roles `api` / `ingestor` / `all` | `backend/app/core/settings.py`, `app/runtime.py`, `app/ingestor.py` | ✅ Ready |
| Production rejects `APP_PROCESS_ROLE=all` | `validate_runtime_contract` | ✅ Ready |
| API role cannot auto-connect MQTT in production | `validate_runtime_contract` | ✅ Ready |
| Singleton ingestor lease | `pg_try_advisory_lock` held for process lifetime, `storage/database.py` | ✅ Ready |
| Railway-injected `PORT` | Entrypoint hardcoded 8000 | 🔧 Code — now reads `PORT` |
| Uvicorn proxy headers | `--proxy-headers --forwarded-allow-ips` in `app/runtime.py` | ✅ Ready |
| Bind `0.0.0.0` | `app/runtime.py` | ✅ Ready |
| Non-root container | `backend/Dockerfile` creates and uses `apex` | ✅ Ready |
| Health endpoints `/health/live`, `/health/ready`, `/health/provider` | `app/api/routes.py`, `app/ingestor.py` | ✅ Ready |
| No local filesystem dependency | No writes outside logging | ✅ Ready |
| Structured JSON logging | `LOG_FORMAT=json` | ⚙️ Config |
| Secrets never logged | `SecretStr` masking; handlers log `type(exc).__name__` | ✅ Ready |
| Proxy-token origin protection | Did not exist | 🔧 Code — `app/api/proxy.py` middleware, constant-time compare, 403 |
| Public host/proto derivation | Did not exist | 🔧 Code — trusted-host allowlist prevents header spoofing |
| Graceful shutdown | FastAPI lifespan closes services and releases the lease | ✅ Ready |
| CORS | Explicit origin list, `allow_credentials=False` | ⚙️ Config |

## Neon PostgreSQL

| Component | Evidence | Status |
|---|---|---|
| TLS required in production | `validate_runtime_contract` rejects a non-TLS DSN | ✅ Ready |
| asyncpg driver | `async_database_url` rewrites to `postgresql+asyncpg://` | ✅ Ready |
| Neon libpq parameters (`sslmode`, `channel_binding`) | asyncpg rejects both at connect time; a pasted Neon string would fail | 🔧 Code — normalized to `ssl=` and unsupported keys dropped |
| Single managed DSN | `POSTGRES_PASSWORD` was mandatory and cross-checked | 🔧 Code — optional; cross-check retained when supplied |
| Conservative pooling | Engine used SQLAlchemy defaults (5 + 10 overflow) | 🔧 Code — `DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2`, timeout 15s, recycle 300s |
| Pooled vs direct endpoint | Session advisory locks break under transaction pooling | 🔧 Code — `DATABASE_MIGRATION_URL` used by Alembic **and** the ingestor |
| Alembic migrations | `migrations/env.py` now targets the direct DSN | 🔧 Code |
| One-shot migration strategy | Container `CMD` ran `alembic upgrade head` on every start | ⚙️ Config — use `scripts/run-production-migrations.sh` as a release step |
| Telemetry growth | Raw/normalized events grow unbounded | 🔧 Code — opt-in retention settings added (default `0` = no pruning) |
| Autosuspend cold start | Free-tier compute suspends when idle | ⚙️ Config — `pool_pre_ping` recovers; first request is slow |

## Upstash Redis

| Component | Evidence | Status |
|---|---|---|
| TLS required in production | `rediss://` enforced by the settings validator | ✅ Ready |
| Commands used | `PING`, `XADD` (`MAXLEN ~`), `XREVRANGE`, `XREAD` (± `BLOCK`) — `storage/redis.py` | ✅ Ready — all Upstash-supported |
| No pub/sub, Lua, transactions, consumer groups | Verified absent | ✅ Ready |
| No unsafe deserialization | JSON only, never pickle | ✅ Ready |
| Client tuning | No socket/connect timeout or health-check interval | 🔧 Code — added and configurable |
| Blocking read vs socket timeout | Room stream blocked 15s uncapped; a 5s socket timeout would abort **every idle heartbeat** and report a healthy stream as degraded | 🔧 Code — block capped at 10s, effective socket timeout derived above it |
| Bounded stream length | `MAXLEN` on every publish | ✅ Ready |
| Command budget | Each SSE client polls continuously | ⚙️ Config — budget with the formula in `upstash-setup.md` |

## Containers and CI

| Component | Evidence | Status |
|---|---|---|
| Backend Dockerfile | Non-root, slim, no `.env` copied | ✅ Ready |
| Frontend Dockerfile | Non-root, standalone, only `NEXT_PUBLIC_*` build args | ✅ Ready (unused if the frontend runs on Vercel) |
| `.dockerignore` excludes `.env*` | Both contexts | ✅ Ready |
| Compose datastores bound to loopback | `docker-compose.yml` | ✅ Ready (local only) |
| CI quality gates | Lint, typecheck, tests, builds, Trivy CRITICAL | ✅ Ready |
| Deployment validation | Did not exist | 🔧 Code — manifests, managed URL handling, production guards, role startup, scripts, secret scan, doc links |
| Gated deployment jobs | Did not exist | 🔧 Code — disabled unless `RAILWAY_DEPLOY_ENABLED` / `VERCEL_APEX_DEPLOY_ENABLED` is `true` |
| GitHub Actions pinned by SHA | Only Trivy is pinned | 💡 Future |

## Remaining blockers before production traffic

These are **provisioning and configuration** steps, not code defects. No code-level
blocker remains on this branch.

1. ⛔ Neon, Upstash and Railway resources do not exist yet. Nothing was created.
2. ⛔ The portfolio repository has no `/apex-arena` rewrite. Exact middleware is in
   `portfolio-vercel-integration.md`; that repo was deliberately not modified.
3. ⛔ `APEX_ARENA_PROXY_TOKEN` (Railway) must equal `APEX_ARENA_BACKEND_PROXY_TOKEN`
   (Apex Vercel project). A mismatch makes the backend answer `403` to everything.
4. ⛔ Migrations must be run once via `scripts/run-production-migrations.sh` against the
   **direct** Neon endpoint before the first rollout.
5. ⛔ Vercel SSE duration limits must be accepted, and reconnect behaviour observed under a
   real session, before relying on live mode.

## Optional future improvements

- 💡 Cloudflare R2 archive for large telemetry (`REPLAY_ARCHIVE_ENABLED` reserves the switch).
- 💡 Application-layer rate limiting; today expensive operations are internal-key gated.
- 💡 Pin all GitHub Actions by immutable commit SHA.
- 💡 Move `pytest` past PYSEC-2026-1845 (dev-only, absent from the production image).

<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Low-Cost Production Architecture

How Apex Arena is deployed for the lowest sustainable cost without pretending the
compromises do not exist.

> Current direction, 2026-07-20: deploy from `main` and use `APP_PROCESS_ROLE=combined`
> for the one-service backend. The older `APP_PROCESS_ROLE=all` notes are superseded.

Grounded in:

- `backend/app/core/settings.py` — `app_process_role`, `validate_runtime_contract`,
  `async_migration_database_url`
- `backend/app/runtime.py` — the single container entrypoint
- `backend/app/ingestor.py` — the ingestor ASGI app and the singleton lease
- `backend/app/storage/database.py` — `acquire_ingestor_lease` / `release_ingestor_lease`
- `backend/app/services/container.py` — which DSN each role connects with
- `backend/app/api/proxy.py` — `ProxyContextMiddleware`
- `frontend/src/app/api/[[...path]]/route.ts` — the server-side backend proxy
- `deploy/railway/api.toml`, `deploy/railway/ingestor.toml`

Companion documents: [`neon-setup.md`](./neon-setup.md),
[`upstash-setup.md`](./upstash-setup.md),
[`apex-arena-vercel-deployment.md`](./apex-arena-vercel-deployment.md),
[`portfolio-vercel-integration.md`](./portfolio-vercel-integration.md).

---

## Where each piece runs

| Component | Platform | Why there |
| --- | --- | --- |
| Public domain + rewrite | Vercel (portfolio project) | Owns `chaitanyasingh.org`; the rewrite keeps one public origin |
| Apex Arena frontend | Vercel (second project) | Next.js on Vercel is the cheapest correct option; no container to keep warm |
| FastAPI API | Railway | Long-lived process, SSE fan-out, needs persistent connections |
| OpenF1 ingestor | Railway | Long-lived MQTT subscription; cannot run on a serverless function |
| PostgreSQL | Neon | Managed, free-tier entry, direct + pooled endpoints |
| Redis Streams | Upstash | Managed, free-tier entry, native Redis protocol |

**Railway runs only the FastAPI API and the ingestor.** The frontend is not on Railway.
Putting it there would mean a third always-on container for something Vercel serves for
free.

## Full request flow

```
                            ┌────────────────────────────────────────────┐
   browser                  │  everything below is invisible to the user │
      │                     └────────────────────────────────────────────┘
      │  https://chaitanyasingh.org/apex-arena/...
      ▼
┌──────────────────────────────────────────────────────────────────┐
│ VERCEL PROJECT 1 — portfolio (owns the domain)                   │
│   middleware.ts                                                  │
│     matcher: /apex-arena, /apex-arena/:path*                     │
│     NextResponse.rewrite(APEX_ARENA_ORIGIN + pathname)  [NOT 3xx] │
│     sets  x-apex-proxy-token   = APEX_ARENA_PROXY_TOKEN          │
│           x-apex-public-host   = chaitanyasingh.org              │
│           x-apex-public-proto  = https                           │
│           x-apex-original-path = /apex-arena/...                 │
└───────────────────────────────┬──────────────────────────────────┘
                                │ server-side fetch, address bar unchanged
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ VERCEL PROJECT 2 — Apex Arena frontend (origin only, no domain)  │
│   Next.js 16, basePath = /apex-arena                             │
│   pages + assets under /apex-arena/_next/...                     │
│                                                                  │
│   /apex-arena/api/*  →  frontend/src/app/api/[[...path]]/route.ts│
│     • DELETES any inbound x-apex-proxy-token                     │
│     • mints x-apex-proxy-token = APEX_ARENA_BACKEND_PROXY_TOKEN  │
│     • forwards x-apex-public-host / -proto and x-forwarded-*     │
│     • origin = BACKEND_INTERNAL_URL ?? BACKEND_PUBLIC_ORIGIN     │
│                ?? INTERNAL_BACKEND_URL ?? BACKEND_URL            │
│                ?? localhost:8000 (non-production only)           │
│     • fails closed with 503 when unset in production             │
└───────────────────────────────┬──────────────────────────────────┘
                                │ server-side fetch (browser never sees Railway)
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ RAILWAY SERVICE A — FastAPI API   (APP_PROCESS_ROLE=api)         │
│   entrypoint: python -m app.runtime  →  uvicorn app.main:app     │
│   ProxyContextMiddleware (outermost, before CORS):               │
│     hmac.compare_digest(x-apex-proxy-token, APEX_ARENA_PROXY_TOKEN)│
│     mismatch/missing → 403 "Direct origin access is not permitted"│
│     EXEMPT: /health/live  (platform probe, exposes no state)     │
│   routes: /health/live, /health/ready, /health/provider, /health, │
│           /api/... , SSE room + session streams                  │
│   NEVER subscribes to OpenF1 MQTT in production (validator)      │
└───────┬──────────────────────────────────────────┬───────────────┘
        │ pooled DSN                               │ rediss:// (XREAD BLOCK)
        ▼                                          ▼
┌────────────────────┐                    ┌──────────────────────┐
│ NEON PostgreSQL    │                    │ UPSTASH Redis        │
│  -pooler endpoint  │                    │  Streams transport   │
│  direct endpoint   │◄───────┐           │                      │
└────────────────────┘        │           └──────────▲───────────┘
        ▲                     │ direct DSN           │ XADD
        │ (Alembic, direct)   │ (session advisory    │
        │                     │  lock)               │
┌───────┴─────────────────────┴──────────────────────┴───────────┐
│ RAILWAY SERVICE B — ingestor   (APP_PROCESS_ROLE=ingestor)      │
│   entrypoint: python -m app.runtime                             │
│     → uvicorn app.ingestor:create_ingestor_app --factory        │
│   lifespan:                                                     │
│     acquire_ingestor_lease()  →  pg_try_advisory_lock(1095782232)│
│       false → RuntimeError, process refuses to start            │
│     if OPENF1_LIVE_AUTO_CONNECT: start_live_services()          │
│   exposes ONLY /health/live and /health/provider                │
│   public networking: disabled                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ MQTT over TLS, authenticated
                             ▼
                   ┌──────────────────────┐
                   │ OpenF1 (paid tier)   │
                   │ mqtt.openf1.org:8883 │
                   └──────────────────────┘
```

Both hops between Vercel projects and Railway are **server-side fetches**. The browser
issues exactly one origin's worth of requests: `chaitanyasingh.org`.

## The single entrypoint

`backend/app/runtime.py` is the only start command either Railway service needs:

```python
if len(sys.argv) > 1 and sys.argv[1] == "migrate":
    os.execvp("alembic", ["alembic", "upgrade", "head"])

target = ("app.ingestor:create_ingestor_app"
          if settings.app_process_role == "ingestor" else "app.main:app")
port = os.getenv("PORT", "8000")
```

Consequences worth knowing:

- **One Docker image, one start command, two services.** `APP_PROCESS_ROLE` alone decides
  whether the container becomes the API or the ingestor. `deploy/railway/*.toml` both set
  `startCommand = "python -m app.runtime"`.
- **`PORT` is read from the environment**, defaulting to `8000`. Railway injects it; do not
  hard-code a port in the service settings.
- **`python -m app.runtime migrate`** is the underlying mechanism: it `execvp`s straight into
  `alembic upgrade head`, so the container exits with Alembic's status. It takes no lock of
  its own and offers no dry run. **Do not use it as the deployment procedure** — use
  `scripts/run-production-migrations.sh`, described under [Deployment order](#deployment-order).
- `--proxy-headers` and `--forwarded-allow-ips` (default `127.0.0.1`, overridable with
  `FORWARDED_ALLOW_IPS`) are passed to uvicorn.

---

## Mode 1 — Combined mode (one Railway service)

One Railway service, `APP_PROCESS_ROLE=combined`, **one replica**, FastAPI and worker duties in
the same process. `app/main.py` takes the singleton lease before starting live services or
recent-session reconciliation:

```python
if settings.app_process_role == "combined" and worker_enabled:
    if not await services.database.acquire_ingestor_lease():
        raise RuntimeError("Another Apex Arena ingestor owns the singleton lease")
```

This mode is production-capable when `DATABASE_MIGRATION_URL` is set for the direct Neon endpoint
and replicas stay at 1 while worker duties are enabled. `validate_runtime_contract` refuses an
API-only process with worker settings:

```python
if self.recent_session_reconciliation_enabled and self.app_process_role == "api":
    raise ValueError("Recent-session reconciliation requires ingestor or combined role")
```

The process fails during settings construction, not after it starts serving requests.

### Combined mode does take the singleton lease

`app/main.py` calls `acquire_ingestor_lease()` before `start_live_services()` and raises if
it cannot get it, exactly as `app/ingestor.py` does — the same advisory lock id, the same
fail-fast behaviour. A second combined-mode container, or a Railway rolling deploy that
briefly overlaps old and new containers, will find the lease held and refuse to start rather
than run a duplicate MQTT subscription.

Two conditions bound that guarantee, and both matter:

- **The lease is only taken when ingestion is actually starting** — the branch requires
  `OPENF1_LIVE_AUTO_CONNECT=true`. A combined container with auto-connect off takes no lease,
  which is correct, because it also ingests nothing.
- **The lease is only as reliable as the connection under it.** `container.py` selects
  `async_migration_database_url` for `APP_PROCESS_ROLE=ingestor` and for `combined` when worker
  duties are enabled. If that direct endpoint is missing, startup fails before serving traffic.

Settings requires `DATABASE_MIGRATION_URL` for worker duties in staging or production, so
migrations and advisory locks are configured on the direct endpoint either way.

Operational discipline still applies:

- Keep `numReplicas = 1`. Never raise it.
- Watch `/health/provider` for a `connection_state` that flaps between sessions.

### What it costs and what it buys

| | |
| --- | --- |
| **Cost** | One Railway service instead of two — roughly half the container spend |
| **Buys** | Fewer moving parts, one variable set, one deploy |
| **Gives up** | Independent restarts; blast-radius isolation; production eligibility |

An ingestor crash restarts the API with it, dropping every open SSE connection. An API
memory spike can starve the MQTT consumer. Both failure modes are real, and both are
invisible until a live session is running.

---

## Mode 2 — RECOMMENDED PRODUCTION MODE (two Railway services)

Two Railway services from the same image and the same start command, **one replica each**.

| | API service | Ingestor service |
| --- | --- | --- |
| `APP_ENV` | `production` | `production` |
| `APP_PROCESS_ROLE` | `api` | `ingestor` |
| `OPENF1_LIVE_AUTO_CONNECT` | `false` (enforced) | `true` |
| ASGI target | `app.main:app` | `app.ingestor:create_ingestor_app` (`--factory`) |
| `DATABASE_URL` | Neon **pooled** | Neon pooled (present but unused for the engine) |
| `DATABASE_MIGRATION_URL` | optional | Neon **direct** — this is the DSN it actually connects with |
| Public networking | enabled | **disabled** |
| Replicas | 1 | **1 — never more** |
| Healthcheck | `/health/live` | `/health/live` |

Two more production validators back this up:

```python
if (self.app_env == "production" and self.app_process_role == "api"
        and self.openf1_live_auto_connect):
    raise ValueError("API processes cannot auto-connect OpenF1 live ingestion in production")
```

so an API service cannot start ingesting by accident, plus the production requirements for
TLS on `DATABASE_URL`, `rediss://` on `REDIS_URL`, and
`DEBUG_INGESTION_ENABLED` / `DEVELOPMENT_FIXTURE_ENABLED` / `ROOM_DIAGNOSTICS_ENABLED`
all being false.

### Why separation is worth the second container

- **The lease runs over a guaranteed-direct DSN.** `container.py` gives the `ingestor` role
  `async_migration_database_url`, and settings refuses to start the role without
  `DATABASE_MIGRATION_URL`. Combined mode has no equivalent enforcement of the endpoint the
  lock actually sits on.
- **Independent restart.** Redeploying the API does not interrupt MQTT; restarting the
  ingestor does not drop SSE clients.
- **Independent scale.** If SSE fan-out ever needs a second API replica, that is safe.
  Adding an ingestor replica never is.
- **Smaller blast radius.** An ingestion crash loop cannot take the read path down.

### Production must never default to multiple ingestion replicas

Say it plainly: **`numReplicas` on the ingestor service is 1, permanently.** Scaling the
ingestor is not a capacity lever — it is a correctness bug. A second replica either fails
to start (the lease is held) or, if the lease is undermined by a pooled connection, runs a
duplicate MQTT subscription that double-writes every event. `deploy/railway/ingestor.toml`
carries this warning in-file; keep it there.

The API service is the only thing in this stack that may be scaled horizontally, and even
then only after checking the Neon connection budget (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`
per replica) and the Upstash concurrent-connection cap.

---

## The singleton advisory lease

`Database.acquire_ingestor_lease` in `backend/app/storage/database.py`:

```python
connection = await self.engine.connect()
acquired = bool(await connection.scalar(
    text("SELECT pg_try_advisory_lock(:lock_id)"),
    {"lock_id": 1_095_782_232},
))
if not acquired:
    await connection.close()
    return False
self._ingestor_lease_connection = connection
```

Mechanics:

1. `pg_try_advisory_lock` is **non-blocking**. It returns `true` or `false` immediately —
   a losing ingestor fails fast rather than hanging.
2. The lock is **session-scoped**. It belongs to the PostgreSQL backend session, and it is
   held for as long as that connection lives.
3. The connection is deliberately **kept outside the pool**, stored on
   `_ingestor_lease_connection`, so nothing can return it to the pool and recycle it.
4. Both ingesting entrypoints treat failure as fatal — `app/ingestor.py` and, for
   `APP_PROCESS_ROLE=combined`, `app/main.py`:
   `raise RuntimeError("Another Apex Arena ingestor owns the singleton lease")`.
   The container exits and Railway's `ON_FAILURE` policy retries.
5. `release_ingestor_lease` runs `pg_advisory_unlock` and closes the connection; `close()`
   calls it, and the lifespan `finally` block calls `close()`.
6. If the process dies without unlocking, PostgreSQL releases the advisory lock when the
   backend session ends. Recovery is automatic — no manual cleanup, no stale lock file.

The lock id `1_095_782_232` is a fixed constant. Nothing else in the codebase uses advisory
locks, so there is no collision risk within the app; a different application sharing the
database could in principle collide, which is one more reason not to share the database.

### Why the ingestor uses the DIRECT (non-pooled) Neon DSN

`backend/app/services/container.py` selects the DSN by process behavior:

```python
self.database = Database(
    settings.async_process_database_url,
    ...
)
```

The process-aware property selects the direct endpoint for the ingestor and for combined mode
while live ingestion is enabled. API-only processes keep the pooled endpoint.

and `async_migration_database_url` falls back to the runtime DSN when
`DATABASE_MIGRATION_URL` is unset:

```python
if self.database_migration_url is None:
    return self.async_database_url
return self._asyncpg_dsn(self.database_migration_url.get_secret_value())
```

Neon's pooled endpoint is PgBouncer in **transaction pooling** mode. A server connection is
handed back to the pool at the end of every transaction, so consecutive statements from one
client can land on different backend sessions. Against a session-scoped advisory lock that
is fatal:

- `pg_try_advisory_lock` can return `true` and then lose the lock when the connection is
  recycled — the singleton guarantee silently evaporates and two ingestors coexist.
- `pg_advisory_unlock` can be issued on a session that never held the lock, orphaning it.
- asyncpg's named prepared statements are unsafe across transaction pooling, producing
  intermittent `prepared statement "__asyncpg_stmt_..." does not exist`.

**So: set `DATABASE_MIGRATION_URL` to the Neon direct (no `-pooler`) endpoint on the
ingestor service.** Leaving it unset is not a neutral default — it silently downgrades the
ingestor onto whatever `DATABASE_URL` holds, which on a correctly configured service is the
pooled endpoint. That is the single most damaging misconfiguration available in this stack,
because it fails quietly rather than loudly.

Alembic uses the same property: `backend/migrations/env.py` builds its engine from
`get_settings().async_migration_database_url` with `poolclass=pool.NullPool`. Migrations
must run against the direct endpoint for the same reasons.

The API service, by contrast, opens many short-lived sessions and takes no session-scoped
locks, so the pooled endpoint is correct and cheaper there.

---

## Cost and reliability trade-off, stated honestly

| Property | Combined (`all`, staging) | Split (`api` + `ingestor`, production) |
| --- | --- | --- |
| Railway services | 1 | 2 |
| Container cost | ~1× | ~2× |
| Singleton guarantee | advisory lock taken by `main.py`, over `DATABASE_URL` | advisory lock over an enforced direct DSN |
| Deploy interrupts SSE | always | only when the API is redeployed |
| Ingestion crash impact | takes the API down | isolated |
| Allowed with `APP_ENV=production` | **no** — validator rejects it | yes |
| Neon connections | one pool, one endpoint | two pools; ingestor also holds one lease connection outside its pool |

The honest summary: combined mode saves one container's worth of money and gives up isolation.
A worker crash can take the read path with it, and every redeploy drops every open SSE
connection. It keeps the singleton lease and uses the direct Neon endpoint when
`DATABASE_MIGRATION_URL` is configured. Combined mode remains **single-replica while worker
duties are enabled**.

Other costs this architecture accepts deliberately:

- **Two rewrite hops** (portfolio → Apex Arena → Railway) add latency to every API call and
  double the Vercel function invocations counted against plan limits.
- **SSE cannot stay open indefinitely** through a Vercel Function. See the SSE section of
  `apex-arena-vercel-deployment.md`; the client reconnects with backoff and the backend
  replays from `Last-Event-ID` / `after_sequence`, bounded by `ROOM_STREAM_BACKLOG_LIMIT`.
- **Neon free-tier autosuspend** means a cold first request, and it terminates the idle
  lease connection during long gaps between race weekends. An ingestor restart after a long
  idle period is expected, not a fault.
- **No platform here is guaranteed to stay free.** See
  [`deployment-cost-controls.md`](./deployment-cost-controls.md).

---

## Health endpoints, and what each one actually tells you

| Endpoint | Served by | Meaning |
| --- | --- | --- |
| `/health/live` | API and ingestor | Process is up. No network dependencies. **Exempt from proxy-token enforcement** — this is what Railway probes |
| `/health/ready` | API only | `Database.health_check` and `RedisStore.health_check` in parallel; 200 when both are ready, **503** otherwise |
| `/health/provider` | API and ingestor | Ingestion state. On the ingestor it reads `services.openf1_live.status()` directly. On the API (`role == "api"`) it reads `event_bus.latest_connection_status()` from Redis, reporting `source: "redis_status_stream"`. 200 when `LIVE_MODE_ENABLED` is false or state is `CONNECTED`/`DISABLED`; **503** otherwise |
| `/health` | API | Full component report |

`/health/provider` is the endpoint that makes split-mode observable from the API side
without the API touching MQTT: the ingestor publishes its connection status to a Redis
stream, and the API reads the latest entry.

## Deployment order

1. Provision Neon ([`neon-setup.md`](./neon-setup.md)) and Upstash
   ([`upstash-setup.md`](./upstash-setup.md)) in the same region as the Railway region.
2. Run migrations against the **direct** endpoint with `DATABASE_MIGRATION_URL` set, as a
   one-off, using `scripts/run-production-migrations.sh`:

   ```bash
   scripts/run-production-migrations.sh --check   # current vs head; applies nothing
   scripts/run-production-migrations.sh           # upgrade to head
   ```

   Prefer the script over calling `python -m app.runtime migrate` directly. It:

   - takes its own advisory lock (id `1_095_782_233`, deliberately **distinct** from the
     ingestor lease `1_095_782_232`) so two concurrent runs cannot collide — the loser exits
     `75` (`EX_TEMPFAIL`), which is safe to retry;
   - prefers `DATABASE_MIGRATION_URL` and falls back to `DATABASE_URL`, erroring out if
     neither is set;
   - prints only the variable name it chose and a redacted hostname — never a DSN or credentials;
   - supports `--check` as a dry run before a release.

   The lock is session-scoped, so a crashed run releases it and cannot wedge later
   deployments. Irreversible migrations still cannot be undone — read the migration section
   of [`deployment-rollback-runbook.md`](./deployment-rollback-runbook.md) **before** running
   this against production.
3. Deploy the Railway API service (`deploy/railway/api.toml`). Confirm `/health/live` 200
   and a tokenless call to any other path returns 403.
4. Deploy the Railway ingestor service (`deploy/railway/ingestor.toml`), public networking
   disabled. Confirm the lease is held (see the `pg_locks` query in `neon-setup.md`).
5. Deploy the Apex Arena Vercel project
   ([`apex-arena-vercel-deployment.md`](./apex-arena-vercel-deployment.md)).
6. Add the rewrite to the portfolio project
   ([`portfolio-vercel-integration.md`](./portfolio-vercel-integration.md)).

Rolling back any of these is covered in
[`deployment-rollback-runbook.md`](./deployment-rollback-runbook.md).

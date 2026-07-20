<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Railway Deployment Runbook (Apex Arena Backend)

Step-by-step operational procedure for deploying the Apex Arena **backend** to Railway.

> **The frontend is not deployed on Railway.** The Next.js application runs as a separate
> Vercel project and is served to the public through the portfolio domain at
> `https://chaitanyasingh.org/apex-arena`. Railway hosts only the FastAPI API and the
> OpenF1 ingestor. If you are looking for the frontend, see
> [`apex-arena-vercel-deployment.md`](./apex-arena-vercel-deployment.md) and
> [`portfolio-vercel-integration.md`](./portfolio-vercel-integration.md).

Architecture background for everything below is in
[`low-cost-production-architecture.md`](./low-cost-production-architecture.md); the
platform-readiness assessment is in
[`low-cost-deployment-audit.md`](./low-cost-deployment-audit.md).

All hostnames, tokens, and connection strings in this document are **placeholders**.
Never paste a real credential into a document, a commit, or a shell history.

---

## 1. Prerequisites

Provision the datastores **before** creating the Railway project. The backend refuses to
start without a valid `DATABASE_URL` and `REDIS_URL`, and production additionally requires
TLS on both.

| Prerequisite | Document | What you need out of it |
| --- | --- | --- |
| Neon PostgreSQL project | [`neon-setup.md`](./neon-setup.md) | The **pooled** DSN (`DATABASE_URL`) and the **direct** DSN (`DATABASE_MIGRATION_URL`) |
| Upstash Redis database | [`upstash-setup.md`](./upstash-setup.md) | A `rediss://` URL (`REDIS_URL`) — plaintext `redis://` is rejected in production |
| OpenF1 credentials | [`deployment-secrets.md`](./deployment-secrets.md) | `OPENF1_USERNAME` / `OPENF1_PASSWORD` for the ingesting role |
| A shared proxy token | [`deployment-secrets.md`](./deployment-secrets.md) | One long random value used as `APEX_ARENA_PROXY_TOKEN` here and `APEX_ARENA_BACKEND_PROXY_TOKEN` on the Apex Arena Vercel project |
| A GitHub account with access to this repository | — | Railway deploys from the repository, not from a local build |

Pick the Neon and Upstash regions first: the Railway region should match them
(see [§10](#10-region-selection)).

---

## 2. Create the Railway project and connect the repository

1. Create a new Railway project (empty, not from a template).
2. **New → GitHub Repo** and select `CSingh26/Apex-Arena`. Authorize the Railway GitHub
   app for the repository if prompted.
3. Choose the branch you intend to deploy. Railway watches that branch; every push to it
   triggers a build unless you disable automatic deploys ([§13](#13-deploy-triggers)).
4. Do not attach a Railway PostgreSQL or Redis plugin. Both datastores are external
   (Neon and Upstash) and adding Railway equivalents only duplicates spend.

---

## 3. Service build settings

Apply these to **every** backend service you create, in *Settings → Build*:

| Setting | Value | Why |
| --- | --- | --- |
| Root directory | `backend` | The build context must be the backend package; `pyproject.toml`, `app/`, `migrations/`, and `alembic.ini` all live there |
| Builder | Dockerfile | Reproducible, non-root, and identical to the image CI builds |
| Dockerfile path | `Dockerfile` (relative to the `backend` root directory) | With the root directory set to `backend`, the path is relative to it |

The tracked manifests carry the same values, so you can point a service at one instead of
setting them by hand:

- [`deploy/railway/api.toml`](../deploy/railway/api.toml) — API service (recommended production)
- [`deploy/railway/ingestor.toml`](../deploy/railway/ingestor.toml) — ingestor service (recommended production)
- [`deploy/railway/combined.toml`](../deploy/railway/combined.toml) — single combined service (staging only)
- [`railway.toml`](../railway.toml) — repository default for a single-service deployment

Note that [`railway.toml`](../railway.toml) uses `dockerfilePath = "backend/Dockerfile"`
because it is evaluated from the repository root, while the manifests under
`deploy/railway/` use `dockerfilePath = "Dockerfile"` because the service root directory is
already `backend`. Do not mix the two.

The container image itself is defined by [`backend/Dockerfile`](../backend/Dockerfile):
Python 3.12 slim, dependencies installed from `pyproject.toml`, a non-root `apex` user, and
`STOPSIGNAL SIGTERM` so FastAPI's lifespan shutdown runs (which is what releases the
ingestor lease cleanly).

---

## 4. Choose a topology

### 4a. RECOMMENDED — two services (`api` + `ingestor`)

Two Railway services built from the same repository, the same Dockerfile, and the same
start command. `APP_PROCESS_ROLE` alone decides what each container becomes.

| | API service | Ingestor service |
| --- | --- | --- |
| `APP_ENV` | `production` | `production` |
| `APP_PROCESS_ROLE` | `api` | `ingestor` |
| `OPENF1_LIVE_AUTO_CONNECT` | `false` | `true` |
| `APP_BASE_PATH` | `/apex-arena` | not required |
| `DATABASE_URL` | Neon **pooled** | Neon pooled |
| `DATABASE_MIGRATION_URL` | optional | Neon **direct** — the lease connects through this |
| `REDIS_URL` | `rediss://…` | `rediss://…` |
| `APEX_ARENA_PROXY_TOKEN` | required (secret) | not required |
| `INTERNAL_API_KEY` | required (secret) | not required |
| `OPENF1_USERNAME` / `OPENF1_PASSWORD` | not required | required (secrets) |
| `PUBLIC_PROXY_HOST` / `TRUSTED_PROXY_HOSTS` | `chaitanyasingh.org` | not required |
| `CORS_ALLOWED_ORIGINS` | `https://chaitanyasingh.org` | not required |
| Public domain | **enabled** | **disabled** |
| Replicas | 1 | **1 — never more** |

While the OpenF1 MQTT broker is refusing connections, set the ingestor value to `false` and use
the explicit, one-session historical REST workflow in
[`openf1-rest-backfill.md`](./openf1-rest-backfill.md). Historical backfill never starts merely
because the Railway service boots.

Two production validators in
[`backend/app/core/settings.py`](../backend/app/core/settings.py) enforce this split:
`APP_PROCESS_ROLE=all` is rejected outright in production, and `APP_PROCESS_ROLE=api`
combined with `OPENF1_LIVE_AUTO_CONNECT=true` is rejected as well — so an API container
cannot begin ingesting by accident.

### 4b. LOW-COST — one combined service (`all`)

One service running both roles in a single process. It halves the container count, and it
**cannot run in production**:

```python
if self.app_env == "production" and self.app_process_role == "all":
    raise ValueError("APP_PROCESS_ROLE=all is not allowed in production")
```

The failure happens while settings are constructed, so the process never reaches request
handling. There is no override. Combined mode therefore requires `APP_ENV=staging`, and
that is deliberate: `app/main.py` never acquires the singleton ingestor lease, so combined
mode has no protection against two overlapping containers double-subscribing to OpenF1
during a rolling deploy.

Use it for staging or a controlled single-instance beta. Set `APP_ENV=staging`,
`APP_PROCESS_ROLE=all`, `OPENF1_LIVE_AUTO_CONNECT=true`, both Neon DSNs, `REDIS_URL`, the
OpenF1 credentials, and a **staging-specific** `APEX_ARENA_PROXY_TOKEN`. Keep replicas at 1.

### Variables

Do not transcribe variables from this page. The authoritative, commented template is
[`.env.railway.example`](../.env.railway.example) — copy the keys from there into the
Railway service variables, marking everything flagged `(secret)` as a Railway secret.
[`deployment-secrets.md`](./deployment-secrets.md) explains what each value is, which side
of the proxy owns it, and how to rotate it.

---

## 5. Start command and role selection

Every service uses the same start command:

```
python -m app.runtime
```

[`backend/app/runtime.py`](../backend/app/runtime.py) reads `APP_PROCESS_ROLE` and selects
the ASGI target:

- `ingestor` → `app.ingestor:create_ingestor_app` (launched with `--factory`)
- `api` or `all` → `app.main:app`

It then execs uvicorn with `--host 0.0.0.0`, `--proxy-headers`, and
`--forwarded-allow-ips` (default `127.0.0.1`, overridable with `FORWARDED_ALLOW_IPS`).
The same entrypoint also accepts `python -m app.runtime migrate`, which execs
`alembic upgrade head` and nothing else — that is the migration job form
([§8](#8-run-migrations-once)).

---

## 6. PORT injection

Railway injects `PORT` into the container at runtime, and `app/runtime.py` reads it:

```python
port = os.getenv("PORT", "8000")
```

**Do not set `PORT` yourself and do not hardcode a port in the service settings.** The
`EXPOSE 8000` line in the Dockerfile is documentation for local use; the listening port in
production is whatever Railway supplies. Setting `PORT` manually to a value Railway is not
routing to produces a service that builds, starts, logs nothing unusual, and fails its
health check.

---

## 7. Health check path

Set the health check path to **`/health/live`** on every service, with a timeout of 30
seconds (the value in the manifests).

This is the correct probe specifically because it is the one path exempt from proxy-token
enforcement. [`backend/app/api/proxy.py`](../backend/app/api/proxy.py) declares:

```python
UNPROTECTED_PATHS = frozenset({"/health/live"})
```

Railway probes the container directly, without traversing the Vercel proxy chain, so it
never carries the `X-Apex-Proxy-Token` header. Any other path would answer `403` to the
platform probe, and the deployment would fail health checks forever while the application
was in fact perfectly healthy. `/health/live` also performs no network I/O — it does not
touch Neon or Upstash — so a slow datastore cannot cause a restart loop.

The other two endpoints are for humans and smoke tests, not for the platform probe:

| Endpoint | Served by | Meaning |
| --- | --- | --- |
| `/health/live` | API and ingestor | Process is up. No dependencies. Token-exempt. **This is the platform probe** |
| `/health/ready` | API | Neon and Upstash checked in parallel; `200` when both are ready, `503` otherwise |
| `/health/provider` | API and ingestor | Ingestion state. On the ingestor, read directly; on the API, read from the Redis status stream (`source: "redis_status_stream"`). `503` when degraded |
| `/health` | API | Full component report |

Do not point the platform health check at `/health/ready`: a Neon cold start or a brief
Upstash blip would then restart a working container.

---

## 8. Run migrations once

Migrations are a **release step**, not a startup step. The container `CMD` deliberately
does not run Alembic — replicas must not race to migrate, and a transaction pooler breaks
the session-scoped locks Alembic relies on.

Run this once per release, before rolling out the application, as a Railway one-off command
(or an equivalent release job), with `DATABASE_MIGRATION_URL` set to the **direct** Neon
endpoint:

```bash
# Dry run: report current revision vs head, apply nothing.
scripts/run-production-migrations.sh --check

# Apply.
scripts/run-production-migrations.sh
```

[`scripts/run-production-migrations.sh`](../scripts/run-production-migrations.sh) prefers
`DATABASE_MIGRATION_URL` and falls back to `DATABASE_URL` only if the former is unset. It
prints the chosen variable name and a redacted hostname but never a connection string, and it
serializes concurrent runs behind a PostgreSQL advisory lock — a second simultaneous
invocation exits `75` (`EX_TEMPFAIL`, safe to retry) rather than corrupting the schema.

**Rules:**

- Run it from exactly one place, once. Never add it to the service start command.
- Always use the direct endpoint. The Neon pooler in transaction mode silently breaks both
  Alembic and the advisory lock — see [`neon-setup.md`](./neon-setup.md).
- Deploy the application only after the migration exits `0`.

For a local operator invocation, export the pooled and direct URLs in the shell, run
`python -m alembic current`, `heads`, and `upgrade head` from `backend`, then unset both variables.
Shell values override the untracked development `.env`. Full commands and redacted verification
queries are in [`neon-setup.md`](./neon-setup.md#safe-local-operator-flow).

---

## 9. Replicas

| Service | Replicas | Rule |
| --- | --- | --- |
| API | 1 | May be scaled later, but only after checking the Neon connection budget (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW` per replica) and the Upstash concurrent-connection cap |
| Ingestor | **1, permanently** | Scaling it is a correctness bug, not a capacity lever |
| Combined (`all`) | **1** | Takes the same lease when `OPENF1_LIVE_AUTO_CONNECT=true`, so a second instance fails to start rather than double-writing. Still keep it at 1: the API and ingestion share one process, so a restart drops both. |

`app/ingestor.py` takes a session-level PostgreSQL advisory lease for the lifetime of the
process and refuses to start if another instance already holds it:

```python
if not await services.database.acquire_ingestor_lease():
    raise RuntimeError("Another Apex Arena ingestor owns the singleton lease")
```

A second ingestor replica therefore either crash-loops or — if the lease is undermined by a
pooled connection — runs a duplicate MQTT subscription that double-writes every event. Both
outcomes are worse than having one. `numReplicas = 1` is set in every manifest; keep it.

---

## 10. Region selection

Choose the Railway region to match the Neon region and the Upstash region. Every request
path in this stack crosses all three:

```
API container  →  Neon (query)  →  Upstash (stream read/write)
```

A cross-region hop adds latency to each leg, and the SSE path pays it repeatedly. Provision
Neon and Upstash first, then select the closest Railway region — regions are effectively
fixed after creation on all three platforms, so getting this wrong means recreating
resources.

---

## 11. Public networking

- **API service:** enable a public domain. Railway issues a `*.up.railway.app` hostname.
  Put that value in `BACKEND_PUBLIC_ORIGIN` on the Apex Arena Vercel project; it is
  server-side only and must never carry a `NEXT_PUBLIC_` prefix. The hostname is an
  implementation detail — the smoke test asserts it never appears in public HTML.
- **Ingestor service:** leave public networking **disabled**. It serves no public API. Its
  minimal health server exists only so the platform has something to probe.
- **Combined service:** enable a public domain (it is also the API).

Attaching `chaitanyasingh.org` to a Railway service is not part of this design. The public
domain stays on the portfolio Vercel project, which rewrites `/apex-arena/*` to the Apex
Arena frontend, which in turn proxies `/apex-arena/api/*` server-side to Railway.

### Private networking and `BACKEND_INTERNAL_URL`

Railway gives services in the same project a private network and `*.railway.internal`
DNS names. The frontend proxy prefers a private origin when one is configured:

```
BACKEND_INTERNAL_URL ?? BACKEND_PUBLIC_ORIGIN ?? INTERNAL_BACKEND_URL ?? BACKEND_URL
```

`BACKEND_INTERNAL_URL` (for example `http://backend.railway.internal:8000`) is useful only
when the caller runs **inside the same Railway project**. In the production topology the
caller is a Vercel function, which cannot reach Railway's private network, so
`BACKEND_PUBLIC_ORIGIN` is the value that applies. Set `BACKEND_INTERNAL_URL` only if you
later co-locate a service on Railway that talks to the API — for example an internal job
container. Private networking is still worth having between the API and ingestor services
for any future service-to-service call; today they communicate only through Neon and
Upstash.

---

## 12. Restart policy

Match the manifests:

- Restart policy: **on failure**
- Maximum retries: **10**

On-failure restarts matter for the ingestor in particular. The OpenF1 subscription
reconnects with backoff internally, but a hard failure — a lost lease, a Neon connection
that cannot be re-established — exits the process, and the platform restart is what brings
it back. The retry ceiling stops a genuinely broken configuration from restarting forever
and burning execution time; a service that exhausts its retries needs a human, not another
restart.

---

## 13. Deploy triggers

- Railway redeploys on every push to the watched branch by default. For production, disable
  automatic deploys and promote deliberately, so that a migration can be run *before* the
  new code rolls out.
- Deploy the API and ingestor services from the same commit. They share one image and one
  database schema; running them from different commits across a migration is unsupported.
- CI in this repository gates deployment jobs behind opt-in flags
  (`RAILWAY_DEPLOY_ENABLED` / `VERCEL_APEX_DEPLOY_ENABLED`), so merging alone does not
  deploy anything.
- Prefer a restart-style rollout over an overlapping one for the ingestor and for combined
  mode. An overlapping deploy briefly runs two containers; the lease makes that safe for
  both the dedicated ingestor and `APP_PROCESS_ROLE=all` (the new instance fails to start
  and retries rather than double-ingesting). A restart-style rollout still avoids the
  failed-start noise, and for combined mode it also avoids serving API traffic from a
  container that could not take the lease.

---

## 14. Spending limits and resource monitoring

Set the limits before the first deploy, not after the first surprise. This document
deliberately quotes no prices — plans and free-tier allowances change, and a stale number
in a runbook is worse than none.

| Where | What to configure |
| --- | --- |
| Railway → Workspace/Account → Usage & Billing | A hard usage limit for the workspace, plus usage alerts below it. Railway bills on resource-seconds, so an idle service still accrues |
| Railway → Service → Metrics | Watch CPU, memory, and network per service. A memory line that only climbs is a leak, not load |
| Neon console → Usage | Compute hours and storage against the plan allowance — see [`neon-setup.md`](./neon-setup.md) |
| Upstash console → Usage | Monthly command count; each SSE client polls continuously, so this is the number most likely to move. The estimation formula is in [`upstash-setup.md`](./upstash-setup.md) |
| Application | `AI_KILL_SWITCH`, `AI_DAILY_TOKEN_BUDGET`, and the retention settings (`RAW_EVENT_RETENTION_DAYS`, `NORMALIZED_EVENT_RETENTION_DAYS`, `PROVIDER_PAYLOAD_RETENTION_DAYS`) bound the two costs the platform cannot cap for you |

Consolidated guidance, including which limits fail safe and which fail closed, is in
[`deployment-cost-controls.md`](./deployment-cost-controls.md).

Two cost properties specific to this stack:

- **The ingestor costs money between race weekends.** It holds an idle process and a lease
  connection continuously. That is the price of the singleton guarantee.
- **Neon autosuspend makes the first request after an idle period slow.** `pool_pre_ping`
  recovers from it; it is not an error.

---

## 15. Post-deploy verification

Run in this order. Do not proceed past a failing step.

1. **Liveness, directly at the origin.** `GET https://<api-service>.up.railway.app/health/live`
   returns `200`. This is the only path that should answer without a token.
2. **Origin protection.** `GET https://<api-service>.up.railway.app/rooms` with no token
   returns `403 {"detail":"Direct origin access is not permitted"}`. If it returns `200`,
   proxy enforcement is off or `APEX_ARENA_PROXY_TOKEN` is unset — fix it before the
   service takes traffic.
3. **Readiness, through the public URL.** `GET https://chaitanyasingh.org/apex-arena/api/health/ready`
   returns `200` with both `database` and `redis` reported ready. A `503` names the
   dependency that is not.
4. **Provider status.** `GET https://chaitanyasingh.org/apex-arena/api/health/provider`
   returns `200`. On the API this reads the ingestor's status from the Redis stream, so a
   healthy response here proves the split topology is wired end to end.
5. **The lease is held.** Check the ingestor logs for a clean startup, and confirm the
   advisory lock with the `pg_locks` query in [`neon-setup.md`](./neon-setup.md).
6. **Full smoke test.**

   ```bash
   PUBLIC_BASE_URL=https://chaitanyasingh.org/apex-arena \
   API_BASE_URL=https://chaitanyasingh.org/apex-arena/api \
   scripts/smoke-test-deployment.sh [room-slug]
   ```

   [`scripts/smoke-test-deployment.sh`](../scripts/smoke-test-deployment.sh) exercises the
   public pages, all three health endpoints, the rooms API contract, and — when you pass a
   room slug — the SSE content type and cache headers. It also asserts production
   hardening: `/debug/config` returns `404`, no development fixtures are exposed, and no
   infrastructure hostname (`railway.app`, `vercel.app`, `neon.tech`, `upstash.io`) appears
   in the public HTML. It exits non-zero if anything fails.

---

## 16. Troubleshooting

| Symptom | Most likely cause | Resolution |
| --- | --- | --- |
| **`403` on every request** except `/health/live` | `APEX_ARENA_PROXY_TOKEN` on Railway does not equal `APEX_ARENA_BACKEND_PROXY_TOKEN` on the Apex Arena Vercel project. The comparison is constant-time and exact | Re-set both sides to the same value. Redeploy the Vercel project — env changes there only apply to a **new** deployment. The rejected-request log line carries a request id, never the token |
| **`503 {"detail":"Apex Arena backend origin is not configured"}`** from the frontend API route | Neither `BACKEND_INTERNAL_URL` nor `BACKEND_PUBLIC_ORIGIN` is set on the Vercel project. The route fails closed in production instead of falling back to `localhost` | Set `BACKEND_PUBLIC_ORIGIN` to the Railway public URL and redeploy the Vercel project |
| **Ingestor exits immediately at startup** with `Another Apex Arena ingestor owns the singleton lease` | Another instance holds the advisory lease — usually an overlapping deploy, or a previous container that has not fully shut down | Wait for the old container to exit and let the restart policy retry. If it persists, confirm replicas is `1` and inspect `pg_locks`. Never "fix" this by scaling up |
| **`asyncpg` connect error mentioning an unknown parameter** (`sslmode`, `channel_binding`) | A Neon libpq connection string pasted verbatim. `Settings._asyncpg_dsn` now translates `sslmode` → `ssl` and drops `channel_binding`, so this should no longer occur | If it still appears, the DSN carries some other libpq-only parameter. Use the plain form `postgresql://USER:PASSWORD@HOST/apex_arena?ssl=require` |
| **Startup fails with `Production DATABASE_URL must require TLS`** | The DSN has no `ssl`/`sslmode` parameter, or one with an accepted-but-weaker value | Append `?ssl=require`. Accepted values are `require`, `verify-ca`, `verify-full`, `true` |
| **Startup fails with `Production REDIS_URL must use rediss://`** | A plaintext Upstash URL | Use the TLS endpoint from the Upstash console |
| **`DATABASE_URL password must match POSTGRES_PASSWORD` while using Neon** | An older revision applied the local Compose credential check to every host because the development `.env` also contained `POSTGRES_PASSWORD` | Use the local-host-scoped validator. External managed URLs may coexist with local Compose variables; do not delete `.env`, reset the Neon password, or copy managed credentials into `POSTGRES_PASSWORD` |
| **Startup fails with `APP_PROCESS_ROLE=all is not allowed in production`** | Combined mode with `APP_ENV=production` | Either split into `api` + `ingestor`, or set `APP_ENV=staging`. There is no override |
| **SSE connections flap to `degraded`** | Historically a Redis socket timeout below the blocking `XREAD` window aborted every idle heartbeat. The blocking read is now capped at 10s and `effective_redis_socket_timeout` keeps a margin above it | Do not lower `REDIS_SOCKET_TIMEOUT_SECONDS` below `SSE_HEARTBEAT_SECONDS`. If flapping continues, check `/health/provider` — a genuinely disconnected ingestor also reports `degraded`. Note that Vercel's function duration cap ends long streams by design; the client resumes with `Last-Event-ID` |
| **First request after a quiet period times out** | Neon compute autosuspended and is cold-starting | Expected on a small plan. `pool_pre_ping` recovers the connection; retry. If it happens under real traffic, the health check timeout (30s) is the value to review, not the pool |
| **Health check fails but the app looks healthy in logs** | The health check path is not `/health/live`, or `PORT` was set manually | Set the path to `/health/live` and remove any manual `PORT` variable |
| **Deployment builds but the container exits instantly** | A settings validation error. Every production guard raises during settings construction, before the server starts | Read the first traceback line in the deploy logs — it names the offending variable exactly |

---

## Quick reference

| Item | Value |
| --- | --- |
| Root directory | `backend` |
| Builder | Dockerfile |
| Dockerfile path | `Dockerfile` (root dir `backend`) |
| Start command | `python -m app.runtime` |
| Migration command | `scripts/run-production-migrations.sh` (one-off, direct DSN) |
| Health check path | `/health/live` |
| Health check timeout | 30s |
| Restart policy | on failure, max 10 retries |
| Replicas | 1 (ingestor: permanently 1) |
| `PORT` | injected by Railway — never set it |
| Public domain | API only; ingestor none |
| Variable template | [`.env.railway.example`](../.env.railway.example) |

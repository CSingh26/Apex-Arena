<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Deployment Rollback and Incident Runbook

Concrete recovery steps for every layer of the Apex Arena low-cost production deployment,
with a verification step for each. Written to be usable at 3am during a race weekend.

Grounded in `backend/app/core/settings.py`, `backend/app/api/routes.py`,
`backend/app/api/proxy.py`, `backend/app/ingestor.py`,
`backend/app/storage/database.py`, `frontend/src/app/api/[[...path]]/route.ts`,
`deploy/railway/api.toml`, `deploy/railway/ingestor.toml`.

Companion documents: [`low-cost-production-architecture.md`](./low-cost-production-architecture.md),
[`deployment-secrets.md`](./deployment-secrets.md),
[`deployment-cost-controls.md`](./deployment-cost-controls.md),
[`neon-setup.md`](./neon-setup.md), [`upstash-setup.md`](./upstash-setup.md).

---

## Triage: find the broken layer in under two minutes

Run these in order. The first failure tells you which section to jump to.

```bash
# 1. Public path end to end
curl -sS -o /dev/null -w '%{http_code}\n' https://chaitanyasingh.org/apex-arena

# 2. API through the full proxy chain
curl -sS -i https://chaitanyasingh.org/apex-arena/api/health

# 3. Railway API liveness (bypasses the proxy; exempt from token enforcement)
curl -sS -i https://<railway-api-host>/health/live

# 4. Railway API readiness (database + Redis)
curl -sS https://<railway-api-host>/health/ready   # expect 403 without a token; see note

# 5. Ingestion state, read from the API side
curl -sS https://chaitanyasingh.org/apex-arena/api/health/provider
```

Note on step 4: `/health/ready` is **not** in `UNPROTECTED_PATHS` — only `/health/live` is.
A direct call without `x-apex-proxy-token` returns 403 in staging/production. Reach it
through the public path (`/apex-arena/api/health/ready`) or supply the header from a trusted
machine.

| Symptom | Likely layer | Section |
| --- | --- | --- |
| 1 fails, 3 succeeds | Portfolio rewrite or Apex frontend | [Frontend](#frontend-rollback) / [Portfolio proxy](#portfolio-proxy-rollback) |
| 2 returns 403 `Direct origin access is not permitted` | Token mismatch | [Token mismatch](#proxy-token-mismatch) |
| 2 returns 503 `Apex Arena backend origin is not configured` | `BACKEND_*` unset on the Apex Vercel project | [Frontend](#frontend-rollback) |
| 3 fails | Railway API is down | [API](#api-rollback) |
| 4 shows `database: unavailable` | Neon | [Neon outage](#neon-outage) |
| 4 shows `redis: unavailable` | Upstash | [Redis failure](#redis-failure) |
| 5 shows `connection_state` not `CONNECTED` | Ingestor or OpenF1 | [Ingestor](#ingestor-rollback) / [OpenF1 auth](#openf1-authentication-failure) |

Every request and response carries an `X-Request-ID` header stamped by
`ProxyContextMiddleware`. Capture it — it is the only way to correlate a browser complaint
with a backend log line.

---

## Frontend rollback

**When:** the Apex Arena UI is broken, blank, 404ing, or the API proxy returns 503
`Apex Arena backend origin is not configured`.

Vercel keeps every previous deployment as an immutable, already-built artifact. Rolling back
is a promotion, not a rebuild — it takes seconds and needs no CI.

### Steps

1. Vercel dashboard → **Apex Arena** project → **Deployments**.
2. Find the last deployment known good. Confirm the commit SHA before promoting.
3. `⋯` → **Instant Rollback** (or **Promote to Production**).
4. Wait for the production alias to repoint. No rebuild occurs.

CLI equivalent:

```bash
vercel rollback https://<previous-deployment-url> --scope <team>
```

### Important: environment variables roll back too

Vercel snapshots environment variables into a deployment at build time. Rolling back to an
older deployment restores **that deployment's** variable values, including
`APEX_ARENA_BACKEND_PROXY_TOKEN` and `BACKEND_PUBLIC_ORIGIN`. If you rotated the backend
token since that build, the rolled-back deployment carries the **old** token and every API
call will 403. Check this before rolling back across a rotation — see
[Token mismatch](#proxy-token-mismatch).

### Verify recovery

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://chaitanyasingh.org/apex-arena          # 200
curl -sS -i https://chaitanyasingh.org/apex-arena/api/health                             # 200 JSON
```

Then in a browser: load `/apex-arena`, navigate to `/apex-arena/rooms`, open a room deep
link in a fresh tab, and confirm DevTools → Network shows every `_next` asset 200 and
same-origin with **zero** requests to any `.vercel.app` or Railway host.

Do not skip the deep-link check — a broken `basePath` build serves the landing page fine and
404s everything else.

---

## Portfolio proxy rollback

**When:** `chaitanyasingh.org/apex-arena` is broken but the Apex Arena origin itself is
healthy, or the Apex Arena section is damaging the rest of the portfolio.

The rewrite lives in the **separate portfolio repository**, in `middleware.ts`. Nothing in
the Apex Arena repo affects it.

### Option A — take Apex Arena offline gracefully (fastest)

The middleware documented in `portfolio-vercel-integration.md` returns a clean **503** with
`cache-control: no-store` when either variable is missing:

```ts
if (!origin || !token) {
  return new NextResponse("Apex Arena is temporarily unavailable.", { status: 503, ... });
}
```

1. Portfolio Vercel project → Settings → Environment Variables.
2. **Delete** `APEX_ARENA_ORIGIN` (Production).
3. **Redeploy the portfolio.** This is required — Vercel binds env vars at build time, so
   deleting the value has **no effect on the currently live deployment**. There is no way to
   make this take effect without a new deployment.

Result: `/apex-arena*` serves a 503 page; the rest of the portfolio is untouched.

### Option B — revert the middleware entirely

1. In the portfolio repo, revert the commit that added the Apex Arena branch to
   `middleware.ts` (or remove `/apex-arena` and `/apex-arena/:path*` from `config.matcher`).
2. Push; Vercel builds and deploys.
3. `/apex-arena*` now 404s from the portfolio itself.

Option B is the right choice if the Apex Arena branch is interfering with the portfolio's own
routing. Remember there can only be **one** `middleware.ts` per project — do not delete the
file if the portfolio has its own logic in it.

### Option C — instant rollback of the portfolio deployment

Same Instant Rollback flow as the frontend, on the portfolio project. Use this when the
problem was introduced by a portfolio deploy rather than by a variable value.

### Verify recovery

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://chaitanyasingh.org/            # 200, portfolio intact
curl -sS -o /dev/null -w '%{http_code}\n' https://chaitanyasingh.org/apex-arena  # 503 (A) / 404 (B) / 200 (C)
```

Check a few existing portfolio routes by hand. The failure mode that matters here is a
matcher change that swallows portfolio paths.

Confirm no **redirect** appears anywhere on `/apex-arena*`: `curl -sSI` must not return a
3xx with a `Location` pointing at a `.vercel.app` host. A redirect on this prefix defeats the
entire URL-preservation design and is always a bug.

---

## API rollback

**When:** the Railway FastAPI service is erroring, crash-looping, or a deploy introduced a
regression.

### Steps

1. Railway dashboard → **apex-arena-api** service → **Deployments**.
2. Identify the last good deployment (match the commit SHA).
3. `⋯` → **Redeploy** on that deployment. Railway redeploys the previously built image; no
   rebuild is needed.
4. Watch the deploy logs until the health check passes.

If the failure is a **variable**, not code: fix the variable and let Railway restart the
service. A settings validation error surfaces as an immediate crash on start with the
`ValueError` text from `validate_runtime_contract` in the logs — for example
`Recent-session reconciliation requires ingestor or combined role`,
`Production DATABASE_URL must require TLS`, or
`Production REDIS_URL must use rediss://`. These are startup failures, not request failures;
the container never becomes healthy.

Rolling the API back **does not** affect ingestion — that is the point of the split-role
architecture. The ingestor keeps consuming MQTT and writing to Neon and Upstash throughout.

### Verify recovery

```bash
curl -sS -i https://<railway-api-host>/health/live            # 200, {"status":"alive","role":"api",...}
curl -sS -i https://<railway-api-host>/api/health             # 403 expected — no proxy token
curl -sS https://chaitanyasingh.org/apex-arena/api/health/ready
```

`/health/ready` must return 200 with `dependencies.database == "ready"` and
`dependencies.redis == "ready"`. A 503 from that endpoint means a dependency, not the API.

Then open a race room in a browser and confirm the SSE badge reaches `live`.

---

## Ingestor rollback

**When:** ingestion stopped, duplicated, or a deploy broke normalization.

### Steps

1. Railway dashboard → **apex-arena-ingestor** service → **Deployments** → last good →
   `⋯` → **Redeploy**.
2. Watch the logs through startup. Two things must happen in order:
   - the advisory lease is acquired (no `RuntimeError`), and
   - `start_live_services()` runs, which requires `OPENF1_LIVE_AUTO_CONNECT=true`.
3. If the log shows
   `RuntimeError: Another Apex Arena ingestor owns the singleton lease`,
   an old container is still holding the lock. See
   [Duplicate ingestion](#duplicate-ingestion).

### Expected behaviour during the restart

- **A gap in live events.** The ingestor is a singleton by design; while it is restarting,
  nothing is consuming MQTT. Events emitted during the gap are not recovered by the
  ingestor — OpenF1 MQTT is a live subscription, not a replayable log. Some data can be
  backfilled afterwards through the historical REST path, but do not assume the gap
  self-heals.
- **The API stays up.** SSE clients see no new events, and `/health/provider` will report a
  non-`CONNECTED` state, but connections are not dropped.
- **Lease recovery is automatic.** PostgreSQL releases a session-scoped advisory lock when
  the backend session ends, so a hard-killed ingestor does not leave a lock requiring manual
  cleanup.

### Verify recovery

```bash
curl -sS https://<railway-ingestor-host>/health/provider   # if reachable; public networking is normally OFF
curl -sS https://chaitanyasingh.org/apex-arena/api/health/provider
```

The API-side response should show `source: "redis_status_stream"` and
`connection_state: "CONNECTED"` with a `last_event_at` that advances between two calls
30 seconds apart. A `last_event_at` that is not moving during a live session means the
subscription is up but no data is flowing — treat that as still broken.

Confirm exactly one lease holder from a `psql` session on the **direct** endpoint:

```sql
SELECT pid, granted FROM pg_locks
WHERE locktype = 'advisory' AND objid = 1095782232;
```

Exactly one row, `granted = true`.

---

## Database migration failure

**Read this section before you migrate, not after.**

### The limitation, stated honestly

**Irreversible migrations cannot be automatically rolled back.** `alembic downgrade` only
works when the migration author wrote a correct `downgrade()`, and a `downgrade()` cannot
recover data that a `DROP COLUMN`, `DROP TABLE`, or destructive `UPDATE` has already
removed. The schema can be reverted; the data cannot. Do not plan around
`alembic downgrade -1` as a safety net for a destructive migration — it is not one.

There is no application-level guard here. `scripts/run-production-migrations.sh` — the
documented procedure — serializes concurrent runs with its own advisory lock and offers
`--check` as a dry run, but it does **not** snapshot anything, and neither does the
underlying `python -m app.runtime migrate`, which `execvp`s straight into
`alembic upgrade head`. The backup in step 1 below is the only rollback mechanism.

### Before every production migration

1. **Take a Neon branch or a dump.** This is the actual rollback mechanism.
   - **Neon branch (preferred, near-instant):** in the Neon console, create a branch from
     the current production `main` at the current timestamp, named e.g.
     `pre-migration-<date>`. It is copy-on-write, so it is cheap and immediate.
   - **Or `pg_dump`:**
     ```bash
     pg_dump --format=custom --file=pre-migration-<date>.dump   # DSN supplied by the environment
     ```
     Treat the dump file as a secret — it contains the whole dataset.
2. **Rehearse on a Neon branch.** Point `DATABASE_MIGRATION_URL` at a branch of production
   and run the migration there first. This is the single highest-value habit in this
   runbook.
3. **Confirm you are on the direct endpoint.** Alembic uses
   `get_settings().async_migration_database_url` (see `backend/migrations/env.py`), so
   `DATABASE_MIGRATION_URL` must be the Neon endpoint **without** the `-pooler` suffix.
   Running DDL through a transaction pooler risks a migration transaction split across
   backends and a half-applied schema.
4. **Do not migrate during a live session.**

### If a migration fails

1. **Stop.** Do not re-run `upgrade head` hoping it settles. Do not deploy application code
   that expects the new schema.
2. Record the failing revision: `alembic current` and `alembic history`.
3. Decide which case you are in:

   **Case A — the migration was transactional and fully rolled back.**
   `env.py` wraps the run in `context.begin_transaction()`, and PostgreSQL supports
   transactional DDL, so a failure part-way often leaves the schema untouched. Verify with
   `alembic current` (unchanged revision) and by inspecting the affected tables. Fix the
   migration and re-run.

   **Case B — the schema is partially applied.**
   Some operations (concurrent index builds, some data-migration steps, anything the
   migration committed explicitly) escape the outer transaction. If `alembic current` shows
   the new revision but the schema is wrong, or the revision is stale but objects exist, do
   **not** hand-patch production. Restore.

   **Case C — the migration succeeded but the data is wrong.** Restore.

4. **Restore** by creating a Neon branch from the pre-migration point (or from the branch
   taken in step 1), then repointing `DATABASE_URL` and `DATABASE_MIGRATION_URL` at the new
   branch's endpoint on **both** Railway services and restarting both. Note the hostname
   changes, so both services need updating — rehearse this before you need it.
5. **Roll the application back** to the deployment that matches the restored schema:
   Railway redeploy for both backend services, Vercel Instant Rollback for the frontend.

### Verify recovery

```bash
alembic current                                                  # expected revision
curl -sS https://chaitanyasingh.org/apex-arena/api/health/ready   # database: "ready"
```

Then exercise a real read path — load the rooms list and open a room — and confirm the
ingestor re-acquires its lease against the restored endpoint.

---

## Redis failure

**Symptom:** `/health/ready` reports `redis: "unavailable"`; SSE clients show `degraded`;
logs contain `RedisPublishError`.

`RedisStore.health_check` and `EventBus._publish` deliberately log only the exception class
name — never the URL, which embeds the password. Preserve that when debugging.

### Steps

1. **Check the Upstash console first.** Distinguish an outage from a **quota or spend-cap
   stop**. Hitting the command limit or the budget cap presents as connection or command
   errors, not as a maintenance banner. This is the most common cause on a free tier — see
   [`deployment-cost-controls.md`](./deployment-cost-controls.md).
2. **Check for a self-inflicted timeout.** If every idle heartbeat produces a `degraded`
   event, the socket timeout is below the blocking `XREAD` window.
   `effective_redis_socket_timeout` in `settings.py` raises it to at least
   `min(10, SSE_HEARTBEAT_SECONDS) + 5`, but a `socket_timeout` forced lower in the
   `REDIS_URL` query string can still bite. See `upstash-setup.md`.
3. **Restart both Railway services** to rebuild connection pools after a provider-side blip.
4. **If Upstash is genuinely down or capped:** provision a replacement Redis (a Railway Redis
   add-on in the same region is the pragmatic fallback), set the new `REDIS_URL` on both
   services, restart both. The app touches only five Redis commands and reads the endpoint
   purely from `REDIS_URL`, so this is a variable change and a restart.

### What is lost

Redis is the **transport**, not the store of record. Normalized events are persisted to
Postgres. A Redis outage means live streaming stops and in-flight stream contents are lost;
it does not mean historical data is lost. Streams are trimmed with approximate `MAXLEN`
anyway, so they were never durable.

Production requires `rediss://` — a replacement URL on `redis://` will fail startup
validation, which is by design.

### Verify recovery

```bash
curl -sS https://chaitanyasingh.org/apex-arena/api/health/ready   # redis: "ready"
```

Open a race room during live ingestion and confirm events arrive incrementally and the
connection badge holds at `live` across at least two heartbeat intervals.

---

## Neon outage

**Symptom:** `/health/ready` reports `database: "unavailable"`; the ingestor cannot acquire
its lease; `/health` shows the database component failing.

### First: is it actually an outage?

1. **Autosuspend cold start.** Neon free-tier compute suspends after a few minutes idle.
   `Database.health_check` uses a **2 second** timeout, so a cold start can report
   `unavailable (TimeoutError)` while the database is perfectly fine. Call the endpoint
   again after a few seconds before escalating — and never wire that health check to an
   automatic restart policy without accounting for this.
2. **Quota suspension.** Exceeding a Neon quota can suspend the project. Data is not lost,
   but it is unreachable until usage resets or you upgrade. Check the console's usage page.
3. **Connection exhaustion.** Count your connections:
   `DB_POOL_SIZE + DB_MAX_OVERFLOW` per process (default 5), plus one lease connection held
   outside the pool on the ingestor. If you recently scaled the API, this is the first
   suspect.
4. **Genuine provider incident.** Check Neon's status page.

### Steps

1. Confirm which of the above applies before changing anything.
2. For connection exhaustion: reduce `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`, or scale the API
   back to one replica, and restart.
3. For a quota suspension: resolve the quota, then reduce storage. Note there is **no
   automatic pruning** — `RAW_EVENT_RETENTION_DAYS` and the other retention variables are
   reserved names that no code reads, so setting them will not shed a single row. Cleanup is
   a manual `DELETE` against the direct endpoint plus a shorter Neon history-retention
   window. See [`deployment-cost-controls.md`](./deployment-cost-controls.md).
4. For a provider incident: there is no failover in this architecture. Both Railway services
   will fail readiness. Decide whether to leave them running (they recover on their own when
   Neon returns, thanks to `pool_pre_ping`) or stop the ingestor to avoid restart churn.
5. If you must move: restore a `pg_dump` into another managed Postgres, update
   `DATABASE_URL` and `DATABASE_MIGRATION_URL` on both services, restart. Preserve the
   pooled/direct split — the ingestor still needs a direct, non-pooled endpoint.

### Known consequence for the ingestor

When Neon suspends the compute it terminates the long-lived advisory-lease connection. The
lock is released server-side, but `_ingestor_lease_connection` still points at a dead socket
and `release_ingestor_lease()` will fail at shutdown. `pool_pre_ping` does **not** protect
this connection — it is not checked out from the pool. **Restarting the ingestor
re-acquires the lease cleanly.** Treat an ingestor restart after a long idle period as
expected behaviour, not as an incident.

### Verify recovery

```bash
curl -sS https://chaitanyasingh.org/apex-arena/api/health/ready   # database: "ready"
```

Plus the `pg_locks` query from the ingestor section: exactly one row, `granted = true`.

---

## Duplicate ingestion

**Symptom:** duplicated events in rooms, doubled writes, `/health/provider` reporting a
`connection_state` or `current_session_key` that flips between two values on consecutive
calls.

### How to detect it

1. **`/health/provider`, called repeatedly.**

   ```bash
   for i in 1 2 3 4 5; do
     curl -sS https://chaitanyasingh.org/apex-arena/api/health/provider
     echo; sleep 3
   done
   ```

   On the API this reads `event_bus.latest_connection_status()` — the **latest** entry in a
   Redis status stream. Two ingestors both publishing status produce values that alternate
   between calls: a `current_session_key`, `reconnect_attempts`, or `last_event_at` that
   goes backwards or oscillates is the signature. A single ingestor produces monotonic
   values.

2. **The advisory lock is the authority.** From `psql` on the **direct** endpoint:

   ```sql
   SELECT l.pid, l.granted, a.backend_start, a.client_addr, a.state
   FROM pg_locks l JOIN pg_stat_activity a USING (pid)
   WHERE l.locktype = 'advisory' AND l.objid = 1095782232;
   ```

   **More than one row is proof of duplicate ingestion.** Two `granted = true` rows can only
   happen if the ingestors are on different backend sessions that both believe they hold the
   lock — which is exactly the failure mode a transaction pooler produces.

3. **Check for duplicate normalized events** for a single session key over a recent window.

### Root causes, in order of likelihood

| Cause | Check | Fix |
| --- | --- | --- |
| Ingestor on the **pooled** DSN | Does `DATABASE_MIGRATION_URL` on the ingestor contain `-pooler`? Is it set at all? | Set it to the **direct** endpoint and restart. If it is unset, `async_migration_database_url` silently falls back to `DATABASE_URL` — the pooled one |
| `numReplicas > 1` on the ingestor | Railway service settings | Set to 1 |
| Combined mode without a direct DSN | `APP_PROCESS_ROLE=combined` and missing `DATABASE_MIGRATION_URL` | Set `DATABASE_MIGRATION_URL` to the direct endpoint and restart |
| Combined mode with worker settings inconsistent across containers | Railway variables | Keep replicas at 1 while reconciliation or live ingestion is enabled |
| An API service with `OPENF1_LIVE_AUTO_CONNECT=true` | Railway API variables | Set `false`. In production the validator already rejects this combination |
| A leftover staging ingestor on the same database | Which services point at this Neon project? | Give staging its own Neon branch |

### Recovery

1. Stop **all but one** ingestor. Set the ingestor `numReplicas` to 1.
2. Fix the root cause — usually setting `DATABASE_MIGRATION_URL` to the direct endpoint.
3. Restart the surviving ingestor so the lease is re-acquired on a session-scoped, non-pooled
   connection.
4. Re-run the `pg_locks` query: exactly one row.
5. Assess data damage. Duplicate normalized events may need cleaning; do it against the
   direct endpoint, outside a live session, after taking a Neon branch.

### Verify recovery

Exactly one `pg_locks` row with `granted = true`, and five consecutive `/health/provider`
calls showing monotonically advancing `last_event_at` with a stable `current_session_key`.

---

## SSE failure

**Symptom:** the room connection badge sits at `reconnecting` or `degraded`; events arrive
in one buffered blob rather than incrementally; the stream dies every 60 seconds.

### Frequent reconnects are expected, not a fault

Vercel Functions have a **maximum duration** (60 s default; roughly up to 300 s on Hobby and
800 s on Pro with Fluid Compute). An SSE connection traversing a Vercel Function **cannot
stay open indefinitely.** A 90-minute race will be dozens of forced reconnects. This is a
platform ceiling, not a bug in this repository, and it is documented in the SSE section of
`apex-arena-vercel-deployment.md`.

The stack is built for it: the backend accepts a resume cursor via `?after_sequence=<n>` and
the standard `Last-Event-ID` header, taking the max of the two; the route handler forwards
all inbound request headers so `Last-Event-ID` reaches FastAPI; the client reconnects with
capped exponential backoff. So a cut connection degrades to a gap-free resume.

### Steps for a genuine failure

1. **Confirm the transport headers survive the chain:**

   ```bash
   curl -N -sS -i https://chaitanyasingh.org/apex-arena/api/rooms/<slug>/stream
   ```

   Expect `content-type: text/event-stream` and `cache-control: no-cache, no-transform`, and
   events arriving incrementally. The route handler sets `cache-control: no-cache,
   no-transform` and `x-accel-buffering: no` whenever the upstream content type is
   `text/event-stream` — if those are missing, something in the chain is rewriting them.
2. **One buffered blob instead of a stream** means an intermediary is buffering. Check for
   compression or response buffering added anywhere on the path, and confirm the route
   handler still exports `dynamic = "force-dynamic"` and `fetchCache = "force-no-store"`.
3. **`degraded` on every heartbeat** is a Redis socket-timeout problem, not an SSE problem —
   see [Redis failure](#redis-failure).
4. **Gaps in events after a reconnect** mean the replay buffer drained before the client
   reconnected. `ROOM_STREAM_BACKLOG_LIMIT` (default 250, max 1000) bounds how far back the
   server can replay. Raise it for live sessions if reconnects are slow.
5. **Raise `maxDuration`** on the streaming path to the highest value your Vercel plan
   allows — fewer, longer sessions beat frequent churn.
6. **Do not lengthen `SSE_HEARTBEAT_SECONDS` to fix a reconnect problem.** It is bounded
   1–120, it raises the effective Redis socket timeout in step, and a longer heartbeat makes
   an idle stream *more* likely to be dropped by an intermediary, not less.

### Verify recovery

Open a room during live ingestion. The badge must move `connecting → live` and stay there
across at least two heartbeat intervals. Force a disconnect (toggle network) and confirm it
goes `reconnecting → live` with no duplicated and no skipped events.

---

## OpenF1 authentication failure

**Symptom:** `/health/provider` reports a `connection_state` of `MISSING_CREDENTIALS`, or a
state that never reaches `CONNECTED` while `reconnect_attempts` climbs.

`OpenF1LiveClient.connect` checks credentials explicitly:

```python
if not self.settings.live_mode_enabled:
    await self._set_state(LiveConnectionState.DISABLED)
    return
if not self.auth.credentials_present:
    await self._set_state(LiveConnectionState.MISSING_CREDENTIALS,
                          "OpenF1 live credentials are not configured")
    return
```

So a credential problem is a **reported state**, not a crash. The container stays healthy and
`/health/live` keeps returning 200 — which is why you must check `/health/provider`
explicitly rather than trusting the platform probe.

### Steps

1. Confirm `OPENF1_USERNAME` and `OPENF1_PASSWORD` are both set on the **ingestor** service.
   `openf1_credentials_present` requires username, password, **and** a non-empty password
   value; `settings.py` converts an empty string to `None` before validation, so a variable
   set to `""` is the same as unset.
2. Confirm the credentials still work with OpenF1 directly (subscription lapsed? password
   rotated? account locked?).
3. Check `OPENF1_AUTH_URL` (`https://api.openf1.org/token`) and `OPENF1_MQTT_HOST` /
   `OPENF1_MQTT_PORT` (`mqtt.openf1.org` / `8883`) are unmodified.
4. Restart the ingestor after correcting the values. Token acquisition happens at connect
   time; a running process will not pick up a new credential.
5. If the auth endpoint itself is failing, `OPENF1_RECONNECT_*` governs the backoff
   (base 1000 ms, max 30000 ms, 20 attempts by default). Let the backoff work — do not
   restart in a loop, which resets the attempt counter and hammers the provider.
6. **Do not "fix" this by disabling live mode permanently.** The OpenF1 subscription is paid
   and authenticated live ingestion is the product. Use
   [Disabling live mode](#disabling-live-mode) only as a temporary incident control.

### Verify recovery

```bash
curl -sS https://chaitanyasingh.org/apex-arena/api/health/provider
```

`connection_state: "CONNECTED"` with `last_event_at` advancing during a live session, and
`reconnect_attempts` back to 0.

---

## Disabling live mode

**When:** live ingestion is causing damage — malformed upstream data, a write storm, a cost
emergency — and you need to stop it without taking the application down.

`LIVE_MODE_ENABLED=false` on the **ingestor** service:

- `OpenF1LiveClient.connect` short-circuits to `LiveConnectionState.DISABLED` and never
  subscribes.
- `/health/provider` treats `DISABLED` as healthy —
  `healthy = not live_mode_enabled or state in {"CONNECTED", "DISABLED"}` — so the endpoint
  returns 200 rather than 503, and platform health checks keep passing.
- Historical and replay routes are unaffected. Existing stored data is untouched.

### Steps

1. Railway → **apex-arena-ingestor** → Variables → `LIVE_MODE_ENABLED=false`.
2. The service restarts and comes up with ingestion disabled.
3. Set it on the API service too if you want `/health` and `safe_runtime_metadata` to report
   the same state consistently.
4. To restore: set `true` and restart. The ingestor reconnects and re-acquires the lease.

A blunter alternative is `OPENF1_LIVE_AUTO_CONNECT=false`, which skips `start_live_services()`
entirely at startup. `LIVE_MODE_ENABLED=false` is preferable because it produces an explicit
`DISABLED` state on `/health/provider` rather than an ambiguous silence.

### Verify

`/health/provider` returns 200 with `connection_state: "DISABLED"`, and the rooms UI still
loads historical content.

---

## Disabling AI reactions without deleting stored messages

**When:** AI output is inappropriate, looping, or spending beyond budget.

Two variables, both verified in `backend/app/core/settings.py`:

```python
ai_enabled: bool = True        # AI_ENABLED
ai_kill_switch: bool = False   # AI_KILL_SWITCH
```

They combine as `ai_enabled and not ai_kill_switch` — so **either** `AI_KILL_SWITCH=true`
**or** `AI_ENABLED=false` turns the feature off. `AI_KILL_SWITCH=true` is the one to reach
for in an incident: it reads as a deliberate emergency action in the variable list, and it
leaves `AI_ENABLED=true` as the record of the intended steady state.

### Steps

1. Railway → **apex-arena-api** → Variables → `AI_KILL_SWITCH=true`.
2. The service restarts.
3. To restore: `AI_KILL_SWITCH=false` and restart.

### Nothing is deleted

Neither variable deletes anything. There is no destructive path attached to either flag — no
migration, no cleanup job, no truncation. Previously generated reactions and room messages
remain in PostgreSQL and remain readable. Turning the switch back off restores generation of
*new* reactions; it does not restore anything, because nothing was removed.

**Do not** attempt to suppress past output by deleting rows. That is a data-loss operation
with no rollback on a free-tier plan whose point-in-time restore window is short. If specific
stored content must be removed, take a Neon branch first and remove it deliberately, as a
separate, reviewed action.

### Verify

```bash
curl -sS https://chaitanyasingh.org/apex-arena/api/health
```

The AI component reports `"disabled"` (`routes.py` derives it from
`settings.ai_enabled and not settings.ai_kill_switch`). Open a room with historical content
and confirm previously stored messages still render.

> **Verified state of the code:** the only reader of `ai_enabled` / `ai_kill_switch` in
> `backend/app/` is `backend/app/api/routes.py:154`, which produces that status string. No
> OpenAI client currently exists in the backend. Setting `AI_KILL_SWITCH=true` therefore
> changes the reported status; whether it stops any generation depends on an integration
> that is not present in this revision. Re-verify against the code before relying on it as a
> spend control during an incident.

---

## Proxy token mismatch

**Symptom:** every API call returns **403** `{"detail": "Direct origin access is not
permitted"}` while `/health/live` returns 200.

That asymmetry is diagnostic: `UNPROTECTED_PATHS` is `frozenset({"/health/live"})`, so a
healthy `/health/live` alongside a blanket 403 everywhere else means the process is fine and
the **token is wrong**.

### Fix

The enforced pair uses different names on each side:

| Side | Variable | Set on |
| --- | --- | --- |
| Frontend | `APEX_ARENA_BACKEND_PROXY_TOKEN` | Apex Arena Vercel project |
| Backend | `APEX_ARENA_PROXY_TOKEN` | Railway API service |

Both must hold the **identical string**. Common causes: a rotation applied to one side only;
a Vercel Instant Rollback that restored a deployment built with the previous token; a
trailing newline or space pasted into one value.

1. Set both to the same value.
2. **Redeploy the Vercel project** — a variable change alone does not affect an existing
   deployment.
3. Railway restarts on a variable change automatically.

`ProxyContextMiddleware` compares against exactly one configured value, so there is no
dual-token rollover window. Expect a brief 403 period during any rotation, and do not attempt
one during a race session. Full procedure in
[`deployment-secrets.md`](./deployment-secrets.md).

### Emergency loosening — last resort only

`PROXY_ENFORCEMENT_ENABLED=false` on the Railway API disables the token check. That leaves
the Railway origin **publicly reachable and unauthenticated**. Use it only to confirm a
diagnosis, for minutes, and set it back to `true` immediately. It is not a fix.

### Verify recovery

```bash
curl -sS -i https://chaitanyasingh.org/apex-arena/api/health   # 200 JSON
curl -sS -o /dev/null -w '%{http_code}\n' https://<railway-api-host>/api/health   # 403
curl -sS -o /dev/null -w '%{http_code}\n' https://<railway-api-host>/health/live  # 200
```

All three must hold. A 200 on the second line means enforcement is off and the origin is
exposed.

---

## Post-incident

1. Record the `X-Request-ID` values, the timeline, and which layer actually failed.
2. Note whether a rollback restored an **older environment variable set** — this is the most
   common second-order failure after a Vercel Instant Rollback across a token rotation.
3. If the incident touched Neon, confirm the ingestor lease is held by exactly one process
   before declaring recovery.
4. If a migration was involved, keep the pre-migration Neon branch until you are confident,
   then delete it (branches consume storage).
5. Never record a secret value in the incident notes — record *what* was rotated and *when*.

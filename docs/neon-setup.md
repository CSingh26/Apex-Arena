<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Neon PostgreSQL Setup (Low-Cost Production)

This guide provisions Neon as the PostgreSQL backend for Apex Arena running on Railway.
It is grounded in the actual repository configuration:

- `backend/app/core/settings.py` — `database_url`, `database_migration_url`, `postgres_*`,
  `validate_database_url`, `_asyncpg_dsn`, `async_database_url`, `async_migration_database_url`
- `backend/app/storage/database.py` — SQLAlchemy async engine and the ingestor advisory lock
- `backend/app/services/container.py` — which DSN each process role connects with
- `backend/migrations/env.py` and `backend/alembic.ini` — Alembic runs through
  `get_settings().async_migration_database_url`
- `backend/pyproject.toml` — `asyncpg>=0.30,<1`, `sqlalchemy[asyncio]>=2.0.41,<3`, `alembic>=1.16,<2`

All credentials in this document are placeholders. Never paste a real connection string
into a file, a log line, a shell history, or a commit.

---

> ## ⚠ `DATABASE_MIGRATION_URL` is REQUIRED for any ingesting role
>
> In `APP_ENV=staging` or `APP_ENV=production`, a process with
> `APP_PROCESS_ROLE=ingestor` **or** `APP_PROCESS_ROLE=all` **will refuse to start** unless
> `DATABASE_MIGRATION_URL` is set. `validate_runtime_contract` in `settings.py` raises:
>
> ```
> Ingesting roles require DATABASE_MIGRATION_URL (the direct, non-pooled endpoint)
> so the singleton lease is reliable
> ```
>
> This is deliberate, not an oversight. The singleton ingestion lease is a **session-scoped**
> PostgreSQL advisory lock, and a pooled (transaction-mode) endpoint makes that lock
> unreliable — see the next section. Rather than let a misconfiguration silently downgrade
> the ingestor onto the pooled endpoint, startup fails loudly.
>
> Set `DATABASE_MIGRATION_URL` to the Neon **direct** (no `-pooler`) endpoint.

---

## Read this first: the pooler will break the ingestor

`backend/app/storage/database.py` acquires a **session-level** advisory lock and holds it for
the entire lifetime of the ingestor process:

```python
await connection.scalar(
    text("SELECT pg_try_advisory_lock(:lock_id)"),
    {"lock_id": 1_095_782_232},
)
```

The connection object is stashed in `self._ingestor_lease_connection` and only released in
`release_ingestor_lease()` at shutdown. `app/ingestor.py` treats a failed acquisition as fatal
("Another Apex Arena ingestor owns the singleton lease").

Neon's pooled endpoint is **PgBouncer in transaction pooling mode**. In transaction mode a
backend server connection is returned to the pool at the end of every transaction, so the
next statement from the same client may land on a *different* backend session. Session-level
advisory locks are owned by the backend session, not by the client. The consequences:

- `pg_try_advisory_lock` may return `true` and then silently lose the lock when the
  transaction ends and the server connection is recycled — the singleton guarantee evaporates
  and two ingestors can run concurrently.
- `pg_advisory_unlock` may be issued on a session that never held the lock, leaving an orphaned
  lock on a pooled backend until that backend is closed.
- asyncpg's implicit prepared statements are also unsafe across transaction-mode pooling
  (asyncpg names its statements; PgBouncer can route the `EXECUTE` to a backend that never saw
  the `PREPARE`), producing intermittent `InvalidSQLStatementNameError` / "prepared statement
  does not exist".

**Therefore: the ingestor process MUST use the DIRECT (non-pooled) Neon connection string.**
Only the API process should use the pooled endpoint. Alembic must also use the direct string
(see step 6). Both are supplied through `DATABASE_MIGRATION_URL`, and in a deployed
environment an ingesting role cannot start without it.

---

## 1. Create a free Neon project

1. Sign up at <https://neon.tech> and create an organization.
2. Create a new project, e.g. `apex-arena`.
3. Note that the Neon free plan is a *current* offering, not a guarantee. Neon has changed free
   tier limits before. Treat the free plan as best-effort and keep the direct/pooled strings
   portable so you can move to another managed Postgres without code changes — the app only
   ever reads `DATABASE_URL`.

## 2. Choose a region near your Railway region

Every query pays the round trip twice (Railway → Neon → Railway), and the ingestor is
write-heavy. Pick the Neon region geographically closest to the Railway region hosting the
backend. For example, Railway `us-west1` pairs with Neon `AWS US West (Oregon)`; Railway
`europe-west4` pairs with Neon `AWS Europe (Frankfurt)`.

The region cannot be changed after project creation — you would have to create a new project
and dump/restore. Decide the Railway region first.

## 3. Create the database and role

1. In the Neon console, open **Databases** and create `apex_arena`.
2. Open **Roles** and create a dedicated application role, e.g. `apex`. Do not reuse the
   project owner role for the app.
3. Copy the generated password once. Neon will not show it again; you can only reset it.

**With a managed provider you do not need the discrete `POSTGRES_*` parts at all.** Neon
issues one authoritative DSN; `DATABASE_URL` is the only value the application reads to
connect. The discrete fields exist for the local Docker Compose build, which assembles a DSN
from them:

```
# Optional with Neon. Leave POSTGRES_PASSWORD unset.
POSTGRES_DB=apex_arena
POSTGRES_USER=apex
POSTGRES_HOST=<PROJECT_ID>.<REGION>.aws.neon.tech
POSTGRES_PORT=5432
```

> **On the password cross-check.** `postgres_password` is `SecretStr | None = None` — it is
> **optional**, and `validate_runtime_contract` only compares it against the password
> embedded in `DATABASE_URL` when it is actually supplied:
>
> ```python
> if self.postgres_password is not None:
>     ...
>     raise ValueError("DATABASE_URL password must match POSTGRES_PASSWORD")
> ```
>
> So for a managed Neon DSN, **simply leave `POSTGRES_PASSWORD` unset** and the check never
> runs. There is no need to hunt for a URL-safe password. If you do set it (local Compose),
> store the *raw* value in `POSTGRES_PASSWORD` and the percent-encoded value inside
> `DATABASE_URL`; the validator URL-decodes before comparing.

## 4. Obtain BOTH connection strings

Neon's **Connection Details** panel has a "Connection pooling" toggle. Capture both forms.

**Pooled (PgBouncer)** — the hostname carries a `-pooler` suffix:

```
postgresql://apex:<APEX_DB_PASSWORD>@ep-<ENDPOINT_ID>-pooler.<REGION>.aws.neon.tech/apex_arena
```

**Direct (non-pooled)** — same host without `-pooler`:

```
postgresql://apex:<APEX_DB_PASSWORD>@ep-<ENDPOINT_ID>.<REGION>.aws.neon.tech/apex_arena
```

Neon's copy button appends `?sslmode=require&channel_binding=require`. **You may now paste
Neon's string verbatim.** `Settings._asyncpg_dsn` normalizes it before the engine is built:
it rewrites the scheme to `postgresql+asyncpg://`, translates `sslmode` into asyncpg's `ssl`
(mapping libpq's `true` to `require`), and drops `channel_binding`, which asyncpg cannot
consume. Writing `?ssl=require` yourself is still the clearest form and is what the rest of
this document uses — see step 7.

## 5. Which URL the application uses (pooled, for API runtime)

The **API** service (`APP_PROCESS_ROLE=api`) uses the **pooled** endpoint. The API opens many
short-lived sessions through `async_sessionmaker` and never takes a session-level advisory
lock, so transaction pooling is safe and keeps the Neon connection count low.

## 6. Which URL migrations and the ingestor use (direct)

Use the **direct** endpoint for:

- **The ingestor service** (`APP_PROCESS_ROLE=ingestor`) — because of the session-level
  advisory lock described at the top of this document. This is non-negotiable.
- **Alembic.** `backend/migrations/env.py` builds its engine from
  `get_settings().async_migration_database_url` with `poolclass=pool.NullPool` and wraps the
  whole migration in a single `context.begin_transaction()`. Alembic itself takes locks on
  `alembic_version` and DDL, and asyncpg prepared statements are used throughout. Running DDL
  through PgBouncer transaction pooling risks the migration transaction being split across
  backends and leaving the schema half-applied.

### How the split is configured: `DATABASE_MIGRATION_URL`

`settings.py` exposes a **second, dedicated setting** for exactly this purpose:

```python
database_migration_url: SecretStr | None = None   # env var DATABASE_MIGRATION_URL
```

and a property that prefers it:

```python
@property
def async_migration_database_url(self) -> str:
    if self.database_migration_url is None:
        return self.async_database_url
    return self._asyncpg_dsn(self.database_migration_url.get_secret_value())
```

`backend/app/services/container.py` picks the DSN by role — you do **not** set a different
`DATABASE_URL` per service:

```python
self.database = Database(
    settings.async_migration_database_url
    if settings.app_process_role == "ingestor"
    else settings.async_database_url,
    ...
)
```

So the intended configuration is:

- `DATABASE_URL` — the **pooled** endpoint, set identically on every service.
- `DATABASE_MIGRATION_URL` — the **direct** endpoint. Required on the ingestor service and
  in the migration job; harmless (and useful for one-off `alembic` runs) elsewhere.

Note the fallback: when `DATABASE_MIGRATION_URL` is unset, `async_migration_database_url`
silently returns the runtime DSN. That fallback is why an ingesting role in staging or
production is **hard-failed at startup** if the variable is missing (see the warning at the
top of this document) — a silent downgrade onto the pooled endpoint is the single most
damaging misconfiguration available in this stack.

## 7. TLS and the exact URL form

`validate_database_url` in `settings.py` accepts exactly two prefixes:

```python
if not value.startswith(("postgresql://", "postgresql+asyncpg://")):
    raise ValueError("DATABASE_URL must use PostgreSQL")
```

So `postgres://` (the shorthand some providers emit) is **rejected**. It also strips a trailing
slash. `async_database_url` rewrites a bare `postgresql://` into `postgresql+asyncpg://` at
engine construction, so either accepted prefix works — write the explicit one for clarity.

For `APP_ENV=production`, `validate_runtime_contract` additionally requires a TLS query
parameter on `DATABASE_URL`: `ssl` or `sslmode` must be one of `require`, `verify-ca`,
`verify-full`, `true`. Note this check reads the **raw** value, so either spelling satisfies
it.

Prefer the `ssl` form: it is what asyncpg itself consumes and it makes the intent unambiguous
in a variable list. Pasting Neon's `?sslmode=require&channel_binding=require` also works —
`_asyncpg_dsn` translates `sslmode` to `ssl` and drops `channel_binding` before the engine
sees the DSN.

Final forms:

```
# Every service — runtime DSN (pooled)
DATABASE_URL=postgresql+asyncpg://apex:<APEX_DB_PASSWORD>@ep-<ENDPOINT_ID>-pooler.<REGION>.aws.neon.tech/apex_arena?ssl=require

# Ingestor service and the migration job — direct, non-pooled
DATABASE_MIGRATION_URL=postgresql+asyncpg://apex:<APEX_DB_PASSWORD>@ep-<ENDPOINT_ID>.<REGION>.aws.neon.tech/apex_arena?ssl=require
```

Neon terminates TLS at the endpoint and rejects plaintext connections, so `ssl=require` matches
reality; it does not verify the CA. If you want certificate verification, use `ssl=verify-full`
and supply a CA bundle to asyncpg — that requires `connect_args` wiring that this codebase does
not currently have.

## 8. Add the values as Railway secrets

On each Railway service, under **Variables**:

| Variable | API service | Ingestor service |
| --- | --- | --- |
| `DATABASE_URL` | pooled, `?ssl=require` | pooled, `?ssl=require` (present, not used for the engine) |
| `DATABASE_MIGRATION_URL` | optional | **direct**, `?ssl=require` — **required, startup fails without it** |
| `POSTGRES_*` | unset (managed DSN) | unset (managed DSN) |
| `APP_ENV` | `production` | `production` |
| `APP_PROCESS_ROLE` | `api` | `ingestor` |

`APP_PROCESS_ROLE=all` is rejected when `APP_ENV=production`, so the two services are mandatory,
not optional. Enter the values through the Railway UI or `railway variables --set` — never
commit them and never echo them in a build step.

### Conservative pool sizing for a free tier

Neon's free plan caps concurrent connections (the pooled endpoint fans out to a much higher
client limit; the direct endpoint does not). `Database.__init__` takes **explicit** pool
parameters, which `container.py` supplies from settings:

| Engine kwarg | Env var | Default |
| --- | --- | --- |
| `pool_size` | `DB_POOL_SIZE` | `3` (range 1–20) |
| `max_overflow` | `DB_MAX_OVERFLOW` | `2` (range 0–20) |
| `pool_timeout` | `DB_POOL_TIMEOUT_SECONDS` | `15` (range 1–120) |
| `pool_recycle` | `DB_POOL_RECYCLE_SECONDS` | `300` (range 30–3600) |

`pool_pre_ping=True` is set unconditionally. SQLAlchemy's own defaults (`pool_size=5`,
`max_overflow=10`) do **not** apply — the shipped defaults cap each process at **5**
connections, not 15.

Budget per replica:

- **API service:** 5 connections (3 + 2), 1–2 replicas.
- **Ingestor service:** 5 connections, plus **one more** for the advisory-lease connection,
  which is held *outside* the pool for the process lifetime. Exactly **1** replica.

There is no need to smuggle pool settings through the URL query string; set the environment
variables. `pool_recycle=300` is well suited to Neon: it retires connections every five
minutes, which avoids handing a socket that Neon closed during idle scale-down to a live
request.

### Autosuspend and cold starts

Neon free-tier computes **autosuspend after a few minutes of inactivity**. Consequences:

- **First request latency.** A request arriving at a suspended compute waits for the compute to
  resume — typically a few hundred milliseconds to a couple of seconds. The API health check in
  `Database.health_check` uses a **2 second** timeout, so a cold start can make `/health` report
  `unavailable (TimeoutError)` even though the database is fine. Do not wire that health check
  to a restart policy without allowing for it.
- **The long-lived ingestor connection.** The advisory-lock connection is idle whenever there is
  no live session. When Neon suspends the compute it terminates that connection; the lock is
  released server-side, but `_ingestor_lease_connection` still points at a dead socket and
  `release_ingestor_lease()` will fail at shutdown. `pool_pre_ping` does **not** protect this
  connection — it is not checked out from the pool. Restarting the ingestor re-acquires the
  lease cleanly; treat an ingestor restart after a long idle period as expected behaviour, and
  do not run the ingestor continuously between race weekends if you want to avoid the churn.
- Continuous traffic (an always-connected SSE client, a monitor ping) keeps the compute awake
  but also keeps compute hours accruing against the free allowance.

## 9. Run Alembic

Use `scripts/run-production-migrations.sh`. It prefers `DATABASE_MIGRATION_URL`, falls back
to `DATABASE_URL`, holds its own advisory lock so two concurrent runs cannot collide, and
prints only the chosen variable name and the hostname — never a DSN:

```bash
scripts/run-production-migrations.sh --check    # report current vs head, apply nothing
scripts/run-production-migrations.sh            # upgrade to head
```

On Railway, run it as a one-off command on the ingestor service (which already holds the
direct URL) rather than as a release step on the API service:

```bash
railway run --service apex-arena-ingestor scripts/run-production-migrations.sh
```

If you see `prepared statement "__asyncpg_stmt_..." does not exist` or the migration hangs on
a lock, you are on the pooled endpoint. Re-check the hostname for the `-pooler` suffix.

## 10. Verify connectivity

```bash
# From the backend/ directory, with the service environment loaded.
python -c "
import asyncio
from app.core.settings import get_settings
from app.storage.database import Database

async def main():
    db = Database(get_settings().async_database_url)
    print(await db.health_check())
    await db.close()

asyncio.run(main())
"
```

Expect `(True, 'connected')`. `health_check` deliberately reports only the exception class on
failure so connection strings never reach the logs — keep it that way.

To confirm the ingestor lease works on the direct endpoint, start the ingestor, then from a
`psql` session on the same database:

```sql
SELECT pid, granted FROM pg_locks WHERE locktype = 'advisory' AND objid = 1095782232;
```

One row with `granted = true` means the session lock is held. If you run this against the
pooled endpoint and see zero rows or a row that disappears between calls, that is the pooling
problem in action.

## 11. Monitor storage

Neon's free plan meters **storage** and **compute hours** separately. Watch both under
**Monitoring** / **Usage** in the console.

The write volume here is dominated by normalized race events and room messages. Points to keep
an eye on:

- Neon storage includes the history retention window used for point-in-time restore. Reducing
  the restore window is the fastest way to shed storage on a free project.
- High-churn tables inflate storage until autovacuum runs. Check bloat with:

```sql
SELECT relname, n_live_tup, n_dead_tup, last_autovacuum
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 10;
```

- Set a Neon usage alert (or a calendar reminder) well before the quota, because exceeding it
  can suspend the project — which takes the app down, not just the writes.

## 12. Backup and restore limitations on the free plan

Be realistic about what the free plan gives you:

- **Point-in-time restore** is available but with a **short retention window** on the free plan
  (on the order of a day, and subject to change). It is not an archive.
- **No scheduled logical backups.** Neon's PITR is branch-based; there is no managed
  `pg_dump` schedule. If you need a durable copy, run `pg_dump` yourself against the direct
  endpoint and store the artifact somewhere off-platform.
- **Restore is a branch operation.** Recovering means creating a branch at a timestamp and
  repointing `DATABASE_URL` at the new endpoint — which changes the hostname, so both Railway
  services need updating. Rehearse this before you need it.
- **Project suspension for quota overage is not a backup event.** Data is not lost, but it is
  unreachable until usage resets or you upgrade.

Minimum viable practice for a low-cost deployment: a periodic `pg_dump --format=custom` against
the direct endpoint, written to storage you control, with the dump file treated as a secret
(it contains the full dataset). Do not schedule that job on the ingestor service — a long dump
competes with the advisory-lock connection for the free-tier connection budget.

---

## Quick reference

| Concern | Endpoint | Why |
| --- | --- | --- |
| API request handling | Pooled (`-pooler`), from `DATABASE_URL` | Many short sessions, no session state |
| Ingestor process | **Direct**, from `DATABASE_MIGRATION_URL` | Session-level `pg_try_advisory_lock` held for process lifetime |
| Alembic migrations | **Direct**, from `DATABASE_MIGRATION_URL` | DDL transaction + asyncpg prepared statements |
| Ad-hoc `psql` / `pg_dump` | Direct | Session state, long transactions |

Accepted `DATABASE_URL` prefixes: `postgresql://`, `postgresql+asyncpg://`. Nothing else.
Required TLS parameter in production: `ssl=require` (or `verify-ca` / `verify-full` / `true`);
Neon's `sslmode=require` is accepted and normalized.
`DATABASE_MIGRATION_URL` is **mandatory** for `APP_PROCESS_ROLE=ingestor` or `all` whenever
`APP_ENV` is `staging` or `production`.

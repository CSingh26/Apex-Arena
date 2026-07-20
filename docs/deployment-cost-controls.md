<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Deployment Cost Controls

How to keep Apex Arena cheap without breaking it, and where the money actually goes.

> **No platform in this stack is guaranteed to remain free, and pricing changes.**
> Railway, Neon, Upstash, and Vercel have all revised their free and entry tiers before.
> Every quota, allowance, and price named in this document is a **budgeting figure for
> arithmetic**, not a contractual guarantee — verify current numbers in each provider's
> console and pricing page before relying on them. Design so that moving a component is a
> variable change and a restart, and keep it that way.

Companion documents: [`low-cost-production-architecture.md`](./low-cost-production-architecture.md),
[`neon-setup.md`](./neon-setup.md), [`upstash-setup.md`](./upstash-setup.md),
[`apex-arena-vercel-deployment.md`](./apex-arena-vercel-deployment.md).

---

## Where the money goes

| Component | Billing model | Controllable? |
| --- | --- | --- |
| Railway API service | Compute time + memory, always on | Yes — replicas, resources |
| Railway ingestor service | Compute time + memory, always on | Yes — replicas, run only when needed |
| Neon | Storage + compute hours | Partly — pool size and autosuspend; storage only by manual cleanup |
| Upstash | Commands + storage + connections | Yes — heartbeat, idle connections |
| Vercel (two projects) | Function invocations + bandwidth | Partly — the two-hop design doubles invocations |
| OpenAI | Per token | Already funded — **do not disable to save infra money** |
| OpenF1 | Fixed subscription | Already paid — no lever here |

The always-on Railway containers are the dominant recurring cost. The Upstash command
counter is the most likely thing to surprise you.

---

## Railway

### One replica, always

`deploy/railway/api.toml` and `deploy/railway/ingestor.toml` both set `numReplicas = 1`.
Keep it there.

- **The ingestor must never exceed one replica.** This is a correctness constraint, not a
  cost one — see the singleton advisory lease in
  [`low-cost-production-architecture.md`](./low-cost-production-architecture.md). It also
  happens to be the cheapest configuration.
- **The API may be scaled later**, but check the connection budget first: each replica
  opens up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` (default 3 + 2 = 5) Neon connections and
  holds one Upstash connection per blocking `XREAD`. A second replica doubles both, and on
  free tiers that is where you hit a ceiling before you hit a bill.

### Combined mode as a cost lever

`APP_PROCESS_ROLE=combined` runs FastAPI and narrowly scoped worker duties in one container,
halving the container spend. It must stay at **one replica** while reconciliation or live
ingestion is enabled.

`app/main.py` acquires the singleton advisory lease before starting worker duties, and raises
if another process holds it — so combined mode is not without duplicate-ingestion protection.
What it gives up is isolation: a worker crash can take the API down with it, and every redeploy
drops every open SSE connection. Keep `DATABASE_MIGRATION_URL` set so the worker lease uses the
direct Neon endpoint.

### No preview services by default

Do not enable Railway PR/preview environments. Each one spins up its own always-on
container and its own database connections, and the cost is per-environment, not per-use.
Preview and staging are served by Vercel preview deployments plus, if genuinely needed, a
single long-lived staging service that you switch off between test windows.

If you do create a staging Railway service, give it its own Neon branch and its own Upstash
database — sharing production's is both a cost and a safety problem.

### Spending warning and a hard monthly limit

Set both, before pointing anything at production:

1. **Usage alert** at roughly 50% of your tolerable monthly spend — this is the one that
   gives you time to react.
2. **Hard monthly limit** at the maximum you are willing to lose. Railway's hard limit
   stops workloads when it is reached. That means an outage — which is the correct
   behaviour for a hobby-scale deployment, and far better than an open-ended bill from a
   crash loop or a runaway reconnect storm.

Decide now which you prefer: a stopped service or an unbounded invoice. Configure
accordingly and write the choice down, because the failure mode arrives at an inconvenient
moment either way.

### Resource monitoring

Watch, per service:

- **Memory** — a slow climb across a race weekend usually means an unbounded buffer.
  `ROOM_STREAM_BACKLOG_LIMIT` (default 250) and `ENGINE_RECENT_EVENTS_LIMIT` (default 100)
  are the in-process bounds worth checking first.
- **CPU** — the ingestor's normalization pipeline spikes with event rate; the API's CPU
  tracks SSE client count.
- **Restart count** — `restartPolicyMaxRetries = 10` with `ON_FAILURE`. A service that is
  restarting repeatedly is burning compute and, if it is the ingestor, thrashing the
  advisory lease.
- **Egress** — SSE is a long-lived stream of small frames; it is bandwidth-cheap, but a
  reconnect loop is not.

### Run the ingestor only when there is something to ingest

Between race weekends the ingestor has no work. Neon's autosuspend will terminate its idle
lease connection anyway (see `neon-setup.md`), so leaving it running buys nothing but
compute charges and lease churn. Stopping the ingestor service between sessions is a
legitimate and reversible saving. The API can stay up — historical and replay routes do not
depend on the ingestor.

---

## Neon (PostgreSQL)

### Conservative pool sizing

`settings.py` defaults, passed through to the SQLAlchemy engine by
`backend/app/services/container.py`:

```
DB_POOL_SIZE=3            # 1..20
DB_MAX_OVERFLOW=2         # 0..20
DB_POOL_TIMEOUT_SECONDS=15
DB_POOL_RECYCLE_SECONDS=300
```

That is a maximum of **5 pooled connections per process**. Budget:

| Process | Pooled | Extra | Total |
| --- | --- | --- | --- |
| API (1 replica) | 5 | — | 5 |
| Ingestor (1 replica) | 5 | 1 advisory-lease connection held **outside** the pool | 6 |

Do not raise `DB_POOL_SIZE` to "fix" a slow endpoint — a `pool_timeout` exhaustion usually
means a query is holding a session too long, and more connections just move the ceiling to
Neon's side, where it fails harder. `pool_pre_ping=True` and `pool_recycle=300` together
handle sockets that Neon dropped during autosuspend.

### Storage monitoring

Watch **storage** and **compute hours** separately in the Neon console; they meter
independently. Practical notes:

- Storage includes the history-retention window used for point-in-time restore. Shortening
  that window is the fastest way to shed storage on a small project.
- Write volume is dominated by raw and normalized race events. Check bloat:

```sql
SELECT relname, n_live_tup, n_dead_tup, last_autovacuum
FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;
```

- Set a usage alert well before the quota. Exceeding a Neon quota can **suspend the
  project**, which takes the whole application down, not just writes.
- Continuous traffic (a permanently open SSE client, an uptime monitor) keeps the compute
  awake and accrues compute hours. An always-on external pinger is a real cost.

### Telemetry retention variables — RESERVED, not implemented

> **These four variables do nothing today. Setting them has no runtime effect.**
> They are **reserved names**: declared in `settings.py` so deployment configuration and this
> document can be written against a stable contract, but **no pruning job consumes them**.
> There is no scheduled task, no background worker, and no migration that reads them.
> Storage is **not** controlled by any setting in this application. Manage it manually.

`settings.py` declares four, and labels them accordingly:

```python
# RESERVED: these record the intended retention policy but nothing prunes yet.
# No pruning job exists in the application, so setting them has no runtime
# effect today. They are declared so deployment configuration and the cost
# documentation can be written against stable names. Defaults are inert.
```

| Variable | Default | Range | Intended meaning (not yet implemented) |
| --- | --- | --- | --- |
| `RAW_EVENT_RETENTION_DAYS` | `0` | 0–3650 | Days of raw provider events to keep |
| `NORMALIZED_EVENT_RETENTION_DAYS` | `0` | 0–3650 | Days of normalized events to keep |
| `PROVIDER_PAYLOAD_RETENTION_DAYS` | `0` | 0–3650 | Days of normalized provider payloads to keep |
| `REPLAY_ARCHIVE_ENABLED` | `false` | bool | Whether replay archiving is on |

A repository-wide search finds **no reader** of `raw_event_retention_days`,
`normalized_event_retention_days`, `provider_payload_retention_days`, or
`replay_archive_enabled` anywhere in `backend/app/` outside `settings.py` itself. Confirm
that yourself before assuming otherwise; it is a one-line grep.

**Practical consequence:** database storage grows monotonically. Until a pruning
implementation exists, the only actual storage controls are manual — a periodic `DELETE`
against the direct endpoint, run outside a live session and after taking a Neon branch, plus
shortening Neon's history-retention window. Do not report "retention is configured" on the
basis of these variables being set.

When a pruning implementation lands, sensible starting values for a low-cost deployment:

```
RAW_EVENT_RETENTION_DAYS=14
NORMALIZED_EVENT_RETENTION_DAYS=90
PROVIDER_PAYLOAD_RETENTION_DAYS=7
REPLAY_ARCHIVE_ENABLED=false
```

Raw events and provider payloads are the bulky, low-value-after-the-fact datasets;
normalized events are what replays are built from, so keep them longer.

### Backup reality

The free plan gives point-in-time restore over a short window and **no scheduled logical
backups**. If the data matters, run `pg_dump --format=custom` against the direct endpoint
yourself, on a schedule, storing the artifact off-platform and treating it as a secret. Do
not schedule that job on the ingestor service — a long dump competes with the advisory-lease
connection for the connection budget. Details in `neon-setup.md`.

---

## Upstash (Redis)

### Command usage is the binding constraint

Upstash meters **commands per month**, plus storage and concurrent connections. The command
counter is what will bite first, because idle SSE clients consume commands continuously:
each blocking `XREAD` that returns empty still counts, and the consumer loops re-block
immediately.

**Do not restate the arithmetic here.** `docs/upstash-setup.md` contains the verified
commands-per-SSE-client formula, the per-stream block windows, the publisher-side cost, and
the worked monthly budget. Use that document as the single source of truth — a second copy
of the formula in a second file is a copy that will drift and be wrong.

The short version, with the details in `upstash-setup.md`: idle cost per client is
`60 / (block_ms / 1000)` commands per minute, and a single permanently open idle tab
consumes a large fraction of a free monthly allowance. Read the section titled *"Estimating
commands per SSE client per minute"* before setting any budget.

### Avoid unnecessary polling and health-check commands

Every avoidable command is money:

- **`REDIS_HEALTH_CHECK_INTERVAL_SECONDS=0`.** Each health check is an extra `PING` **per
  pooled connection**, billed as a command, and it interacts badly with long blocking
  reads. The SSE loops already detect and report connection failures. Set this to `0` unless
  you have a specific reason not to.
- **Do not point an external uptime monitor at `/health/ready`.** That endpoint runs a
  database health check *and* a Redis `PING` on every call. A one-minute monitor is 43,200
  extra Redis commands a month, plus 43,200 Neon queries keeping the compute awake. Monitor
  `/health/live` instead — it is deliberately dependency-free and it is the path Railway's
  own probe uses.
- **Do not add client-side polling** alongside SSE. The stream is the transport; a polling
  fallback doubles the cost of every connected client.
- **Close idle SSE connections.** Neither consumer loop has an idle timeout; they run until
  `request.is_disconnected()`. A tab left open overnight bills all night.
- **Raise `SSE_HEARTBEAT_SECONDS`** if you need a bigger lever — but read the caveat in
  `upstash-setup.md` about the hard-coded 10 s ceiling on the session stream, and remember
  that `effective_redis_socket_timeout` in `settings.py` raises the socket timeout in step
  so a longer block cannot be aborted mid-read.

### Free-tier command budget and the spend cap

Set, in this order:

1. A **usage alert** at 50% and again at 80% of the monthly command allowance.
2. A **hard budget/spend cap** in Account → Billing at an amount you accept losing (for
   example $5/month), before pointing production at the database.

Without a cap, a client reconnecting in a tight loop after an error turns a free deployment
into an open-ended bill. When the cap is reached, `EventBus._publish` raises
`RedisPublishError` and the SSE loops emit `degraded` — visible failure rather than silent
data loss, which is correct, but is no substitute for the alert that should have fired
first.

### Storage

Streams are explicitly trimmed with approximate `MAXLEN`, so memory is bounded by design.
The main storage risk is stray keys — clean up smoke-test streams after verification runs
(see step 6 of `upstash-setup.md`), because a forgotten test stream is retained forever.

---

## AI / OpenAI

**AI is already funded, and it is not an infrastructure cost lever.** Do not disable agents,
lower `AI_MAX_AGENTS_PER_EVENT`, or flip `AI_KILL_SWITCH` in pursuit of Railway or Neon
savings. Those are different budgets, and the AI-driven commentary is the product.

Preserve the configured safeguards as written:

```
AI_MAX_CALLS_PER_MINUTE=20
AI_MAX_CALLS_PER_SESSION=500
AI_MAX_AGENTS_PER_EVENT=4
AI_REQUEST_TIMEOUT_MS=20000
AI_DAILY_TOKEN_BUDGET=1000000
EVENT_IMPORTANCE_MIN_FOR_AI=0.55
AI_KILL_SWITCH=false
```

`EVENT_IMPORTANCE_MIN_FOR_AI` is the quality-and-cost dial that is actually intended to be
tuned: raising it means fewer, more significant events trigger a reaction. Raise it if
reactions feel noisy — not as an emergency cost measure.

> **Verified state of the code:** as documented in
> [`deployment-secrets.md`](./deployment-secrets.md), no OpenAI client currently exists in
> `backend/app/`. `ai_enabled` and `ai_kill_switch` are read in exactly one place
> (`backend/app/api/routes.py:154`) to report a status string. The rate limits and the token
> budget above are **declared but not currently enforced by any code**. When the integration
> lands, verify each limit is actually applied before treating it as a spend control.

`AI_KILL_SWITCH=true` is an incident control, not a budget control. Its use is covered in
[`deployment-rollback-runbook.md`](./deployment-rollback-runbook.md).

---

## OpenF1

**The OpenF1 subscription is already paid.** Preserve authenticated live ingestion — it is
the reason the live product exists, and disabling it saves nothing.

- Keep `OPENF1_USERNAME` / `OPENF1_PASSWORD` set on the ingestor service.
- Keep `OPENF1_LIVE_AUTO_CONNECT=true` on the ingestor and `false` on the API (the latter is
  enforced in production by `validate_runtime_contract`).
- `OPENF1_LIVE_TOPICS` is the one dial that affects downstream cost — every subscribed topic
  becomes normalized events, database writes, and Redis `XADD`s. Trimming it reduces Neon
  storage and Upstash commands. Trim only topics the product genuinely does not use;
  removing one silently removes a feature.
- `OPENF1_LIVE_CATALOG_SYNC_SECONDS` (default 60, range 15–900) controls REST catalog polling
  against OpenF1. Raising it reduces request volume against the provider. It does not
  reduce your bill, but it is good citizenship.

The reconnect settings (`OPENF1_RECONNECT_*`) exist so a flapping link backs off rather than
hammering. Leave the backoff in place; a tight reconnect loop is expensive on both sides.

---

## Optional future: Cloudflare R2 archive

**Not required for the first deployment. Do not build it now.**

If telemetry volume eventually outgrows what a small managed Postgres should hold, the
natural next step is to move cold raw events and provider payloads to object storage —
Cloudflare R2 is the obvious candidate because it has no egress charge, which matters for a
replay workload that reads archives back.

Shape it would take, when and if it is needed:

- Raw events and provider payloads past their retention window are written to R2 as
  compressed batched objects keyed by session, then deleted from Postgres.
- Normalized events stay in Postgres — they are what the live and replay paths query.
- `REPLAY_ARCHIVE_ENABLED` is the settings flag that anticipates this; it is currently
  declared and unused.

Preconditions before considering it: the retention pruning described above must actually be
implemented and running, and Neon storage must be the demonstrated constraint. Adding an
object store to a deployment that is nowhere near its storage quota adds a failure mode, a
credential, and a bill for no benefit.

---

## Monthly review checklist

Run this once a month, and again after every race weekend:

1. Railway — spend against the hard limit; restart counts on both services; memory trend.
2. Neon — storage used, compute hours, and whether autovacuum is keeping up.
3. Upstash — commands used against the monthly allowance, and peak concurrent connections.
4. Vercel — function invocations and bandwidth across **both** projects (the two-hop design
   means every API call counts twice).
5. OpenAI — token spend against `AI_DAILY_TOKEN_BUDGET`, once the integration exists.
6. Confirm every alert and hard cap is still configured — provider UI changes have silently
   dropped these before.
7. Re-read each provider's current pricing page. The numbers in this document were written
   against a snapshot in time and are not authoritative.

---

## Quick reference

| Lever | Setting | Effect |
| --- | --- | --- |
| Fewest containers | `APP_PROCESS_ROLE=combined` + one replica | ~half the Railway spend; lease is taken, but no isolation |
| Neon connections | `DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2` | 5 per process + 1 lease connection on the ingestor |
| Upstash commands | `REDIS_HEALTH_CHECK_INTERVAL_SECONDS=0`, longer `SSE_HEARTBEAT_SECONDS`, close idle streams | See the formula in `upstash-setup.md` |
| Uptime monitoring | probe `/health/live`, never `/health/ready` | Avoids a DB query and a Redis `PING` per probe |
| Storage | manual `DELETE` + Neon history window | The retention vars are **reserved and unimplemented** — they change nothing |
| Ingestor between races | stop the service | No ingestion, no compute charge, no lease churn |
| AI | leave alone | Already funded; not an infra lever |
| OpenF1 | leave alone | Already paid; keep live ingestion authenticated |

<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Upstash Redis Setup (Low-Cost Production)

This guide provisions Upstash Redis as the event transport for Apex Arena on Railway.
It is grounded in the actual repository code:

- `backend/app/storage/redis.py` — `RedisStore`, `EventBus` (the only Redis call sites)
- `backend/app/api/streaming.py` and `backend/app/api/room_streaming.py` — the SSE consumer loops
- `backend/app/core/settings.py` — `redis_url`, `validate_redis_url`, `redis_dsn`, `sse_heartbeat_seconds`
- `backend/pyproject.toml` — `redis>=6.2,<7` (redis-py asyncio client, not the Upstash REST SDK)

All credentials below are placeholders. Never log, echo, or commit a real Redis URL — it embeds
the password. `RedisStore.health_check` and `EventBus._publish` deliberately log only the
exception class name; preserve that.

---

## Command audit: exactly what this application sends

Every Redis call in the codebase lives in `backend/app/storage/redis.py`. The complete set:

| Command | Call site | Options used | Upstash support |
| --- | --- | --- | --- |
| `PING` | `RedisStore.health_check` | — | Supported |
| `XADD` | `EventBus._publish` (all publish methods) | `MAXLEN ~ <n>` (approximate trim) | Supported |
| `XREVRANGE` | `latest_connection_status`, `latest_room_stream_id` | `COUNT 1` | Supported |
| `XREAD` | `read_events` | `COUNT` (non-blocking) | Supported |
| `XREAD` | `read_room_stream`, `read_session_streams` | `COUNT` + **`BLOCK`** | Supported, with caveats — see below |

Plus the handshake redis-py issues per connection: `HELLO`/`AUTH` on connect, and `PING` if
`health_check_interval` is set. Those count as commands too.

Notable **absences**, which is good news for Upstash: no consumer groups (`XGROUP`, `XREADGROUP`,
`XACK`), no `SUBSCRIBE`/pub-sub, no `SCAN`, no Lua scripting, no `WAIT`, no transactions or
pipelining. Streams are trimmed with approximate `MAXLEN` (2000 events, 500 state, 200 status,
5000 room), so memory is bounded by design.

Upstash implements Redis Streams, so **every command this application uses is supported**. The
risk is not compatibility — it is the connection and command-count cost of blocking `XREAD`.

### The blocking XREAD problem

`read_session_streams` and `read_room_stream` call `XREAD ... BLOCK <block_ms>`. A blocking read
**occupies a connection for the whole block window**. Upstash bills per command and caps
concurrent connections, so an SSE client that is connected but idle still costs one connection
continuously and one command per block window.

Block windows in the current code:

- `app/api/streaming.py`: `block_ms = min(10_000, sse_heartbeat_seconds * 1000)` → **10 s** at
  the default `SSE_HEARTBEAT_SECONDS=15`.
- `app/api/room_streaming.py`: `block_ms = sse_heartbeat_seconds * 1000` → **15 s** at the
  default. (Note the room stream has no 10 s ceiling — it scales directly with the heartbeat.)

Both loops re-enter immediately after each read, so an idle client issues a steady stream of
`XREAD` calls forever.

---

## 1. Create a free Upstash Redis database

1. Sign up at <https://upstash.com> and open the Redis section.
2. Create a database named e.g. `apex-arena`.
3. Choose the **regional** (single-region) type. Global replication multiplies cost and this
   workload is a single Railway region talking to a single stream set.
4. Leave eviction **disabled**. Apex Arena's streams are trimmed explicitly with `MAXLEN ~`;
   eviction could silently drop a live stream mid-session.

Upstash's free tier is a current offering, not a guarantee, and its limits have changed over
time. Verify the numbers in the console before you rely on them; the figures in this document
are for budgeting arithmetic, not contractual.

## 2. Pick a region near Railway and Neon

Every `XADD` and every blocking `XREAD` round-trips from the Railway container. Choose the
Upstash region matching the Railway region hosting the backend, and keep it consistent with the
Neon region chosen in `docs/neon-setup.md`. Cross-continent latency here directly delays live
race events reaching the browser.

The region cannot be changed after creation — recreate the database if you get it wrong.

## 3. Obtain the TLS Redis URL

In the database detail page, open **Connect** and choose the `redis-cli` / native Redis protocol
tab (not the REST tab — this app uses redis-py over RESP, not `@upstash/redis`). Copy the
`rediss://` URL:

```
rediss://default:<UPSTASH_PASSWORD>@<ENDPOINT>.upstash.io:6379
```

Upstash uses the `default` user. The TLS port is typically `6379` on the `rediss://` endpoint;
use whatever the console shows.

`validate_redis_url` in `settings.py` accepts `redis://` or `rediss://`. When `APP_ENV=production`,
`validate_runtime_contract` additionally requires the URL to start with `rediss://`:

```python
if not self.redis_url.get_secret_value().startswith("rediss://"):
    raise ValueError("Production REDIS_URL must use rediss://")
```

So a plaintext URL will fail startup in production, by design.

## 4. Enter it into Railway

Set on **both** the API service and the ingestor service (they share one Redis; the ingestor
publishes, the API consumes):

```
REDIS_URL=rediss://default:<UPSTASH_PASSWORD>@<ENDPOINT>.upstash.io:6379?socket_timeout=20&socket_connect_timeout=5&health_check_interval=0&max_connections=20
```

`REDIS_PORT` also exists in settings (default `6379`) but is informational — `RedisStore` builds
the client purely from the URL via `Redis.from_url(redis_url, decode_responses=True)`.

Enter the value through the Railway UI or `railway variables --set`. Do not print it in a build
step, a start script, or a debug endpoint.

### Recommended timeout and health-check settings

`RedisStore` passes no keyword arguments beyond `decode_responses`, so the **only** way to tune
the client without a code change is redis-py's URL query-string parsing, which understands
`socket_timeout`, `socket_connect_timeout`, `socket_keepalive`, `health_check_interval`,
`retry_on_timeout`, and `max_connections`. The extra parameters do not affect the settings
validator, which only checks the scheme prefix.

| Parameter | Value | Reason |
| --- | --- | --- |
| `socket_timeout` | `20` | **Must exceed the longest `BLOCK` window.** Room streams block for `sse_heartbeat_seconds` seconds (15 s by default). A `socket_timeout` below that turns every idle heartbeat into a `TimeoutError`, which the SSE loops catch and report as `degraded` — a self-inflicted outage. If you raise `SSE_HEARTBEAT_SECONDS`, raise this in step with it (heartbeat + 5 s). |
| `socket_connect_timeout` | `5` | Fail fast on a suspended or unreachable endpoint rather than hanging a request. |
| `health_check_interval` | `0` (disabled) | Each health check is an extra `PING` **per connection**, billed as a command, and it interacts badly with long blocking reads. `pool_pre_ping`-style checking is unnecessary here: the SSE loops already catch connection errors, emit a `degraded` event, sleep 1 s, and retry. If you prefer a safety net for the low-traffic publisher path, `30` is the highest-frequency value worth paying for. |
| `max_connections` | `20` | Bounds the pool so a burst of SSE clients cannot exhaust the Upstash connection limit; excess clients queue instead of erroring. Tune against your observed concurrency and your plan's connection cap. |
| `retry_on_timeout` | omit | The SSE loop's own retry is more informative than a silent client-level retry, and a hidden retry doubles command spend on a flapping link. |

If you later add `connect_args` to `RedisStore`, prefer explicit kwargs over URL parameters —
they are easier to review than a query string embedded in a secret.

## 5. Verify TLS

```bash
# Environment supplies REDIS_URL; the value is never printed.
python -c "
import asyncio
from app.core.settings import get_settings
from app.storage.redis import RedisStore

async def main():
    store = RedisStore(get_settings().redis_dsn)
    print(await store.health_check())
    print('tls:', store.client.connection_pool.connection_kwargs.get('connection_class', type(None)).__name__)
    await store.close()

asyncio.run(main())
"
```

Expect `(True, 'connected')`. To confirm the transport is genuinely TLS rather than relying on
the scheme string, check that a plaintext connection to the same endpoint is refused, and
confirm `safe_runtime_metadata` reports the expected `redis_host` — that property exposes host
and port only, never the password, which is why it is safe to surface on an admin endpoint.

Do not test with `redis-cli -u <url>` on a shared machine: the URL lands in shell history.

## 6. Test the supported commands

Exercise the actual code paths rather than raw commands, so you test what production runs:

```bash
python -c "
import asyncio
from app.core.settings import get_settings
from app.storage.redis import EventBus, RedisStore

async def main():
    store = RedisStore(get_settings().redis_dsn)
    bus = EventBus(store.client)
    await bus.publish_connection_status({'state': 'smoke-test'})   # XADD
    print('xrevrange:', await bus.latest_connection_status())      # XREVRANGE
    print('xread:', await bus.read_room_stream('smoke', '\$', count=1, block_ms=1000))  # XREAD BLOCK
    await store.close()

asyncio.run(main())
"
```

A successful run proves `PING`, `XADD` with approximate `MAXLEN`, `XREVRANGE COUNT`, and
`XREAD BLOCK` all work on your Upstash instance. That is the complete command surface.

Clean up the smoke-test stream afterwards (`apex:rooms:smoke`) — it is otherwise a permanently
retained key counting against the 256 MB storage limit.

## 7. Monitor the monthly command limit

Upstash's free plan meters **commands per month** (commonly quoted at 500,000), plus a storage
cap and a concurrent-connection cap. Watch the **Usage** tab. The command counter is the one
that will bite first, because idle SSE clients consume commands continuously.

### Estimating commands per SSE client per minute

The consumer loops re-block immediately after each read, so for an **idle** client:

```
commands per client per minute = 60 / (block_ms / 1000)
```

At the defaults:

| Stream | `block_ms` | Idle commands/min | Idle commands/hour |
| --- | --- | --- | --- |
| Session stream (`/streaming.py`) | 10 s | **6** | 360 |
| Room stream (`/room_streaming.py`) | 15 s | **4** | 240 |

Add one `XREVRANGE` per room-stream connection at handshake (`latest_room_stream_id`), and one
`HELLO`/`AUTH` per new pooled connection.

For an **active** client, each `XREAD` returns as soon as any subscribed stream has data, and the
loop immediately re-issues, so:

```
active commands per client per minute ≈ 60 / (block_ms/1000) + (batches delivered per minute)
```

where batches are bounded above by the event publish rate and below by `count=100` per read
(`engine_recent_events_limit`). During a live race, a publish rate of ~2 events/second produces
roughly **120 additional XREADs per client per minute** in the worst case — a 20x increase over
idle. That is the number that determines whether the free plan survives a race weekend.

On the **publisher** side, `RaceEventRedisPublisher.consume` issues **2 XADDs per normalized
event** (event stream + state stream). At 2 events/second that is 240 commands/minute total,
independent of client count.

Worked monthly budget against a 500,000 command allowance:

- One idle session-stream client: 6/min × 60 × 24 × 30 ≈ **259,000 commands/month**. A *single*
  permanently open idle tab consumes roughly half the free allowance.
- Total idle capacity: 500,000 ÷ 6 ≈ 83,000 client-minutes ≈ **1,390 client-hours per month**.
  Ten concurrent viewers exhaust that in under six days of continuous connection.
- A four-hour race weekend with ten active viewers at ~126 commands/min each: 10 × 126 × 240 ≈
  **302,000 commands** — most of a month's allowance in one weekend.

Levers, in order of effectiveness:

1. **Raise `SSE_HEARTBEAT_SECONDS`.** It is validated to `1..120`. Raising it to 60 cuts the
   room-stream idle rate from 4/min to 1/min. Note the session stream is capped at 10 s by the
   hard-coded `min(10_000, ...)` in `streaming.py`, so raising the heartbeat alone does **not**
   reduce session-stream cost — changing that ceiling requires a code change.
2. **Close idle SSE connections.** Both loops run until `request.is_disconnected()`; there is no
   idle timeout. A client left open overnight bills all night.
3. **Reduce concurrent room subscriptions per user** — each open stream is its own connection
   and its own command stream.

Set an Upstash usage alert at 50% and 80% of the monthly allowance.

## 8. Set a budget cap

Upstash's pay-as-you-go tier charges per command past the free allowance. In **Account →
Billing**, set a hard **budget/spend cap** at an amount you are willing to lose (e.g. $5/month)
before pointing production at the database. Without a cap, a stuck SSE loop — or a client that
reconnects in a tight loop after an error — turns a free deployment into an open-ended bill.

Pair the cap with an alert threshold below it, so you learn about the overage before the
database starts rejecting commands. When the cap is hit, `EventBus._publish` raises
`RedisPublishError` and the SSE loops emit `degraded` — degraded, not silent, which is the
correct behaviour but is not a substitute for the alert.

## 9. Recognizing when the free plan is insufficient

Move off the free plan (or off Upstash) when any of these appear:

- **Sustained concurrent SSE clients above roughly 5–10.** The idle command arithmetic above
  makes the free allowance untenable past that, regardless of traffic patterns.
- **Frequent `degraded` events in the logs** ("Session stream degraded" / "Race room stream
  degraded") that correlate with usage spikes rather than deploys — a symptom of hitting
  command or connection limits.
- **Connection-limit errors** as viewer count grows. Each blocking `XREAD` holds a connection
  for its entire block window, so peak concurrent connections ≈ peak concurrent SSE clients,
  not peak request rate.
- **`RedisPublishError` during live ingestion.** Dropping published events is a correctness
  problem, not a performance one — the ingestor is the only writer.
- **Any need for consumer groups.** If the app grows to multiple API replicas that must not
  each replay the full stream, you will want `XREADGROUP`, and the per-command billing model
  becomes markedly less attractive than a fixed-price Redis instance.

Realistic alternatives at that point: Railway's own Redis add-on (fixed monthly cost, no
per-command metering, same region as the app), or an Upstash paid fixed-price plan. Because the
app touches only five Redis commands and reads the endpoint from `REDIS_URL`, migrating is a
variable change plus a restart — keep it that way.

---

## Quick reference

**Command surface:** `PING`, `XADD` (`MAXLEN ~`), `XREVRANGE` (`COUNT`), `XREAD` (`COUNT`, `BLOCK`).
Nothing else. All supported by Upstash.

**Idle cost:** `60 / (block_ms / 1000)` commands per client per minute — 6/min for session
streams, 4/min for room streams at default settings.

**Required in production:** `rediss://` scheme (enforced by `validate_runtime_contract`).

**Critical setting:** `socket_timeout` must be greater than `SSE_HEARTBEAT_SECONDS`, or every
idle heartbeat becomes a spurious `degraded` event.

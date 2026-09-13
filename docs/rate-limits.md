# Distributed API admission and stream capacity

The API uses **aggregate** Redis budgets shared across replicas. It does not
identify visitors. X-Forwarded-For, X-Real-IP, cookies, scope client addresses,
room slugs and operator credentials never partition quota. The authenticated
Next-to-backend proxy hop remains required in deployed environments; it does
not prove a visitor's identity. One caller can exhaust the shared allowance.
Per-visitor fairness requires a separately verified first-ingress identity
contract and is not implemented.

## Defaults and configuration

Readiness retains dependency diagnostics during admission-store failure: `/health/ready`
runs its bounded checks and returns 503 with `dependencies.admission=unavailable` even
if database and Redis PING succeed. This exception never admits business routes. Ordinary
over-budget readiness requests still receive 429. Liveness remains dependency-free.

| Class | Tokens per minute | Burst |
| --- | ---: | ---: |
| GET/HEAD reads, including unknown paths and docs | 600 | 120 |
| Replay-operator verification POST | 20 | 5 |
| Room control POST | 30 | 5 |
| Sync/generate and other POST mutations | 4 | 1 |
| New room/session SSE connections | 60 | 20 |
| Health/readiness probes other than liveness | 60 | 10 |

`RATE_LIMIT_ENABLED=true` is the default. Explicitly disabling it removes this
protection; isolated route tests do so to avoid unrelated Redis I/O. All API
replicas must use the same Redis database and namespace. Empty
`RATE_LIMIT_NAMESPACE` selects `apex:limits:v1:<APP_ENV>`; an override is limited
to 80 ASCII letters/digits/colon/underscore/hyphen. Separate environments should
not share an override. Do not rotate namespaces to work around throttling:
that starts fresh budgets and temporarily separates stream capacity accounting.

Read, auth, mutation and SSE rates/bursts are positive bounded integer settings
with the `RATE_LIMIT_<CLASS>_PER_MINUTE` and `RATE_LIMIT_<CLASS>_BURST` names in
the environment examples. Maintenance and health budgets are fixed above.
`RATE_LIMIT_STREAM_CAPACITY=200` counts **both** SSE routes together, not each
individually. A room page normally opens two streams. These are starting safety
budgets, not a verified production audience/capacity target; load-test and tune
them before increasing traffic. Ordinary polling and tabs all share the budget.

`RATE_LIMIT_TIMEOUT_SECONDS=0.2` bounds each Redis command (positive, finite,
at most 2 seconds). `RATE_LIMIT_STREAM_TTL_SECONDS=30` must be 3–120 seconds;
the Redis timeout must be less than TTL/3 to preserve renewal margin. Stream
capacity must be 1–10,000. No per-path/client keys are allocated: token buckets
expire and one bounded-capacity sorted set holds active stream tokens. Redis
TIME supplies token refill and stream expiry; Lua makes admission atomic.

## Ordering and availability

CORS wraps proxy authentication, which wraps admission, which wraps routes.
Invalid proxy traffic is rejected before Redis; OPTIONS does not spend quota.
Existing operator/internal authorization and replay ownership/409 semantics
remain in place after admission. Invalid operator attempts consume allowance.
`/health/live` is always independent of admission/Redis. Other reads and
mutations fail closed on Redis failure: HTTP503, `rate_limit_unavailable`,
Retry-After:5. Exhausted budgets return HTTP429, `rate_limit_exceeded`, and
Retry-After in seconds. Both return Cache-Control:no-store and safe messages.
There is no in-memory fail-open fallback.

## Whole-response SSE leases

The pure ASGI middleware admits the request, then acquires a unique expiring
Redis slot **before** entering route work or sending HTTP200. It owns the full
response body, not just creation of a StreamingResponse. Renewal runs every
TTL/3 independently of blocked response sends or source reads. Failed renewal
may retry only until the last confirmed conservative local deadline; expired
or removed tokens cannot be resurrected. Lease loss cancels downstream work.
Before headers a lease failure can return503; after headers the stream closes
without trying to append an HTTP error response.

A single receive pump observes disconnects without racing the application's
receive. SSE endpoints are GET streams without upload bodies; only one pending
request message is buffered, and excessive unconsumed request messages close
the stream. Normal EOF, disconnect, application error, cancellation (including
before headers), and blocked sends all cancel/join response and renewal tasks.
Cleanup is shielded against framework cancellation and bounded by the Redis
deadline. If a response finalizer fails to finish in that bound, it is canceled
again and its slot is left to expire rather than freeing capacity underneath
still-running work. ASGI application code must cooperate with cancellation;
Python cannot forcibly terminate code that deliberately suppresses it.

Release removes only the request's random token, including best-effort cleanup
after an uncertain acquisition timeout. A release outage or process death is
recovered by TTL. Redis removal/flush/restart is ownership loss, not continued
permission to stream. Preserve Redis independently of API scaling.

## Browser behavior and operational limits

The Next proxy preserves429/503, Retry-After and no-store. Browser abort signals
reach upstream fetch; cancellation of its streamed response body cancels the
upstream body. JSON ApiError exposes status and bounded Retry-After (1–300
seconds; malformed/missing values on429/503 fall back to5). Room polling keeps
rendered data and honors that delay. Mutations are never automatically replayed.

Native EventSource cannot inspect HTTP status or Retry-After. Both first-party
streams instead retain cursor/state and use exponentially increasing jittered
reconnect delays, capped at60 seconds (45–60 seconds at the cap). This is **not**
exact Retry-After handling. The initial retries remain short for ordinary
disconnects. Throttled bootstrap/control errors include actionable retry text.

This protects backend admission, not all Next/edge bandwidth or request-body
buffering, distributed ingestion ownership, source-consumer recovery, or fair
visitor allocation. Existing deployment-level network and operator controls
remain necessary. No production deployment or hosting configuration change is
implied by these defaults.

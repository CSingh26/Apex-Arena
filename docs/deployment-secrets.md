<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Deployment Secrets and Environment Variables

Every variable this deployment uses, classified by where it lives and whether it is a
secret. **Every value in this document is a placeholder.** Never paste a real credential
into a file, a commit, a log line, a build step, or a shell history.

Grounded in `backend/app/core/settings.py`, `frontend/src/app/api/[[...path]]/route.ts`,
`deploy/railway/api.toml`, `deploy/railway/ingestor.toml`, and `.env.example`.

Companion documents: [`neon-setup.md`](./neon-setup.md),
[`upstash-setup.md`](./upstash-setup.md),
[`apex-arena-vercel-deployment.md`](./apex-arena-vercel-deployment.md),
[`portfolio-vercel-integration.md`](./portfolio-vercel-integration.md).

Service abbreviations used in the tables:

| Code | Service |
| --- | --- |
| **P** | Portfolio Vercel project (owns `chaitanyasingh.org`) |
| **F** | Apex Arena frontend Vercel project |
| **A** | Railway API service (`APP_PROCESS_ROLE=api`) |
| **I** | Railway ingestor service (`APP_PROCESS_ROLE=ingestor`) |
| **M** | Migration job (`python -m app.runtime migrate`) |

---

## NEVER expose through `NEXT_PUBLIC_*`

Next.js **inlines every `NEXT_PUBLIC_*` value into the client JavaScript bundle at build
time**. It is a literal string substitution, not a runtime lookup. Once a build ships, the
value is public forever — in the served `.js`, in every CDN cache, and in every browser
cache. Rotating the secret afterwards does not remove it from the old build output.

The following must **never** carry a `NEXT_PUBLIC_` prefix, under any circumstances:

| Variable | What leaking it costs you |
| --- | --- |
| `DATABASE_URL` | Full read/write access to the production database, credentials embedded |
| `DATABASE_MIGRATION_URL` | Same, on the unpooled endpoint — plus the ability to break the ingestor lease |
| `REDIS_URL` | Full access to the event transport; the password is inside the URL |
| `OPENAI_API_KEY` | Billable API access charged to you |
| `OPENF1_PASSWORD` | Your paid OpenF1 subscription, usable by anyone |
| `APEX_ARENA_PROXY_TOKEN` | Bypasses the portfolio hop and the backend's 403 gate |
| `APEX_ARENA_BACKEND_PROXY_TOKEN` | Direct authenticated access to the Railway API |
| `JWT_SECRET` | Token forgery |
| `SESSION_SECRET` | Session forgery |
| `INTERNAL_API_KEY` | Access to internal/administrative endpoints |
| `ADMIN_DASHBOARD_PASSWORD` | Administrative access |

Also never prefixed, though not secrets in the credential sense: `BACKEND_PUBLIC_ORIGIN`,
`BACKEND_INTERNAL_URL`, `APEX_ARENA_ORIGIN`. Publishing them reveals the internal topology
the whole architecture exists to hide.

Verify after every deploy:

```bash
curl -s https://chaitanyasingh.org/apex-arena/_next/static/chunks/*.js \
  | grep -iE 'railway|\.vercel\.app|neon\.tech|upstash\.io|postgresql://|rediss://'
```

Any match is a leak. Also grep the served HTML — Next.js inlines some values there too.

---

## 1. Public frontend (`NEXT_PUBLIC_*`) — never secret

These **are** compiled into the browser bundle. That is the point. Only put values here
that you are content to publish.

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_APP_NAME` | F | No | `Apex Arena` |
| `NEXT_PUBLIC_APP_URL` | F | No | `https://chaitanyasingh.org/apex-arena` |
| `NEXT_PUBLIC_APP_BASE_PATH` | F | No | `/apex-arena` |
| `NEXT_PUBLIC_API_BASE_PATH` | F | No | `/apex-arena/api` |
| `NEXT_PUBLIC_SENTRY_DSN` | F | No (see note) | `https://<public-key>@o000000.ingest.sentry.io/0000000` |

Notes:

- `NEXT_PUBLIC_APP_BASE_PATH` is **build-time**: `next.config.ts` derives `basePath` from
  it. Changing it requires a rebuild, not just a redeploy, and the portfolio matcher must
  change in step.
- `NEXT_PUBLIC_APP_URL` is what `layout.tsx` uses for `metadataBase` and what `publicUrl()`
  in `frontend/src/lib/app-paths.ts` builds canonical/OG URLs from. Unset means canonical
  tags fall back to `http://localhost:3000`.
- `NEXT_PUBLIC_API_BASE_PATH` is optional; `app-paths.ts` derives `${APP_BASE_PATH}/api`
  when it is unset. Leaving it unset keeps the two in step automatically.
- A Sentry **DSN** is designed to be public (it only permits event ingestion), but it does
  allow anyone to send you events. Treat it as low-sensitivity, not zero-sensitivity.

## 2. Server-side frontend (Vercel, no prefix)

Readable only in route handlers, Server Components, and middleware. Never serialized to the
client.

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `PUBLIC_APP_URL` | F | No | `https://chaitanyasingh.org/apex-arena` |
| `BACKEND_PUBLIC_ORIGIN` | F | No, but internal | `https://apex-arena-api-production.up.railway.app` |
| `BACKEND_INTERNAL_URL` | F | No, but internal | *(unset — only for same-platform private networking)* |
| `INTERNAL_BACKEND_URL` | F | No, but internal | *(legacy fallback; unset for new deployments)* |
| `BACKEND_URL` | F | No, but internal | *(legacy fallback; unset for new deployments)* |
| `APEX_ARENA_BACKEND_PROXY_TOKEN` | F | **Yes** | `<apex-backend-proxy-token-placeholder>` |
| `APEX_ARENA_ORIGIN` | P | No, but internal | `https://apex-arena.vercel.app` |
| `APEX_ARENA_PROXY_TOKEN` | P | **Yes** | `<portfolio-hop-token-placeholder>` |

`frontend/src/app/api/[[...path]]/route.ts` resolves the backend origin in this exact
order, and fails closed with **503** in production when all four are unset:

```
BACKEND_INTERNAL_URL ?? BACKEND_PUBLIC_ORIGIN ?? INTERNAL_BACKEND_URL ?? BACKEND_URL
  ?? (NODE_ENV === "production" ? null : "http://localhost:8000")
```

Origin format: scheme + host, **no trailing slash, no path**.

## 3. Backend non-secret (Railway)

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `APP_NAME` | A, I | No | `Apex Arena` |
| `APP_ENV` | A, I, M | No | `production` (`local` \| `test` \| `staging` \| `production`) |
| `APP_PROCESS_ROLE` | A, I | No | `api` on A, `ingestor` on I (`all` is rejected in production) |
| `NODE_ENV` | A, I | No | `production` |
| `PORT` | A, I | No | *injected by Railway; read by `app/runtime.py`, default `8000`* |
| `FORWARDED_ALLOW_IPS` | A | No | `127.0.0.1` (default; only change if you understand the trust implications) |
| `APP_BASE_PATH` | A | No | `/apex-arena` |
| `SEASON_YEAR` | A, I | No | `2026` |
| `SEASON_ONLY_MODE` | A, I | No | `true` (requires `SEASON_YEAR=2026`) |
| `TARGET_GRAND_PRIX` | A, I | No | `Belgian Grand Prix` |
| `TARGET_CIRCUIT` | A, I | No | `Spa-Francorchamps` |
| `FRONTEND_URL` / `BACKEND_URL` | A | No | `https://chaitanyasingh.org/apex-arena` |
| `PRODUCTION_FRONTEND_URL` | A | No | `https://chaitanyasingh.org/apex-arena` |
| `PRODUCTION_BACKEND_URL` | A | No | `https://chaitanyasingh.org/apex-arena/api` |
| `PUBLIC_BASE_URL` | A | No | `https://chaitanyasingh.org/apex-arena` |
| `DB_POOL_SIZE` | A, I | No | `3` (1–20) |
| `DB_MAX_OVERFLOW` | A, I | No | `2` (0–20) |
| `DB_POOL_TIMEOUT_SECONDS` | A, I | No | `15` (1–120) |
| `DB_POOL_RECYCLE_SECONDS` | A, I | No | `300` (30–3600) |
| `REDIS_PORT` | A, I | No | `6379` (informational; the client is built from the URL) |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | A, I | No | `5` (1–60) |
| `REDIS_CONNECT_TIMEOUT_SECONDS` | A, I | No | `5` (1–60) |
| `REDIS_HEALTH_CHECK_INTERVAL_SECONDS` | A, I | No | `0` (0–300; `0` disables the extra `PING`) |
| `STREAM_BACKEND` | A | No | `sse` (only accepted value) |
| `SSE_HEARTBEAT_SECONDS` | A | No | `15` (1–120) |
| `LIVE_MODE_ENABLED` | A, I | No | `true` |
| `LIVE_STALE_AFTER_SECONDS` | A | No | `15` |
| `LIVE_DEGRADED_AFTER_SECONDS` | A | No | `45` |
| `EVENT_DEDUP_TTL_SECONDS` | A, I | No | `3600` |
| `EVENT_ORDERING_BUFFER_MS` | A, I | No | `1500` |
| `EVENT_IMPORTANCE_MIN_FOR_AI` | A, I | No | `0.55` (0–1) |
| `REACTION_QUEUE_ENABLED` | A | No | `true` |
| `REACTION_QUEUE_MAX_SIZE` | A | No | `100` |
| `REACTION_STALE_AFTER_SECONDS` | A | No | `30` |
| `RACE_STATE_SNAPSHOT_EVERY_N_EVENTS` | A, I | No | `10` (1–1000) |
| `ENGINE_RECENT_EVENTS_LIMIT` | A | No | `100` (1–1000) |
| `ROOM_TOPIC_COOLDOWN_SECONDS` | A | No | `20` (0–600) |
| `ROOM_STREAM_BACKLOG_LIMIT` | A | No | `250` (1–1000) |
| `ROOM_REPLAY_INTERVAL_SECONDS` | A | No | `0.6` (0.05–10) |
| `ROOM_DIAGNOSTICS_ENABLED` | A | No | `false` — **must be false in production** |
| `DEVELOPMENT_FIXTURE_ENABLED` | A | No | `false` — **must be false in production** |
| `DEBUG_INGESTION_ENABLED` | A, I | No | `false` — **must be false in production** |
| `HISTORICAL_INGESTION_ENABLED` | A, I | No | `true` |
| `HISTORICAL_INGESTION_MAX_RECORDS_PER_ENDPOINT` | A, I | No | `5000` (1–50000) |
| `HISTORICAL_PROVIDER_RETRY_ATTEMPTS` | A, I | No | `3` |
| `HISTORICAL_PROVIDER_RETRY_BASE_DELAY_MS` | A, I | No | `100` |
| `HISTORICAL_PROVIDER_MIN_INTERVAL_MS` | A, I | No | `25` |
| `HISTORICAL_PROVIDER_CACHE_TTL_SECONDS` | A, I | No | `900` |
| `ENABLE_LIVE_ROOMS` | A | No | `true` |
| `ENABLE_HISTORICAL_REPLAY` | A | No | `true` |
| `ENABLE_AUTO_ROOM_CREATION` | A | No | `true` |
| `ENABLE_AGENT_MEMORY` | A | No | `true` |
| `ENABLE_AGENT_PREDICTIONS` | A | No | `true` |
| `ENABLE_PUBLIC_REPLAYS` | A | No | `true` |
| `ENABLE_USER_CHAT` | A | No | `false` |
| `ENABLE_USER_CREATED_AGENTS` | A | No | `false` |
| `ENABLE_VECTOR_MEMORY` | A | No | `false` |
| `ENABLE_MONTE_CARLO` | A | No | `false` |
| `CORS_ALLOWED_ORIGINS` | A | No | `https://chaitanyasingh.org` (comma-separated) |
| `JOLPICA_BASE_URL` | A, I | No | `https://api.jolpi.ca/ergast/f1` |

Retention variables have their own table in
[`deployment-cost-controls.md`](./deployment-cost-controls.md).

## 4. Backend secret (Railway)

Store as Railway **secret** variables. Never echo them in a start command, a build step, or
a debug endpoint. `settings.py` types every one of these as `SecretStr`, which keeps them
masked in `repr()` and log output — preserve that when adding code.

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `DATABASE_URL` | A, I | **Yes** | `postgresql+asyncpg://apex:<PASSWORD>@ep-<ID>-pooler.<REGION>.aws.neon.tech/apex_arena?ssl=require` |
| `DATABASE_MIGRATION_URL` | I, M | **Yes** | `postgresql+asyncpg://apex:<PASSWORD>@ep-<ID>.<REGION>.aws.neon.tech/apex_arena?ssl=require` |
| `POSTGRES_PASSWORD` | A, I | **Yes** | `<APEX_DB_PASSWORD>` |
| `REDIS_URL` | A, I | **Yes** | `rediss://default:<UPSTASH_PASSWORD>@<ENDPOINT>.upstash.io:6379?socket_timeout=20` |
| `OPENF1_PASSWORD` | I | **Yes** | `<OPENF1_PASSWORD>` |
| `OPENAI_API_KEY` | A | **Yes** | `sk-<placeholder>` |
| `APEX_ARENA_PROXY_TOKEN` | A | **Yes** | `<apex-backend-proxy-token-placeholder>` |
| `JWT_SECRET` | A | **Yes** | `<32-byte-random-hex>` |
| `SESSION_SECRET` | A | **Yes** | `<32-byte-random-hex>` |
| `INTERNAL_API_KEY` | A | **Yes** | `<internal-api-key-placeholder>` |
| `ADMIN_DASHBOARD_PASSWORD` | A | **Yes** | `<admin-password-placeholder>` |
| `SENTRY_DSN` | A, I | Treat as secret | `https://<key>@o000000.ingest.sentry.io/0000000` |

`JWT_SECRET`, `SESSION_SECRET`, `INTERNAL_API_KEY`, and `ADMIN_DASHBOARD_PASSWORD` are all
declared as optional (`SecretStr | None`) in `settings.py`. Set them anyway in production:
an unset secret is a silently disabled protection, and `deploy/railway/api.toml` lists
`INTERNAL_API_KEY` as required.

Generate each one independently:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Never reuse one value across two variables, two environments, or two services.

## 5. Neon (PostgreSQL)

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `DATABASE_URL` | A, I | **Yes** | pooled endpoint — see above |
| `DATABASE_MIGRATION_URL` | I, M | **Yes** | **direct** endpoint — see above |
| `POSTGRES_DB` | Local Compose | No | `apex_arena` |
| `POSTGRES_USER` | Local Compose | No | `apex` |
| `POSTGRES_PASSWORD` | Local Compose | **Yes** | `<LOCAL_DB_PASSWORD>` |
| `POSTGRES_HOST` | Local Compose | No | `postgres` |
| `POSTGRES_PORT` | Local Compose | No | `5432` |

Constraints enforced by `settings.py`:

- `validate_database_url` accepts **only** `postgresql://` or `postgresql+asyncpg://`.
  `postgres://` is rejected.
- When `POSTGRES_PASSWORD` is set, `validate_runtime_contract` compares it (URL-decoded) only
  against local-host `DATABASE_URL` / `DATABASE_MIGRATION_URL` values. External managed hosts
  ignore this unrelated Compose variable. Percent-encode in a local URL and store the raw value
  in `POSTGRES_PASSWORD`.
- In production, `DATABASE_URL` must carry `ssl` or `sslmode` of `require`, `verify-ca`,
  `verify-full`, or `true`.
- `_asyncpg_dsn` strips `sslmode`/`channel_binding` and rewrites to asyncpg's `ssl`, so
  Neon's copy-button string can be pasted with those parameters present.

**The ingestor connects with `DATABASE_MIGRATION_URL` when it is set** — see
`backend/app/services/container.py`. It must be the direct (no `-pooler`) endpoint or the
singleton advisory lease is not reliable. Details in
[`low-cost-production-architecture.md`](./low-cost-production-architecture.md) and
[`neon-setup.md`](./neon-setup.md).

## 6. Upstash (Redis)

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `REDIS_URL` | A, I | **Yes** | `rediss://default:<UPSTASH_PASSWORD>@<ENDPOINT>.upstash.io:6379?socket_timeout=20&socket_connect_timeout=5&health_check_interval=0&max_connections=20` |
| `REDIS_PORT` | A, I | No | `6379` |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | A, I | No | `5` |
| `REDIS_CONNECT_TIMEOUT_SECONDS` | A, I | No | `5` |
| `REDIS_HEALTH_CHECK_INTERVAL_SECONDS` | A, I | No | `0` |

The password is embedded in the URL, which is why the whole string is a secret. Production
requires the `rediss://` scheme (`validate_runtime_contract`). Note that
`effective_redis_socket_timeout` raises the configured socket timeout to at least
`min(10, SSE_HEARTBEAT_SECONDS) + 5` so a blocking `XREAD` cannot be aborted mid-heartbeat.

## 7. OpenF1

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `OPENF1_USERNAME` | I | Treat as secret | `<openf1-username>` |
| `OPENF1_PASSWORD` | I | **Yes** | `<openf1-password>` |
| `OPENF1_LIVE_AUTO_CONNECT` | A, I | No | `false` on A (**enforced** in production), `true` on I |
| `OPENF1_REST_BASE_URL` | A, I | No | `https://api.openf1.org/v1` |
| `OPENF1_AUTH_URL` | A, I | No | `https://api.openf1.org/token` |
| `OPENF1_MQTT_HOST` | I | No | `mqtt.openf1.org` |
| `OPENF1_MQTT_PORT` | I | No | `8883` |
| `OPENF1_MQTT_WS_URL` | I | No | `wss://mqtt.openf1.org:8084/mqtt` |
| `OPENF1_TOKEN_REFRESH_BUFFER_SECONDS` | I | No | `300` |
| `OPENF1_RECONNECT_MAX_ATTEMPTS` | I | No | `20` |
| `OPENF1_RECONNECT_BASE_DELAY_MS` | I | No | `1000` |
| `OPENF1_RECONNECT_MAX_DELAY_MS` | I | No | `30000` |
| `OPENF1_LIVE_CATALOG_SYNC_SECONDS` | I | No | `60` (15–900) |
| `OPENF1_LIVE_TOPICS` | I | No | `v1/sessions,v1/drivers,v1/position,v1/intervals,v1/laps,v1/pit,v1/stints,v1/race_control,v1/weather` |

Credentials belong on the **ingestor service only**. The API never subscribes to MQTT in
production, so it has no reason to hold them. `openf1_credentials_present` is surfaced
through `safe_runtime_metadata` as a boolean — never the values.

`OPENF1_RECONNECT_BASE_DELAY_MS` must not exceed `OPENF1_RECONNECT_MAX_DELAY_MS`; the
validator rejects it.

## 8. OpenAI

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | A | **Yes** | `sk-<placeholder>` |
| `AI_ENABLED` | A | No | `true` |
| `AI_KILL_SWITCH` | A | No | `false` |
| `OPENAI_REACTION_MODEL` | A | No | `gpt-4.1-mini` |
| `OPENAI_SUMMARY_MODEL` | A | No | `gpt-4.1-mini` |
| `AI_MAX_CALLS_PER_MINUTE` | A | No | `20` |
| `AI_MAX_CALLS_PER_SESSION` | A | No | `500` |
| `AI_MAX_AGENTS_PER_EVENT` | A | No | `4` |
| `AI_REQUEST_TIMEOUT_MS` | A | No | `20000` |
| `AI_DAILY_TOKEN_BUDGET` | A | No | `1000000` |

> **State of the code, verified:** the only place in `backend/app/` that reads `ai_enabled`
> or `ai_kill_switch` is `backend/app/api/routes.py:154`, which reports
> `"enabled" if settings.ai_enabled and not settings.ai_kill_switch else "disabled"`.
> `safe_runtime_metadata` exposes the same derived boolean. No OpenAI client, no call-rate
> enforcement, and no token-budget enforcement currently exists in the backend — a
> repository-wide search for `openai` outside `settings.py` and `tests/conftest.py` returns
> nothing. Set `OPENAI_API_KEY` if you want it in place for when the integration lands, but
> do not assume the rate limits or the kill switch currently gate any spend. Re-verify
> before relying on them.

## 9. Proxy chain

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `APEX_ARENA_ORIGIN` | P | No, but internal | `https://apex-arena.vercel.app` |
| `APEX_ARENA_PROXY_TOKEN` | P | **Yes** | `<portfolio-hop-token>` |
| `APEX_ARENA_BACKEND_PROXY_TOKEN` | F | **Yes** | `<backend-hop-token>` |
| `APEX_ARENA_PROXY_TOKEN` | A | **Yes** | `<backend-hop-token>` — same value as the row above |
| `PROXY_ENFORCEMENT_ENABLED` | A | No | `true` (default) |
| `PUBLIC_PROXY_HOST` | A | No | `chaitanyasingh.org` |
| `TRUSTED_PROXY_HOSTS` | A | No | `chaitanyasingh.org` (comma-separated) |
| `APP_BASE_PATH` | A | No | `/apex-arena` |

### Token pairing — the thing most likely to be got wrong

There are **two independent tokens** on the two hops, and one of them is spelled
differently on each side:

```
  ┌──────────────┐  x-apex-proxy-token: $APEX_ARENA_PROXY_TOKEN (portfolio value)
  │  Portfolio   │──────────────────────────────────┐
  │  (Vercel P)  │                                  │  TOKEN #1
  └──────────────┘                                  ▼
                                        ┌────────────────────────┐
                                        │ Apex Arena frontend (F)│
                                        │  route.ts DELETES the  │
                                        │  inbound token and     │
                                        │  MINTS a new one       │
                                        └───────────┬────────────┘
       x-apex-proxy-token: $APEX_ARENA_BACKEND_PROXY_TOKEN │  TOKEN #2
                                                    ▼
                                        ┌────────────────────────┐
                                        │ Railway API (A)        │
                                        │ compares against       │
                                        │ $APEX_ARENA_PROXY_TOKEN│
                                        └────────────────────────┘
```

| Hop | Sender variable | Receiver variable | Must match? |
| --- | --- | --- | --- |
| Portfolio → Apex frontend | `APEX_ARENA_PROXY_TOKEN` on **P** | *(nothing currently validates it on F)* | — |
| Apex frontend → Railway API | `APEX_ARENA_BACKEND_PROXY_TOKEN` on **F** | `APEX_ARENA_PROXY_TOKEN` on **A** | **Yes, identical strings** |

Three things follow:

1. **`APEX_ARENA_PROXY_TOKEN` means different things on P and on A.** On the portfolio it
   is token #1; on Railway it is the expected value of token #2. They should hold
   **different** values. Naming them the same is a trap in the current design — label them
   clearly in your password manager.
2. **Token #1 is not currently verified.** `route.ts` deletes any inbound
   `x-apex-proxy-token` unconditionally and mints its own. That deletion is the security
   property that matters (a client cannot forge the backend token), but it also means the
   Apex Arena origin does not today reject a request that arrived without the portfolio
   token. The origin's obscurity, not a check, is what keeps it unlisted.
3. **Token #2 is strictly enforced.** `ProxyContextMiddleware` compares it with
   `hmac.compare_digest` and returns 403 `{"detail": "Direct origin access is not
   permitted"}`. Enforcement requires **all** of: `PROXY_ENFORCEMENT_ENABLED` true,
   `APEX_ARENA_PROXY_TOKEN` set, and `APP_ENV` in `{staging, production}`. `/health/live`
   is the only exempt path.

`PUBLIC_PROXY_HOST` pins the browser-visible host outright. `TRUSTED_PROXY_HOSTS` is the
allow-list consulted when nothing is pinned. Set both, or `_public_host` falls back to
`request.url.netloc` — the Railway hostname — and generated links leak it.

## 10. Observability

| Variable | Services | Secret | Example placeholder |
| --- | --- | --- | --- |
| `LOG_LEVEL` | A, I | No | `info` |
| `LOG_FORMAT` | A, I | No | `json` in production (`pretty` \| `json`) |
| `SENTRY_DSN` | A, I | Treat as secret | `https://<key>@o000000.ingest.sentry.io/0000000` |
| `NEXT_PUBLIC_SENTRY_DSN` | F | No (public by design) | `https://<public-key>@o000000.ingest.sentry.io/0000000` |

`SENTRY_DSN` and `NEXT_PUBLIC_SENTRY_DSN` are declared in `settings.py` but no Sentry SDK
call site was found in `backend/app/`. Setting them is harmless and forward-looking; do not
assume errors are being reported until the integration is verified.

Set `LOG_FORMAT=json` on both Railway services — `deploy/railway/*.toml` both call for it,
and structured logs are the only practical way to correlate the `X-Request-ID` that
`ProxyContextMiddleware` stamps on every request and response.

---

## Per-environment separation

**Preview, staging, and production must use entirely different values for every secret.**
Not "different for the important ones" — different for all of them.

| Resource | Production | Preview / staging |
| --- | --- | --- |
| Neon | production project/branch | separate branch or project |
| Upstash | production database | separate database |
| Proxy token #1 | `<prod-portfolio-token>` | `<preview-portfolio-token>` |
| Proxy token #2 | `<prod-backend-token>` | `<preview-backend-token>` |
| `JWT_SECRET` / `SESSION_SECRET` / `INTERNAL_API_KEY` | unique | unique |
| `OPENF1_*` credentials | live subscription | the same paid account, but keep it off preview unless a preview genuinely needs live data |
| `APP_ENV` | `production` | `staging` |

Why it matters concretely:

- A preview deployment is built from an unreviewed branch. If it shares production's token,
  any branch can reach production data.
- Vercel preview URLs are guessable and are not access-controlled by default.
- `APP_PROCESS_ROLE=all` is only legal with `APP_ENV=staging`, so a combined-mode staging
  service **cannot** share a variable set with production anyway.
- Sharing one Neon database means a preview migration alters production's schema.

Vercel binds environment variables per environment (Production / Preview / Development).
Set each one explicitly rather than letting Preview inherit Production.

---

## Rotation procedure

Rotation is only useful if it is rehearsed. Every one of these requires a **redeploy** on
Vercel — environment variables are snapshotted into a deployment at build time, so editing
a dashboard value has no effect on a deployment that already exists, including for
middleware and other server-side code.

### Proxy token #2 (`APEX_ARENA_BACKEND_PROXY_TOKEN` ⇄ `APEX_ARENA_PROXY_TOKEN` on Railway)

This pair is enforced, so a mismatch is a blanket 403 on every API call. There is no
dual-token acceptance in `ProxyContextMiddleware` today — it compares against exactly one
configured value — so plan for a short mismatch window.

1. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Set `APEX_ARENA_PROXY_TOKEN` on the **Railway API service** to the new value. Railway
   restarts the service; the old frontend deployment now gets 403.
3. Immediately set `APEX_ARENA_BACKEND_PROXY_TOKEN` on the **Apex Arena Vercel project** to
   the same value and **redeploy** it.
4. Verify: `curl -i https://chaitanyasingh.org/apex-arena/api/health` returns 200, and a
   direct tokenless call to the Railway host returns 403 while `/health/live` returns 200.
5. Do it outside a race session. The window between steps 2 and 3 is a full outage of the
   API path.

To avoid the window entirely you would need the middleware to accept a set of tokens during
rollover. It does not today; do not claim otherwise in an incident review.

### Proxy token #1 (`APEX_ARENA_PROXY_TOKEN` on the portfolio)

Nothing validates it downstream today, so rotating it is low-risk: set the new value on the
portfolio project and redeploy. Note that the portfolio middleware returns **503** when
either `APEX_ARENA_ORIGIN` or `APEX_ARENA_PROXY_TOKEN` is unset — so do not clear it as an
intermediate step.

### `DATABASE_URL` / `DATABASE_MIGRATION_URL` / `POSTGRES_PASSWORD`

1. Reset the role password in the Neon console. Neon shows it once.
2. Update both managed secrets consistently: the pooled `DATABASE_URL` and direct
   `DATABASE_MIGRATION_URL`. `POSTGRES_PASSWORD` belongs to local Compose and is not a Railway
   or Neon secret; do not overwrite it during managed credential rotation.
3. Apply to the API service and the ingestor service and restart both.
4. Verify: `/health/ready` returns 200 with `database: "ready"` on the API, and the
   ingestor holds the advisory lock (`pg_locks` query in `neon-setup.md`).
5. Expect a brief ingestion gap while the ingestor restarts and re-acquires the lease.

### `REDIS_URL`

Rotate the password in the Upstash console, update both Railway services, restart both.
Verify with `/health/ready` (`redis: "ready"`) and by watching a room SSE stream deliver a
live event. Publishing and consuming use the same credential, so both sides must be updated
together.

### `OPENF1_PASSWORD`

Rotate with OpenF1, update the ingestor service only, restart it. Verify with
`/health/provider` on the ingestor: `connection_state` should return to `CONNECTED`. A wrong
credential surfaces as `MISSING_CREDENTIALS` or a failed auth rather than a crash, so check
the endpoint rather than assuming a clean restart means success.

### `JWT_SECRET` / `SESSION_SECRET`

Rotating these invalidates every issued token and session. Do it deliberately, not
routinely, and expect users to be signed out.

### `OPENAI_API_KEY`

Revoke the old key in the OpenAI dashboard **after** the new one is live, not before.

### General rules

- Rotate immediately on any suspicion of exposure — a value pasted into a chat, a log, a
  screenshot, or a public repo is compromised regardless of how briefly.
- Rotate on personnel change and on a fixed schedule (quarterly is a reasonable floor).
- Never rotate during a live race session.
- After every rotation, re-run the bundle grep in the first section of this document.
- Record *what* was rotated and *when* — never the value.

---

## Quick reference

**Enforced pair:** `APEX_ARENA_BACKEND_PROXY_TOKEN` (Vercel, Apex project) ==
`APEX_ARENA_PROXY_TOKEN` (Railway API). Mismatch = 403 on everything except `/health/live`.

**Direct DSN required:** `DATABASE_MIGRATION_URL` on the ingestor service and for Alembic.

**Never `NEXT_PUBLIC_`:** `DATABASE_URL`, `DATABASE_MIGRATION_URL`, `REDIS_URL`,
`OPENAI_API_KEY`, `OPENF1_PASSWORD`, `APEX_ARENA_PROXY_TOKEN`,
`APEX_ARENA_BACKEND_PROXY_TOKEN`, `JWT_SECRET`, `SESSION_SECRET`, `INTERNAL_API_KEY`,
`ADMIN_DASHBOARD_PASSWORD`.

**Vercel:** every env var change needs a new deployment. **Railway:** a variable change
restarts the service.

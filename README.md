<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Apex Arena

Apex Arena is a public Formula racing fan-simulation platform. Version 0.1 is intentionally
limited to the 2026 season: completed weekends become replay/archive candidates, and the Belgian
Grand Prix at Spa-Francorchamps is the first live target.

This repository contains the Day 2 unified race engine: live MQTT and historical REST records enter
the same idempotent processing pipeline, produce normalized session events and race state, persist
to PostgreSQL, publish through Redis Streams, and reach the dashboard over Server-Sent Events. It
does **not** yet contain AI fan reactions, user accounts or agents, vector memory, Monte Carlo
models, or replay-speed controls.

## Repository layout

```text
.
├── backend/             FastAPI, provider clients, domain/storage models, tests
├── frontend/            Next.js operational dashboard
├── docker-compose.yml   Local PostgreSQL and Redis
├── .env.example         Shared local environment contract
└── LICENSE              GNU AGPL v3 full text
```

## Race engine capabilities

- Typed, startup-validated settings with masked database, Redis, API-key, password, and token
  fields.
- PostgreSQL 17 and Redis 7.4 services with health checks and persistent volumes.
- Alembic migrations for the season catalog plus idempotent `raw_provider_events`, ordered
  `normalized_race_events`, `race_state_snapshots`, and observable `ingestion_runs`.
- Jolpica 2026 calendar/results client and completed/upcoming/live race classification.
- Unauthenticated OpenF1 historical REST client for sessions, drivers, position, intervals, laps,
  pit data, stints, race control, and weather.
- Backend-only OpenF1 OAuth token acquisition, expiry-aware in-memory caching, TLS MQTT
  subscriptions, reconnect state, and clean shutdown. Missing credentials degrade only live mode.
- Historical OpenF1 session ingestion through the exact same processor used by MQTT messages.
- Deterministic raw and normalized deduplication, a configurable event-time ordering buffer, and
  monotonic per-session sequence numbers within one application process.
- Deterministic race-state reduction with periodic PostgreSQL snapshots.
- Redis Streams for normalized events, state updates, and live connection status.
- Reconnect-safe SSE with persisted missed-event recovery and heartbeats.
- A Day 2 dashboard showing engine counts, session events, current state, stream health, the 2026
  calendar, and the Belgian Grand Prix target.

## Unified race engine

```text
OpenF1 MQTT live ─────┐
                     ├─> raw persistence -> normalize -> deduplicate -> event-time order
OpenF1 REST replay ──┘                                           |
                                                                 v
                         PostgreSQL events <- sequence -> race state -> snapshots
                                                           |
                                                           v
                                      Redis event/state/status Streams -> SSE -> dashboard
```

Both adapters create the same `RawEventInput`. Replay records are marked `is_replay`, but they do
not bypass persistence, deduplication, ordering, state reduction, or Redis publication. Raw payloads
are retained as JSONB for traceability and are never written to application logs.

## Prerequisites

- Docker with Compose
- Python 3.12 or newer
- Node.js 20.9 or newer and npm

## Local setup

1. Create the private local environment file:

   ```bash
   cp .env.example .env
   ```

2. Replace both `change-me` values with the same local PostgreSQL password. Never commit `.env`.

3. Start only PostgreSQL and Redis for manual development:

   ```bash
   docker compose up -d --wait postgres redis
   ```

4. Install and migrate the backend:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   alembic upgrade head
   uvicorn app.main:app --reload --port 8000
   ```

5. In another terminal, install and start the frontend:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

6. Open [http://localhost:3000](http://localhost:3000). API docs are available at
   [http://localhost:8000/docs](http://localhost:8000/docs).

### Full Docker stack

To build and run the frontend, backend, PostgreSQL, and Redis entirely in Docker:

```bash
docker compose up -d --build --wait
```

Compose runs database migrations before starting the API. The published application ports come
from `FRONTEND_PORT` and `BACKEND_PORT`; their browser-facing URLs must match `FRONTEND_URL`,
`BACKEND_URL`, `NEXT_PUBLIC_APP_URL`, `NEXT_PUBLIC_API_URL`, and `CORS_ALLOWED_ORIGINS`.

Inspect or stop the stack with:

```bash
docker compose ps
docker compose logs -f backend frontend
docker compose down
```

The Next.js config reads only `NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_APP_URL`, and
`NEXT_PUBLIC_API_URL` from the root `.env`. Backend secrets are not copied into browser code.

If the default ports are already occupied, change `POSTGRES_PORT` and `REDIS_PORT` and update the
matching ports in `DATABASE_URL` and `REDIS_URL` for that local run.

## Environment contract

Required for the local backend runtime:

- `DATABASE_URL` and `POSTGRES_PASSWORD` (the embedded URL password must match)
- `REDIS_URL`
- `SEASON_YEAR=2026` while `SEASON_ONLY_MODE=true`

Required by Docker Compose, with safe defaults except the password:

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT`
- `REDIS_PORT`

Optional race-engine configuration:

- `OPENF1_USERNAME` and `OPENF1_PASSWORD`: required only for authenticated live MQTT. Historical
  REST remains available without them.
- `OPENF1_LIVE_AUTO_CONNECT`: opt into MQTT connection during API startup; defaults to `false`.
- `OPENF1_LIVE_TOPICS`, reconnect delays, and maximum attempts configure the live adapter.
- `EVENT_ORDERING_BUFFER_MS`, `EVENT_DEDUP_TTL_SECONDS`, and
  `RACE_STATE_SNAPSHOT_EVERY_N_EVENTS` tune the processing pipeline.
- `SSE_HEARTBEAT_SECONDS` and `ENGINE_RECENT_EVENTS_LIMIT` tune client recovery and streaming.
- `HISTORICAL_INGESTION_ENABLED` and `DEBUG_INGESTION_ENABLED` gate replay ingestion.
- `INTERNAL_API_KEY` is required by the mutating historical-ingestion endpoint.
- `OPENAI_API_KEY`: no Day 2 code calls OpenAI. `AI_ENABLED` reports intended configuration only.
- `JWT_SECRET`, `SESSION_SECRET`, and `ADMIN_DASHBOARD_PASSWORD` remain reserved for later work.
- Sentry DSNs and production URLs: reserved for later deployment work.

See [.env.example](./.env.example) for the complete, standardized variable set and feature flags.
The application never logs passwords, provider tokens, API keys, Redis URLs, or full database
URLs. Health/debug responses contain only safe hosts, ports, and enabled/readiness states.

## Provider behavior

### Jolpica

`GET /api/v1/season/2026` retrieves the current calendar from Jolpica and normalizes round, race,
circuit, location, start time, lifecycle state, and target metadata. The client also exposes a
round-results method for completed races when provider results are available.

### OpenF1

Historical OpenF1 REST data from 2023 onward is available without authentication. Live REST,
MQTT, and WebSocket access require an OpenF1 subscription and OAuth token. Credentials and token
exchange remain backend-only; token values never appear in status responses or logs.

`OpenF1LiveClient` opens MQTT over TLS when live auto-connect is enabled and credentials are
present. It subscribes to configured `v1/*` topics, forwards dictionary payloads to the unified
processor, publishes safe connection state, and reconnects with bounded backoff. Provider tokens
and credentials never reach the browser, response bodies, or logs.

`HistoricalOpenF1Adapter` fetches selected categories for one OpenF1 session, caps records per
endpoint, sorts them by provider event time, and passes them through the same processor. The
mutating trigger is disabled unless both historical and debug ingestion are enabled and a private
internal key is configured.

## API endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Safe app, database, Redis, provider, live-auth, and AI status |
| `GET /api/v1/season/2026` | Normalized 2026 calendar summary |
| `GET /api/v1/openf1/status` | Historical REST configuration and live-auth readiness |
| `GET /api/v1/live/status` | Live mode, credential, token-cache, and connection state |
| `GET /api/v1/engine/status` | Dependency health, engine counts, sequence, live, and ingestion status |
| `GET /api/v1/sessions/{session_key}/events` | Persisted normalized session events after a sequence |
| `GET /api/v1/sessions/{session_key}/state` | Current in-memory or latest snapshotted race state |
| `GET /api/v1/stream/sessions/{session_key}` | SSE event, state, and live-status stream with recovery |
| `POST /api/v1/debug/ingest-historical-session` | Internal-key-protected historical ingestion trigger |
| `GET /api/v1/debug/config` | Non-secret runtime metadata and feature flags |

## Validation

Run the repository checks with:

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/pytest -q
alembic check

cd ../frontend
npm run lint
npm run typecheck
npm run build
npm audit

cd ..
docker compose config --quiet
```

With the services running, smoke-test the API using:

```bash
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/api/v1/season/2026
curl --fail http://localhost:8000/api/v1/openf1/status
curl --fail http://localhost:8000/api/v1/live/status
curl --fail http://localhost:8000/api/v1/engine/status

# Replace 9839 with a selected OpenF1 session key.
curl --fail http://localhost:8000/api/v1/sessions/9839/events
curl --fail http://localhost:8000/api/v1/sessions/9839/state
curl --no-buffer http://localhost:8000/api/v1/stream/sessions/9839

# Mutates local data and requires INTERNAL_API_KEY in the private .env.
curl --fail -X POST http://localhost:8000/api/v1/debug/ingest-historical-session \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  --data '{"session_key":"9839","endpoints":["laps","position","race_control"]}'
```

For the documented alternate local ports, set `POSTGRES_PORT=55432` with port `55432` in
`DATABASE_URL`, and `REDIS_PORT=56379` with port `56379` in `REDIS_URL`. Compose maps those host
ports to the containers' standard ports; application code needs no changes.

## Known limitations

- Per-session sequence allocation is process-local and should move to a database or distributed
  allocator before horizontally scaling API workers.
- The ordering buffer tolerates bounded lateness; records later than the configured watermark can
  still receive a later sequence number.
- On process restart, state loads the latest periodic snapshot; events after that snapshot are not
  yet replayed automatically into the reducer.
- Historical ingestion fetches endpoint payloads in one request per category and is intended as an
  internal Day 2 tool, not a public job queue.
- SSE is the only client streaming transport today. Live MQTT depends on valid OpenF1 subscription
  credentials and is deliberately disabled by default.

## Day 3 direction

Day 3 should add meaningful derived race events, a durable replay scheduler with pause/speed/seek,
database-backed sequence allocation and state rehydration, then place fan-reaction work behind the
now-observable race engine. User auth, deployment, and broad product polish remain separate work.

## License

Copyright (C) 2026 Chaitanya Singh.

Apex Arena is licensed under the [GNU Affero General Public License v3.0 only](./LICENSE)
(`AGPL-3.0-only`). Network users must be offered the corresponding source as required by the
license. See [NOTICE](./NOTICE) and [COPYRIGHT](./COPYRIGHT) for attribution details.

## Unofficial fan project disclaimer

Apex Arena is an unofficial fan project and is not affiliated with, endorsed by, or associated
with Formula 1, the FIA, Formula One Management, any racing team, OpenF1, or Jolpica. Formula 1 and
related marks belong to their respective owners. Provider data and services may be subject to
separate terms.

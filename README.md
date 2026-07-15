<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Apex Arena

Apex Arena is a public Formula racing fan-simulation platform. Version 0.1 is intentionally
limited to the 2026 season: completed weekends become replay/archive candidates, and the Belgian
Grand Prix at Spa-Francorchamps is the first live target.

This repository currently contains the Day 1 foundation—provider connections, typed race-domain
models, storage, operational health, and a basic control dashboard. It does **not** yet contain AI
fan reactions, user accounts or agents, vector memory, Monte Carlo models, or the full replay
engine.

## Repository layout

```text
.
├── backend/             FastAPI, provider clients, domain/storage models, tests
├── frontend/            Next.js operational dashboard
├── docker-compose.yml   Local PostgreSQL and Redis
├── .env.example         Shared local environment contract
└── LICENSE              GNU AGPL v3 full text
```

## Day 1 capabilities

- Typed, startup-validated settings with masked database, Redis, API-key, password, and token
  fields.
- PostgreSQL 17 and Redis 7.4 services with health checks and persistent volumes.
- Alembic migration for `seasons`, `race_meetings`, `sessions`, `drivers`, `constructors`, `rooms`,
  `raw_provider_events`, `normalized_race_events`, and `race_state_snapshots`.
- Jolpica 2026 calendar/results client and completed/upcoming/live race classification.
- Unauthenticated OpenF1 historical REST client for sessions, drivers, position, intervals, laps,
  pit data, stints, race control, and weather.
- Backend-only OpenF1 OAuth token acquisition, expiry-aware in-memory caching, and a Day 2 MQTT
  lifecycle boundary.
- Minimal Redis Stream event bus with publish/read methods.
- Live status dashboard with the Belgian Grand Prix highlighted as the target.

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

3. Start PostgreSQL and Redis:

   ```bash
   docker compose up -d --wait
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

Optional on Day 1:

- `OPENF1_USERNAME` and `OPENF1_PASSWORD`: required only when authenticated live access is
  attempted. Missing values produce a clear degraded live state; historical REST still works.
- `OPENAI_API_KEY`: no Day 1 code calls OpenAI. `AI_ENABLED` reports intended configuration only.
- `JWT_SECRET`, `SESSION_SECRET`, `INTERNAL_API_KEY`, and `ADMIN_DASHBOARD_PASSWORD`: reserved for
  later authenticated/admin features.
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

The Day 1 live client intentionally stops at an auth-ready transport boundary. It does not yet
open an MQTT connection or consume live topics.

## API endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Safe app, database, Redis, provider, live-auth, and AI status |
| `GET /api/v1/season/2026` | Normalized 2026 calendar summary |
| `GET /api/v1/openf1/status` | Historical REST configuration and live-auth readiness |
| `GET /api/v1/live/status` | Live mode, credential, token-cache, and connection state |
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
```

## Day 2 direction

The next milestone should consume OpenF1 live MQTT topics behind `OpenF1LiveClient`, persist raw
events idempotently, normalize and order events, publish them through the Redis event bus, and
maintain race-state snapshots. Replay timing and AI fan reactions should remain separate follow-on
work until the live ingestion path is observable and reliable.

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

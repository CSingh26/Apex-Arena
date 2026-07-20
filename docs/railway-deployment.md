# Apex Arena Railway deployment

Apex Arena no longer requires manually replacing a Railway Docker image tag after each backend
change. Both Railway backend services should be connected directly to the GitHub repository
`CSingh26/Apex-Arena` on branch `main`, using service-specific Railway config files.

## Services

### API service

- Railway service: `apex-arena-backend`
- GitHub branch: `main`
- Railway source root: repository root
- Railway custom config path: `/backend/deploy/railway/api.toml`
- Dockerfile: `backend/Dockerfile`
- Container working directory: `/app`
- Start command: runs `alembic upgrade head`, then `python -m app.runtime`
- Health check: `/health/live`
- Replicas: `1`
- Historical chat generation: disabled

Required variables:

```dotenv
RUN_ROOM_CHAT_BUILD=false
APP_ENV=production
APP_PROCESS_ROLE=api
DEBUG_INGESTION_ENABLED=false
DEVELOPMENT_FIXTURE_ENABLED=false
ROOM_DIAGNOSTICS_ENABLED=false
OPENF1_LIVE_AUTO_CONNECT=false
```

Also configure the existing production secrets/references:

- `DATABASE_URL`
- `REDIS_URL`
- `INTERNAL_API_KEY`
- `APEX_ARENA_PROXY_TOKEN` if proxy enforcement is enabled

### Historical chat-build service

- Railway service: `apex-arena-historical-chat`
- GitHub branch: `main`
- Railway source root: repository root
- Railway custom config path: `/backend/deploy/railway/chat-build.toml`
- Dockerfile: `backend/Dockerfile`
- Container working directory: `/app`
- HTTP health check: none
- Restart policy: never restart after successful completion
- Start command: exits unless `RUN_ROOM_CHAT_BUILD=true`

Required variables:

```dotenv
RUN_ROOM_CHAT_BUILD=false
APP_ENV=production
APP_PROCESS_ROLE=ingestor
DEBUG_INGESTION_ENABLED=false
DEVELOPMENT_FIXTURE_ENABLED=false
ROOM_DIAGNOSTICS_ENABLED=false
SEASON_YEAR=2026
MAX_ROOMS=100
MAX_MESSAGES_PER_ROOM=250
GENERATION_VERSION=v1
FORCE_REGENERATE=false
```

Existing secret/reference variables:

- `DATABASE_URL`
- `DATABASE_MIGRATION_URL`
- `REDIS_URL`
- `OPENAI_API_KEY`
- `OPENF1_USERNAME`
- `OPENF1_PASSWORD`

Keep `RUN_ROOM_CHAT_BUILD=false` except during the manual historical job window.

## GitHub Actions

Required repository secrets:

- `RAILWAY_TOKEN`
- `RAILWAY_PROJECT_ID`

Optional repository variables:

- `RAILWAY_API_SERVICE` defaults to `apex-arena-backend`
- `RAILWAY_HISTORICAL_SERVICE` defaults to `apex-arena-historical-chat`

`.github/workflows/deploy-railway.yml` deploys only the API service on pushes to `main` that touch
backend/deployment files. It never deploys the historical service and never runs the chat build.

`.github/workflows/run-historical-chat-build.yml` is `workflow_dispatch` only. It deploys the
historical service source, but it does not mutate Railway variables and does not generate chats
inside GitHub Actions. The Railway service must already have `RUN_ROOM_CHAT_BUILD=true` to execute
the finite job.

## Local deploy script

```bash
RAILWAY_TOKEN=... \
RAILWAY_PROJECT_ID=... \
scripts/deploy_railway.sh api

RAILWAY_TOKEN=... \
RAILWAY_PROJECT_ID=... \
scripts/deploy_railway.sh historical

RAILWAY_TOKEN=... \
RAILWAY_PROJECT_ID=... \
scripts/deploy_railway.sh all
```

The script validates the Railway CLI and required variables, deploys repository source, and never
prints token values. It does not change Railway variables.

## Safe historical run

Recommended production sequence inside the historical Railway service:

1. Set `RUN_ROOM_CHAT_BUILD=true`.
2. Keep `FORCE_REGENERATE=false`.
3. Start with a small `MAX_ROOMS` or a single room via direct CLI if doing a pilot.
4. Deploy the historical service manually or through `Run historical chat build`.
5. Watch logs until the finite job exits.
6. Set `RUN_ROOM_CHAT_BUILD=false` immediately after completion.

The script runs:

```bash
alembic upgrade head
python -m app.cli.database_status --json-summary
python -m app.cli.build_race_rooms --season "$SEASON_YEAR" --completed-only --json-summary --force-refresh
python -m app.cli.generate_room_chats --season "$SEASON_YEAR" --completed-only --json-summary
```

Generation is idempotent through deterministic message keys. Re-running the same version should not
duplicate messages. `FORCE_REGENERATE=true` soft-archives generated messages for the selected
version before rewriting them, so leave it false unless deliberately refreshing copy.

## Verification

Deployed commit:

```bash
railway status
railway logs --service apex-arena-backend
```

Neon migration state:

```bash
python -m app.cli.database_status --json-summary
```

Message idempotency:

```sql
SELECT slug, chat_generation_status, generation_version,
       generated_message_count, last_generated_sequence
FROM race_rooms
WHERE season = 2026
ORDER BY scheduled_start;

SELECT room_id, generation_key, count(*)
FROM room_messages
WHERE archived_at IS NULL AND generation_key IS NOT NULL
GROUP BY room_id, generation_key
HAVING count(*) > 1;
```

The duplicate query should return zero rows.

## Rollback

API rollback:

1. Redeploy a known-good commit from Railway or GitHub.
2. Keep `APP_PROCESS_ROLE=api`.
3. Confirm `/health/live`.
4. Confirm `python -m app.cli.database_status --json-summary`.

Historical rollback:

1. Set `RUN_ROOM_CHAT_BUILD=false`.
2. Stop or redeploy the historical service.
3. Do not drop, truncate, or reset Neon.
4. If a generated copy refresh was bad, run a reviewed regeneration with the intended
   `GENERATION_VERSION` and `FORCE_REGENERATE=true`; this soft-archives generated messages only.

## Troubleshooting

- `TypeError` from `force_sync`: ensure `build_race_rooms.py` calls `invalidate_catalog()` for
  refresh and then zero-arg `force_sync()`.
- Migration mismatch: run `python -m app.cli.database_status --json-summary`; API start also runs
  `alembic upgrade head`.
- Script missing from Docker image: verify `backend/Dockerfile` contains `COPY scripts ./scripts`.
- Historical service unexpectedly starts API: confirm custom config path is
  `/backend/deploy/railway/chat-build.toml`.
- Finite job health-check failure: the chat-build config must not define `healthcheckPath`.
- `RUN_ROOM_CHAT_BUILD=false`: service exits successfully with `Historical chat job disabled`.
- Wrong Railway config path: set API to `/backend/deploy/railway/api.toml` and historical to
  `/backend/deploy/railway/chat-build.toml`.
- Wrong Dockerfile path: for repository-root source, use `backend/Dockerfile`.
- Wrong root directory: these new manifests assume repository root, not `backend`.
- GitHub autodeploy not enabled: connect the API service to GitHub `main`, set the custom config
  path, and configure `RAILWAY_TOKEN` and `RAILWAY_PROJECT_ID` in GitHub Actions.

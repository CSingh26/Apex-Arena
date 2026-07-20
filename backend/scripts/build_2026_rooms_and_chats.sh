#!/usr/bin/env bash
set -euo pipefail

if [[ "${APP_ENV:-}" != "production" ]]; then
  echo "Refusing to run: APP_ENV must be production" >&2
  exit 2
fi

if [[ -z "${DATABASE_URL:-}" || -z "${DATABASE_MIGRATION_URL:-}" ]]; then
  echo "Refusing to run: DATABASE_URL and DATABASE_MIGRATION_URL are required" >&2
  exit 2
fi

if [[ "${DATABASE_URL}" == *"localhost"* || "${DATABASE_URL}" == *"127.0.0.1"* ]]; then
  echo "Refusing to run: DATABASE_URL points at a local database" >&2
  exit 2
fi

alembic upgrade head
python -m app.cli.database_status --json-summary
python -m app.cli.build_race_rooms \
  --season "${SEASON_YEAR:-2026}" \
  --completed-only \
  --json-summary \
  --force-refresh

generate_args=(
  --season "${SEASON_YEAR:-2026}"
  --completed-only
  --json-summary
  --generation-version "${GENERATION_VERSION:-v1}"
  --max-rooms "${MAX_ROOMS:-100}"
  --max-messages-per-room "${MAX_MESSAGES_PER_ROOM:-250}"
)

if [[ "${FORCE_REGENERATE:-false}" == "true" ]]; then
  generate_args+=(--force-regenerate)
fi

python -m app.cli.generate_room_chats "${generate_args[@]}"

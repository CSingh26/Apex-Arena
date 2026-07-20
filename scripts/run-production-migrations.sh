#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# One-shot Alembic migration runner for a managed PostgreSQL deployment.
#
# Run this ONCE per release, before rolling out the application, as a Railway
# one-off command or a release job. Application replicas must not race to
# migrate on startup.
#
# Concurrency is guarded by a PostgreSQL advisory lock, so two simultaneous
# invocations cannot apply migrations at the same time.
#
# Usage:
#   scripts/run-production-migrations.sh            # upgrade to head
#   scripts/run-production-migrations.sh --check    # show current vs head only
#
# This script never prints connection strings or credentials.

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
cd "$BACKEND_DIR"

# Prefer the direct (non-pooled) endpoint: Alembic and advisory locks are
# session-scoped and a transaction pooler silently breaks both.
if [[ -n "${DATABASE_MIGRATION_URL:-}" ]]; then
  MIGRATION_SOURCE="DATABASE_MIGRATION_URL"
elif [[ -n "${DATABASE_URL:-}" ]]; then
  MIGRATION_SOURCE="DATABASE_URL"
else
  echo "ERROR: set DATABASE_MIGRATION_URL (preferred) or DATABASE_URL." >&2
  exit 2
fi

# Report only which variable was chosen and a redacted host - never the full DSN.
MIGRATION_HOST="$(
  python3 - <<'PY'
import os
from urllib.parse import urlparse

raw = os.environ.get("DATABASE_MIGRATION_URL") or os.environ.get("DATABASE_URL", "")
host = urlparse(raw).hostname or "unknown"
labels = host.split(".")
if len(labels) > 2:
    print(f"{labels[0][:4]}....{'.'.join(labels[-2:])}")
else:
    print("local-or-external")
PY
)"

echo "Apex Arena migrations"
echo "  source : ${MIGRATION_SOURCE}"
echo "  host   : ${MIGRATION_HOST}"

CURRENT="$(alembic current 2>/dev/null | tail -n 1 || true)"
HEAD="$(alembic heads 2>/dev/null | tail -n 1 || true)"
echo "  current: ${CURRENT:-<none>}"
echo "  head   : ${HEAD:-<unknown>}"

if [[ "${1:-}" == "--check" ]]; then
  if [[ -n "$CURRENT" && "$CURRENT" == "$HEAD" ]]; then
    echo "Database is already at head; no migration required."
    exit 0
  fi
  echo "Migration required (dry run: nothing was applied)."
  exit 0
fi

# Serialize concurrent runners. The lock is released when the session ends,
# including on failure, so a crashed run cannot wedge later deployments.
python3 - <<'PY'
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATION_LOCK_ID = 1_095_782_233  # Distinct from the ingestor singleton lease.


def dsn() -> str:
    from app.core.settings import get_settings

    return get_settings().async_migration_database_url


async def main() -> int:
    engine = create_async_engine(dsn(), pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            if not acquired:
                print("ERROR: another migration run holds the lock.", file=sys.stderr)
                return 75  # EX_TEMPFAIL: safe to retry later.
            code = os.system("alembic upgrade head")
            await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            return 0 if code == 0 else 1
    finally:
        await engine.dispose()


raise SystemExit(asyncio.run(main()))
PY

echo "Migrations applied successfully."

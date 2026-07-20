#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/deploy_railway.sh api
  scripts/deploy_railway.sh historical
  scripts/deploy_railway.sh all

Required environment:
  RAILWAY_TOKEN
  RAILWAY_PROJECT_ID

Optional environment:
  RAILWAY_ENVIRONMENT=production
  RAILWAY_API_SERVICE=apex-arena-backend
  RAILWAY_HISTORICAL_SERVICE=apex-arena-historical-chat

Notes:
  - This deploys repository source; it does not mutate Railway variables.
  - Historical service execution still requires RUN_ROOM_CHAT_BUILD=true in Railway.
USAGE
}

die() {
  echo "deploy_railway: $*" >&2
  exit 2
}

command -v railway >/dev/null 2>&1 || die "Railway CLI is not installed"

target="${1:-}"
case "$target" in
  api|historical|all) ;;
  -h|--help|"")
    usage
    exit 0
    ;;
  *)
    usage >&2
    die "unknown target '$target'"
    ;;
esac

[[ -n "${RAILWAY_TOKEN:-}" ]] || die "RAILWAY_TOKEN is required"
[[ -n "${RAILWAY_PROJECT_ID:-}" ]] || die "RAILWAY_PROJECT_ID is required"

RAILWAY_ENVIRONMENT="${RAILWAY_ENVIRONMENT:-production}"
RAILWAY_API_SERVICE="${RAILWAY_API_SERVICE:-apex-arena-backend}"
RAILWAY_HISTORICAL_SERVICE="${RAILWAY_HISTORICAL_SERVICE:-apex-arena-historical-chat}"

deploy_service() {
  local service="$1"
  echo "Deploying Railway service '$service' to environment '$RAILWAY_ENVIRONMENT'"
  railway up \
    --project "$RAILWAY_PROJECT_ID" \
    --environment "$RAILWAY_ENVIRONMENT" \
    --service "$service" \
    --ci
}

if [[ "$target" == "api" || "$target" == "all" ]]; then
  deploy_service "$RAILWAY_API_SERVICE"
fi

if [[ "$target" == "historical" || "$target" == "all" ]]; then
  echo "Historical service deploy selected."
  echo "The job only runs when RUN_ROOM_CHAT_BUILD=true is set on the Railway service."
  deploy_service "$RAILWAY_HISTORICAL_SERVICE"
fi

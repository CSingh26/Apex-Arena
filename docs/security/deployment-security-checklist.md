<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Apex Arena — Staging Deployment Security Checklist

_These are deploy-time controls that are **not** represented as code in this repository and
therefore could not be verified during the code audit. Confirm each before exposing staging.
Do not treat any item as satisfied until independently verified._

## Application configuration
- [ ] `APP_ENV=staging` (never `local`/`test`) so debug/diagnostics endpoints stay gated.
- [ ] `CORS_ALLOWED_ORIGINS` set to the exact staging frontend origin (no wildcard). Credentials remain disabled.
- [ ] `INTERNAL_API_KEY` set to a strong, unique random value (rotate from any CI/test value). Without it, ingestion/sync/generate return 503 (safe default).
- [ ] `ROOM_DIAGNOSTICS_ENABLED=false` and `DEVELOPMENT_FIXTURE_ENABLED=false` in staging.
- [ ] `DEBUG_INGESTION_ENABLED` left off unless an operator actively needs a one-off ingest; re-disable afterward.
- [ ] `LOG_FORMAT=json`, `LOG_LEVEL=info` (or higher); confirm logs contain no secrets/tokens/authorization headers (code emits exception *types* only).
- [ ] OpenF1 live credentials (`OPENF1_USERNAME`/`OPENF1_PASSWORD`) are staging-scoped, distinct from production.

## Secret management
- [ ] All secrets injected from AWS Secrets Manager / SSM Parameter Store — **never** committed, and not plaintext in ECS task-definition env in source control.
- [ ] No secret passed as a Docker build arg (only `NEXT_PUBLIC_*` are build args, and none are secret).
- [ ] Confirm no `.env` is baked into any image (`.dockerignore` now excludes `.env*`; verify built layers).
- [ ] If the CI e2e internal key (`apex-arena-ci-release`) was ever reused anywhere real, rotate it.

## Network isolation (AWS)
- [ ] RDS PostgreSQL in private subnets, **no** public accessibility; encryption at rest + in transit; automated backups; deletion protection considered.
- [ ] ElastiCache Redis private; encryption in transit + at rest; AUTH enabled (`rediss://`). Do **not** reuse the local compose, which runs Redis without auth.
- [ ] ECS/Fargate tasks in private subnets; only the ALB is public.
- [ ] Security groups least-privilege: ALB→backend on app port only; backend→RDS/Redis only; no `0.0.0.0/0` to datastores.
- [ ] The local `docker-compose.yml` is **not** used for staging (datastores are loopback-only there by design).

## Transport / edge
- [ ] HTTPS enforced on all public endpoints; HTTP→HTTPS redirect; valid ACM certificate.
- [ ] HSTS enabled at the edge/CDN for staging (never for local).
- [ ] Security headers applied at the edge or a reverse proxy: `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, and a CSP with `frame-ancestors 'none'`. Validate the CSP does not break Next.js streaming/SSE/fonts/images before enforcing.
- [ ] ALB/WAF rate limiting on public read and SSE stream routes (app has no in-process limiter by design).

## IAM / least privilege
- [ ] ECS task role and execution role separated; task role scoped to only the secrets/resources it needs (no wildcard actions/resources).
- [ ] ECR pull access scoped; image tags immutable (publish uses `type=sha` + semver tags).
- [ ] Application DB user is **not** a PostgreSQL superuser; migration privileges separated where practical.
- [ ] ECS task metadata endpoint not reachable via any application proxy behavior (none exists in code today).

## Data / migrations
- [ ] `alembic upgrade head` tested against a staging-equivalent database (the backend container runs it on start).
- [ ] Staging and production databases are separate instances.
- [ ] Rollback procedure documented (previous image tag + migration downgrade path).

## Pre-flight verification
- [ ] `/health` returns healthy; datastores reachable only privately.
- [ ] `GET /api/v1/debug/config` returns **404** in staging/production (gated).
- [ ] A privileged op without `X-Internal-API-Key` returns 401/503, not 200.
- [ ] CI `release.yml` gates (lint, tests, Trivy CRITICAL scan, e2e) are green for the deployed commit.
- [ ] Confirm no source maps or debug tooling exposed by the production frontend build.

## Follow-up hardening (non-blocking)
- [ ] Pin all GitHub Actions by immutable commit SHA (Trivy already pinned).
- [ ] Consider an in-app or edge rate limiter tuned per route.
- [ ] Bump dev-only `pytest` past PYSEC-2026-1845 when convenient (not in the production image).

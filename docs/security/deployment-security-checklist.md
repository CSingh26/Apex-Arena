<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Apex Arena — Staging Deployment Security Checklist

_These are deploy-time controls that are **not** represented as code in this repository and
therefore could not be verified during the code audit. Confirm each before exposing staging.
Do not treat any item as satisfied until independently verified._

Target topology (see `../low-cost-production-architecture.md`):

```
browser -> chaitanyasingh.org (portfolio, Vercel project 1, owns the public domain)
        -> portfolio middleware rewrites /apex-arena/* and attaches APEX_ARENA_PROXY_TOKEN
        -> Apex Arena frontend (Vercel project 2, internal origin only, basePath=/apex-arena)
        -> /apex-arena/api/* proxied server-side to Railway
        -> FastAPI API on Railway (APP_PROCESS_ROLE=api)
        -> OpenF1 ingestor on Railway (APP_PROCESS_ROLE=ingestor, singleton advisory lease)
        -> Neon PostgreSQL (managed, TLS) + Upstash Redis (managed, rediss://)
```

## Application configuration
- [ ] `APP_ENV=production` (never `local`/`test`) so debug/diagnostics endpoints stay gated.
- [ ] `CORS_ALLOWED_ORIGINS` set to the exact staging frontend origin (no wildcard). Credentials remain disabled.
- [ ] `INTERNAL_API_KEY` set to a strong, unique random value (rotate from any CI/test value). Without it, ingestion/sync/generate return 503 (safe default).
- [ ] `ROOM_DIAGNOSTICS_ENABLED=false` and `DEVELOPMENT_FIXTURE_ENABLED=false` in production.
- [ ] `DEBUG_INGESTION_ENABLED` left off unless an operator actively needs a one-off ingest; re-disable afterward.
- [ ] `LOG_FORMAT=json`, `LOG_LEVEL=info` (or higher); confirm logs contain no secrets/tokens/authorization headers (code emits exception *types* only).
- [ ] OpenF1 live credentials (`OPENF1_USERNAME`/`OPENF1_PASSWORD`) are staging-scoped, distinct from production.

## Secret management
- [ ] All secrets injected as Railway service variables (secret type) and Vercel environment variables — **never** committed, and never plaintext in any manifest tracked in source control. See `../deployment-secrets.md` for the per-project inventory.
- [ ] No secret passed as a Docker build arg (only `NEXT_PUBLIC_*` are build args, and none are secret).
- [ ] Confirm no `.env` is baked into any image (`.dockerignore` now excludes `.env*`; verify built layers).
- [ ] If the CI e2e internal key (`apex-arena-ci-release`) was ever reused anywhere real, rotate it.

## Proxy chain and origin protection
- [ ] `APEX_ARENA_PROXY_TOKEN` on the Railway API service equals `APEX_ARENA_BACKEND_PROXY_TOKEN` on the Apex Arena Vercel project. A mismatch makes the backend answer **403 to everything** except `/health/live`.
- [ ] `PROXY_ENFORCEMENT_ENABLED` is on and `APP_ENV` is `staging`/`production` — enforcement in `backend/app/api/proxy.py` requires both plus a configured token.
- [ ] A direct request to the Railway API origin **without** the proxy token returns `403` (origin protection working).
- [ ] `/health/live` remains reachable without a token for the platform probe — it is deliberately exempt and exposes no state. Confirm no other path is.
- [ ] `PUBLIC_PROXY_HOST` / `TRUSTED_PROXY_HOSTS` pin the browser-visible host so a forwarded-host header cannot poison generated links.
- [ ] The Apex Arena Vercel project does **not** have the public domain attached; only the portfolio project owns `chaitanyasingh.org`.

## Managed datastore access (Neon / Upstash)
- [ ] **Understood limitation:** the free tiers of Neon and Upstash provide **no private networking or VPC**. Both endpoints are reachable from the internet and are protected by **credentials + TLS, not network isolation**. This is a materially weaker control than the private-subnet model; the compensating controls are the three items below.
- [ ] Neon credentials are unique to this project (not reused from any other environment) and rotated from any earlier value.
- [ ] Upstash credentials are unique to this project and rotated from any earlier value.
- [ ] Neon `DATABASE_URL` requires TLS (`sslmode`/`ssl` in `require`/`verify-ca`/`verify-full`); the production settings validator rejects anything else.
- [ ] Upstash `REDIS_URL` uses `rediss://` with AUTH; the production settings validator rejects `redis://`. Do **not** reuse the local compose, which runs Redis without auth.
- [ ] Rotation procedure for both is exercised at least once (see `../deployment-secrets.md`).
- [ ] The local `docker-compose.yml` is **not** used for staging (datastores are loopback-only there by design).

## Transport / edge
- [ ] HTTPS enforced on all public endpoints; HTTP→HTTPS redirect; valid certificate on `chaitanyasingh.org` (Vercel-managed).
- [ ] HSTS enabled at the Vercel edge for the public domain (never for local).
- [ ] Security headers applied at the portfolio edge: `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, and a CSP with `frame-ancestors 'none'`. Validate the CSP does not break Next.js streaming/SSE/fonts/images before enforcing.
- [ ] Edge rate limiting on public read and SSE stream routes at the Vercel edge / portfolio proxy (app has no in-process limiter by design).

## Platform access / least privilege
- [ ] Railway project and service access limited to the operators who need it; deploy tokens scoped per service and not shared with CI beyond what the gated release job needs.
- [ ] Vercel project access limited per project; the portfolio and Apex Arena projects do not share environment variables beyond the paired proxy token.
- [ ] GHCR pull access scoped; image tags immutable (publish uses `type=sha` + semver tags).
- [ ] Application DB user is **not** a PostgreSQL superuser; migration privileges separated where practical.
- [ ] No cloud instance-metadata endpoint exists in this topology, so metadata SSRF is not reachable; the provider clients remain host-allowlisted regardless (`OPENF1_ENDPOINTS`, fixed base URLs).

## Data / migrations
- [ ] `DATABASE_MIGRATION_URL` (the **direct, non-pooled** Neon endpoint) is set for migrations and for any ingesting role — otherwise startup fails by design (`validate_runtime_contract`), because the singleton advisory lease is unreliable through a transaction pooler.
- [ ] Migrations are run **once** via `scripts/run-production-migrations.sh` as a release step, not per replica on container start.
- [ ] `alembic upgrade head` tested against a staging-equivalent database.
- [ ] Staging and production use separate Neon databases (and separate Upstash databases).
- [ ] Rollback procedure documented and rehearsed (see `../deployment-rollback-runbook.md`).

## Pre-flight verification
- [ ] `/health/ready` reports healthy through the public path; datastore endpoints themselves are never exposed to the browser.
- [ ] `GET /api/v1/debug/config` returns **404** in staging/production (gated).
- [ ] A privileged op without `X-Internal-API-Key` returns 401/503, not 200.
- [ ] CI `release.yml` gates (lint, tests, Trivy CRITICAL scan, e2e) are green for the deployed commit.
- [ ] Confirm no source maps or debug tooling exposed by the production frontend build.
- [ ] Run `scripts/smoke-test-deployment.sh` after deploy and confirm no `railway.app`, `vercel.app`, `neon.tech`, or `upstash.io` hostname appears anywhere in public output.

## Follow-up hardening (non-blocking)
- [ ] Pin all GitHub Actions by immutable commit SHA (Trivy already pinned).
- [ ] Consider an in-app or edge rate limiter tuned per route.
- [ ] Bump dev-only `pytest` past PYSEC-2026-1845 when convenient (not in the production image).

<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Apex Arena — Threat Model (Staging)

_Last reviewed: 2026-07-19. Scope: pre-staging security audit of the FastAPI backend, Next.js frontend, and container/CI configuration in this repository._

## System overview

Apex Arena is a read-mostly Formula racing intelligence app. It ingests historical
and live session data from external providers (OpenF1, Jolpica), normalizes it into a
race-event pipeline, and renders deterministic multi-agent "race room" discussions to
anonymous public visitors. There is currently **no end-user authentication** — all
public surfaces are unauthenticated reads of non-sensitive, public sporting data.
Privileged operations (ingestion, catalog sync, room generation) are gated behind a
shared internal API key.

## Trust boundaries

| Boundary | From → To | Trust posture |
|---|---|---|
| B1 | Browser → `chaitanyasingh.org` (portfolio, Vercel project 1) | Untrusted client; the only public entry point; owns the public domain and the TLS/edge controls |
| B2 | Portfolio middleware → Apex Arena frontend (Vercel project 2) | Internal origin, `basePath=/apex-arena`, no public domain attached; the rewrite attaches `APEX_ARENA_PROXY_TOKEN` |
| B3 | Apex Arena frontend → FastAPI API on Railway | Server-side proxy of `/apex-arena/api/*`; mints the hop token from `APEX_ARENA_BACKEND_PROXY_TOKEN`; the backend origin never appears in the browser bundle |
| B4 | Public internet → Railway API origin | `ProxyContextMiddleware` rejects any request without a matching proxy token with `403` (constant-time compare); `/health/live` is deliberately exempt for the platform probe |
| B5 | Browser → FastAPI backend (logical) | Untrusted client; CORS-restricted, read endpoints public, mutating ops internal-key-gated |
| B6 | Backend → Neon PostgreSQL | Managed endpoint reachable over the internet on the free tier — **no private networking**; controlled by unique credentials + enforced TLS; parameterized ORM only; app DB user should be non-superuser |
| B7 | Backend → Upstash Redis | Managed endpoint reachable over the internet — **no private networking**; controlled by credentials + `rediss://` TLS; JSON transport, namespaced keys, no untrusted deserialization |
| B8 | Ingestor (Railway, `APP_PROCESS_ROLE=ingestor`) → Neon direct endpoint | Singleton advisory lease requires the direct, non-pooled `DATABASE_MIGRATION_URL`; startup fails without it |
| B9 | Backend → OpenF1 REST/OAuth/MQTT | Fixed provider hosts; TLS; server-side credentials only |
| B10 | Backend → Jolpica | Fixed provider host; TLS; response schema-validated |
| B11 | CI → GHCR | Tag-gated publish, scoped `GITHUB_TOKEN` |

## Assets

- **Provider credentials** (OpenF1 username/password), **internal API key**, **DB/Redis
  connection strings**, optional **JWT/session/admin secrets**, **Sentry DSN**. All held
  as `SecretStr`, injected via environment, never returned by any endpoint.
- **Data integrity** of the normalized event pipeline and room discussions.
- **Service availability** and **provider cost/quota** (ingestion is expensive).

## Primary threats and current mitigations

| # | Threat | Mitigation in code |
|---|---|---|
| T1 | Unauthorized expensive ops (ingest/sync/generate) | `hmac.compare_digest` internal-key gate; 503 when unconfigured; 404 when feature-disabled |
| T2 | SQL injection | SQLAlchemy Core/ORM expressions, bound params, allowlisted sort keys; only raw SQL is a constant `SELECT 1` |
| T3 | SSRF via provider clients | Provider base URLs are fixed settings; endpoints allowlisted (`OPENF1_ENDPOINTS`); filter keys regex-validated; timeouts + bounded retries; TLS verified |
| T4 | XSS from provider/agent content | React auto-escaping; no `dangerouslySetInnerHTML`; no HTML/markdown renderer; single external link is `rel="noreferrer"` |
| T5 | Unsafe deserialization | Redis payloads are JSON (`json.loads`), never pickle/yaml; MQTT payloads type-checked |
| T6 | Secret disclosure | `SecretStr` masking; logs emit `type(exc).__name__` only; `safe_runtime_metadata` excludes secrets; error envelopes generic |
| T7 | Business-logic abuse (future sessions spawning rooms) | `RoomEligibilityService` default-deny policy; future/unstarted sessions are read-only |
| T8 | Stream/DoS abuse | Bounded SSE backlog, heartbeats, disconnect cleanup, sanitized+capped Redis keys, bounded stream lengths |
| T9 | Prompt injection into privileged actions | No live LLM calls; content is deterministic templates that can only state grounded values; no tool access from generated content |
| T10 | Supply-chain / CI compromise | Default `contents: read`, tag-gated publish with scoped `packages: write`, `GITHUB_TOKEN`, no `pull_request_target`, Trivy CRITICAL gate |

## Residual risk (out of code scope — deployment-time)

- No application-layer rate limiting; relies on the Vercel edge and the portfolio proxy for
  public read/stream throttling.
- **Datastore network isolation is not available.** Neon and Upstash free tiers offer no
  private networking or VPC, so both endpoints are reachable from the internet. The
  previously assumed private-subnet boundary does not exist in this topology. What replaces
  it is weaker and must be treated as such: a unique per-project credential, enforced TLS
  (`sslmode=require`+ for Neon, `rediss://` for Upstash — both checked by the production
  settings validator), and a rotation procedure. Credential compromise is therefore directly
  exploitable from anywhere, with no network layer to fall back on.
- Platform controls replace the previously assumed VPC boundary: Railway project/service
  access, Vercel project access, and the paired proxy token that makes the Railway API origin
  answer `403` to anything that did not traverse the portfolio → Apex frontend chain. These
  are deploy-time controls with no code representation here beyond the middleware itself, and
  must be verified at deploy time (see `deployment-security-checklist.md`).
- No cloud instance-metadata endpoint exists in this topology, so metadata SSRF is not
  reachable; the provider clients remain host-allowlisted regardless.
- **Availability, not security:** Vercel caps function duration, which bounds the lifetime of
  an SSE stream. Clients reconnect with `Last-Event-ID`, so this is a continuity concern for
  live mode rather than a security control.
- Secret injection is via Railway service variables (secret type) and Vercel environment
  variables; correctness of that injection cannot be verified from this repository.
- No end-user auth yet: any future authenticated/mutating features must add authZ,
  CSRF, and session controls before exposure.

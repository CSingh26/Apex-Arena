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
| B1 | Browser → Next.js frontend | Untrusted client; server components render escaped data only |
| B2 | Browser → FastAPI backend | Untrusted client; CORS-restricted, read endpoints public, mutating ops key-gated |
| B3 | Backend → PostgreSQL | Private network; parameterized ORM only; app DB user should be non-superuser |
| B4 | Backend → Redis | Private network; JSON transport, namespaced keys, no untrusted deserialization |
| B5 | Backend → OpenF1 REST/OAuth/MQTT | Fixed provider hosts; TLS; server-side credentials only |
| B6 | Backend → Jolpica | Fixed provider host; TLS; response schema-validated |
| B7 | CI → GHCR | Tag-gated publish, scoped `GITHUB_TOKEN` |
| B8 | Public internet → AWS (staging) | Only the load balancer is public; RDS/ElastiCache/ECS tasks private |

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

- No application-layer rate limiting; relies on ALB/WAF for public read/stream throttling.
- Datastore isolation, IAM least-privilege, and secret injection are AWS controls not
  represented as code in this repo — they must be verified at deploy time
  (see `deployment-security-checklist.md`).
- No end-user auth yet: any future authenticated/mutating features must add authZ,
  CSRF, and session controls before exposure.

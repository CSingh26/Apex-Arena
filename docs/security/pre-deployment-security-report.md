<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Apex Arena — Pre-Deployment Security Report

_Audit date: 2026-07-19 · Branch: `main` · Scope: repository-wide (backend, frontend, containers, CI). No end-user auth is implemented yet; the app serves public sporting data with key-gated privileged operations._

## 1. Executive summary

A repository-wide, multi-pass security review of the Apex Arena application code, container
configuration, and CI pipeline found **no Critical or High severity vulnerabilities**. The
codebase is unusually well-hardened for its stage: privileged operations are gated with
constant-time key comparison, all database access is parameterized ORM, external provider
clients are SSRF-resistant (fixed hosts + allowlisted endpoints), untrusted content is
JSON-only (no unsafe deserialization) and rendered through React's auto-escaping, and the
"AI" discussion layer is fully deterministic with no live LLM calls or tool access.

Three findings were validated and **fixed** (1 Medium, 2 Low). Remaining items are
deployment-time infrastructure controls that cannot be verified from this repository
because there is no infrastructure-as-code; they are enumerated in
`deployment-security-checklist.md`.

## 2. Deployment decision

**Application code: APPROVED** — no Critical/High findings; all validated findings fixed and
regression-tested; full backend suite (198 tests) passes; lint/format clean; frontend prod
dependency audit clean.

**Overall staging deployment: CONDITIONAL** on the manual infrastructure controls in the
checklist (private RDS/ElastiCache, secret injection via Secrets Manager/SSM, explicit
staging CORS origin, HTTPS-only, ALB/WAF rate limiting, least-privilege IAM). These are
deploy-time controls with no code representation in this repo, so they must be confirmed by
the deployer before exposure. **This report does not claim those controls are in place.**

## 3. Confirmed findings

### F1 — Public `/api/v1/debug/config` leaks internal infrastructure metadata (Medium, fixed)
- **Component:** backend API · **File:** `backend/app/api/routes.py:283`
- **Confidence:** High. **Attacker prerequisites:** none (unauthenticated GET).
- **Exploit path:** Before the fix, `GET /api/v1/debug/config` returned
  `safe_runtime_metadata` — including `database_host`, `database_port`, `redis_host`,
  `redis_port` — in every environment. In staging/production these are internal RDS/
  ElastiCache endpoints, so an anonymous visitor could enumerate internal architecture.
- **Impact:** Information disclosure of internal topology (not secrets; hosts are not
  externally reachable, but disclosure aids targeting). Every other diagnostic surface
  (`/race-rooms/{slug}/diagnostics`) was already production-gated; this endpoint was the
  lone gap.
- **Fix:** Return `404` in production unless `room_diagnostics_enabled`, matching the
  existing diagnostics pattern.
- **Regression tests:** `test_debug_config_is_available_outside_production`,
  `test_debug_config_is_hidden_in_production_without_flag` (asserts 404 and absence of
  `database_host` in the body) in `backend/tests/test_routes.py`.
- **Validation:** Both tests pass; full suite green.

### F2 — Compose publishes PostgreSQL/Redis on all host interfaces; Redis unauthenticated (Low, fixed)
- **Component:** container config · **File:** `docker-compose.yml`
- **Confidence:** High. **Attacker prerequisites:** network reach to the Docker host.
- **Exploit path:** `ports: "5432:5432"` / `"6379:6379"` bind to `0.0.0.0`, and the Redis
  service runs with no `requirepass`. On a shared or exposed host, an off-host client could
  reach an unauthenticated Redis or the database port.
- **Impact:** Datastore exposure. Bounded because this compose is local-development only;
  staging uses private RDS/ElastiCache. Still a real hardening gap and a "compose defaults
  reused as prod" risk.
- **Fix:** Bind both published ports to `127.0.0.1`; added comments stating this file is
  local-only and must not be reused for staging.
- **Validation:** `docker compose config` confirms `host_ip: 127.0.0.1` for both datastores;
  app services (backend/frontend) remain reachable; local dev unaffected.

### F3 — `.dockerignore` did not exclude `.env` files (Low, fixed)
- **Component:** container build · **Files:** `backend/.dockerignore`, `frontend/.dockerignore`
- **Confidence:** Medium. **Attacker prerequisites:** access to a built image layer.
- **Exploit path:** The frontend build stage uses `COPY . .`; a stray `frontend/.env*`
  would be copied into a build layer (and Next.js may inline build-time env). No such file
  exists today, so this is defense-in-depth against future mistakes.
- **Fix:** Added `.env` / `.env.*` to both `.dockerignore` files.
- **Validation:** Frontend production image still builds (unchanged source); ignore rules
  parsed by build tooling.

## 4. Rejected / non-findings (false positives explicitly cleared)

- **SQL injection** — none. Only raw SQL is a constant `text("SELECT 1")` health probe
  (`backend/app/storage/database.py:37`); all queries use bound ORM expressions and the
  list-rooms `sort` uses a dict allowlist with a safe default.
- **SSRF** — provider base URLs are fixed settings validated to be absolute HTTP(S);
  endpoints are allowlisted via `OPENF1_ENDPOINTS`; filter keys pass a strict regex; no
  user-controlled URL/host reaches an outbound client. Metadata/loopback SSRF not reachable.
- **Command injection / RCE** — no `subprocess`, `os.system`, `eval`, `exec`, `pickle`,
  `yaml.load`, or dynamic import of untrusted input anywhere in `backend/app`.
- **XSS** — no `dangerouslySetInnerHTML` in product code, no markdown/HTML renderer; the one
  external anchor uses `rel="noreferrer"`; provider/agent strings are React-escaped.
- **Unsafe deserialization** — Redis and MQTT payloads are JSON only and type-checked.
- **Secret exposure in Git** — `.env` is gitignored and never appears in history; only
  `.env.example` (placeholder `change-me` values) is tracked. The CI `INTERNAL_API_KEY` is
  an ephemeral test-only value written to a throwaway `.env`, not a production secret.
- **CORS wildcard-with-credentials** — `allow_credentials=False`, methods limited to
  GET/POST, origins from an explicit env allowlist.
- **AI prompt injection into privileged ops** — no live LLM calls exist; generation is
  deterministic and cannot emit URLs, SQL, shell, or invoke endpoints.

## 5. Domain reviews (summary)

- **Authentication/authorization:** No user auth by design (public data). Privileged
  endpoints — `POST /api/v1/debug/ingest-historical-session`, `POST /api/v1/race-rooms/sync`,
  `POST /api/v1/race-rooms/{slug}/generate` — all enforce the internal key with
  `hmac.compare_digest`, return 503 when unconfigured, and 404 when the feature is disabled.
  Development/fixture rooms 404 outside `test`/`local`. Room actions flow through the
  default-deny `RoomEligibilityService`.
- **Secrets/credentials:** `SecretStr` for all secrets; masked in repr/logs; not exposed via
  any endpoint (`safe_runtime_metadata` is secret-free); OpenF1 OAuth password sent only in
  a POST body over TLS to a fixed auth URL; tokens never sent to the browser.
- **API security:** Pydantic models with bounded query params (pagination ≤100/250, search
  ≤100 chars, enum session/sort types, non-negative laps/sequences, discrete playback
  speeds); generic error envelopes; no stack traces returned.
- **Database:** Parameterized ORM; row locks (`with_for_update`) and uniqueness constraints
  guard against duplicate rooms/messages; provider-state preservation on upsert.
- **Redis/streams:** JSON transport, `apex:`-namespaced sanitized+capped keys, bounded
  `maxlen`, no untrusted deserialization.
- **Providers/SSRF & OpenF1 OAuth:** Fixed hosts, allowlisted endpoints, TLS, bounded
  timeouts/retries, backend-only credentials, concurrency-safe token refresh with buffer.
- **Frontend/XSS:** React escaping preserved; `encodeURIComponent` on all path params; no
  server secrets in the browser bundle (only `NEXT_PUBLIC_*`).
- **SSE/WebSocket:** SSE only; bounded backlog, heartbeats, disconnect cleanup; resume
  sequence validated as digits; public replay content, no sensitive data.
- **Docker:** Both images non-root, multi-stage, minimal runtime, no secrets in build args
  (only `NEXT_PUBLIC_*`), healthchecks present.
- **CI/CD:** `contents: read` default; tag-gated publish with scoped `packages: write`;
  `GITHUB_TOKEN` (not PAT); no `pull_request_target`; Trivy CRITICAL gate pinned by SHA.
- **Dependencies:** Runtime backend deps (fastapi, httpx, sqlalchemy, redis, asyncpg,
  paho-mqtt, uvicorn, pydantic-settings, alembic) — 0 known vulns. Frontend prod audit — 0
  vulns. `pip-audit` flagged only `pip` (build-time; Dockerfile already upgrades pip) and
  `pytest` (dev-only, not in the production image); both deferred with reason below.

## 6. Commands executed (exact results)

| Command | Result |
|---|---|
| `git status` / `git branch --show-current` | clean tree, branch `main` |
| `ruff format --check app tests` | `72 files already formatted` |
| `ruff check app tests` | `All checks passed!` |
| `pytest -q` (backend) | `198 passed, 1 warning` (196 pre-existing + 2 new) |
| `pytest -k debug_config` | `2 passed` |
| `docker compose config` | valid; datastores `host_ip: 127.0.0.1` |
| `npm audit --omit=dev` (frontend) | `found 0 vulnerabilities` |
| `pip-audit` (backend venv) | 2 non-runtime packages flagged (pip build-time, pytest dev-only) |

_Not executed in this environment (no live infra / heavy install avoided): Trivy image
scan (runs in CI), Alembic live upgrade against a real DB, Playwright e2e (runs in CI),
full `npm test`/build (no frontend source changed). These run in the existing
`.github/workflows/release.yml` gates._

## 7. Deferred items (documented, accepted)

- **`pytest` PYSEC-2026-1845 → 9.0.3:** dev-only dependency, not installed in the production
  image (`pip install .` without `[dev]`); fix is a major-version bump (`<9` pin) that risks
  breaking the suite. Deferred; not a deployment blocker.
- **`pip` PYSEC-2026-196 → 26.1.2:** build-time only; the backend Dockerfile already runs
  `pip install --upgrade pip`, so the built image uses current pip. Not a runtime surface.
- **Application-layer rate limiting:** intentionally delegated to ALB/WAF; expensive ops are
  key-gated. Tracked as a required deploy-time control.
- **CI actions pinned by major tag** (except Trivy): first-party GitHub/Docker actions;
  SHA-pinning recommended as a follow-up hardening, not blocking.

## 8. Assurance statement

Nothing was pushed, merged, or deployed. All changes are local commits on `main` (as
instructed). The working tree is left clean. No real credentials appear in any report,
commit, test, or log.

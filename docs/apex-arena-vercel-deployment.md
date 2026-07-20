<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Apex Arena Frontend — Standalone Vercel Project

This document describes how to deploy the Apex Arena **frontend** (`frontend/`) as its own
Vercel project, serving as a private origin behind the portfolio site at
`https://chaitanyasingh.org/apex-arena`.

Companion document: [`portfolio-vercel-integration.md`](./portfolio-vercel-integration.md)
(the rewrite layer that lives in the separate portfolio repository).

## Architecture recap

```
browser
  └─ https://chaitanyasingh.org/apex-arena/...
       └─ Portfolio Vercel project (owns the public domain)
            └─ middleware rewrite  ──►  Apex Arena Vercel project (origin only)
                                          └─ /apex-arena/api/*  ──►  FastAPI on Railway
```

Three rules the whole design depends on:

1. The browser only ever talks to `chaitanyasingh.org`. It never learns the Vercel or
   Railway hostnames.
2. The Apex Arena Vercel project must **not** have `chaitanyasingh.org` (or any other
   public/marketing domain) attached. It is reachable only through the portfolio rewrite.
3. Backend calls are made **server-side** by the Next.js route handler at
   `frontend/src/app/api/[[...path]]/route.ts`. The browser never issues a cross-origin
   request to Railway.

## Vercel project settings

| Setting | Value |
| --- | --- |
| Repository | `CSingh26/Apex-Arena` |
| **Root Directory** | `frontend` |
| Framework Preset | Next.js |
| Build Command | `npm run build` (default; leave the override off) |
| Install Command | `npm ci` (default) |
| Output Directory | leave empty — the Next.js preset handles it |
| Node.js Version | 22.x (`@types/node` ^26 and Next 16 both target modern Node; do not pin below 20.x) |
| Production Branch | `deployment/low-cost-production` |

Next.js version in use: **16.2.x** (`next: ^16.2.10`, React 19).

### `output: "standalone"`

`frontend/next.config.ts` sets `output: "standalone"`. That setting exists for the
container/Railway/Cloudflare deployment paths. On Vercel it is harmless — the Next.js
builder ignores it and produces its own function bundles — so **leave it as-is**. Do not
remove it just because you are deploying to Vercel; the other targets still need it.

## basePath implications

`next.config.ts` derives `basePath` from `NEXT_PUBLIC_APP_BASE_PATH` at **build time**:

```ts
const basePath = normalizeBasePath(process.env.NEXT_PUBLIC_APP_BASE_PATH);
```

With `NEXT_PUBLIC_APP_BASE_PATH=/apex-arena`, the built app behaves as follows:

* Every page lives under `/apex-arena/...` **on the Apex Arena origin too**. Hitting the
  bare Vercel URL at `/` returns a 404; the app only answers at
  `https://<apex-arena>.vercel.app/apex-arena`. This is expected and is precisely why the
  portfolio middleware must rewrite while **preserving** the full pathname (see the
  companion doc).
* Static assets are served from `/apex-arena/_next/...`, so the portfolio matcher must
  cover `/apex-arena/:path*` — not just the page routes.
* `Link`/`router` destinations get the prefix automatically. Browser-native URLs
  (`fetch`, `EventSource`, `<img src>`) must go through the helpers in
  `frontend/src/lib/app-paths.ts` — `withBasePath()`, `apiPath()`, `publicAssetPath()`.
* The permanent redirects declared in `next.config.ts` (`/race-rooms` → `/rooms`,
  `/race-rooms/:slug` → `/rooms/:slug`) emit a `Location` of `/apex-arena/rooms...`.
  Because that is a **path-relative** `Location`, the browser resolves it against
  `chaitanyasingh.org` and the public URL is preserved. No change needed.

Because `basePath` is baked in at build time, **changing `NEXT_PUBLIC_APP_BASE_PATH`
requires a rebuild**, not just a redeploy of the same artifact.

## Environment variables

Set these in **Project Settings → Environment Variables** on the Apex Arena project.
Apply to Production and Preview (Preview may point at a staging backend).

```
PUBLIC_APP_URL=https://chaitanyasingh.org/apex-arena
NEXT_PUBLIC_APP_URL=https://chaitanyasingh.org/apex-arena
NEXT_PUBLIC_APP_BASE_PATH=/apex-arena
NEXT_PUBLIC_API_BASE_PATH=/apex-arena/api
BACKEND_PUBLIC_ORIGIN=https://<railway-backend-host>
APEX_ARENA_BACKEND_PROXY_TOKEN=<server-side-only-token>
```

Notes on each:

* **`PUBLIC_APP_URL`** — the canonical public URL. Note that
  `frontend/src/app/layout.tsx` currently reads **`NEXT_PUBLIC_APP_URL`** for
  `metadataBase` (and `alternates.canonical: "."`). Set both, with the same value, or the
  canonical tag and Open Graph URLs will fall back to `http://localhost:3000`.
  `publicUrl()` in `frontend/src/lib/app-paths.ts` is the helper for building canonical,
  Open Graph and share URLs: it reads `NEXT_PUBLIC_APP_URL`, applies the base path without
  doubling it when the value already contains one, and never emits a trailing slash for the
  root.
* **`NEXT_PUBLIC_APP_BASE_PATH`** — build-time; drives `basePath` and `APP_BASE_PATH`.
* **`NEXT_PUBLIC_API_BASE_PATH`** — honoured by the code. `app-paths.ts` exports
  `API_BASE_PATH`, which uses `NEXT_PUBLIC_API_BASE_PATH` when set and otherwise derives
  `` `${APP_BASE_PATH}/api` ``; `apiPath()` composes every browser-facing API URL from it.
  Set it only when the API mount point must diverge from the app base path — otherwise
  leaving it unset keeps the two in step automatically.
* **`BACKEND_PUBLIC_ORIGIN`** — the Railway FastAPI origin. **Server-side only.** Read
  directly by the route handler; no workaround variable is needed.
* **`APEX_ARENA_BACKEND_PROXY_TOKEN`** — shared secret for authenticating the Next.js
  server → FastAPI hop. **Server-side only.** It must hold the **same value** as
  `APEX_ARENA_PROXY_TOKEN` on the backend — see below.

### Backend origin resolution

`frontend/src/app/api/[[...path]]/route.ts` resolves the upstream origin in this order:

```
BACKEND_INTERNAL_URL
  ?? BACKEND_PUBLIC_ORIGIN
  ?? INTERNAL_BACKEND_URL     // legacy
  ?? BACKEND_URL              // legacy
  ?? http://localhost:8000    // non-production only
```

* `BACKEND_INTERNAL_URL` is first — use it when the frontend can reach the backend over
  Railway private networking. `BACKEND_PUBLIC_ORIGIN` is the correct choice when the
  frontend is a separate Vercel project, which is the case here.
* `INTERNAL_BACKEND_URL` and `BACKEND_URL` remain accepted as **legacy fallbacks** so
  existing local and Docker Compose setups keep working unchanged. New deployments do not
  need them.
* The `localhost:8000` default applies **only** outside production. In production with none
  of the four variables set, the handler fails closed and returns **HTTP 503**
  (`{"detail": "Apex Arena backend origin is not configured"}`) rather than silently
  proxying to localhost.

### Proxy token — enforced end to end

The shared token is enforced, and the two sides use **different variable names for the same
secret**. This is the easiest thing to get wrong:

| Side | Variable | Set on |
| --- | --- | --- |
| Frontend (Next.js) | `APEX_ARENA_BACKEND_PROXY_TOKEN` | Apex Arena Vercel project |
| Backend (FastAPI) | `APEX_ARENA_PROXY_TOKEN` | Railway service |

**Both must hold the same value.** A mismatch produces a blanket 403 on every API call.

How it works:

1. The route handler **deletes** any client-supplied `x-apex-proxy-token` and mints its own
   from `APEX_ARENA_BACKEND_PROXY_TOKEN`, so a caller cannot forge one.
2. `ProxyContextMiddleware` in `backend/app/api/proxy.py` compares the header against the
   configured token with a constant-time `hmac.compare_digest` check. On a missing or wrong
   token it returns **403** with `{"detail": "Direct origin access is not permitted"}`.
3. Enforcement is active only when **all** of the following hold: `APP_ENV` is `staging` or
   `production`, a token is configured, and `PROXY_ENFORCEMENT_ENABLED` is true (the
   default). Local and test environments are therefore unaffected.
4. **`/health/live` is deliberately exempt** so Railway's health checks — which hit the
   service directly rather than through the proxy — keep passing. It exposes no state.

### Backend public-host settings

The route handler sets `x-apex-public-host` and `x-apex-public-proto` (plus the matching
`x-forwarded-*` headers) so FastAPI can rebuild browser-visible URLs. The backend only
honours a forwarded host when it is explicitly trusted, so a spoofed header cannot poison
generated links. Set on the Railway service:

```
APEX_ARENA_PROXY_TOKEN=<same value as APEX_ARENA_BACKEND_PROXY_TOKEN>
PUBLIC_PROXY_HOST=chaitanyasingh.org
TRUSTED_PROXY_HOSTS=chaitanyasingh.org
```

`PUBLIC_PROXY_HOST` pins the public host outright; `TRUSTED_PROXY_HOSTS` is the allow-list
consulted when no explicit host is pinned. Set both.

### Never prefix these with `NEXT_PUBLIC_`

`BACKEND_PUBLIC_ORIGIN`, `BACKEND_INTERNAL_URL`, and `APEX_ARENA_BACKEND_PROXY_TOKEN` must
**never** be named with a `NEXT_PUBLIC_` prefix.

Next.js inlines any `NEXT_PUBLIC_*` value into the **client JavaScript bundle at build
time**. It is not a runtime lookup and it is not redactable — the literal string ends up in
a `.js` file served to every visitor, and it stays in that immutable build output forever.
A `NEXT_PUBLIC_APEX_ARENA_BACKEND_PROXY_TOKEN` would be a published secret the moment the
build finishes, and rotating it would still leave the old value in any cached bundle.

Rules:

* Secrets and internal origins: **no prefix**. Readable only in Server Components, route
  handlers, and middleware.
* If you need one of these values in the browser, that is a design error. Add a server-side
  route handler instead — the existing `/api/[[...path]]` proxy is exactly this pattern.
* After any change, confirm with a grep over the deployed bundle:
  `curl -s https://chaitanyasingh.org/apex-arena/_next/static/chunks/*.js | grep -i railway`
  should return nothing.

### Env var changes require a new deployment

Vercel snapshots environment variables into the deployment at build time. Editing a value
in the dashboard does **not** affect any deployment that already exists — not even
server-side (non-`NEXT_PUBLIC_`) values, because Vercel binds them per-deployment.

After changing any variable:

1. Save it in Project Settings → Environment Variables.
2. Trigger a **new deployment** — Deployments → ⋯ → Redeploy (uncheck "Use existing Build
   Cache" when the change affects `NEXT_PUBLIC_*`, since those are compiled in), or push a
   commit, or `vercel --prod`.
3. Promote it if you deployed to Preview.

Rotating `APEX_ARENA_BACKEND_PROXY_TOKEN` therefore means: update it on the Apex Arena
project, update `APEX_ARENA_PROXY_TOKEN` on the Railway backend to the same value, and
redeploy **both**. (The portfolio → Apex Arena hop has its own token, also named
`APEX_ARENA_PROXY_TOKEN` but set on the portfolio project; rotate it separately.)
Plan for a brief window where the two disagree, or accept both old and new tokens on the
receiving side during the rollover.

## SSE on Vercel — a real limitation

The room stream is Server-Sent Events. `backend/app/api/room_routes.py` exposes
`GET /api/v1/race-rooms/{slug}/stream` as a `StreamingResponse` with
`media_type="text/event-stream"`, and the browser connects via `EventSource` in
`frontend/src/components/race-rooms/room-experience.tsx`. That connection traverses:

```
EventSource ─► portfolio middleware ─► Apex Arena Vercel function ─► Railway FastAPI
```

The middle hop is a Vercel Function, and **Vercel Functions have a maximum duration**.
This is a hard platform ceiling, not a tunable in this repository:

| Plan | Default max duration | Configurable ceiling |
| --- | --- | --- |
| Hobby | 60s (Fluid Compute) | up to ~300s |
| Pro | 60s default | up to ~800s (Fluid Compute) |

Be honest about what this means: **an Apex Arena SSE connection cannot stay open
indefinitely on Vercel.** On Hobby, expect the stream to be cut roughly every minute
unless the duration is raised; even at the Pro ceiling the connection is terminated after
a few minutes. A live race lasting 90+ minutes will be dozens of forced reconnects. This is
the single biggest functional compromise of hosting the frontend proxy on Vercel.

### Mitigation: resumable reconnect (already implemented)

The stack is already built for a lossy transport, so a truncated connection degrades to a
gap-free resume rather than lost events:

* **Backend accepts a resume cursor two ways** —
  `?after_sequence=<n>` (query) and the standard `Last-Event-ID` request header. It takes
  `max(after_sequence, Last-Event-ID)` as the recovery point, so it replays from the last
  event the client actually saw.
* **The proxy forwards it.** The route handler copies all inbound request headers to the
  upstream fetch, so `Last-Event-ID` (which browsers send automatically on native
  `EventSource` reconnect) reaches FastAPI intact.
* **The client reconnects on its own.** `room-experience.tsx` closes and re-opens the
  `EventSource` on error with capped exponential backoff
  (`min(8000, 750 * 2 ** min(attempt, 4))` ms), passing the last sequence it processed via
  `roomStreamUrl(slug, lastSequenceRef.current)`. UI state moves
  `connecting → live → reconnecting → degraded` (after 3 failed attempts) rather than
  breaking.
* **Backlog depth bounds the recovery window.** `ROOM_STREAM_BACKLOG_LIMIT` (default 250)
  caps how far back the server can replay. If a reconnect is slower than the event rate
  drains that buffer, events are dropped. Keep backoff short and consider raising the
  backlog for live sessions.

Practical guidance:

* Set a `maxDuration` on the streaming path (in `vercel.json` or the route segment config)
  to the highest value your plan allows — fewer, longer sessions beat frequent churn.
* Keep `SSE_HEARTBEAT_SECONDS` (default 15) below any idle timeout so intermediaries do
  not drop an idle-looking stream.
* Do **not** enable response buffering or compression anywhere on the path. The backend
  already sends `Cache-Control: no-cache, no-transform`; preserve it.
* If forced reconnects prove unacceptable in production, the fallback is to move the SSE
  path off Vercel — e.g. let the browser reach a dedicated streaming host — which conflicts
  with the "browser only sees chaitanyasingh.org" requirement and would need a
  same-origin subpath on the domain instead.

## Domain configuration — deliberately none

**Do not attach `chaitanyasingh.org`, `www.chaitanyasingh.org`, or any custom domain to
the Apex Arena project.** The domain belongs exclusively to the portfolio project; a domain
can only be assigned to one Vercel project, and attaching it here would break the portfolio.

The Apex Arena project should be reachable **only** as an origin:

* Its address is the Vercel-assigned deployment/project URL
  (`https://<project>-<hash>.vercel.app`). That is what
  `APEX_ARENA_ORIGIN` on the portfolio project points at.
* Prefer the **stable project alias** (not a per-deployment URL) for Production, so
  promoting a new deployment does not require editing the portfolio env var.
* Do not link it from anywhere. Do not submit it to search engines. If you want to
  discourage indexing of the origin directly, serve `X-Robots-Tag: noindex` when the
  request lacks the expected proxy headers.
* Vercel Deployment Protection (password/SSO) is **not** a usable lock here — it would
  challenge the portfolio's server-side rewrite too. The shared proxy token serves that
  purpose instead, and it is enforced by the backend (see above).

## Deployment checklist

1. Create the Vercel project from the Apex Arena repo, Root Directory `frontend`.
2. Set Node 22.x and the environment variables above (Production + Preview).
3. Confirm **no custom domain** is attached.
4. Deploy; verify `https://<project>.vercel.app/apex-arena` renders and
   `https://<project>.vercel.app/` returns 404 (correct, given `basePath`).
5. Verify `https://<project>.vercel.app/apex-arena/api/health` proxies to FastAPI.
6. Confirm the token pairing: `APEX_ARENA_BACKEND_PROXY_TOKEN` (Vercel) and
   `APEX_ARENA_PROXY_TOKEN` (Railway) hold the same value. A direct, tokenless call to the
   Railway host must return 403 `Direct origin access is not permitted`, while
   `/health/live` must still return 200.
7. Grep the client bundle for the backend host and the token — both must be absent.
8. Hand the project URL to the portfolio repo as `APEX_ARENA_ORIGIN` and follow
   [`portfolio-vercel-integration.md`](./portfolio-vercel-integration.md).

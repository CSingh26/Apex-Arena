<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Portfolio ↔ Apex Arena Integration Guide

> **This file documents changes to be made in a DIFFERENT repository.**
> Everything below applies to the portfolio repo
> (`https://github.com/CSingh26/portfolio`), which is a separate codebase and a separate
> Vercel project. **Nothing in the portfolio repository has been modified.** No file was
> created, edited, or committed there. This is a future implementation guide only — apply
> it yourself, in that repo, when you are ready.
>
> The only files written by this work live in the Apex Arena repo under `docs/`.

Companion document: [`apex-arena-vercel-deployment.md`](./apex-arena-vercel-deployment.md)
(how the Apex Arena origin project is configured).

## Goal

Serve Apex Arena at `https://chaitanyasingh.org/apex-arena` while:

* the browser's address bar **always** shows `chaitanyasingh.org/apex-arena/...`,
* the Apex Arena Vercel origin and the Railway backend hostnames are never exposed,
* the proxy token never reaches client JavaScript.

```
browser ──► chaitanyasingh.org/apex-arena/...
              │  portfolio Vercel project (owns the domain)
              │  middleware.ts: NextResponse.rewrite(...)
              ▼
            <apex-arena>.vercel.app/apex-arena/...   (origin only, no public domain)
              │  Next.js route handler /apex-arena/api/*
              ▼
            FastAPI on Railway
```

## Why `rewrite`, never `redirect`

`NextResponse.redirect(url)` returns a **3xx** response with a `Location` header. The
browser then navigates to that URL: the address bar changes to the Vercel hostname, the
user can bookmark it, search engines index it, and the whole "one public domain" property
collapses. It also breaks relative asset resolution back onto the wrong origin.

`NextResponse.rewrite(url)` performs the fetch **server-side inside Vercel's edge network**
and returns the upstream response body under the original URL. Status stays 200. The
browser never learns the destination — there is no `Location` header and no navigation.
The address bar, `document.location`, `history`, canonical URLs, and refresh behaviour all
continue to reference `chaitanyasingh.org/apex-arena/...`.

**`rewrite` is mandatory here.** A redirect anywhere on this path is a bug.

One corollary: because the rewrite is transparent, the Apex Arena app must be built with
`basePath: "/apex-arena"` and the rewrite must **preserve the full pathname including the
`/apex-arena` prefix**. Do not strip it. The origin's routes literally live under
`/apex-arena/...` (that is what `NEXT_PUBLIC_APP_BASE_PATH=/apex-arena` produces), so
stripping the prefix yields 404s and, worse, `_next/static` asset misses.

## `middleware.ts`

Place this at the **repository root** of the portfolio project (next to `app/`), or inside
`src/` if the portfolio uses a `src/` directory. Next.js App Router, Vercel.

```ts
// middleware.ts — portfolio repository (NOT Apex Arena)
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Routes /apex-arena and /apex-arena/* to the Apex Arena Vercel origin.
 *
 * Rewrite (not redirect) so the browser keeps showing chaitanyasingh.org.
 * Both env vars are server-side only: middleware runs on Vercel's
 * infrastructure and its process.env is never serialized to the client.
 */
export function middleware(request: NextRequest) {
  const origin = process.env.APEX_ARENA_ORIGIN;
  const token = process.env.APEX_ARENA_PROXY_TOKEN;

  if (!origin || !token) {
    return new NextResponse(
      "Apex Arena is temporarily unavailable.",
      {
        status: 503,
        headers: {
          "content-type": "text/plain; charset=utf-8",
          "cache-control": "no-store",
        },
      },
    );
  }

  const incoming = request.nextUrl;

  // Preserve the full pathname (including the /apex-arena prefix, which the
  // Apex Arena app expects because it is built with basePath=/apex-arena)
  // and the entire query string.
  const target = new URL(origin);
  target.pathname = incoming.pathname;
  target.search = incoming.search;

  const headers = new Headers(request.headers);
  headers.set("x-apex-proxy-token", token);
  headers.set("x-apex-public-host", incoming.host);
  headers.set("x-apex-public-proto", incoming.protocol.replace(":", ""));
  headers.set("x-apex-original-path", incoming.pathname);

  return NextResponse.rewrite(target, {
    request: { headers },
  });
}

export const config = {
  matcher: ["/apex-arena", "/apex-arena/:path*"],
};
```

### Why it is written this way

* **`config.matcher` lists both patterns.** `/apex-arena/:path*` does not match the bare
  `/apex-arena` in Next.js matcher syntax, so the exact path is listed separately.
  Otherwise the landing page 404s while every subpage works.
* **`/apex-arena/:path*` also covers assets.** `_next/static`, `_next/image`, fonts,
  favicons, and the `/apex-arena/api/*` proxy routes all live under the prefix, so one
  matcher handles pages, assets, and API in a single rule.
* **Matcher strings must be static literals.** Next.js parses `config.matcher` at build
  time; it cannot contain variables or template interpolation.
* **`new URL(origin)` then assigning `pathname`/`search`** avoids string concatenation
  bugs (double slashes, a trailing slash on `APEX_ARENA_ORIGIN`, or a lost `?query`).
  `incoming.search` carries the full query string, `""` when absent.
* **`NextResponse.rewrite(target, { request: { headers } })`** is the form that forwards
  *request* headers upstream. Passing a plain `{ headers }` object at the top level sets
  *response* headers instead — a common and silent mistake that would leave the origin
  without the token.
* **The token never reaches the client.** It is read inside middleware, attached to an
  outbound request header, and the response body is streamed back unchanged. It is not in
  any bundle, not in any HTML payload, and not in any header the browser can read (the
  request headers set here are consumed by the origin, not echoed to the client). Confirm
  by checking DevTools → Network: the client-visible request to
  `chaitanyasingh.org/apex-arena` carries no `x-apex-*` headers.
* **`APEX_ARENA_ORIGIN` and `APEX_ARENA_PROXY_TOKEN` have no `NEXT_PUBLIC_` prefix — keep
  it that way.** A `NEXT_PUBLIC_*` variable is inlined verbatim into the client JavaScript
  bundle at build time; naming the token that way publishes it permanently to every
  visitor and to every cached copy of that build.

### Forwarded headers

| Header | Value | Purpose |
| --- | --- | --- |
| `x-apex-proxy-token` | `APEX_ARENA_PROXY_TOKEN` | Proves the request came through the portfolio, not directly to the Vercel origin. |
| `x-apex-public-host` | `chaitanyasingh.org` | Lets the origin build absolute URLs against the public domain. |
| `x-apex-public-proto` | `https` | Same, for scheme. |
| `x-apex-original-path` | e.g. `/apex-arena/rooms/spa-2026` | The public path as the user sees it, for canonical URLs and logging. |

Note on current Apex Arena behaviour: the route handler at
`frontend/src/app/api/[[...path]]/route.ts` **deletes** any inbound `x-apex-proxy-token`
and mints a fresh one from its own `APEX_ARENA_BACKEND_PROXY_TOKEN` for the FastAPI hop, so
a client cannot forge the header. `ProxyContextMiddleware` in `backend/app/api/proxy.py`
then validates it with a constant-time `hmac.compare_digest` check and returns **403**
`{"detail": "Direct origin access is not permitted"}` when it is missing or wrong.
Enforcement is active only when `APP_ENV` is `staging` or `production`, a token is
configured, and `PROXY_ENFORCEMENT_ENABLED` is true; `/health/live` is deliberately exempt
so Railway health checks still pass.

The two hops carry **separate** tokens. The portfolio's `APEX_ARENA_PROXY_TOKEN` guards the
portfolio → Apex Arena hop; the Apex Arena → FastAPI hop uses
`APEX_ARENA_BACKEND_PROXY_TOKEN` on the Vercel project, which must match
`APEX_ARENA_PROXY_TOKEN` on the **Railway backend**. Rotating one does not rotate the other.

The route handler also forwards `x-apex-public-host` / `x-apex-public-proto` onward. The
backend only trusts a forwarded host that appears in `TRUSTED_PROXY_HOSTS` (or ignores it
entirely when `PUBLIC_PROXY_HOST` is pinned), so set `PUBLIC_PROXY_HOST=chaitanyasingh.org`
and `TRUSTED_PROXY_HOSTS=chaitanyasingh.org` on the backend or generated links fall back to
the Railway host.

### Interaction with the portfolio's own middleware

If the portfolio already has a `middleware.ts` (analytics, i18n, auth, redirects):

* There can only be **one** `middleware.ts` per project. Merge, do not add a second file.
* Merge the matchers into one array; keep `/apex-arena` and `/apex-arena/:path*` in it.
* Handle the Apex Arena branch **first** and `return` immediately. Do not let the
  portfolio's own locale/trailing-slash/auth logic rewrite or redirect these paths — any
  redirect on this prefix defeats URL preservation.
* Make sure no `next.config.js` `redirects()` entry in the portfolio matches
  `/apex-arena*`. Config redirects run before middleware and would win.

## Portfolio Vercel environment variables

Set on the **portfolio** Vercel project, Settings → Environment Variables.

| Variable | Environment | Value |
| --- | --- | --- |
| `APEX_ARENA_ORIGIN` | Production | `https://<apex-arena-production-alias>.vercel.app` |
| `APEX_ARENA_PROXY_TOKEN` | Production | `<production-token-placeholder>` |
| `APEX_ARENA_ORIGIN` | Preview | `https://<apex-arena-preview-alias>.vercel.app` |
| `APEX_ARENA_PROXY_TOKEN` | Preview | `<preview-token-placeholder>` |

Requirements:

* **Separate values per environment.** Preview must point at the Apex Arena *preview*
  deployment and use a *different* token. Sharing one token means a leak from a preview
  build compromises production, and sharing one origin means preview traffic hits the live
  backend and its data.
* **No `NEXT_PUBLIC_` prefix.** Ever. See above.
* **Origin format:** scheme + host only, **no trailing slash**, no path. `https://x.vercel.app`,
  not `https://x.vercel.app/` and not `https://x.vercel.app/apex-arena`. The middleware
  supplies the pathname.
* **Prefer the stable project alias** over a per-deployment URL, so promoting a new Apex
  Arena deployment does not require editing the portfolio.
* **Do not add these to Development** unless you are running the Apex Arena app locally;
  local dev will otherwise proxy to a remote origin unexpectedly.

### Changes require a redeploy

Vercel binds environment variables to a deployment at build time. Editing a value in the
dashboard has **no effect on the currently live deployment**, including for middleware and
other server-side code. After any change to `APEX_ARENA_ORIGIN` or
`APEX_ARENA_PROXY_TOKEN`:

1. Save the value.
2. Deployments → ⋯ → **Redeploy** (or push a commit, or `vercel --prod`).
3. Verify against the new deployment, not a cached tab.

Token rotation touches two projects. Change it on the Apex Arena project *and* the
portfolio project, then redeploy **both**. Either accept both old and new tokens on the
receiving side during the rollover, or schedule a short window where mismatches are
expected.

## Validation checklist

Run against production after the portfolio redeploy. Every URL below must remain on
`chaitanyasingh.org` in the address bar throughout.

| # | Check | Expected |
| --- | --- | --- |
| 1 | **Home page** — visit `https://chaitanyasingh.org/apex-arena` | Apex Arena landing page renders, 200, URL unchanged, no flash of a Vercel hostname |
| 2 | **Rooms page** — click through to `/apex-arena/rooms` | Room list renders, client-side navigation, URL is `chaitanyasingh.org/apex-arena/rooms` |
| 3 | **Room deep link** — open `/apex-arena/rooms/<slug>` directly in a new tab | Room loads from a cold server render, 200, not a 404 |
| 4 | **Legacy redirect** — open `/apex-arena/race-rooms` | 308 to `/apex-arena/rooms` **on chaitanyasingh.org**, never to a `.vercel.app` host |
| 5 | **Static assets** — DevTools → Network, filter `_next` | All `/apex-arena/_next/static/...` requests are 200 and same-origin; zero requests to any `.vercel.app` or Railway host |
| 6 | **Browser refresh on a deep link** — hard-reload (Cmd-Shift-R) on `/apex-arena/rooms/<slug>` | Page re-renders correctly; URL unchanged; no 404 and no redirect |
| 7 | **Back/forward** — navigate a few pages, then use browser back and forward | History entries all under `chaitanyasingh.org/apex-arena`; no bounce to an origin URL |
| 8 | **API calls** — `curl -i https://chaitanyasingh.org/apex-arena/api/health` | 200 JSON from FastAPI; also confirm `/apex-arena/api/rooms` returns the room list |
| 9 | **SSE stream** — `curl -N -i https://chaitanyasingh.org/apex-arena/api/rooms/<slug>/stream` | `content-type: text/event-stream`, `cache-control: no-cache, no-transform`, events arrive incrementally (not one buffered blob), heartbeats every ~15s |
| 10 | **SSE resume** — open a room, watch the connection badge across a forced disconnect | Badge goes `live → reconnecting → live`; no duplicated or skipped events (backend resumes from `Last-Event-ID` / `after_sequence`). Expect a reconnect at the Vercel function duration ceiling — see the SSE section of the deployment doc |
| 11 | **Canonical URL** — view source, find `<link rel="canonical">` and `og:url` | Both resolve to `https://chaitanyasingh.org/apex-arena/...`, never localhost and never a `.vercel.app` host, and no trailing slash on the root. Built by `publicUrl()` in `frontend/src/lib/app-paths.ts` from `NEXT_PUBLIC_APP_URL`, which must be set on the Apex Arena project |
| 12 | **Favicon** — check the tab icon and the `/apex-arena/favicon.ico` (or `/apex-arena/icon`) request | 200, correct icon; confirm the portfolio's own favicon still loads at `/` |
| 13 | **Light/dark mode** — toggle the OS/system theme, and reload | Apex Arena honours the theme both on first paint and after reload; no flash of the wrong theme; confirm it does not clash with the portfolio's theme handling |
| 14 | **Mobile navigation** — DevTools device emulation (375px) plus a real phone | Nav/menu opens and closes, room list is scrollable, no horizontal overflow, touch targets work, SSE still connects on cellular |
| 15 | **Origin isolation** — open the raw `https://<apex-arena>.vercel.app/apex-arena` | Reachable but unlisted; nothing links to it. The Railway backend is authenticated: a direct, tokenless call to it returns 403 `Direct origin access is not permitted`, while `/health/live` still returns 200 |
| 16 | **Secret leakage** — `curl -s https://chaitanyasingh.org/apex-arena/_next/static/chunks/*.js \| grep -iE 'railway\|vercel.app\|<token-prefix>'` | No matches. Also check the HTML for inlined env values |
| 17 | **Portfolio unaffected** — visit `/`, and a few existing portfolio routes | Unchanged behaviour; the new matcher touches only `/apex-arena*` |

## Known limitations to accept going in

* **SSE duration.** Vercel Functions have a maximum duration (60s default; up to ~300s on
  Hobby, ~800s on Pro). Long-lived race streams **will** be cut and reconnected. The client
  auto-reconnects with backoff and the backend replays from the last sequence, so this is
  degradation rather than breakage — but it is real, and it is worse on Hobby.
* **Two rewrite hops.** Portfolio → Apex Arena → Railway adds latency to every API call and
  doubles the number of function invocations counted against plan limits.
* **Build-time `basePath` coupling.** `/apex-arena` is compiled into the Apex Arena build.
  Changing the public path means changing `NEXT_PUBLIC_APP_BASE_PATH`, rebuilding Apex
  Arena, and updating the portfolio matcher — in that order.
* **Paired token values.** The Apex Arena → FastAPI token is enforced, but its two halves
  are named differently: `APEX_ARENA_BACKEND_PROXY_TOKEN` on the Vercel project and
  `APEX_ARENA_PROXY_TOKEN` on the Railway backend. They must hold the same value, and a
  mismatch means a blanket 403 on every API call.

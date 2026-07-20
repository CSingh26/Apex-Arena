// SPDX-License-Identifier: AGPL-3.0-only
import type { NextRequest } from "next/server";

import { backendPath } from "@/lib/backend-proxy";

export const dynamic = "force-dynamic";
// Streaming responses (SSE) must never be buffered or collapsed by the runtime.
export const fetchCache = "force-no-store";

type RouteContext = { params: Promise<{ path?: string[] }> };

/**
 * Resolve the FastAPI origin.
 *
 * Railway private networking is preferred when the frontend runs alongside the
 * backend; the public origin is the fallback when the frontend is a separate
 * Vercel project. Neither value may carry a NEXT_PUBLIC_ prefix — the backend
 * origin must never reach the browser bundle.
 */
function resolveBackendOrigin(): string | null {
  const configured =
    process.env.BACKEND_INTERNAL_URL
    ?? process.env.BACKEND_PUBLIC_ORIGIN
    // Retained so existing local/compose setups keep working unchanged.
    ?? process.env.INTERNAL_BACKEND_URL
    ?? process.env.BACKEND_URL
    ?? (process.env.NODE_ENV === "production" ? null : "http://localhost:8000");
  return configured ? configured.replace(/\/$/, "") : null;
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const backendOrigin = resolveBackendOrigin();
  if (!backendOrigin) {
    // Fail closed rather than silently proxying to localhost in production.
    return Response.json(
      { detail: "Apex Arena backend origin is not configured" },
      { status: 503 },
    );
  }

  const { path = [] } = await context.params;
  const incoming = new URL(request.url);
  const upstream = new URL(`${backendOrigin}${backendPath(path)}`);
  upstream.search = incoming.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  // Drop any inbound token and mint our own so a client cannot forge one.
  headers.delete("x-apex-proxy-token");

  const backendToken = process.env.APEX_ARENA_BACKEND_PROXY_TOKEN;
  if (backendToken) {
    headers.set("x-apex-proxy-token", backendToken);
  }

  // Preserve the browser-visible origin so the backend can rebuild public URLs.
  const publicHost = request.headers.get("x-apex-public-host") ?? incoming.host;
  const publicProto =
    request.headers.get("x-apex-public-proto") ?? incoming.protocol.replace(":", "");
  headers.set("x-apex-public-host", publicHost);
  headers.set("x-apex-public-proto", publicProto);
  headers.set("x-forwarded-host", publicHost);
  headers.set("x-forwarded-proto", publicProto);

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();

  const response = await fetch(upstream, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
    redirect: "manual",
  });

  const responseHeaders = new Headers(response.headers);

  responseHeaders.delete("content-length");
  responseHeaders.delete("content-encoding");

  const contentType = responseHeaders.get("content-type") ?? "";

  if (contentType.includes("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  }

  const responseBody = await response.arrayBuffer();

  return new Response(responseBody, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;

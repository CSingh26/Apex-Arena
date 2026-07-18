// SPDX-License-Identifier: AGPL-3.0-only
import type { NextRequest } from "next/server";

import { backendPath } from "@/lib/backend-proxy";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path?: string[] }> };

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path = [] } = await context.params;
  const backendOrigin = (
    process.env.INTERNAL_BACKEND_URL
    ?? process.env.BACKEND_URL
    ?? "http://localhost:8000"
  ).replace(/\/$/, "");
  const incoming = new URL(request.url);
  const upstream = new URL(`${backendOrigin}${backendPath(path)}`);
  upstream.search = incoming.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("x-apex-proxy-token");
  headers.set("x-forwarded-host", incoming.host);
  headers.set("x-forwarded-proto", incoming.protocol.replace(":", ""));

  const body = request.method === "GET" || request.method === "HEAD"
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
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;

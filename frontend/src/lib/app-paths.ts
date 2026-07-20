// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Build-time application mount point. Next.js applies this automatically to
 * Link/router destinations; browser-native URLs still need `withBasePath`.
 */
export const APP_BASE_PATH = normalizeBasePath(process.env.NEXT_PUBLIC_APP_BASE_PATH);

export const appRoutes = {
  home: "/",
  rooms: "/rooms",
  room: (slug: string) => `/rooms/${encodeURIComponent(slug)}`,
} as const;

export function normalizeBasePath(value: string | undefined): string {
  const trimmed = value?.trim();
  if (!trimmed || trimmed === "/") return "";
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}`;
}

export function withBasePath(path: string): string {
  if (/^(?:[a-z][a-z\d+.-]*:)?\/\//i.test(path)) return path;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (!APP_BASE_PATH || normalizedPath === APP_BASE_PATH || normalizedPath.startsWith(`${APP_BASE_PATH}/`)) {
    return normalizedPath;
  }
  return `${APP_BASE_PATH}${normalizedPath}`;
}

/**
 * Browser-facing API mount point. Defaults to `<base path>/api` so a single
 * NEXT_PUBLIC_APP_BASE_PATH keeps routes and API calls in step; an explicit
 * NEXT_PUBLIC_API_BASE_PATH overrides it when the two must diverge.
 */
export const API_BASE_PATH = normalizeBasePath(process.env.NEXT_PUBLIC_API_BASE_PATH)
  || `${APP_BASE_PATH}/api`;

export function apiPath(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_PATH}${normalizedPath}`;
}

/**
 * Absolute, browser-visible URL. Used for canonical tags, Open Graph metadata
 * and share links, which must always resolve to the public domain rather than
 * an internal Vercel or Railway origin.
 */
export function publicUrl(path = "/"): string {
  const origin = (process.env.NEXT_PUBLIC_APP_URL ?? "").trim().replace(/\/+$/, "");
  // Canonical URLs must be stable, so never emit a trailing slash for the root.
  const relative = withBasePath(path).replace(/(.)\/$/, "$1");
  if (!origin) return relative;
  // NEXT_PUBLIC_APP_URL may already include the base path; avoid doubling it.
  const base = APP_BASE_PATH && origin.endsWith(APP_BASE_PATH)
    ? origin.slice(0, -APP_BASE_PATH.length)
    : origin;
  return `${base}${relative}`;
}

export function publicAssetPath(path: string): string {
  return withBasePath(path);
}

export function stripBasePath(pathname: string): string {
  if (!APP_BASE_PATH) return pathname || "/";
  if (pathname === APP_BASE_PATH) return "/";
  return pathname.startsWith(`${APP_BASE_PATH}/`)
    ? pathname.slice(APP_BASE_PATH.length)
    : pathname;
}

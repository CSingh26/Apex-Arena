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

export function apiPath(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return withBasePath(`/api${normalizedPath}`);
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

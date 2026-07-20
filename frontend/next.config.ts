// SPDX-License-Identifier: AGPL-3.0-only
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import type { NextConfig } from "next";
import { parse } from "dotenv";

const rootEnvPath = path.resolve(process.cwd(), "../.env");
const publicKeys = ["NEXT_PUBLIC_APP_NAME", "NEXT_PUBLIC_APP_URL", "NEXT_PUBLIC_APP_BASE_PATH"];

if (existsSync(rootEnvPath)) {
  const rootEnv = parse(readFileSync(rootEnvPath));
  for (const key of publicKeys) {
    if (rootEnv[key] && !process.env[key]) {
      process.env[key] = rootEnv[key];
    }
  }
}

function normalizeBasePath(value: string | undefined): string {
  const trimmed = value?.trim();
  if (!trimmed || trimmed === "/") return "";
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}`;
}

const basePath = normalizeBasePath(process.env.NEXT_PUBLIC_APP_BASE_PATH);

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  basePath,
  allowedDevOrigins: ["127.0.0.1"],
  async redirects() {
    return [
      {
        source: "/race-rooms",
        destination: "/rooms",
        permanent: true,
      },
      {
        source: "/race-rooms/:slug",
        destination: "/rooms/:slug",
        permanent: true,
      },
    ];
  },
};

export default nextConfig;

// SPDX-License-Identifier: AGPL-3.0-only
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import type { NextConfig } from "next";
import { parse } from "dotenv";

const rootEnvPath = path.resolve(process.cwd(), "../.env");
const publicKeys = ["NEXT_PUBLIC_APP_NAME", "NEXT_PUBLIC_APP_URL", "NEXT_PUBLIC_API_URL"];

if (existsSync(rootEnvPath)) {
  const rootEnv = parse(readFileSync(rootEnvPath));
  for (const key of publicKeys) {
    if (rootEnv[key] && !process.env[key]) {
      process.env[key] = rootEnv[key];
    }
  }
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;

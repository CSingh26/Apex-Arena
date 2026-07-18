// SPDX-License-Identifier: AGPL-3.0-only
import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Apex Arena — Formula racing, interpreted live",
  description: "A telemetry-grounded Formula racing experience where five specialist AI agents analyse every decisive moment.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}

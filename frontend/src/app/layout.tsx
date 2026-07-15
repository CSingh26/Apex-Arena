// SPDX-License-Identifier: AGPL-3.0-only
import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Apex Arena — 2026 Race Control",
  description: "Live-data foundation for the Apex Arena 2026 Formula racing fan simulation.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

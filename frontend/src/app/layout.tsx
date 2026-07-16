// SPDX-License-Identifier: AGPL-3.0-only
import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Apex Arena — Race Rooms",
  description: "Grounded AI race analysis from five distinct voices, live and on replay.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

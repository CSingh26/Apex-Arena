// SPDX-License-Identifier: AGPL-3.0-only
import type { Metadata } from "next";

import "./globals.css";

const publicAppUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(publicAppUrl),
  title: "Apex Arena — Formula racing, interpreted live",
  description: "A telemetry-grounded Formula racing experience where five specialist AI agents analyse every decisive moment.",
  alternates: { canonical: "." },
  openGraph: {
    type: "website",
    title: "Apex Arena — Formula racing, interpreted live",
    description: "Five specialist AI agents turn Formula racing data into an evidence-linked live conversation.",
    siteName: "Apex Arena",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}

import type { Metadata, Viewport } from "next";
import { connection } from "next/server";

import { Intro } from "@/components/intro";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "FraudLens", template: "%s · FraudLens" },
  description: "Real-time fraud decisions, investigation and model oversight for mobile money.",
  robots: { index: false, follow: false },
  // File-based icons live beside this layout: icon.svg, favicon.ico, apple-icon.png.
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "48x48" },
      { url: "/icon.svg", type: "image/svg+xml" },
    ],
    apple: [{ url: "/apple-icon.png", sizes: "180x180" }],
  },
  // Added to an iPhone's home screen, the console opens full screen like an app.
  appleWebApp: { capable: true, title: "FraudLens", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  themeColor: "#1b1b1b",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
  // Draw under the notch and the home indicator; the bars pad themselves with the safe areas.
  viewportFit: "cover",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Rendered per request, so every script tag carries the nonce that proxy.ts put in the CSP.
  await connection();
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full font-sans">
        <Intro />
        {children}
      </body>
    </html>
  );
}


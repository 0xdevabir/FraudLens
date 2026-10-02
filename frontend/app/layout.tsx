import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "FraudLens", template: "%s · FraudLens" },
  description: "Real-time fraud decisions, investigation and model oversight for mobile money.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#1b1b1b",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full font-sans">{children}</body>
    </html>
  );
}

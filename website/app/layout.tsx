import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const forwardedHost = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost";
  const host = /^[a-z0-9.-]+(?::\d{1,5})?$/i.test(forwardedHost) ? forwardedHost : "localhost";
  const forwardedProto = requestHeaders.get("x-forwarded-proto");
  const protocol = forwardedProto === "http" || host.startsWith("localhost") ? "http" : "https";
  const origin = `${protocol}://${host}`;
  const image = `${origin}/manuscript-social-v3.png`;
  const description = "Flawless writing powered locally, with private dictation and writing assistance on your devices.";
  return {
    title: "Manuscript — Private AI writing",
    description,
    metadataBase: new URL(origin),
    alternates: { canonical: origin },
    icons: { icon: "/manuscript-icon.png", shortcut: "/manuscript-icon.png" },
    robots: { index: true, follow: true },
    referrer: "strict-origin-when-cross-origin",
    openGraph: {
      title: "Manuscript — Flawless writing powered locally",
      description: "Private, local-first dictation and writing assistance.",
      type: "website",
      url: origin,
      images: [{ url: image, width: 1730, height: 909, alt: "Manuscript — Flawless writing powered locally" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Manuscript — Flawless writing powered locally",
      description,
      images: [image],
    },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

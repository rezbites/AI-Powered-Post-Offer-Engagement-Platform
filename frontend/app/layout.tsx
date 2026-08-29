import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";
import { ModeBadge } from "@/components/ModeBadge";

export const metadata: Metadata = {
  title: "Post-Offer Engagement",
  description:
    "Track candidates between offer acceptance and joining: engagement journey, joining risk, and recommended next actions.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div className="min-h-screen">
            <header className="border-b border-slate-200 bg-white">
              <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
                <div className="flex items-center gap-8">
                  <Link href="/" className="text-sm font-semibold text-slate-900">
                    Post-Offer Engagement
                  </Link>
                  <nav className="flex gap-5 text-sm text-slate-600">
                    <Link href="/" className="hover:text-slate-900">
                      Dashboard
                    </Link>
                    <Link href="/analytics" className="hover:text-slate-900">
                      Analytics
                    </Link>
                  </nav>
                </div>
                {/* Always visible: the mode must never be something a viewer
                    has to go looking for. */}
                <ModeBadge />
              </div>
            </header>

            <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}

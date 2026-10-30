import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "RefundPilot",
  description: "AI reads the evidence, policy decides the amount, a human approves, PayPal executes.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <header className="border-b border-gray-200 bg-white">
          <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 text-sm">
            <Link href="/" className="text-base font-semibold text-brand-dark">
              Refund<span className="text-brand">Pilot</span>
            </Link>
            <Link href="/" className="text-gray-600 hover:text-gray-900">Buyer</Link>
            <Link href="/merchant" className="text-gray-600 hover:text-gray-900">Merchant queue</Link>
            <Link href="/merchant/policy" className="text-gray-600 hover:text-gray-900">Policy simulator</Link>
            <span className="ml-auto hidden text-xs text-gray-400 sm:inline">PayPal Sandbox</span>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}

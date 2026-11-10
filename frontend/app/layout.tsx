import type { Metadata } from "next";
import ColdStartNotice from "@/components/ColdStartNotice";
import NavBar from "@/components/NavBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "RefundPilot",
  description: "AI reads the evidence, policy decides the amount, a human approves, PayPal executes.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <NavBar />
        <ColdStartNotice />
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}

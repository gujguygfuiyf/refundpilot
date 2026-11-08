"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function NavBar() {
  const pathname = usePathname();

  const isBuyerActive = pathname === "/" || pathname.startsWith("/claims");
  const isMerchantActive = pathname === "/merchant";
  const isPolicyActive = pathname.startsWith("/merchant/policy");

  const activeClass = "rounded-lg bg-blue-50 px-3 py-1.5 font-medium text-brand-dark ring-1 ring-blue-200";
  const inactiveClass = "rounded-lg px-3 py-1.5 text-gray-600 transition hover:bg-gray-100 hover:text-gray-900";

  return (
    <header className="border-b border-gray-200 bg-white">
      <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-2 px-4 py-3 text-sm sm:gap-4">
        <Link href="/" className="mr-2 text-base font-semibold text-brand-dark">
          Refund<span className="text-brand">Pilot</span>
        </Link>
        <Link
          href="/"
          className={isBuyerActive ? activeClass : inactiveClass}
        >
          1. Buyer Portal
        </Link>
        <Link
          href="/merchant"
          className={isMerchantActive ? activeClass : inactiveClass}
        >
          2. Merchant Queue
        </Link>
        <Link
          href="/merchant/policy"
          className={isPolicyActive ? activeClass : inactiveClass}
        >
          3. Policy Simulator
        </Link>
        <div className="ml-auto hidden items-center gap-2 lg:flex">
          <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">
            AI describes · Policy decides · Human approves · PayPal executes
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 ring-1 ring-emerald-200">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            PayPal Sandbox
          </span>
        </div>
      </nav>
    </header>
  );
}

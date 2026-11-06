"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { money, when } from "@/lib/format";
import { Badge, ErrorNote, Spinner } from "@/components/ui";

const QUICK_SCENARIOS = [
  {
    key: "broken",
    title: "1. Clearly broken item",
    badge: "Full refund path",
    badgeTone: "green" as const,
    desc: "Pre-fills a broken desk lamp photo and description → AI reports structured damage → Policy R1 suggests $49.00 → Merchant approves via PayPal.",
  },
  {
    key: "blurry",
    title: "2. Blurry photo",
    badge: "Uncertainty → request evidence",
    badgeTone: "amber" as const,
    desc: "Pre-fills an out-of-focus photo → AI flags unclear evidence quality → Policy R5 requests a clearer photo ($0.00 suggested).",
  },
  {
    key: "reused",
    title: "3. Suspected reused screenshot",
    badge: "Fraud guard → manual review",
    badgeTone: "violet" as const,
    desc: "Pre-fills a phone screenshot of a photo → AI flags stock/reused suspicion → Policy 1.2 routes to human review instead of recommending a payout.",
  },
];

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.orders().then(setOrders).catch((e) => setError(`Could not load orders: ${e.message}`));
  }, []);

  const sampleOrder = useMemo(() => {
    if (!orders) return null;
    return (
      orders.find((o) => o.claimable && o.product.sku === "LAMP-02") ||
      orders.find((o) => o.claimable) ||
      null
    );
  }, [orders]);

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-blue-200 bg-gradient-to-br from-blue-50/80 via-white to-indigo-50/50 p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Badge tone="blue">Quick start for judges</Badge>
              <span className="text-xs text-gray-500">No file download needed · pre-fills photo &amp; description</span>
            </div>
            <h2 className="mt-1.5 text-lg font-semibold text-gray-900">
              Try a sample scenario in one click, or inspect recorded claims
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/merchant?tab=all"
              className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-gray-800 shadow-xs ring-1 ring-gray-300 hover:bg-gray-50"
            >
              View completed &amp; hard-case claims →
            </Link>
            <Link
              href="/merchant/policy"
              className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-brand-dark shadow-xs ring-1 ring-blue-200 hover:bg-blue-50"
            >
              Batch policy replay simulator →
            </Link>
          </div>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {QUICK_SCENARIOS.map((s) => {
            const href = sampleOrder ? `/claims/new?order=${sampleOrder.id}&sample=${s.key}` : "#";
            return (
              <div
                key={s.key}
                className="flex flex-col justify-between rounded-lg border border-gray-200 bg-white p-3.5 shadow-2xs"
              >
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-gray-900">{s.title}</span>
                  </div>
                  <div className="mt-1">
                    <Badge tone={s.badgeTone}>{s.badge}</Badge>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-gray-600">{s.desc}</p>
                </div>
                <div className="mt-3 pt-2 border-t border-gray-100">
                  {sampleOrder ? (
                    <Link
                      href={href}
                      className="block w-full rounded-lg bg-brand px-3 py-1.5 text-center text-xs font-medium text-white hover:bg-brand-dark"
                    >
                      Load scenario on Order #{sampleOrder.id} →
                    </Link>
                  ) : (
                    <span className="block text-center text-xs text-gray-400">
                      {orders ? "No claimable orders left" : "Finding a claimable order…"}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <div>
        <h1 className="text-2xl font-semibold">Your orders</h1>
        <p className="mt-1 text-sm text-gray-600">
          Something wrong with an item? Report it with a photo. Each order below was paid in the PayPal sandbox,
          and each one can be claimed once, so orders you can still use are listed first.
        </p>
      </div>
      <ErrorNote error={error} />
      {!orders && !error && <p className="flex items-center gap-2 text-sm text-gray-500"><Spinner /> Loading orders…</p>}
      {orders && orders.length === 0 && (
        <p className="rounded-xl border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
          No orders yet. Run <code>python scripts/mint_orders.py</code> in the backend to create sandbox orders.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {orders && [...orders].sort((a, b) => Number(b.claimable) - Number(a.claimable) || a.id - b.id).map((o) => {
          const fullyRefunded = o.remaining_cents <= 0;
          return (
            <article key={o.id} className="flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={o.product.image_url} alt={o.product.name} className="aspect-[4/3] w-full object-cover" />
              <div className="flex flex-1 flex-col gap-2 p-4">
                <div className="flex items-start justify-between gap-2">
                  <h2 className="font-medium">{o.product.name}</h2>
                  <span className="shrink-0 font-semibold">{money(o.amount_cents, o.currency)}</span>
                </div>
                <p className="text-xs text-gray-500">
                  Order #{o.id} · {o.buyer_label} · delivered {when(o.delivered_at)}
                </p>
                <div className="flex flex-wrap gap-2 text-xs">
                  {o.refunded_cents > 0 && <Badge tone="green">{money(o.refunded_cents, o.currency)} refunded</Badge>}
                  <Badge>{money(o.remaining_cents, o.currency)} refundable</Badge>
                </div>
                <div className="mt-auto pt-3">
                  {o.open_claim_id ? (
                    <Link href={`/claims/${o.open_claim_id}`} className="block rounded-lg bg-gray-100 px-3 py-2 text-center text-sm font-medium text-gray-800 hover:bg-gray-200">
                      View open claim
                    </Link>
                  ) : fullyRefunded ? (
                    <span className="block rounded-lg bg-gray-50 px-3 py-2 text-center text-sm text-gray-400">Fully refunded</span>
                  ) : !o.claimable && o.last_claim_id ? (
                    <Link href={`/claims/${o.last_claim_id}`} className="block rounded-lg bg-gray-50 px-3 py-2 text-center text-sm text-gray-500 hover:bg-gray-100">
                      Already claimed · view claim
                    </Link>
                  ) : (
                    <Link href={`/claims/new?order=${o.id}`} className="block rounded-lg bg-brand px-3 py-2 text-center text-sm font-medium text-white hover:bg-brand-dark">
                      Report a problem
                    </Link>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

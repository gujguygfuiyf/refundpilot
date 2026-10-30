"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { money, when } from "@/lib/format";
import { Badge, ErrorNote, Spinner } from "@/components/ui";

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.orders().then(setOrders).catch((e) => setError(`Could not load orders: ${e.message}`));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Your orders</h1>
        <p className="mt-1 text-sm text-gray-600">
          Something wrong with an item? Report it with a photo. Each order below was paid in the PayPal sandbox.
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
        {orders?.map((o) => {
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

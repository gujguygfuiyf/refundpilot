"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { compressImage, money } from "@/lib/format";
import PhotoPicker from "@/components/PhotoPicker";
import { Button, Card, ErrorNote, Spinner } from "@/components/ui";

function NewClaimForm() {
  const router = useRouter();
  const orderId = Number(useSearchParams().get("order"));
  const [order, setOrder] = useState<Order | null>(null);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.orders()
      .then((all) => {
        const o = all.find((x) => x.id === orderId);
        if (!o) setError("Order not found.");
        else if (o.open_claim_id) router.replace(`/claims/${o.open_claim_id}`);
        else setOrder(o);
      })
      .catch((e) => setError(e.message));
  }, [orderId, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!order || !files.length) return;
    setBusy(true);
    setError(null);
    try {
      const blobs = await Promise.all(files.map((f) => compressImage(f)));
      const claim = await api.createClaim(order.id, text, blobs);
      router.push(`/claims/${claim.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">Report a problem</h1>
      <ErrorNote error={error} />
      {order && (
        <form onSubmit={submit} className="space-y-6">
          <Card>
            <div className="flex items-center gap-4">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={order.product.image_url} alt="" className="h-16 w-16 rounded-lg object-cover" />
              <div>
                <p className="font-medium">{order.product.name}</p>
                <p className="text-sm text-gray-500">
                  Order #{order.id} · paid {money(order.amount_cents, order.currency)} · {money(order.remaining_cents, order.currency)} refundable
                </p>
              </div>
            </div>
          </Card>
          <Card title="Photos">
            <PhotoPicker files={files} onChange={setFiles} />
          </Card>
          <Card title="What happened?">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={1500}
              rows={4}
              placeholder="e.g. The mug arrived with the handle snapped off."
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand focus:ring-2 focus:ring-blue-100 focus:outline-none"
            />
          </Card>
          <div className="flex items-center justify-end gap-3">
            <Button type="button" variant="ghost" onClick={() => router.back()}>Cancel</Button>
            <Button type="submit" disabled={busy || !files.length}>
              {busy && <Spinner />} Submit claim
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

export default function NewClaimPage() {
  return (
    <Suspense fallback={<p className="text-sm text-gray-500">Loading…</p>}>
      <NewClaimForm />
    </Suspense>
  );
}

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { compressImage, money } from "@/lib/format";
import PhotoPicker from "@/components/PhotoPicker";
import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";

const SAMPLES: Record<
  string,
  { label: string; badge: string; photo: string; filename: string; text: string; hint: string }
> = {
  broken: {
    label: "1. Clearly broken item",
    badge: "Full refund ($49.00)",
    photo: "/samples/lamp-broken.jpg",
    filename: "lamp-broken.jpg",
    text: "The desk lamp arrived with the upper arm snapped at the hinge.",
    hint: "Gemini reports confirmed product match & severe broken damage → Rule R1 suggests full $49.00 refund.",
  },
  blurry: {
    label: "2. Blurry photo",
    badge: "Request clearer evidence",
    photo: "/samples/lamp-blurry.jpg",
    filename: "lamp-blurry.jpg",
    text: "The arm is broken.",
    hint: "Gemini flags unclear evidence quality → Rule R5-weak requests a clearer photo ($0.00 suggested).",
  },
  reused: {
    label: "3. Suspected reused screenshot",
    badge: "Route to manual review",
    photo: "/samples/lamp-screenshot.jpg",
    filename: "lamp-screenshot.jpg",
    text: "Arrived broken out of the box, please refund.",
    hint: "Gemini flags screenshot/reused photo suspicion → Policy 1.2 routes to manual review ($0.00 suggested).",
  },
};

function NewClaimForm() {
  const router = useRouter();
  const params = useSearchParams();
  const orderId = Number(params.get("order"));
  const initialSample = params.get("sample");
  const [order, setOrder] = useState<Order | null>(null);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [activeSample, setActiveSample] = useState<string | null>(null);
  const [loadingSample, setLoadingSample] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applySample = useCallback(async (key: string) => {
    const s = SAMPLES[key];
    if (!s) return;
    setLoadingSample(true);
    setError(null);
    try {
      const res = await fetch(s.photo);
      const blob = await res.blob();
      const file = new File([blob], s.filename, { type: "image/jpeg" });
      setFiles([file]);
      setText(s.text);
      setActiveSample(key);
    } catch (e) {
      setError(`Could not load sample photo: ${(e as Error).message}`);
    } finally {
      setLoadingSample(false);
    }
  }, []);

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

  useEffect(() => {
    if (initialSample && SAMPLES[initialSample]) {
      applySample(initialSample);
    }
  }, [initialSample, applySample]);

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

          <Card
            title="Quick-fill a test scenario (optional)"
            aside={<Badge tone="blue">1-click judge presets</Badge>}
          >
            <p className="mb-3 text-xs text-gray-600">
              Click a preset below to pre-fill a test photo and buyer note, then click <strong>Submit claim</strong> to run the live Gemini assessment and policy engine.
            </p>
            <div className="grid gap-2 sm:grid-cols-3">
              {Object.entries(SAMPLES).map(([k, s]) => {
                const selected = activeSample === k;
                return (
                  <button
                    key={k}
                    type="button"
                    disabled={loadingSample}
                    onClick={() => applySample(k)}
                    className={`flex flex-col items-start rounded-lg border p-2.5 text-left text-xs transition ${
                      selected
                        ? "border-brand bg-blue-50/70 ring-1 ring-brand"
                        : "border-gray-200 bg-gray-50/60 hover:border-gray-300 hover:bg-gray-100/70"
                    }`}
                  >
                    <span className="font-semibold text-gray-900">{s.label}</span>
                    <span className="mt-0.5 text-[11px] font-medium text-brand-dark">{s.badge}</span>
                  </button>
                );
              })}
            </div>
            {activeSample && SAMPLES[activeSample] && (
              <p className="mt-3 rounded-lg bg-blue-50/80 px-3 py-2 text-xs text-blue-900">
                <strong>Expected behavior:</strong> {SAMPLES[activeSample].hint}
              </p>
            )}
          </Card>

          <Card title="Photos">
            <PhotoPicker files={files} onChange={(f) => { setFiles(f); setActiveSample(null); }} />
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
            <Button type="submit" disabled={busy || loadingSample || !files.length}>
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

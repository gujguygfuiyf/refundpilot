"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { compressImage, money } from "@/lib/format";
import PhotoPicker from "@/components/PhotoPicker";
import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";

interface SamplePreset {
  key: string;
  label: string;
  badge: string;
  photo: string;
  filename: string;
  text: string;
  hint: string;
}

const SKU_SAMPLES: Record<string, SamplePreset[]> = {
  "LAMP-02": [
    {
      key: "broken",
      label: "1. Clearly broken lamp",
      badge: "Full refund ($49.00)",
      photo: "/samples/lamp-broken.jpg",
      filename: "lamp-broken.jpg",
      text: "The desk lamp arrived with the upper arm snapped at the hinge.",
      hint: "Gemini reports confirmed product match & severe broken damage → Rule R1 suggests full $49.00 refund.",
    },
    {
      key: "blurry",
      label: "2. Blurry photo",
      badge: "Request clearer evidence",
      photo: "/samples/lamp-blurry.jpg",
      filename: "lamp-blurry.jpg",
      text: "The arm is broken.",
      hint: "Gemini flags unclear evidence quality → Rule R5-weak requests a clearer photo ($0.00 suggested).",
    },
    {
      key: "reused",
      label: "3. Suspected reused screenshot",
      badge: "Route to manual review",
      photo: "/samples/lamp-screenshot.jpg",
      filename: "lamp-screenshot.jpg",
      text: "Arrived broken out of the box, please refund.",
      hint: "Gemini flags screenshot/reused photo suspicion → Policy 1.2 routes to manual review ($0.00 suggested).",
    },
  ],
  "MUG-01": [
    {
      key: "broken",
      label: "1. Broken mug handle",
      badge: "Full refund ($24.00)",
      photo: "/samples/mug-broken.jpg",
      filename: "mug-broken.jpg",
      text: "The mug arrived with the handle snapped off in the box.",
      hint: "Gemini reports confirmed product match & broken handle → Rule R1 suggests full $24.00 refund.",
    },
    {
      key: "blurry",
      label: "2. Blurry mug photo",
      badge: "Request clearer evidence",
      photo: "/samples/mug-blurry.jpg",
      filename: "mug-blurry.jpg",
      text: "The handle is broken.",
      hint: "Gemini flags unclear evidence quality → Rule R5-weak requests a clearer photo ($0.00 suggested).",
    },
    {
      key: "scratch",
      label: "3. Minor cosmetic scratch",
      badge: "Partial refund ($3.60 / 15%)",
      photo: "/samples/mug-scratch.jpg",
      filename: "mug-scratch.jpg",
      text: "There is a visible scuff mark on the side of the mug.",
      hint: "Gemini reports minor cosmetic damage → Standard policy suggests a 15% partial refund ($3.60).",
    },
  ],
  "HEAD-03": [
    {
      key: "broken",
      label: "1. Snapped headband",
      badge: "Full refund ($89.00)",
      photo: "/samples/head-broken.jpg",
      filename: "head-broken.jpg",
      text: "The headphones arrived with the headband snapped on one side.",
      hint: "Gemini reports confirmed product match & severe broken damage → Rule R1 suggests full $89.00 refund.",
    },
    {
      key: "blurry",
      label: "2. Too dark / unclear photo",
      badge: "Request clearer evidence",
      photo: "/samples/head-dark.jpg",
      filename: "head-dark.jpg",
      text: "The earcup is cracked.",
      hint: "Gemini flags insufficient/unclear lighting → Rule R5-weak requests a clearer photo ($0.00 suggested).",
    },
    {
      key: "missing",
      label: "3. Missing ear cushion",
      badge: "Full refund ($89.00)",
      photo: "/samples/head-missing.jpg",
      filename: "head-missing.jpg",
      text: "One of the ear cushions was missing when I opened the box.",
      hint: "Gemini reports confirmed product match & missing part → Policy suggests a full refund.",
    },
  ],
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

  const presets = useMemo(() => {
    if (!order) return [];
    return SKU_SAMPLES[order.product.sku] || [];
  }, [order]);

  const applySample = useCallback(async (preset: SamplePreset) => {
    setLoadingSample(true);
    setError(null);
    try {
      const res = await fetch(preset.photo);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const file = new File([blob], preset.filename, { type: "image/jpeg" });
      setFiles([file]);
      setText(preset.text);
      setActiveSample(preset.key);
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
    if (order && initialSample) {
      const match = (SKU_SAMPLES[order.product.sku] || []).find((p) => p.key === initialSample);
      if (match) applySample(match);
    }
  }, [order, initialSample, applySample]);

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

  const currentPreset = presets.find((p) => p.key === activeSample);

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

          {presets.length > 0 && (
            <Card
              title={`Quick-fill a test scenario for ${order.product.name}`}
              aside={<Badge tone="blue">SKU-matched presets ({order.product.sku})</Badge>}
            >
              <p className="mb-3 text-xs text-gray-600">
                Click a preset below to pre-fill a matching <strong>{order.product.name}</strong> photo and buyer note, then click <strong>Submit claim</strong> to run the live Gemini assessment and policy engine.
              </p>
              <div className="grid gap-2 sm:grid-cols-3">
                {presets.map((s) => {
                  const selected = activeSample === s.key;
                  return (
                    <button
                      key={s.key}
                      type="button"
                      disabled={loadingSample}
                      onClick={() => applySample(s)}
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
              {currentPreset && (
                <p className="mt-3 rounded-lg bg-blue-50/80 px-3 py-2 text-xs text-blue-900">
                  <strong>Expected behavior:</strong> {currentPreset.hint}
                </p>
              )}
            </Card>
          )}

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

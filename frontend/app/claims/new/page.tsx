"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { compressImage, money } from "@/lib/format";
import PhotoPicker from "@/components/PhotoPicker";
import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";

// Default from backend/policies/standard.json (version standard-1.2, rule R2-cosmetic percent: 30)
const STANDARD_COSMETIC_PERCENT = 30;

type ExpectationKind = "full" | "partial" | "evidence" | "review";

interface SamplePresetSpec {
  key: string;
  label: string;
  kind: ExpectationKind;
  photo: string;
  filename: string;
  text: string;
  aiSummary: string;
  ruleId: string;
}

interface ResolvedPreset extends SamplePresetSpec {
  badge: string;
  hint: string;
}

const SKU_SAMPLE_SPECS: Record<string, SamplePresetSpec[]> = {
  "LAMP-02": [
    {
      key: "broken",
      label: "1. Clearly broken lamp",
      kind: "full",
      photo: "/samples/lamp-broken.jpg",
      filename: "lamp-broken.jpg",
      text: "The desk lamp arrived with the upper arm snapped at the hinge.",
      aiSummary: "confirmed product match, broken damage, sufficient evidence",
      ruleId: "R1-broken",
    },
    {
      key: "blurry",
      label: "2. Blurry photo",
      kind: "evidence",
      photo: "/samples/lamp-blurry.jpg",
      filename: "lamp-blurry.jpg",
      text: "The arm is broken.",
      aiSummary: "cannot_tell product match, insufficient evidence quality",
      ruleId: "R5-weak",
    },
    {
      key: "reused",
      label: "3. Suspected reused screenshot",
      kind: "review",
      photo: "/samples/lamp-screenshot.jpg",
      filename: "lamp-screenshot.jpg",
      text: "Arrived broken out of the box, please refund.",
      aiSummary: "stock_photo_suspected=true (screenshot/reused image)",
      ruleId: "R6-stock",
    },
  ],
  "MUG-01": [
    {
      key: "broken",
      label: "1. Broken mug",
      kind: "full",
      photo: "/samples/mug-broken.jpg",
      filename: "mug-broken.jpg",
      text: "The mug arrived with the handle snapped off in the box.",
      aiSummary: "likely product match, broken damage, sufficient evidence",
      ruleId: "R1-broken",
    },
    {
      key: "blurry",
      label: "2. Blurry mug photo",
      kind: "evidence",
      photo: "/samples/mug-blurry.jpg",
      filename: "mug-blurry.jpg",
      text: "The handle is broken.",
      aiSummary: "cannot_tell product match, insufficient evidence quality",
      ruleId: "R5-weak",
    },
    {
      key: "scratch",
      label: "3. Minor cosmetic scratch",
      kind: "partial",
      photo: "/samples/mug-scratch.jpg",
      filename: "mug-scratch.jpg",
      text: "There is a visible scuff mark on the side of the mug.",
      aiSummary: "likely product match, cosmetic damage, sufficient evidence",
      ruleId: "R2-cosmetic",
    },
  ],
  "HEAD-03": [
    {
      key: "broken",
      label: "1. Snapped headband",
      kind: "full",
      photo: "/samples/head-broken.jpg",
      filename: "head-broken.jpg",
      text: "The headphones arrived with the headband snapped on one side.",
      aiSummary: "confirmed product match, broken damage, sufficient evidence",
      ruleId: "R1-broken",
    },
    {
      key: "blurry",
      label: "2. Too dark / unclear photo",
      kind: "evidence",
      photo: "/samples/head-dark.jpg",
      filename: "head-dark.jpg",
      text: "The earcup is cracked.",
      aiSummary: "cannot_tell product match, insufficient evidence quality",
      ruleId: "R5-weak",
    },
    {
      key: "missing",
      label: "3. Missing ear cushion",
      kind: "full",
      photo: "/samples/head-missing.jpg",
      filename: "head-missing.jpg",
      text: "One of the ear cushions was missing when I opened the box.",
      aiSummary: "confirmed product match, missing_part damage, sufficient evidence",
      ruleId: "R1-broken",
    },
  ],
};

function resolvePreset(spec: SamplePresetSpec, order: Order, cosmeticPercent: number): ResolvedPreset {
  const fullCents = Math.min(order.amount_cents, order.remaining_cents);
  const partialCents = Math.floor((fullCents * cosmeticPercent) / 100);
  const zeroFmt = money(0, order.currency);

  if (spec.kind === "full") {
    const fullFmt = money(fullCents, order.currency);
    return {
      ...spec,
      badge: `Expected: Full refund (${fullFmt})`,
      hint: `Expected under Standard policy: AI reports ${spec.aiSummary} → Rule ${spec.ruleId} suggests FULL_REFUND of ${fullFmt}.`,
    };
  }
  if (spec.kind === "partial") {
    const partFmt = money(partialCents, order.currency);
    return {
      ...spec,
      badge: `Expected: ${cosmeticPercent}% partial refund (${partFmt})`,
      hint: `Expected under Standard policy: AI reports ${spec.aiSummary} → Rule ${spec.ruleId} (${cosmeticPercent}%) suggests PARTIAL_REFUND of ${partFmt}.`,
    };
  }
  if (spec.kind === "evidence") {
    return {
      ...spec,
      badge: `Expected: Request evidence (${zeroFmt})`,
      hint: `Expected under Standard policy: AI reports ${spec.aiSummary} → Rule ${spec.ruleId} suggests REQUEST_EVIDENCE (${zeroFmt}).`,
    };
  }
  return {
    ...spec,
    badge: `Expected: Manual review (${zeroFmt})`,
    hint: `Expected under Standard policy: AI reports ${spec.aiSummary} → Rule ${spec.ruleId} routes to REVIEW (${zeroFmt}).`,
  };
}

function NewClaimForm() {
  const router = useRouter();
  const params = useSearchParams();
  const orderId = Number(params.get("order"));
  const initialSample = params.get("sample");
  const [order, setOrder] = useState<Order | null>(null);
  const [cosmeticPercent, setCosmeticPercent] = useState<number>(STANDARD_COSMETIC_PERCENT);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [activeSample, setActiveSample] = useState<string | null>(null);
  const [loadingSample, setLoadingSample] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Read R2-cosmetic percent from backend GET /policies/standard-1.2 if available; fall back to STANDARD_COSMETIC_PERCENT (30)
    api
      .policies()
      .then((list) => {
        const std = list.find((p) => p.version.startsWith("standard-")) || list[0];
        const rules = (std?.body?.rules as Array<{ id?: string; percent?: number }> | undefined) || [];
        const r2 = rules.find((r) => r.id === "R2-cosmetic" && typeof r.percent === "number");
        if (r2 && typeof r2.percent === "number") {
          setCosmeticPercent(r2.percent);
        }
      })
      .catch(() => {
        /* keep STANDARD_COSMETIC_PERCENT = 30 from standard-1.2 */
      });
  }, []);

  const presets = useMemo(() => {
    if (!order) return [];
    const specs = SKU_SAMPLE_SPECS[order.product.sku] || [];
    return specs.map((s) => resolvePreset(s, order, cosmeticPercent));
  }, [order, cosmeticPercent]);

  const applySample = useCallback(async (preset: SamplePresetSpec) => {
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
      const match = (SKU_SAMPLE_SPECS[order.product.sku] || []).find((p) => p.key === initialSample);
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
                  {currentPreset.hint}
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

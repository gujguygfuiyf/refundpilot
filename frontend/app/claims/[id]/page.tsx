"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ACTIVE_STATES, type Claim } from "@/lib/types";
import { compressImage, money, when } from "@/lib/format";
import { DecisionCard, EvidenceCard, Photos, RefundCard, Timeline } from "@/components/ClaimPanels";
import PhotoPicker from "@/components/PhotoPicker";
import { Button, Card, ErrorNote, Spinner, StatusBadge } from "@/components/ui";

const BUYER_MESSAGE: Record<string, string> = {
  SUBMITTED: "We received your claim.",
  ANALYZING: "Reviewing your photos…",
  ANALYZED: "Applying the store's refund policy…",
  ANALYZE_FAILED: "We couldn't review the photos automatically. The store will look at them.",
  REVIEW_REQUIRED: "Your claim is with the store for approval.",
  AUTO_ELIGIBLE: "Your claim is with the store for approval.",
  EVIDENCE_REQUESTED: "The store needs a clearer photo before deciding.",
  APPROVED: "Approved. Sending your refund through PayPal…",
  PROCESSING: "Approved. Sending your refund through PayPal…",
  COMPLETED: "Refund completed in PayPal.",
  REJECTED: "The store declined this claim.",
  CANCELLED: "You withdrew this claim.",
};

export default function ClaimPage() {
  const { id } = useParams<{ id: string }>();
  const [claim, setClaim] = useState<Claim | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.claim(id).then(setClaim).catch((e) => setError(e.message)), [id]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while something is still moving (AI running, refund in flight).
  useEffect(() => {
    if (!claim || !ACTIVE_STATES.includes(claim.status)) return;
    const t = setTimeout(load, 2000);
    return () => clearTimeout(t);
  }, [claim, load]);

  async function sendEvidence() {
    setBusy(true);
    setError(null);
    try {
      const blobs = await Promise.all(files.map((f) => compressImage(f)));
      setClaim(await api.addEvidence(id, note, blobs));
      setFiles([]);
      setNote("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function withdraw() {
    if (!confirm("Withdraw this claim?")) return;
    try {
      setClaim(await api.cancel(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!claim) {
    return error ? <ErrorNote error={error} /> : <p className="flex items-center gap-2 text-sm text-gray-500"><Spinner /> Loading claim…</p>;
  }

  const moving = ACTIVE_STATES.includes(claim.status);
  const canWithdraw = !["COMPLETED", "REJECTED", "CANCELLED", "PROCESSING", "APPROVED"].includes(claim.status);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="text-sm text-gray-500 hover:text-gray-800">← Orders</Link>
          <h1 className="mt-1 text-2xl font-semibold">{claim.order.product.name}</h1>
          <p className="text-sm text-gray-500">
            Claim <code>{claim.id}</code> · order #{claim.order.id} · {money(claim.order.amount_cents, claim.order.currency)} · opened {when(claim.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {moving && <Spinner />}
          <StatusBadge status={claim.status} />
          {canWithdraw && <Button variant="ghost" onClick={withdraw}>Withdraw</Button>}
        </div>
      </div>

      <p className="rounded-xl bg-white px-4 py-3 text-sm ring-1 ring-gray-200">{BUYER_MESSAGE[claim.status] ?? claim.status}</p>
      <ErrorNote error={error} />

      {!moving && claim.assessment && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-200 bg-gradient-to-r from-blue-50/90 to-indigo-50/60 px-4 py-3 text-xs text-blue-950 shadow-2xs">
          <div className="space-y-0.5">
            <span className="font-semibold text-brand-dark">Judge / Merchant shortcut:</span>{" "}
            <span>
              AI evidence review and policy evaluation (<code>{claim.policy_version}</code>
              {claim.decision ? ` · ${claim.decision.rule_id}` : ""}) are complete. Jump directly to the merchant view:
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/merchant?claim=${claim.id}`}
              className="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 font-medium text-white shadow-2xs hover:bg-brand-dark"
            >
              {["REVIEW_REQUIRED", "AUTO_ELIGIBLE"].includes(claim.status)
                ? "Approve & refund in Merchant Queue →"
                : "Inspect in Merchant Queue →"}
            </Link>
            <Link
              href={`/merchant/policy?claim=${claim.id}`}
              className="inline-flex items-center gap-1 rounded-lg bg-white px-3 py-1.5 font-medium text-gray-800 ring-1 ring-blue-200 hover:bg-blue-50"
            >
              Compare across 3 policies →
            </Link>
          </div>
        </div>
      )}

      {claim.status === "EVIDENCE_REQUESTED" && (
        <Card title="Add more evidence">
          <div className="space-y-3">
            <PhotoPicker files={files} onChange={setFiles} />
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="Anything to add? (optional)"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
            <div className="flex justify-end">
              <Button onClick={sendEvidence} disabled={busy || !files.length}>{busy && <Spinner />} Send photos</Button>
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <Card title="Photos">
            <Photos claim={claim} />
            {claim.buyer_text && <blockquote className="mt-4 border-l-2 border-gray-200 pl-3 text-sm text-gray-700">{claim.buyer_text}</blockquote>}
          </Card>
          <EvidenceCard claim={claim} />
        </div>
        <div className="space-y-6 lg:col-span-2">
          <DecisionCard claim={claim} />
          <RefundCard claim={claim} />
          <Timeline claim={claim} />
        </div>
      </div>
    </div>
  );
}

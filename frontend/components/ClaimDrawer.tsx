"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ACTIVE_STATES, type Claim } from "@/lib/types";
import { money } from "@/lib/format";
import { DecisionCard, EvidenceCard, Photos, RefundCard, Timeline } from "./ClaimPanels";
import { Button, Card, ErrorNote, Spinner, StatusBadge } from "./ui";

const APPROVABLE = ["REVIEW_REQUIRED", "AUTO_ELIGIBLE"];

export default function ClaimDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [claim, setClaim] = useState<Claim | null>(null);
  const [amount, setAmount] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api.merchantClaim(id).then((c) => {
        setClaim(c);
        setAmount((prev) => prev || (c.decision?.suggested_cents ? (c.decision.suggested_cents / 100).toFixed(2) : ""));
      }).catch((e) => setError(e.message)),
    [id],
  );

  useEffect(() => {
    setClaim(null);
    setAmount("");
    setMessage("");
    setError(null);
    load();
  }, [id, load]);

  useEffect(() => {
    if (!claim || !ACTIVE_STATES.includes(claim.status)) return;
    const t = setTimeout(load, 2000);
    return () => clearTimeout(t);
  }, [claim, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
      await load();
    } finally {
      setBusy(null);
    }
  }

  const remaining = claim ? claim.order.remaining_cents : 0;
  const cents = Math.round(parseFloat(amount || "0") * 100);
  const suggested = claim?.decision?.suggested_cents ?? 0;
  const amountValid = cents > 0 && cents <= remaining;

  function approve() {
    if (!claim) return;
    const cur = claim.order.currency;
    if (!confirm(`Refund ${money(cents, cur)} to the buyer through PayPal?`)) return;
    act("approve", () => api.approve(claim.id, cents !== suggested ? cents : undefined));
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative h-full w-full max-w-3xl overflow-y-auto bg-gray-50 shadow-xl">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-gray-200 bg-white px-5 py-3">
          <div className="min-w-0">
            <p className="truncate font-medium">{claim?.order.product.name ?? "Claim"}</p>
            <p className="text-xs text-gray-500"><code>{id}</code></p>
          </div>
          <div className="flex items-center gap-2">
            {claim && <StatusBadge status={claim.status} />}
            <Link href={`/claims/${id}`} target="_blank" className="text-xs text-gray-500 hover:text-gray-800">Buyer view ↗</Link>
            <Button variant="ghost" onClick={onClose} aria-label="Close">✕</Button>
          </div>
        </div>

        <div className="space-y-5 p-5">
          <ErrorNote error={error} />
          {!claim ? (
            <p className="flex items-center gap-2 text-sm text-gray-500"><Spinner /> Loading…</p>
          ) : (
            <>
              {APPROVABLE.includes(claim.status) && (
                <Card title="Your decision" className="ring-2 ring-blue-100">
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="text-sm">
                      <span className="block text-xs text-gray-500">Refund amount ({claim.order.currency}), max {money(remaining, claim.order.currency)}</span>
                      <input
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        inputMode="decimal"
                        className="mt-1 w-36 rounded-lg border border-gray-300 px-3 py-2 text-sm"
                      />
                    </label>
                    <Button onClick={approve} disabled={!amountValid || !!busy}>
                      {busy === "approve" && <Spinner />} Approve &amp; refund via PayPal
                    </Button>
                  </div>
                  {cents !== suggested && amountValid && (
                    <p className="mt-2 text-xs text-amber-700">Overrides the policy suggestion of {money(suggested, claim.order.currency)}. The override is recorded in the audit trail.</p>
                  )}
                  <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-gray-100 pt-4">
                    <input
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                      placeholder="Message to the buyer"
                      className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm"
                    />
                    <Button variant="secondary" disabled={!message.trim() || !!busy}
                      onClick={() => act("evidence", () => api.requestEvidence(claim.id, message.trim()))}>
                      Request evidence
                    </Button>
                    <Button variant="danger" disabled={!message.trim() || !!busy}
                      onClick={() => confirm("Reject this claim?") && act("reject", () => api.reject(claim.id, message.trim()))}>
                      Reject
                    </Button>
                  </div>
                </Card>
              )}
              {["ANALYZE_FAILED", "EVIDENCE_REQUESTED", "REVIEW_REQUIRED"].includes(claim.status) && (
                <div className="flex justify-end">
                  <Button variant="ghost" disabled={!!busy} onClick={() => act("rerun", () => api.reassess(claim.id, undefined, true))}>
                    {busy === "rerun" && <Spinner />} Re-run AI assessment
                  </Button>
                </div>
              )}
              <RefundCard claim={claim} />
              <DecisionCard claim={claim} />
              <Card title="Photos">
                <Photos claim={claim} />
                {claim.buyer_text && <blockquote className="mt-4 border-l-2 border-gray-200 pl-3 text-sm text-gray-700">{claim.buyer_text}</blockquote>}
              </Card>
              <EvidenceCard claim={claim} />
              <Timeline claim={claim} />
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

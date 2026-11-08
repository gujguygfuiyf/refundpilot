"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Dispute } from "@/lib/types";
import { label, money, when } from "@/lib/format";
import { DecisionCard, EvidenceCard, Photos, RefundCard } from "./ClaimPanels";
import { Badge, Button, Card, ErrorNote, Spinner, StatusBadge } from "./ui";

const ACTIONABLE_STATUSES = ["WAITING_FOR_SELLER_RESPONSE", "OPEN"];

export default function DisputeDrawer({
  id,
  previewClaimId,
  onClose,
  onChanged,
}: {
  id: string;
  previewClaimId?: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [dispute, setDispute] = useState<Dispute | null>(null);
  const [amount, setAmount] = useState("");
  const [acceptNote, setAcceptNote] = useState("");
  const [evidenceNotes, setEvidenceNotes] = useState("");
  const [confirmMode, setConfirmMode] = useState<"accept" | "evidence" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (previewClaimId) {
      return api
        .merchantClaim(previewClaimId)
        .then((c) => {
          const refundIds = (c.refunds || [])
            .filter((r) => r.status === "COMPLETED" && r.paypal_refund_id)
            .map((r) => r.paypal_refund_id as string);

          const lines: string[] = [
            `Order #${c.order.id} (${c.order.product.name}, SKU ${c.order.product.sku}) · PayPal capture ${c.order.capture_id}.`,
            `RefundPilot claim ${c.id} (status ${c.status}).`,
          ];
          if (c.assessment) {
            lines.push(
              `AI evidence assessment (${c.assessment.provider}/${c.assessment.model}): product_match=${c.assessment.output.product_match}, damage_type=${c.assessment.output.damage_type}, severity=${c.assessment.output.severity}, evidence_quality=${c.assessment.output.evidence_quality}.`,
            );
          }
          if (c.decision) {
            lines.push(
              `Policy ${c.decision.policy_version} rule ${c.decision.rule_id} -> ${c.decision.outcome} (suggested $${(c.decision.suggested_cents / 100).toFixed(2)}): ${c.decision.reason}`,
            );
          }
          if (refundIds.length > 0) {
            lines.push(
              `Completed PayPal refund ID(s): ${refundIds.join(", ")} ($${(c.order.refunded_cents / 100).toFixed(2)} refunded).`,
            );
          }
          const notesLines = lines.join("\n");

          const d: Dispute = {
            id: "preview",
            paypal_dispute_id: "PP-PREVIEW-SANDBOX",
            status: "WAITING_FOR_SELLER_RESPONSE",
            stage: "INQUIRY",
            reason: "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            dispute_amount_cents: c.order.amount_cents,
            currency: c.order.currency,
            suggested_cents: Math.min(c.decision?.suggested_cents ?? 0, c.order.remaining_cents),
            has_policy_decision: Boolean(c.decision),
            can_accept_claim: true,
            can_provide_evidence: true,
            allowed_accept_claim_types: ["REFUND", "PARTIAL_REFUND"],
            order: c.order,
            claim: c,
            evidence_preview: {
              evidence_type: refundIds.length ? "PROOF_OF_REFUND" : "OTHER",
              refund_ids: refundIds,
              notes: notesLines,
              photo_urls: c.photo_urls,
            },
            submitted_evidence_notes: null,
            timeline: c.timeline,
            raw: { preview_only: true },
            created_at: c.created_at,
            updated_at: c.updated_at,
            resolved_at: null,
          };

          setDispute(d);
          setAmount((prev) =>
            prev || (d.suggested_cents > 0 ? (d.suggested_cents / 100).toFixed(2) : ""),
          );
          setEvidenceNotes(d.evidence_preview.notes);
        })
        .catch((e) => setError(e.message));
    }

    return api
      .merchantDispute(id)
      .then((d) => {
        setDispute(d);
        setAmount((prev) =>
          prev || (d.suggested_cents > 0 ? (d.suggested_cents / 100).toFixed(2) : ""),
        );
        setEvidenceNotes((prev) => prev || d.evidence_preview.notes);
      })
      .catch((e) => setError(e.message));
  }, [id, previewClaimId]);

  useEffect(() => {
    setDispute(null);
    setAmount("");
    setAcceptNote("");
    setEvidenceNotes("");
    setConfirmMode(null);
    setError(null);
    load();
  }, [id, previewClaimId, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (confirmMode) setConfirmMode(null);
        else onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, confirmMode]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
      setConfirmMode(null);
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
      await load();
    } finally {
      setBusy(null);
    }
  }

  const currency = dispute?.currency ?? "USD";
  const remaining = dispute?.order?.remaining_cents ?? 0;
  const maxCents =
    dispute && dispute.dispute_amount_cents > 0
      ? Math.min(remaining, dispute.dispute_amount_cents)
      : remaining;
  const cents = Math.round(parseFloat(amount || "0") * 100);
  const suggested = dispute?.suggested_cents ?? 0;
  const hasPolicyDecision = dispute?.has_policy_decision ?? false;
  const allowedAcceptTypes = dispute?.allowed_accept_claim_types ?? [];
  const disputeTotal = (dispute?.dispute_amount_cents || dispute?.order?.amount_cents) ?? 0;
  const acceptClaimType = cents < disputeTotal ? "PARTIAL_REFUND" : "REFUND";
  const claimTypePermitted = allowedAcceptTypes.includes(acceptClaimType);
  const amountValid = cents > 0 && cents <= maxCents && claimTypePermitted;
  const actionable = dispute ? ACTIONABLE_STATUSES.includes(dispute.status) : false;
  const canAcceptClaim = Boolean(dispute?.can_accept_claim) && allowedAcceptTypes.length > 0;
  const canProvideEvidence = Boolean(dispute?.can_provide_evidence);
  const hasUnknownAccept = Boolean(
    dispute?.claim?.refunds?.some(
      (r) => r.status === "UNKNOWN" && r.paypal_request_id.startsWith("rp-dispute-accept-"),
    ),
  );
  const isOverride = !hasPolicyDecision || cents !== suggested;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative h-full w-full max-w-3xl overflow-y-auto bg-gray-50 shadow-xl">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-gray-200 bg-white px-5 py-3">
          <div className="min-w-0">
            <p className="truncate font-medium">
              {dispute?.order?.product.name ?? "PayPal Dispute"} ·{" "}
              <span className="font-mono text-sm text-gray-700">
                {dispute?.paypal_dispute_id ?? id}
              </span>
            </p>
            <p className="text-xs text-gray-500">
              <code>{id}</code>
              {dispute?.order && ` · order #${dispute.order.id} (capture ${dispute.order.capture_id})`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {dispute && <Badge tone="violet">{label(dispute.stage)}</Badge>}
            {dispute && <StatusBadge status={dispute.status} />}
            {dispute?.claim && (
              <Link
                href={`/claims/${dispute.claim.id}`}
                target="_blank"
                className="text-xs text-gray-500 hover:text-gray-800"
              >
                Buyer claim ↗
              </Link>
            )}
            <Button variant="ghost" onClick={onClose} aria-label="Close">
              ✕
            </Button>
          </div>
        </div>

        <div className="space-y-5 p-5">
          <ErrorNote error={error} />
          {!dispute ? (
            <p className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner /> Loading dispute…
            </p>
          ) : (
            <>
              {Boolean(previewClaimId) && dispute.claim && dispute.order && (
                <div className="rounded-xl border border-violet-200 bg-gradient-to-r from-violet-50 to-blue-50 p-4 text-xs text-violet-950 shadow-sm">
                  <div className="flex items-center gap-2 font-semibold text-violet-900">
                    <Badge tone="violet">Interactive Architecture Preview</Badge>
                    <span>Built from real recorded claim</span>
                  </div>
                  <p className="mt-1.5 leading-relaxed">
                    Interactive Architecture Preview — Built from real recorded claim{" "}
                    <code className="rounded bg-violet-100/80 px-1 py-0.5 font-mono">{dispute.claim.id}</code> (PayPal capture{" "}
                    <code className="rounded bg-violet-100/80 px-1 py-0.5 font-mono">{dispute.order.capture_id}</code>). Shows how
                    RefundPilot auto-assembles a <strong>{dispute.evidence_preview.evidence_type}</strong> evidence package and enforces
                    the remaining-balance cap (<strong>{money(dispute.order.remaining_cents, currency)}</strong> left) if a buyer opens a PayPal dispute.
                    Live PayPal write calls are disabled in preview mode.
                  </p>
                </div>
              )}

              <Card
                title="PayPal Dispute Summary"
                aside={
                  <span className="text-xs text-gray-500">
                    Disputed: {money(dispute.dispute_amount_cents, currency)}
                  </span>
                }
              >
                <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                  <div>
                    <dt className="text-xs text-gray-500">PayPal Dispute ID</dt>
                    <dd className="mt-0.5 font-mono text-xs font-medium">{dispute.paypal_dispute_id}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-gray-500">Reason</dt>
                    <dd className="mt-0.5 font-medium">{label(dispute.reason)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-gray-500">Lifecycle Stage</dt>
                    <dd className="mt-0.5 font-medium">{label(dispute.stage)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-gray-500">Refundable Balance</dt>
                    <dd className="mt-0.5 font-medium">
                      {dispute.order ? money(dispute.order.remaining_cents, currency) : "Unmatched"}
                    </dd>
                  </div>
                </dl>
              </Card>

              {dispute.status === "PROCESSING" && (
                <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                  <p>
                    Claim acceptance is in flight. Waiting for PayPal to confirm the refund: RefundPilot marks it
                    refunded only when PayPal resolves the dispute in the buyer&apos;s favour or sends the refund
                    webhook. Nothing is credited before that.
                  </p>
                  {hasUnknownAccept && (
                    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-amber-200/80 pt-2 text-xs">
                      <span>
                        The <code>accept-claim</code> call timed out and remains <code>UNKNOWN</code> so a late PayPal
                        settlement is never missed. If you verified in PayPal that it was not received, you can reset
                        this attempt:
                      </span>
                      <Button
                        variant="secondary"
                        disabled={!!busy}
                        onClick={() => act("reset", () => api.resetDisputeAccept(dispute.id))}
                      >
                        {busy === "reset" && <Spinner />} Check PayPal &amp; reset timed-out attempt
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {actionable && (
                <Card title="Merchant Action (Human Approval Required)" className="ring-2 ring-blue-100">
                  <p className="mb-4 text-xs text-gray-600">
                    AI never moves money or answers PayPal automatically. Choose whether to accept the dispute &amp; refund
                    within the refundable balance, or send the structured evidence package to PayPal.
                  </p>

                  <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50/70 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-xs font-semibold tracking-wide text-gray-800 uppercase">
                        Option 1 · Accept claim &amp; refund via PayPal
                      </h3>
                      <span className="text-xs text-gray-500">
                        {hasPolicyDecision ? (
                          <>
                            Policy suggestion: <strong>{money(suggested, currency)}</strong>
                          </>
                        ) : (
                          <strong>Pending human decision (no policy assessment)</strong>
                        )}{" "}
                        · Max allowed: <strong>{money(maxCents, currency)}</strong>
                      </span>
                    </div>
                    {!canAcceptClaim ? (
                      <p className="text-xs text-amber-800">
                        PayPal does not currently expose an <code>accept-claim</code> action link for this dispute.
                      </p>
                    ) : (
                      <>
                        <div className="flex flex-wrap items-end gap-3">
                          <label className="text-sm">
                            <span className="block text-xs text-gray-500">
                              Refund amount ({currency}), max {money(maxCents, currency)}
                            </span>
                            <input
                              value={amount}
                              onChange={(e) => setAmount(e.target.value)}
                              placeholder={suggested > 0 ? (suggested / 100).toFixed(2) : "Enter override amount"}
                              inputMode="decimal"
                              className="mt-1 w-40 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm"
                            />
                          </label>
                          <label className="min-w-0 flex-1 text-sm">
                            <span className="block text-xs text-gray-500">Note to PayPal / buyer (optional)</span>
                            <input
                              value={acceptNote}
                              onChange={(e) => setAcceptNote(e.target.value)}
                              placeholder="Accepted under merchant refund policy"
                              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm"
                            />
                          </label>
                          <Button
                            onClick={() => setConfirmMode("accept")}
                            disabled={!amountValid || !dispute.order || !!busy}
                          >
                            {busy === "accept" && <Spinner />} Accept claim &amp; refund via PayPal
                          </Button>
                        </div>
                        {!claimTypePermitted && cents > 0 && (
                          <p className="text-xs text-red-700">
                            PayPal only permits <code>{allowedAcceptTypes.join(", ")}</code> on this dispute (
                            <code>{acceptClaimType}</code> is not allowed).
                          </p>
                        )}
                        {hasPolicyDecision && suggested === 0 && cents === 0 && (
                          <p className="text-xs text-gray-600">
                            Policy suggests <strong>$0.00</strong> ({dispute.claim?.decision?.outcome ?? "no refund"}).
                            Enter an explicit override amount if you still want to accept and refund this dispute.
                          </p>
                        )}
                        {isOverride && amountValid && (
                          <p className="text-xs text-amber-700">
                            {hasPolicyDecision
                              ? `Overrides the policy suggestion of ${money(suggested, currency)}.`
                              : "Manual refund amount (no prior policy decision)."}{" "}
                            Recorded as a merchant override in the audit trail.
                          </p>
                        )}
                      </>
                    )}
                  </div>

                  <div className="mt-4 space-y-3 rounded-lg border border-gray-200 bg-gray-50/70 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-xs font-semibold tracking-wide text-gray-800 uppercase">
                        Option 2 · Send evidence to PayPal
                      </h3>
                      <div className="flex items-center gap-2 text-xs text-gray-500">
                        <Badge tone="blue">{dispute.evidence_preview.evidence_type}</Badge>
                        {dispute.evidence_preview.refund_ids.length > 0 && (
                          <span>
                            Refund IDs: <code>{dispute.evidence_preview.refund_ids.join(", ")}</code>
                          </span>
                        )}
                        <span>{dispute.evidence_preview.photo_urls.length} photo(s) attached</span>
                      </div>
                    </div>
                    {!canProvideEvidence ? (
                      <p className="text-xs text-amber-800">
                        PayPal does not currently expose a <code>provide-evidence</code> action link for this dispute.
                      </p>
                    ) : (
                      <>
                        <label className="block text-sm">
                          <span className="block text-xs text-gray-500">
                            Deterministic evidence summary (preview &amp; edit before sending to PayPal)
                          </span>
                          <textarea
                            rows={7}
                            value={evidenceNotes}
                            onChange={(e) => setEvidenceNotes(e.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-300 bg-white p-3 font-mono text-xs text-gray-800"
                          />
                        </label>
                        <div className="flex justify-end">
                          <Button
                            variant="secondary"
                            onClick={() => setConfirmMode("evidence")}
                            disabled={!evidenceNotes.trim() || !!busy}
                          >
                            {busy === "evidence" && <Spinner />} Send evidence to PayPal
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                </Card>
              )}

              {dispute.submitted_evidence_notes && (
                <Card
                  title="Evidence Submitted to PayPal"
                  aside={<Badge tone="violet">{dispute.evidence_preview.evidence_type}</Badge>}
                >
                  <pre className="whitespace-pre-wrap rounded-lg bg-gray-50 p-3 font-mono text-xs text-gray-800 ring-1 ring-gray-200">
                    {dispute.submitted_evidence_notes}
                  </pre>
                </Card>
              )}

              {dispute.claim ? (
                <>
                  <RefundCard claim={dispute.claim} />
                  <DecisionCard claim={dispute.claim} />
                  <Card title="Linked Claim Photos">
                    <Photos claim={dispute.claim} />
                    {dispute.claim.buyer_text && (
                      <blockquote className="mt-4 border-l-2 border-gray-200 pl-3 text-sm text-gray-700">
                        {dispute.claim.buyer_text}
                      </blockquote>
                    )}
                  </Card>
                  <EvidenceCard claim={dispute.claim} />
                </>
              ) : (
                <Card title="Linked Claim">
                  <p className="text-sm text-gray-500">
                    No pre-dispute buyer claim was filed in RefundPilot before this PayPal dispute was opened.
                  </p>
                </Card>
              )}

              <Card title="Audit trail">
                <ol className="relative space-y-3 border-l border-gray-200 pl-5">
                  {dispute.timeline.map((t, i) => (
                    <li key={i} className="text-sm">
                      <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-brand" />
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="font-medium">{label(t.event)}</span>
                        <span className="text-xs text-gray-500">
                          {t.actor} · {when(t.ts)}
                        </span>
                      </div>
                      {t.detail && Object.keys(t.detail).length > 0 && (
                        <p className="mt-0.5 truncate font-mono text-xs text-gray-500">
                          {Object.entries(t.detail)
                            .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
                            .join("  ")}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              </Card>
            </>
          )}
        </div>

        {confirmMode && dispute && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
            <div className="w-full max-w-xl rounded-xl border border-gray-200 bg-white p-5 shadow-2xl">
              {confirmMode === "accept" ? (
                <>
                  <h3 className="text-base font-semibold text-gray-900">
                    Confirm Accept Claim &amp; Refund via PayPal
                  </h3>
                  <p className="mt-2 text-sm text-gray-600">
                    You are about to accept PayPal dispute <code>{dispute.paypal_dispute_id}</code> and issue a refund
                    of <strong>{money(cents, currency)}</strong> (max refundable:{" "}
                    {money(maxCents, currency)}).
                  </p>
                  <div className="mt-3 rounded-lg bg-gray-50 p-3 text-xs text-gray-700 ring-1 ring-gray-200">
                    <p>
                      <strong>Endpoint:</strong>{" "}
                      <code>POST /v1/customer/disputes/{dispute.paypal_dispute_id}/accept-claim</code>
                    </p>
                    <p className="mt-1">
                      <strong>Refund amount:</strong> {money(cents, currency)} (
                      {cents < dispute.dispute_amount_cents ? "PARTIAL_REFUND" : "REFUND"})
                    </p>
                    <p className="mt-1">
                      <strong>Note:</strong>{" "}
                      {acceptNote.trim() ||
                        `Accepted under policy (${dispute.claim?.policy_version ?? "standard-1.2"})`}
                    </p>
                  </div>
                  {Boolean(previewClaimId) && (
                    <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 ring-1 ring-amber-200">
                      Architecture preview mode — no live PayPal dispute ID exists in Sandbox to POST to.
                    </p>
                  )}
                  <div className="mt-4 flex justify-end gap-2">
                    <Button variant="secondary" onClick={() => setConfirmMode(null)} disabled={!!busy}>
                      Cancel
                    </Button>
                    <Button
                      onClick={() =>
                        act("accept", () =>
                          api.acceptDispute(
                            dispute.id,
                            isOverride ? cents : undefined,
                            acceptNote.trim() || undefined,
                          ),
                        )
                      }
                      disabled={!!busy || Boolean(previewClaimId)}
                    >
                      {busy === "accept" && <Spinner />} Confirm &amp; refund {money(cents, currency)}
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <h3 className="text-base font-semibold text-gray-900">
                    Confirm Evidence Submission to PayPal
                  </h3>
                  <p className="mt-2 text-sm text-gray-600">
                    Review the evidence package that will be sent to{" "}
                    <code>POST /v1/customer/disputes/{dispute.paypal_dispute_id}/provide-evidence</code>:
                  </p>
                  <div className="mt-3 space-y-2 rounded-lg bg-gray-50 p-3 text-xs text-gray-700 ring-1 ring-gray-200">
                    <p>
                      <strong>Evidence type:</strong> <code>{dispute.evidence_preview.evidence_type}</code> ·{" "}
                      <strong>Photos attached:</strong> {dispute.evidence_preview.photo_urls.length}
                      {dispute.evidence_preview.refund_ids.length > 0 && (
                        <>
                          {" "}
                          · <strong>Refund IDs:</strong>{" "}
                          <code>{dispute.evidence_preview.refund_ids.join(", ")}</code>
                        </>
                      )}
                    </p>
                    <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded bg-white p-2.5 font-mono text-xs text-gray-800 ring-1 ring-gray-200">
                      {evidenceNotes}
                    </pre>
                  </div>
                  {Boolean(previewClaimId) && (
                    <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 ring-1 ring-amber-200">
                      Architecture preview mode — no live PayPal dispute ID exists in Sandbox to POST to.
                    </p>
                  )}
                  <div className="mt-4 flex justify-end gap-2">
                    <Button variant="secondary" onClick={() => setConfirmMode(null)} disabled={!!busy}>
                      Cancel
                    </Button>
                    <Button
                      onClick={() =>
                        act("evidence", () => api.provideDisputeEvidence(dispute.id, evidenceNotes.trim()))
                      }
                      disabled={!!busy || Boolean(previewClaimId)}
                    >
                      {busy === "evidence" && <Spinner />} Confirm &amp; send to PayPal
                    </Button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

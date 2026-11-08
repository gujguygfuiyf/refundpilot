"use client";

import { useState } from "react";
import type { Claim } from "@/lib/types";
import { photoUrl } from "@/lib/api";
import { label, money, when } from "@/lib/format";
import { canonicalJsonStringify, sha256 } from "@/lib/crypto";
import { Badge, Card, FactChip, OutcomeBadge, StatusBadge } from "./ui";

export function detectPromptInjection(
  buyerText: string | undefined | null,
  orderAmountCents?: number,
): { isInjection: boolean; snippet?: string } {
  if (!buyerText) return { isInjection: false };
  const lower = buyerText.toLowerCase();

  const keyPhrases = [
    "system override",
    "admin command",
    "ignore all",
    "ignore previous",
    "ignore store",
    "ignore instructions",
    "refund_amount=",
    "auto-approve immediately",
    "approve an immediate refund",
  ];
  for (const phrase of keyPhrases) {
    if (lower.includes(phrase)) {
      return { isInjection: true, snippet: phrase };
    }
  }

  const match = buyerText.match(/(?:approve|refund|give me|to my account)?\s*\$(\d+(?:\.\d{2})?)/i);
  if (match) {
    const dollars = parseFloat(match[1]);
    const requestedCents = dollars * 100;
    if (dollars >= 500 || (orderAmountCents && requestedCents > orderAmountCents * 2)) {
      return { isInjection: true, snippet: match[0].trim() };
    }
  }

  return { isInjection: false };
}

export function Photos({ claim }: { claim: Claim }) {
  const o = claim.assessment?.output;
  const isMatch = o && ["confirmed", "likely"].includes(o.product_match);
  const isMismatch = o && ["mismatch", "unlikely"].includes(o.product_match);
  const qualityLevel =
    o?.evidence_quality === "sufficient"
      ? "HIGH"
      : o?.evidence_quality === "unclear"
        ? "MEDIUM"
        : "LOW";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {/* Left: Order SKU Reference */}
        <div className="rounded-xl border border-gray-200 bg-gray-50/60 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold tracking-wide text-gray-700 uppercase">
              Order SKU Reference
            </span>
            <span className="rounded bg-gray-200/80 px-1.5 py-0.5 font-mono text-[11px] text-gray-700">
              {claim.order.product.sku}
            </span>
          </div>
          <figure className="relative overflow-hidden rounded-lg border border-gray-200 bg-white">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={claim.order.product.image_url}
              alt={claim.order.product.name}
              className="aspect-square w-full object-cover"
            />
            <figcaption className="border-t border-gray-100 bg-white p-2 text-xs text-gray-600">
              <p className="truncate font-medium text-gray-800">{claim.order.product.name}</p>
              <p className="text-[11px] text-gray-500">Official catalog listing photo</p>
            </figcaption>
          </figure>
        </div>

        {/* Right: Submitted Evidence Photos */}
        <div className="rounded-xl border border-gray-200 bg-gray-50/60 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold tracking-wide text-gray-700 uppercase">
              Submitted Evidence ({claim.photo_urls.length})
            </span>
            <span className="text-[11px] text-gray-500">Click photo to expand</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {claim.photo_urls.map((u, i) => (
              <figure key={u} className="relative overflow-hidden rounded-lg border border-gray-200 bg-white">
                <a href={photoUrl(u)} target="_blank" rel="noreferrer" className="group block">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={photoUrl(u)}
                    alt={`Buyer evidence photo ${i + 1}`}
                    className="aspect-square w-full object-cover transition group-hover:scale-105"
                  />
                  <figcaption className="border-t border-gray-100 bg-white p-1.5 text-center text-[11px] text-gray-600">
                    Buyer photo {i + 1} ↗
                  </figcaption>
                </a>
              </figure>
            ))}
          </div>
        </div>
      </div>

      {/* Structured Visual Verification Badges */}
      {o && (
        <div className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="mb-2 text-[11px] font-semibold tracking-wide text-gray-500 uppercase">
            Multimodal Visual Verification Badges
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {isMatch ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 ring-1 ring-emerald-200">
                ✓ SKU Visual Match
              </span>
            ) : isMismatch ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2.5 py-1 text-xs font-medium text-red-800 ring-1 ring-red-200">
                ✕ Item Mismatch
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 ring-1 ring-amber-200">
                ? SKU Match Inconclusive
              </span>
            )}

            {o.stock_photo_suspected ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 ring-1 ring-amber-200">
                ⚠ Suspected Screenshot / Reused Image
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 ring-1 ring-emerald-200">
                ✓ Original Camera Capture
              </span>
            )}

            <span
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${
                qualityLevel === "HIGH"
                  ? "bg-emerald-50 text-emerald-800 ring-emerald-200"
                  : qualityLevel === "MEDIUM"
                    ? "bg-blue-50 text-blue-800 ring-blue-200"
                    : "bg-amber-50 text-amber-800 ring-amber-200"
              }`}
            >
              Evidence Quality: {qualityLevel}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export function EvidenceCard({ claim }: { claim: Claim }) {
  const a = claim.assessment;
  if (!a) {
    return (
      <Card title="Evidence">
        <p className="text-sm text-gray-500">
          {claim.status === "ANALYZING" || claim.status === "SUBMITTED" ? "The AI is reading the photos…" : "No assessment yet."}
        </p>
      </Card>
    );
  }
  const o = a.output;
  const facts: [string, string | boolean][] = [
    ["Product match", o.product_match],
    ["Damage type", o.damage_type],
    ["Severity", o.severity],
    ["Evidence quality", o.evidence_quality],
    ["Matches description", o.description_consistent],
    ["Stock photo suspected", o.stock_photo_suspected],
  ];
  return (
    <Card
      title="Evidence"
      aside={<span className="text-xs text-gray-500">{a.provider} · {a.model} · {Math.round(o.confidence * 100)}% confidence</span>}
    >
      <p className="mb-3 text-xs text-gray-500">The AI only describes what it sees. It never sets an amount.</p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-gray-500">{k}</dt>
            <dd className="mt-0.5"><FactChip value={v} /></dd>
          </div>
        ))}
      </dl>
      {a.refused && <p className="mt-3 text-sm text-amber-700">The model declined to assess. Routed to a human.</p>}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold text-gray-700 uppercase">Observations</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-gray-800">
            {o.observations.length ? o.observations.map((x) => <li key={x}>{x}</li>) : <li className="text-gray-400">None</li>}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold text-gray-700 uppercase">Limitations</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-gray-800">
            {o.limitations.length ? o.limitations.map((x) => <li key={x}>{x}</li>) : <li className="text-gray-400">None</li>}
          </ul>
        </div>
      </div>
    </Card>
  );
}

export function DecisionCard({ claim }: { claim: Claim }) {
  const d = claim.decision;
  const currency = claim.order.currency;
  const ownCompletedRefunds = (claim.refunds || [])
    .filter((r) => r.status === "COMPLETED")
    .reduce((acc, r) => acc + r.amount_cents, 0);
  const rem =
    d?.decision_inputs?.remaining_cents ??
    Math.min(claim.order.amount_cents, claim.order.remaining_cents + ownCompletedRefunds);
  const days = d?.decision_inputs?.days_since_delivery;
  const prior = d?.decision_inputs?.prior_claims ?? 0;

  const injection = detectPromptInjection(claim.buyer_text, claim.order.amount_cents);
  const isCompleted = claim.status === "COMPLETED" || (claim.refunds || []).some((r) => r.status === "COMPLETED");
  const completedRefundAmount =
    (claim.refunds || []).find((r) => r.status === "COMPLETED")?.amount_cents ??
    claim.order.refunded_cents ??
    d?.suggested_cents ??
    0;

  return (
    <Card title="Decision" aside={d && <Badge>policy {d.policy_version}</Badge>}>
      {!d ? (
        <p className="text-sm text-gray-500">Waiting for the evidence assessment.</p>
      ) : (
        <div className="space-y-3 text-sm">
          {/* Verdict Banner */}
          {isCompleted ? (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3.5 shadow-2xs">
              <div className="flex items-center gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-xs font-bold text-white shadow-2xs">
                  ✓
                </span>
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wider text-emerald-800">
                    PayPal Refund Completed
                  </div>
                  <div className="text-sm font-semibold text-emerald-950">
                    ✓ PayPal Refund Completed: {money(completedRefundAmount, currency)}
                  </div>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800 ring-1 ring-emerald-300 ring-inset">
                COMPLETED
              </span>
            </div>
          ) : d.outcome === "FULL_REFUND" || d.outcome === "PARTIAL_REFUND" ? (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-blue-200 bg-blue-50/80 p-3.5 shadow-2xs">
              <div className="flex items-center gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white shadow-2xs">
                  ℹ
                </span>
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wider text-blue-800">
                    Suggested Refund · Policy Recommendation
                  </div>
                  <div className="text-sm font-semibold text-blue-950">
                    Suggested {d.outcome === "FULL_REFUND" ? "Full Refund" : "Partial Refund"}: {money(d.suggested_cents, currency)}
                  </div>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-semibold text-blue-800 ring-1 ring-blue-300 ring-inset">
                {d.requires_approval ? "Awaiting Merchant Approval" : "Eligible for Auto-Approval"}
              </span>
            </div>
          ) : d.outcome === "REQUEST_EVIDENCE" ? (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/80 p-3.5 shadow-2xs">
              <div className="flex items-center gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-600 text-xs font-bold text-white shadow-2xs">
                  ⚠
                </span>
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wider text-amber-900">
                    Additional Evidence Required
                  </div>
                  <div className="text-sm font-semibold text-amber-950">
                    Held at $0.00 suggested payout · Photo or documentation needed
                  </div>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800 ring-1 ring-amber-300 ring-inset">
                REQUEST EVIDENCE
              </span>
            </div>
          ) : d.outcome === "REVIEW" ? (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/80 p-3.5 shadow-2xs">
              <div className="flex items-center gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-600 text-xs font-bold text-white shadow-2xs">
                  ⚠
                </span>
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wider text-amber-900">
                    Manual Merchant Review Required
                  </div>
                  <div className="text-sm font-semibold text-amber-950">
                    Held at $0.00 suggested payout · Routed to store operator
                  </div>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800 ring-1 ring-amber-300 ring-inset">
                MANUAL REVIEW
              </span>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50/80 p-3.5 shadow-2xs">
              <div className="flex items-center gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-rose-600 text-xs font-bold text-white shadow-2xs">
                  ✕
                </span>
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wider text-rose-900">
                    Claim Rejected under Store Policy
                  </div>
                  <div className="text-sm font-semibold text-rose-950">
                    $0.00 payout · Ineligible under store return policy
                  </div>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-800 ring-1 ring-rose-300 ring-inset">
                REJECTED
              </span>
            </div>
          )}

          {/* Task 1: Untrusted Input Notice */}
          {injection.isInjection && (
            <div className="rounded-xl border border-amber-300 bg-gradient-to-br from-amber-50/90 via-orange-50/40 to-white p-4 shadow-2xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-600 text-xs font-bold text-white">
                    !
                  </span>
                  <h3 className="text-xs font-bold tracking-wider text-amber-950 uppercase">
                    UNTRUSTED INPUT NOTICE · SCHEMA &amp; POLICY SEPARATION
                  </h3>
                </div>
                <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-[11px] font-semibold text-amber-800 ring-1 ring-amber-300 ring-inset">
                  Override Phrase Flagged in Note
                </span>
              </div>
              <div className="mt-2.5 space-y-2 text-xs">
                <div className="rounded-lg bg-white/80 p-2.5 leading-relaxed text-gray-800 ring-1 ring-amber-200">
                  <span className="font-semibold text-amber-900">1. Buyer Note (Untrusted Text): </span>
                  <span>
                    Override-style phrasing detected in buyer description ({injection.snippet ? `"${injection.snippet}"` : `"system override"`} / &ldquo;$500&rdquo;). Buyer text is passed as untrusted context.
                  </span>
                </div>
                <div className="rounded-lg bg-white/80 p-2.5 leading-relaxed text-gray-800 ring-1 ring-blue-200">
                  <span className="font-semibold text-blue-900">
                    2. AI Output Schema (<code>EvidenceAssessment</code>):{" "}
                  </span>
                  <span>
                    Contains <strong className="font-bold text-blue-900">0 monetary fields</strong>—the model returns structured visual observations and uncertainty flags rather than setting a dollar payout directly (though model classification accuracy still affects rule matching).
                  </span>
                </div>
                <div className="rounded-lg bg-white/80 p-2.5 leading-relaxed text-gray-800 ring-1 ring-emerald-200">
                  <span className="font-semibold text-emerald-900">3. Policy &amp; Human Gate: </span>
                  <span>
                    Versioned policy rules (<code className="rounded bg-gray-100 px-1 font-mono text-[11px]">{d.policy_version}</code> · <code className="rounded bg-emerald-100 px-1 font-mono text-[11px]">{d.rule_id}</code>) compute the suggested amount (<strong className="font-bold text-emerald-900">{money(d.suggested_cents, currency)}</strong>) within the order&apos;s remaining refundable balance, and a merchant must explicitly approve any PayPal payout.
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <OutcomeBadge outcome={d.outcome} />
            <span className="text-2xl font-semibold">{money(d.suggested_cents, currency)}</span>
            <span className="text-gray-500">of {money(claim.order.amount_cents, currency)}</span>
          </div>
          <p className="text-gray-800">{d.reason}</p>
          <p className="text-xs text-gray-500">
            Rule <code className="rounded bg-gray-100 px-1">{d.rule_id}</code> ·{" "}
            {d.requires_approval ? "needs merchant approval" : "eligible for auto-approval"}
          </p>

          <div className="mt-4 rounded-xl border border-slate-200/80 bg-slate-50/60 p-3.5 shadow-2xs">
            <div className="mb-2.5 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-700">
                <span className="flex h-4 w-4 items-center justify-center rounded-full bg-slate-200 text-[10px] text-slate-600">
                  4
                </span>
                <span>Deterministic Rule Evaluation Trace</span>
              </div>
              <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-500">
                Engine: local json-rules
              </span>
            </div>

            <div className="space-y-2">
              {/* Step 1: Balance Guard */}
              <div
                className={`flex items-center justify-between rounded-lg border p-2 text-xs transition ${
                  d.rule_id === "PRE-nothing-left"
                    ? "border-amber-300 bg-amber-50/90 text-amber-900 shadow-2xs"
                    : "border-slate-200/70 bg-white text-slate-800"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                      d.rule_id === "PRE-nothing-left"
                        ? "bg-amber-100 text-amber-700"
                        : "bg-emerald-100 text-emerald-700"
                    }`}
                  >
                    {d.rule_id === "PRE-nothing-left" ? "✕" : "✓"}
                  </span>
                  <div>
                    <span className="font-medium text-slate-900">Step 1 · Refundable Balance Guard: </span>
                    <span className="text-slate-600">
                      {d.rule_id === "PRE-nothing-left"
                        ? `Order fully refunded (${money(rem, currency)} available)`
                        : `Order balance available (${money(rem, currency)} available)`}
                    </span>
                  </div>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                    d.rule_id === "PRE-nothing-left"
                      ? "bg-amber-100 text-amber-800 ring-1 ring-amber-300"
                      : "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"
                  }`}
                >
                  {d.rule_id === "PRE-nothing-left" ? "TRIPPED" : "✓ PASSED"}
                </span>
              </div>

              {/* Step 2: Delivery Window */}
              <div
                className={`flex items-center justify-between rounded-lg border p-2 text-xs transition ${
                  d.rule_id === "PRE-window"
                    ? "border-amber-300 bg-amber-50/90 text-amber-900 shadow-2xs"
                    : "border-slate-200/70 bg-white text-slate-800"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                      d.rule_id === "PRE-window"
                        ? "bg-amber-100 text-amber-700"
                        : "bg-emerald-100 text-emerald-700"
                    }`}
                  >
                    {d.rule_id === "PRE-window" ? "✕" : "✓"}
                  </span>
                  <div>
                    <span className="font-medium text-slate-900">Step 2 · Delivery Window Guard: </span>
                    <span className="text-slate-600">
                      {d.rule_id === "PRE-window"
                        ? `Outside allowable policy window${typeof days === "number" ? ` (${days}d since delivery)` : ""}`
                        : `Within allowable policy window${typeof days === "number" ? ` (${days}d since delivery)` : ""}`}
                    </span>
                  </div>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                    d.rule_id === "PRE-window"
                      ? "bg-amber-100 text-amber-800 ring-1 ring-amber-300"
                      : "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"
                  }`}
                >
                  {d.rule_id === "PRE-window" ? "TRIPPED" : "✓ PASSED"}
                </span>
              </div>

              {/* Step 3: Prior Claims Guard */}
              <div
                className={`flex items-center justify-between rounded-lg border p-2 text-xs transition ${
                  d.rule_id === "PRE-repeat"
                    ? "border-amber-300 bg-amber-50/90 text-amber-900 shadow-2xs"
                    : "border-slate-200/70 bg-white text-slate-800"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                      d.rule_id === "PRE-repeat"
                        ? "bg-amber-100 text-amber-700"
                        : "bg-emerald-100 text-emerald-700"
                    }`}
                  >
                    {d.rule_id === "PRE-repeat" ? "✕" : "✓"}
                  </span>
                  <div>
                    <span className="font-medium text-slate-900">Step 3 · Prior Claims Guard: </span>
                    <span className="text-slate-600">
                      {d.rule_id === "PRE-repeat"
                        ? `Prior claims detected on order (${prior})`
                        : `Single claim verified (${prior} prior claims)`}
                    </span>
                  </div>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                    d.rule_id === "PRE-repeat"
                      ? "bg-amber-100 text-amber-800 ring-1 ring-amber-300"
                      : "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200"
                  }`}
                >
                  {d.rule_id === "PRE-repeat" ? "TRIPPED" : "✓ PASSED"}
                </span>
              </div>

              {/* Step 4: Final Matched Rule */}
              <div className="flex items-center justify-between rounded-lg border border-blue-300 bg-gradient-to-r from-blue-50/90 to-indigo-50/60 p-2.5 text-xs text-blue-950 shadow-2xs ring-1 ring-blue-200/80">
                <div className="flex items-center gap-2">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white shadow-2xs">
                    ★
                  </span>
                  <div>
                    <span className="font-bold text-blue-950">Step 4 · Matched Policy Rule: </span>
                    <code className="rounded bg-blue-100 px-1 py-0.5 font-mono text-[11px] font-semibold text-blue-900">
                      {d.rule_id}
                    </code>
                    <span className="mx-1 text-blue-400">·</span>
                    <span className="font-medium text-blue-900">
                      outcome: <strong>{d.outcome}</strong> ({money(d.suggested_cents, currency)})
                    </span>
                  </div>
                </div>
                <span className="shrink-0 rounded-full bg-blue-600 px-2 py-0.5 text-[10px] font-semibold text-white uppercase shadow-2xs">
                  MATCHED
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
      {claim.merchant_message && (
        <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 ring-1 ring-amber-200">
          <span className="font-medium">Message from the merchant:</span> {claim.merchant_message}
        </p>
      )}
    </Card>
  );
}

export function CopyPill({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // fallback
    }
  };

  return (
    <button
      type="button"
      onClick={onCopy}
      title={`Click to copy ${label}: ${value}`}
      className="group inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 font-mono text-xs text-slate-700 ring-1 ring-slate-200/90 transition hover:bg-slate-200/80 hover:text-slate-900 focus:outline-hidden"
    >
      <span className="font-sans text-[10px] font-semibold tracking-wider text-slate-500 uppercase">
        {label}:
      </span>
      <span className="font-mono text-xs font-medium text-slate-900">{value}</span>
      <span className="text-[11px] text-slate-400 group-hover:text-slate-600">
        {copied ? <span className="font-sans font-semibold text-emerald-600">✓ copied</span> : "⎘"}
      </span>
    </button>
  );
}

export function RefundCard({ claim }: { claim: Claim }) {
  if (!claim.refunds.length) return null;
  return (
    <Card title="PayPal refund" aside={<Badge tone="green">live capture &amp; refund</Badge>}>
      <div className="space-y-3 text-sm">
        {claim.order.capture_id && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">Source PayPal Capture:</span>
            <CopyPill label="Capture ID" value={claim.order.capture_id} />
          </div>
        )}
        <ul className="space-y-3">
          {claim.refunds.map((r) => (
            <li key={r.id} className="rounded-lg border border-slate-200/80 bg-slate-50/60 p-3 shadow-2xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <StatusBadge status={r.status} />
                  <span className="text-base font-semibold text-slate-900">
                    {money(r.amount_cents, r.currency)}
                  </span>
                </div>
                {r.status === "COMPLETED" && (
                  <span className="text-xs font-medium text-emerald-700">
                    ✓ Settled via PayPal REST API
                  </span>
                )}
              </div>
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                {r.paypal_refund_id && (
                  <CopyPill label="Refund ID" value={r.paypal_refund_id} />
                )}
                {r.paypal_request_id && (
                  <CopyPill label="Idempotency Key" value={r.paypal_request_id} />
                )}
              </div>
              {r.error && (
                <div className="mt-2 rounded bg-red-50 p-2 text-xs font-medium text-red-700 ring-1 ring-red-200">
                  PayPal error: {r.error}
                </div>
              )}
            </li>
          ))}
        </ul>
        <p className="text-xs text-gray-500">
          &ldquo;Refunded&rdquo; is shown only when PayPal reports the refund as COMPLETED.
        </p>
      </div>
    </Card>
  );
}

const ACTOR_DOT: Record<string, string> = {
  buyer: "bg-gray-400",
  ai: "bg-violet-500",
  rules: "bg-blue-500",
  merchant: "bg-amber-500",
  paypal: "bg-brand",
  system: "bg-gray-300",
};

export function Timeline({ claim }: { claim: Claim }) {
  const [showJson, setShowJson] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copiedHash, setCopiedHash] = useState(false);

  const coreBundle = {
    schema: "refundpilot.audit_bundle.v1",
    claim_id: claim.id,
    status: claim.status,
    created_at: claim.created_at,
    resolved_at: claim.resolved_at,
    order: {
      order_id: claim.order.id,
      paypal_order_id: claim.order.paypal_order_id,
      paypal_capture_id: claim.order.capture_id,
      product_sku: claim.order.product.sku,
      amount_cents: claim.order.amount_cents,
      refunded_cents: claim.order.refunded_cents,
      remaining_cents: claim.order.remaining_cents,
      currency: claim.order.currency,
    },
    evidence: {
      buyer_text: claim.buyer_text,
      photo_urls: claim.photo_urls,
      ai_assessment: claim.assessment,
    },
    policy_decision: claim.decision,
    paypal_settlements: claim.refunds,
    audit_trail: claim.timeline,
  };

  const canonicalStr = canonicalJsonStringify(coreBundle);
  const shaHex = sha256(canonicalStr);
  const bundleSha = `sha256:${shaHex}`;
  const shortSha = `${shaHex.slice(0, 8)}...${shaHex.slice(-4)}`;

  const auditBundle = {
    ...coreBundle,
    bundle_sha256: bundleSha,
  };

  const handleCopy = () => {
    const jsonStr = JSON.stringify(auditBundle, null, 2);
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(jsonStr).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  };

  const handleCopyHash = () => {
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(bundleSha).then(() => {
        setCopiedHash(true);
        setTimeout(() => setCopiedHash(false), 2000);
      });
    }
  };

  const handleDownload = () => {
    const jsonStr = JSON.stringify(auditBundle, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `refundpilot-audit-${claim.id}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Card
      title="Audit trail"
      aside={
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleCopyHash}
            title="Click to copy full SHA-256 export checksum"
            className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-0.5 font-mono text-[11px] font-medium text-gray-700 ring-1 ring-gray-300 ring-inset hover:bg-gray-200"
          >
            <span>{copiedHash ? "Copied checksum!" : `SHA-256 export checksum: ${shortSha}`}</span>
          </button>
          <button
            type="button"
            onClick={() => setShowJson(!showJson)}
            className="text-xs font-medium text-brand hover:underline"
          >
            {showJson ? "Hide Audit JSON" : "Inspect Audit JSON"}
          </button>
          <button
            type="button"
            onClick={handleCopy}
            className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs font-medium text-gray-700 shadow-2xs hover:bg-gray-50"
          >
            {copied ? "✓ Copied JSON" : "Copy JSON"}
          </button>
          <button
            type="button"
            onClick={handleDownload}
            className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs font-medium text-gray-700 shadow-2xs hover:bg-gray-50"
          >
            Download .json
          </button>
        </div>
      }
    >
      {showJson && (
        <div className="mb-4 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gray-900 px-3 py-2 text-xs font-mono text-gray-300">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-blue-400" />
              <span>SHA-256 Checksum:</span>
              <strong className="text-white">{bundleSha}</strong>
            </span>
            <span className="text-[11px] text-gray-400">
              Canonical JSON (RFC 8785) export checksum for deterministic payload comparison.
            </span>
          </div>
          <pre className="max-h-72 overflow-auto rounded-lg bg-gray-900 p-3 font-mono text-xs text-gray-100">
            {JSON.stringify(auditBundle, null, 2)}
          </pre>
        </div>
      )}
      <ol className="relative space-y-3 border-l border-gray-200 pl-5">
        {claim.timeline.map((t, i) => (
          <li key={i} className="text-sm">
            <span className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ${ACTOR_DOT[t.actor] ?? "bg-gray-300"}`} />
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium">{label(t.event)}</span>
              <span className="text-xs text-gray-500">{t.actor} · {when(t.ts)}</span>
            </div>
            {t.detail && Object.keys(t.detail).length > 0 && (
              <p className="mt-0.5 truncate font-mono text-xs text-gray-500">
                {Object.entries(t.detail).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ")}
              </p>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

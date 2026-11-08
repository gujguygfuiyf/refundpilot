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

  return (
    <Card title="Decision" aside={d && <Badge>policy {d.policy_version}</Badge>}>
      {!d ? (
        <p className="text-sm text-gray-500">Waiting for the evidence assessment.</p>
      ) : (
        <div className="space-y-3 text-sm">
          {/* Task 1: Security Boundary Callout */}
          {injection.isInjection && (
            <div className="rounded-xl border-2 border-red-300 bg-gradient-to-br from-red-50/90 via-amber-50/60 to-white p-4 shadow-2xs">
              <div className="flex items-center gap-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-red-600 text-xs font-bold text-white">
                  🛡
                </span>
                <h3 className="text-xs font-bold tracking-wider text-red-950 uppercase">
                  Security Boundary Callout · Adversarial Injection Defended
                </h3>
                <span className="ml-auto inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold text-red-800 ring-1 ring-red-300 ring-inset">
                  Prompt Injection Neutralized
                </span>
              </div>
              <div className="mt-2.5 space-y-2 text-xs">
                <div className="flex items-start gap-2 rounded-lg bg-white/80 p-2 ring-1 ring-red-200">
                  <span className="shrink-0 font-semibold text-red-900">1. Untrusted Buyer Input:</span>
                  <span className="text-gray-800">
                    Detected override attempt in buyer note ({injection.snippet ? `"${injection.snippet}"` : `"approve $500"`}) — treated strictly as untrusted unstructured text.
                  </span>
                </div>
                <div className="flex items-start gap-2 rounded-lg bg-white/80 p-2 ring-1 ring-blue-200">
                  <span className="shrink-0 font-semibold text-blue-900">
                    2. AI Assessment Schema (<code>EvidenceAssessment</code>):
                  </span>
                  <span className="text-gray-800">
                    <strong className="text-blue-900">0 monetary fields</strong> — structurally incapable of outputting, parsing, or overriding refund dollar amounts.
                  </span>
                </div>
                <div className="flex items-start gap-2 rounded-lg bg-white/80 p-2 ring-1 ring-emerald-200">
                  <span className="shrink-0 font-semibold text-emerald-900">3. Deterministic Policy Output:</span>
                  <span className="text-gray-800">
                    <strong className="text-emerald-900">{money(d.suggested_cents, currency)}</strong> (Strictly bounded by remaining order balance &amp; matched rule{" "}
                    <code className="rounded bg-emerald-100 px-1">{d.rule_id}</code>).
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

          <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50/70 p-3 text-xs">
            <div className="mb-2 font-medium text-gray-700">Rule evaluation trace</div>
            <ul className="space-y-1.5 font-mono">
              <li className="flex items-start gap-1.5">
                {d.rule_id === "PRE-nothing-left" ? (
                  <>
                    <span className="font-bold text-amber-600">✕</span>
                    <span className="text-amber-800">
                      Balance guard tripped: order already fully refunded ({money(rem, currency)} available)
                    </span>
                  </>
                ) : (
                  <>
                    <span className="font-semibold text-emerald-600">✓</span>
                    <span className="text-gray-700">
                      Refundable balance check passed ({money(rem, currency)} available)
                    </span>
                  </>
                )}
              </li>
              <li className="flex items-start gap-1.5">
                {d.rule_id === "PRE-window" ? (
                  <>
                    <span className="font-bold text-amber-600">✕</span>
                    <span className="text-amber-800">
                      Report window guard tripped: reported outside allowable window{typeof days === "number" ? ` (${days}d since delivery)` : ""}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="font-semibold text-emerald-600">✓</span>
                    <span className="text-gray-700">
                      Delivery window check passed{typeof days === "number" ? ` (${days}d since delivery)` : ""}
                    </span>
                  </>
                )}
              </li>
              <li className="flex items-start gap-1.5">
                {d.rule_id === "PRE-repeat" ? (
                  <>
                    <span className="font-bold text-amber-600">✕</span>
                    <span className="text-amber-800">
                      Repeat-claim guard tripped: prior claims detected ({prior})
                    </span>
                  </>
                ) : (
                  <>
                    <span className="font-semibold text-emerald-600">✓</span>
                    <span className="text-gray-700">
                      Single-claim guard passed (prior claims: {prior})
                    </span>
                  </>
                )}
              </li>
              <li className="flex items-start gap-1.5 rounded bg-blue-50/90 px-1.5 py-1 font-medium text-blue-950 ring-1 ring-blue-200/80">
                <span className="font-bold text-blue-600">●</span>
                <span>
                  Matched {d.rule_id} under {d.policy_version} &rarr; {d.outcome} ({money(d.suggested_cents, currency)})
                </span>
              </li>
            </ul>
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

export function RefundCard({ claim }: { claim: Claim }) {
  if (!claim.refunds.length) return null;
  return (
    <Card title="PayPal refund">
      <ul className="space-y-3 text-sm">
        {claim.refunds.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-3">
            <StatusBadge status={r.status} />
            <span className="font-medium">{money(r.amount_cents, r.currency)}</span>
            {r.paypal_refund_id && <code className="rounded bg-gray-100 px-1 text-xs">refund {r.paypal_refund_id}</code>}
            <code className="rounded bg-gray-100 px-1 text-xs text-gray-500">{r.paypal_request_id}</code>
            {r.error && <span className="text-xs text-red-700">{r.error}</span>}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-gray-500">&ldquo;Refunded&rdquo; is shown only when PayPal reports the refund as COMPLETED.</p>
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
            title="Click to copy full SHA-256 tamper-evident digest"
            className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 font-mono text-[11px] font-medium text-emerald-800 ring-1 ring-emerald-300 ring-inset hover:bg-emerald-100"
          >
            <span className="font-bold text-emerald-600">✓</span>
            <span>{copiedHash ? "Copied SHA-256!" : `SHA-256: ${shortSha}`}</span>
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
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-emerald-950 px-3 py-2 text-xs font-mono text-emerald-300">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-400" />
              <span>Tamper-evident Digest:</span>
              <strong className="text-white">{bundleSha}</strong>
            </span>
            <span className="text-[11px] text-emerald-400">Canonical key order verified</span>
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

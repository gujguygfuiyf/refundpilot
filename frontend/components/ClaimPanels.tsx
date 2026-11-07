"use client";

import type { Claim } from "@/lib/types";
import { photoUrl } from "@/lib/api";
import { label, money, when } from "@/lib/format";
import { Badge, Card, FactChip, OutcomeBadge, StatusBadge } from "./ui";

export function Photos({ claim }: { claim: Claim }) {
  return (
    <div className="grid grid-cols-4 gap-3">
      <figure>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={claim.order.product.image_url} alt="Product reference" className="aspect-square w-full rounded-lg object-cover ring-1 ring-gray-200" />
        <figcaption className="mt-1 text-xs text-gray-500">Listing photo</figcaption>
      </figure>
      {claim.photo_urls.map((u, i) => (
        <figure key={u}>
          <a href={photoUrl(u)} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={photoUrl(u)} alt={`Buyer photo ${i + 1}`} className="aspect-square w-full rounded-lg object-cover ring-1 ring-gray-200" />
          </a>
          <figcaption className="mt-1 text-xs text-gray-500">Buyer photo {i + 1}</figcaption>
        </figure>
      ))}
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
  const rem = d?.decision_inputs?.remaining_cents ?? claim.order.remaining_cents;
  const days = d?.decision_inputs?.days_since_delivery;
  const prior = d?.decision_inputs?.prior_claims ?? 0;

  return (
    <Card title="Decision" aside={d && <Badge>policy {d.policy_version}</Badge>}>
      {!d ? (
        <p className="text-sm text-gray-500">Waiting for the evidence assessment.</p>
      ) : (
        <div className="space-y-3 text-sm">
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
              <li className="flex items-start gap-1.5 rounded bg-blue-50/90 px-1.5 py-1 text-blue-950 font-medium ring-1 ring-blue-200/80">
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
  return (
    <Card title="Audit trail">
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

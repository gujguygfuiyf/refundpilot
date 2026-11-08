"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { ClaimRow, EvidenceAssessment, Policy, SimulationResult } from "@/lib/types";
import { label, money } from "@/lib/format";
import TokenGate from "@/components/TokenGate";
import { Badge, Button, Card, ErrorNote, OutcomeBadge, Spinner } from "@/components/ui";

const BASE: EvidenceAssessment = {
  product_match: "confirmed",
  damage_type: "broken",
  severity: "severe",
  visible_damage: true,
  evidence_quality: "sufficient",
  stock_photo_suspected: false,
  description_consistent: "yes",
  needs_human_review: false,
  confidence: 0.9,
  observations: [],
  limitations: [],
};

const PRESETS: Record<string, Partial<EvidenceAssessment>> = {
  "Broken on arrival": {},
  "Cosmetic scratch": { damage_type: "cosmetic", severity: "minor" },
  "Different product in photo": { product_match: "mismatch", damage_type: "broken" },
  "Blurry photo": { evidence_quality: "unclear", damage_type: "other", severity: "none", visible_damage: false },
  "Looks like a stock image": { stock_photo_suspected: true, needs_human_review: true },
};

interface BatchCase {
  id: string;
  title: string;
  sourceLabel: string;
  order_cents: number;
  remaining_cents: number;
  days_since_delivery: number;
  prior_claims: number;
  assessment: EvidenceAssessment;
}

const REPRESENTATIVE_BATCH: BatchCase[] = [
  {
    id: "rep-1",
    title: "Desk Lamp · Severe broken arm ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    remaining_cents: 4900,
    days_since_delivery: 3,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "broken", severity: "severe" },
  },
  {
    id: "rep-2",
    title: "Desk Lamp · Moderate cosmetic scuff ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    remaining_cents: 4900,
    days_since_delivery: 4,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "cosmetic", severity: "moderate" },
  },
  {
    id: "rep-3",
    title: "Ceramic Mug · Minor surface scratch ($19.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 1900,
    remaining_cents: 1900,
    days_since_delivery: 2,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "cosmetic", severity: "minor" },
  },
  {
    id: "rep-4",
    title: "Headphones · Reported at day 10 ($79.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 7900,
    remaining_cents: 7900,
    days_since_delivery: 10,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "broken", severity: "severe" },
  },
  {
    id: "rep-5",
    title: "Desk Lamp · Blurry photo ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    remaining_cents: 4900,
    days_since_delivery: 3,
    prior_claims: 0,
    assessment: { ...BASE, evidence_quality: "unclear", damage_type: "other", severity: "none", visible_damage: false },
  },
  {
    id: "rep-6",
    title: "Desk Lamp · Suspected reused screenshot ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    remaining_cents: 4900,
    days_since_delivery: 2,
    prior_claims: 0,
    assessment: { ...BASE, stock_photo_suspected: true, needs_human_review: true },
  },
];

function daysBetweenIso(deliveredIso: string, createdIso: string): number {
  const a = new Date(deliveredIso).getTime();
  const b = new Date(createdIso).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return 3;
  return Math.max(0, Math.floor((b - a) / 86400000));
}

const ENUMS: Record<string, string[]> = {
  product_match: ["confirmed", "likely", "unlikely", "mismatch", "cannot_tell"],
  damage_type: ["none_visible", "cosmetic", "broken", "shattered", "missing_part", "wrong_item", "not_as_described", "other"],
  severity: ["none", "minor", "moderate", "severe"],
  evidence_quality: ["sufficient", "insufficient", "unclear"],
  description_consistent: ["yes", "partly", "no", "cannot_tell"],
};
const BOOLS = ["visible_damage", "stock_photo_suspected", "needs_human_review"] as const;

function Simulator() {
  const params = useSearchParams();
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [claims, setClaims] = useState<ClaimRow[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState<string>("");
  const [a, setA] = useState<EvidenceAssessment>(BASE);
  const [orderCents, setOrderCents] = useState(4900);
  const [days, setDays] = useState(3);
  const [prior, setPrior] = useState(0);
  const [results, setResults] = useState<Record<string, SimulationResult>>({});
  const [showJson, setShowJson] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Batch replay state
  const [batchSource, setBatchSource] = useState<"recorded" | "benchmark">("benchmark");
  const [batchCases, setBatchCases] = useState<BatchCase[]>(REPRESENTATIVE_BATCH);
  const [batchMatrix, setBatchMatrix] = useState<Record<string, Record<string, SimulationResult>>>({});
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  const loadClaim = useCallback(async (id: string) => {
    try {
      setSelectedClaimId(id);
      const c = await api.merchantClaim(id);
      if (c.assessment) setA(c.assessment.output);
      const snap = c.decision?.decision_inputs;
      setOrderCents(typeof snap?.order_cents === "number" ? snap.order_cents : c.order.amount_cents);
      setDays(
        typeof snap?.days_since_delivery === "number"
          ? snap.days_since_delivery
          : daysBetweenIso(c.order.delivered_at, c.created_at),
      );
      if (typeof snap?.prior_claims === "number") setPrior(snap.prior_claims);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    api.policies().then(setPolicies).catch((e) => setError(e.message));
    api
      .merchantClaims()
      .then((r) => {
        const assessed = r.filter((c) => Boolean(c.damage_type || c.outcome));
        const nonCancelled = assessed.filter((c) => c.status !== "CANCELLED");
        setClaims(nonCancelled.length > 0 ? nonCancelled : assessed);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const cid = params.get("claim");
    if (cid) loadClaim(cid);
  }, [params, loadClaim]);

  useEffect(() => {
    if (!policies.length) return;
    const t = setTimeout(() => {
      Promise.all(
        policies.map((p) =>
          api.simulate({ policy_version: p.version, assessment: a, order_cents: orderCents, days_since_delivery: days, prior_claims: prior }),
        ),
      )
        .then((rs) => {
          setResults(Object.fromEntries(rs.map((r) => [r.policy_version, r])));
          setError(null);
        })
        .catch((e) => setError(e.message));
    }, 150);
    return () => clearTimeout(t);
  }, [policies, a, orderCents, days, prior]);

  const runBatchReplay = useCallback(async () => {
    if (!policies.length) return;
    setBatchBusy(true);
    setBatchError(null);
    setBatchMatrix({});

    try {
      let casesToRun: BatchCase[] = REPRESENTATIVE_BATCH;

      if (batchSource === "recorded") {
        const allRows = await api.merchantClaims();
        const assessedAll = allRows.filter((c) => Boolean(c.damage_type || c.outcome));
        const nonCancelled = assessedAll.filter((c) => c.status !== "CANCELLED");
        const assessedRows = nonCancelled.length > 0 ? nonCancelled : assessedAll;
        setClaims(assessedRows);
        if (assessedRows.length === 0) {
          throw new Error("No recorded claims with completed AI assessments were found.");
        }
        // Chronological order per order_id to reconstruct prior_claims when legacy claims lack decision_inputs
        const sortedChron = [...allRows].sort(
          (x, y) => new Date(x.created_at).getTime() - new Date(y.created_at).getTime(),
        );
        const fullClaims = await Promise.all(assessedRows.slice(0, 8).map((c) => api.merchantClaim(c.id)));
        const withAssessment = fullClaims.filter((fc) => fc.assessment);
        if (withAssessment.length === 0) {
          throw new Error("Could not load AI assessments for the recorded claims.");
        }

        casesToRun = withAssessment.map((fc) => {
          const snap = fc.decision?.decision_inputs;
          const isCancelled = fc.status === "CANCELLED";
          const d =
            typeof snap?.days_since_delivery === "number"
              ? snap.days_since_delivery
              : daysBetweenIso(fc.order.delivered_at, fc.created_at);
          const priorCount =
            typeof snap?.prior_claims === "number"
              ? snap.prior_claims
              : sortedChron.filter(
                  (r) =>
                    r.order_id === fc.order.id &&
                    r.id !== fc.id &&
                    r.status !== "CANCELLED" &&
                    new Date(r.created_at).getTime() < new Date(fc.created_at).getTime(),
                ).length;
          const ownCompletedRefunds = (fc.refunds || [])
            .filter((rf) => rf.status === "COMPLETED")
            .reduce((acc, rf) => acc + rf.amount_cents, 0);
          const remainingAtDecision =
            typeof snap?.remaining_cents === "number"
              ? snap.remaining_cents
              : Math.min(fc.order.amount_cents, fc.order.remaining_cents + ownCompletedRefunds);
          const orderCentsAtDecision =
            typeof snap?.order_cents === "number" ? snap.order_cents : fc.order.amount_cents;
          const hasExactSnapshot = typeof snap?.remaining_cents === "number";

          return {
            id: fc.id,
            title: `${fc.order.product.name} · Order #${fc.order.id} (${money(orderCentsAtDecision)})`,
            sourceLabel: hasExactSnapshot
              ? `Recorded claim ${fc.id.slice(-6)} (exact snapshot)`
              : isCancelled
                ? `Recorded claim ${fc.id.slice(-6)} (withdrawn · reconstructed)`
                : `Recorded claim ${fc.id.slice(-6)} (reconstructed)`,
            order_cents: orderCentsAtDecision,
            remaining_cents: remainingAtDecision,
            days_since_delivery: d,
            prior_claims: priorCount,
            assessment: fc.assessment!.output,
          };
        });
      }

      setBatchCases(casesToRun);

      const entries = await Promise.all(
        casesToRun.map(async (bc) => {
          const resList = await Promise.all(
            policies.map((p) =>
              api.simulate({
                policy_version: p.version,
                assessment: bc.assessment,
                order_cents: bc.order_cents,
                remaining_cents: bc.remaining_cents,
                days_since_delivery: bc.days_since_delivery,
                prior_claims: bc.prior_claims,
              }),
            ),
          );
          return [bc.id, Object.fromEntries(resList.map((r) => [r.policy_version, r]))] as const;
        }),
      );

      setBatchMatrix(Object.fromEntries(entries));
    } catch (e) {
      setBatchError(`Batch replay failed: ${(e as Error).message}`);
      setBatchMatrix({});
    } finally {
      setBatchBusy(false);
    }
  }, [policies, batchSource]);

  useEffect(() => {
    runBatchReplay();
  }, [runBatchReplay, retryCount]);

  // Verify every case x policy cell succeeded before displaying totals
  const batchComplete = useMemo(() => {
    if (batchBusy || batchError || !policies.length || !batchCases.length) return false;
    return batchCases.every((bc) => policies.every((p) => Boolean(batchMatrix[bc.id]?.[p.version])));
  }, [batchBusy, batchError, policies, batchCases, batchMatrix]);

  const batchSummary = useMemo(() => {
    if (!batchComplete) return [];
    const basePolicyVer = policies[0]?.version;
    const items = policies.map((p) => {
      let totalSuggested = 0;
      let reviewOrEvidenceCount = 0;
      let rejectCount = 0;
      let changedVsFirst = 0;

      for (const bc of batchCases) {
        const r = batchMatrix[bc.id]?.[p.version];
        const rBase = basePolicyVer ? batchMatrix[bc.id]?.[basePolicyVer] : undefined;
        if (!r) continue;
        totalSuggested += r.suggested_cents;
        if (r.outcome === "REVIEW" || r.outcome === "REQUEST_EVIDENCE") {
          reviewOrEvidenceCount += 1;
        }
        if (r.outcome === "REJECT") {
          rejectCount += 1;
        }
        if (rBase && (r.outcome !== rBase.outcome || r.suggested_cents !== rBase.suggested_cents)) {
          changedVsFirst += 1;
        }
      }
      const manualOpsCents = reviewOrEvidenceCount * 350; // $3.50 per manual review
      const disputeRiskCents = rejectCount * 1500; // $15.00 dispute escalation / chargeback fee risk
      const totalEffectiveCents = totalSuggested + manualOpsCents + disputeRiskCents;

      return {
        policy: p,
        totalSuggested,
        reviewRate: batchCases.length ? Math.round((reviewOrEvidenceCount / batchCases.length) * 100) : 0,
        reviewOrEvidenceCount,
        rejectCount,
        manualOpsCents,
        disputeRiskCents,
        totalEffectiveCents,
        changedVsFirst,
      };
    });

    const minEffective = items.length ? Math.min(...items.map((i) => i.totalEffectiveCents)) : 0;
    return items.map((i) => ({
      ...i,
      isLowestCost: i.totalEffectiveCents === minEffective,
    }));
  }, [batchComplete, policies, batchCases, batchMatrix]);

  const set = <K extends keyof EvidenceAssessment>(k: K, v: EvidenceAssessment[K]) => setA((x) => ({ ...x, [k]: v }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Policy simulator</h1>
        <p className="mt-1 text-sm text-gray-600">
          Same evidence, different store policies. <Badge tone="amber">Simulation only, no PayPal call</Badge>
        </p>
      </div>
      <ErrorNote error={error} />

      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Single-case evidence" className="lg:col-span-2">
          <div className="mb-4 flex flex-wrap gap-2">
            {Object.entries(PRESETS).map(([name, patch]) => (
              <button key={name} onClick={() => setA({ ...BASE, ...patch })}
                className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700 hover:bg-gray-200">
                {name}
              </button>
            ))}
          </div>
          {claims.length > 0 && (
            <select value={selectedClaimId} onChange={(e) => e.target.value && loadClaim(e.target.value)}
              className="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
              <option value="">Load the AI assessment of a real claim…</option>
              {claims.map((c) => (
                <option key={c.id} value={c.id}>{c.product_name} · {label(c.damage_type)} · {c.id.slice(-6)}</option>
              ))}
            </select>
          )}
          <div className="grid grid-cols-2 gap-3 text-sm">
            {Object.entries(ENUMS).map(([k, opts]) => (
              <label key={k}>
                <span className="block text-xs text-gray-500">{label(k)}</span>
                <select value={a[k as keyof EvidenceAssessment] as string}
                  onChange={(e) => set(k as keyof EvidenceAssessment, e.target.value as never)}
                  className="mt-1 w-full rounded-lg border border-gray-300 px-2 py-1.5">
                  {opts.map((o) => <option key={o} value={o}>{label(o)}</option>)}
                </select>
              </label>
            ))}
            {BOOLS.map((k) => (
              <label key={k} className="flex items-center gap-2 pt-4">
                <input type="checkbox" checked={a[k]} onChange={(e) => set(k, e.target.checked)} />
                <span className="text-xs text-gray-700">{label(k)}</span>
              </label>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3 border-t border-gray-100 pt-4 text-sm">
            <label>
              <span className="block text-xs text-gray-500">Order (USD)</span>
              <input type="number" min={1} step="0.01" value={orderCents / 100}
                onChange={(e) => setOrderCents(Math.max(1, Math.round(Number(e.target.value) * 100)))}
                className="mt-1 w-full rounded-lg border border-gray-300 px-2 py-1.5" />
            </label>
            <label>
              <span className="block text-xs text-gray-500">Days since delivery</span>
              <input type="number" min={0} value={days} onChange={(e) => setDays(Math.max(0, Number(e.target.value)))}
                className="mt-1 w-full rounded-lg border border-gray-300 px-2 py-1.5" />
            </label>
            <label>
              <span className="block text-xs text-gray-500">Prior claims</span>
              <input type="number" min={0} value={prior} onChange={(e) => setPrior(Math.max(0, Number(e.target.value)))}
                className="mt-1 w-full rounded-lg border border-gray-300 px-2 py-1.5" />
            </label>
          </div>
        </Card>

        <div className="grid gap-4 sm:grid-cols-3 lg:col-span-3">
          {policies.map((p) => {
            const r = results[p.version];
            return (
              <Card key={p.version} title={p.name}>
                {r ? (
                  <div className="space-y-3 text-sm">
                    <OutcomeBadge outcome={r.outcome} />
                    <p className="text-3xl font-semibold">{money(r.suggested_cents)}</p>
                    <p className="text-gray-700">{r.reason}</p>
                    <p className="text-xs text-gray-500">
                      Rule <code className="rounded bg-gray-100 px-1">{r.rule_id}</code>
                      <br />
                      {r.requires_approval ? "Needs merchant approval" : "Auto-approvable"}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-gray-400">…</p>
                )}
                <dl className="mt-4 space-y-1 border-t border-gray-100 pt-3 text-xs text-gray-600">
                  <div className="flex justify-between"><dt>Version</dt><dd><code>{p.version}</code></dd></div>
                  <div className="flex justify-between"><dt>Report window</dt><dd>{p.body.report_window_days} days</dd></div>
                  <div className="flex justify-between"><dt>Auto-approve below</dt><dd>{money(p.body.auto_approve_below_cents)}</dd></div>
                </dl>
                <button onClick={() => setShowJson(showJson === p.version ? null : p.version)} className="mt-3 text-xs text-brand hover:underline">
                  {showJson === p.version ? "Hide" : "Show"} policy JSON
                </button>
              </Card>
            );
          })}
        </div>
      </div>

      {showJson && (
        <Card title={`Policy ${showJson}`}>
          <pre className="max-h-96 overflow-auto rounded-lg bg-gray-900 p-4 text-xs text-gray-100">
            {JSON.stringify(policies.find((p) => p.version === showJson)?.body, null, 2)}
          </pre>
          <p className="mt-2 text-xs text-gray-500">Policies are versioned files in <code>backend/policies/</code>. Every decision records the version it used.</p>
        </Card>
      )}

      <Card
        title="Batch policy replay — How does switching policies affect my queue?"
        aside={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="amber">Simulated suggested amounts · no PayPal call</Badge>
            <div className="flex rounded-lg bg-gray-100 p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setBatchSource("benchmark")}
                className={`rounded-md px-2.5 py-1 transition ${
                  batchSource === "benchmark" ? "bg-white font-medium text-gray-900 shadow-2xs" : "text-gray-600 hover:text-gray-900"
                }`}
              >
                6 representative cases
              </button>
              <button
                type="button"
                onClick={() => setBatchSource("recorded")}
                disabled={claims.length === 0}
                className={`rounded-md px-2.5 py-1 transition disabled:opacity-40 ${
                  batchSource === "recorded" ? "bg-white font-medium text-gray-900 shadow-2xs" : "text-gray-600 hover:text-gray-900"
                }`}
              >
                Recorded claims ({Math.min(8, claims.length)})
              </button>
            </div>
          </div>
        }
      >
        <p className="mb-4 text-xs text-gray-600">
          Replays saved AI assessments across all three policies at once so a merchant can see the total suggested payout,
          manual-review / evidence-request rate, and exactly which cases change before adopting a policy.
          {" "}When replaying <strong>Recorded claims</strong>, each claim uses its actual <code>days_since_delivery</code> at submission time (<code>created_at − delivered_at</code>), actual prior claim count on that order, and pre-decision refundable balance.
        </p>

        {batchBusy && (
          <p className="mb-4 flex items-center gap-2 text-xs text-gray-500">
            <Spinner /> Computing batch policy replay across {policies.length || 3} policies…
          </p>
        )}

        {batchError && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-red-50 px-4 py-3 text-xs text-red-800 ring-1 ring-red-200">
            <span>{batchError}</span>
            <Button type="button" variant="secondary" onClick={() => setRetryCount((n) => n + 1)}>
              Retry batch replay
            </Button>
          </div>
        )}

        {batchComplete && (
          <>
            <div className="mb-3 flex items-center gap-2 text-xs text-gray-500">
              <span>Active sample source:</span>
              <Badge tone="blue">
                {batchSource === "benchmark"
                  ? "6 predefined representative scenarios"
                  : `${batchCases.length} recorded demo claims (actual claim-time inputs)`}
              </Badge>
            </div>

            <div className="mb-5 grid gap-4 sm:grid-cols-3">
              {batchSummary.map(
                (
                  {
                    policy: p,
                    totalSuggested,
                    reviewRate,
                    reviewOrEvidenceCount,
                    rejectCount,
                    manualOpsCents,
                    disputeRiskCents,
                    totalEffectiveCents,
                    changedVsFirst,
                    isLowestCost,
                  },
                  idx,
                ) => (
                  <div
                    key={p.version}
                    className={`rounded-xl border p-4 transition ${
                      isLowestCost
                        ? "border-emerald-300 bg-emerald-50/40 ring-1 ring-emerald-200"
                        : "border-gray-200 bg-gray-50/60"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="font-semibold text-gray-900">{p.name}</span>
                        {isLowestCost && (
                          <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800">
                            ★ Lowest Total Cost
                          </span>
                        )}
                      </div>
                      <code className="rounded bg-white px-1.5 py-0.5 text-xs text-gray-600 ring-1 ring-gray-200">
                        {p.version}
                      </code>
                    </div>

                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <div>
                        <span className="block text-[11px] uppercase text-gray-500">Direct Payout</span>
                        <span className="text-lg font-semibold text-gray-900">{money(totalSuggested)}</span>
                      </div>
                      <div>
                        <span className="block text-[11px] uppercase text-gray-500">Review Rate</span>
                        <span className="text-lg font-semibold text-gray-900">
                          {reviewRate}%{" "}
                          <span className="text-xs font-normal text-gray-500">
                            ({reviewOrEvidenceCount}/{batchCases.length})
                          </span>
                        </span>
                      </div>
                    </div>

                    {/* CFO Net Merchant Impact breakdown */}
                    <div className="mt-3 space-y-1 rounded-lg border border-gray-200/80 bg-white/90 p-2.5 text-xs">
                      <div className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-gray-500">
                        <span>CFO Net Merchant Cost</span>
                        <span className="text-xs font-bold text-gray-900">{money(totalEffectiveCents)}</span>
                      </div>
                      <div className="flex justify-between text-[11px] text-gray-600">
                        <span>• Direct refund payout:</span>
                        <span>{money(totalSuggested)}</span>
                      </div>
                      <div className="flex justify-between text-[11px] text-gray-600">
                        <span>• Ops labor ({reviewOrEvidenceCount} rev @ $3.50):</span>
                        <span className="text-amber-700">+{money(manualOpsCents)}</span>
                      </div>
                      <div className="flex justify-between text-[11px] text-gray-600">
                        <span>• Dispute risk ({rejectCount} rej @ $15.00):</span>
                        <span className={rejectCount > 0 ? "font-medium text-red-700" : "text-gray-400"}>
                          +{money(disputeRiskCents)}
                        </span>
                      </div>
                    </div>

                    <p className="mt-2 text-xs text-gray-500">
                      {idx === 0
                        ? "Baseline policy for comparison"
                        : `${changedVsFirst} of ${batchCases.length} cases change outcome or amount vs. ${policies[0]?.name}`}
                    </p>
                  </div>
                ),
              )}
            </div>

            <div className="mb-4 flex items-start gap-2.5 rounded-lg bg-blue-50/70 p-3 text-xs text-blue-900 ring-1 ring-blue-100">
              <span className="text-sm leading-none">💡</span>
              <div>
                <span className="font-semibold">CFO Net Merchant Impact Model:</span> Effective cost combines direct refund payouts + manual agent review labor ($3.50/case) + dispute escalation chargeback risk ($15.00/rejected claim). This models why ultra-strict rejection policies often generate hidden net losses through PayPal dispute fees and customer churn.
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-gray-200 text-gray-500 uppercase">
                    <th className="py-2 pr-4 font-medium">Case &amp; claim-time inputs</th>
                    {policies.map((p) => (
                      <th key={p.version} className="py-2 px-3 font-medium">
                        {p.name} (<code className="lowercase">{p.version}</code>)
                      </th>
                    ))}
                    <th className="py-2 pl-3 font-medium">Policy diff</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {batchCases.map((bc) => {
                    const rowRes = policies.map((p) => batchMatrix[bc.id]?.[p.version]);
                    const allSame =
                      rowRes.every((r) => r && r.outcome === rowRes[0]?.outcome && r.suggested_cents === rowRes[0]?.suggested_cents);
                    return (
                      <tr key={bc.id} className={allSame ? "bg-white" : "bg-blue-50/30"}>
                        <td className="py-2.5 pr-4">
                          <p className="font-medium text-gray-900">{bc.title}</p>
                          <p className="text-[11px] text-gray-500">
                            {bc.sourceLabel} · {bc.days_since_delivery}d after delivery · prior={bc.prior_claims} · refundable={money(bc.remaining_cents)}
                          </p>
                        </td>
                        {policies.map((p) => {
                          const r = batchMatrix[bc.id]?.[p.version];
                          return (
                            <td key={p.version} className="py-2.5 px-3 align-top">
                              {r ? (
                                <div className="space-y-1">
                                  <div className="flex items-center gap-1.5">
                                    <span className="font-semibold text-gray-900">{money(r.suggested_cents)}</span>
                                    <OutcomeBadge outcome={r.outcome} />
                                  </div>
                                  <p className="text-[11px] text-gray-500">
                                    <code>{r.rule_id}</code>
                                  </p>
                                </div>
                              ) : (
                                <span className="text-gray-400">…</span>
                              )}
                            </td>
                          );
                        })}
                        <td className="py-2.5 pl-3 align-top">
                          {allSame ? (
                            <span className="text-gray-400">Identical across policies</span>
                          ) : (
                            <Badge tone="blue">Payout / rule changes</Badge>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

export default function PolicyPage() {
  return (
    <TokenGate>
      <Suspense fallback={<p className="text-sm text-gray-500">Loading simulator…</p>}>
        <Simulator />
      </Suspense>
    </TokenGate>
  );
}

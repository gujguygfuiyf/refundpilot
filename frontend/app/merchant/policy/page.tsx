"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { ClaimRow, EvidenceAssessment, Policy, SimulationResult } from "@/lib/types";
import { label, money } from "@/lib/format";
import TokenGate from "@/components/TokenGate";
import { Badge, Card, ErrorNote, OutcomeBadge, Spinner } from "@/components/ui";

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
    days_since_delivery: 3,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "broken", severity: "severe" },
  },
  {
    id: "rep-2",
    title: "Desk Lamp · Moderate cosmetic scuff ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    days_since_delivery: 4,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "cosmetic", severity: "moderate" },
  },
  {
    id: "rep-3",
    title: "Ceramic Mug · Minor surface scratch ($24.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 2400,
    days_since_delivery: 2,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "cosmetic", severity: "minor" },
  },
  {
    id: "rep-4",
    title: "Headphones · Reported at day 10 ($89.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 8900,
    days_since_delivery: 10,
    prior_claims: 0,
    assessment: { ...BASE, damage_type: "broken", severity: "severe" },
  },
  {
    id: "rep-5",
    title: "Desk Lamp · Blurry photo ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    days_since_delivery: 3,
    prior_claims: 0,
    assessment: { ...BASE, evidence_quality: "unclear", damage_type: "other", severity: "none", visible_damage: false },
  },
  {
    id: "rep-6",
    title: "Desk Lamp · Suspected reused screenshot ($49.00)",
    sourceLabel: "Representative benchmark",
    order_cents: 4900,
    days_since_delivery: 2,
    prior_claims: 0,
    assessment: { ...BASE, stock_photo_suspected: true, needs_human_review: true },
  },
];

const ENUMS: Record<string, string[]> = {
  product_match: ["confirmed", "likely", "unlikely", "mismatch", "cannot_tell"],
  damage_type: ["none_visible", "cosmetic", "broken", "shattered", "missing_part", "wrong_item", "not_as_described", "other"],
  severity: ["none", "minor", "moderate", "severe"],
  evidence_quality: ["sufficient", "insufficient", "unclear"],
  description_consistent: ["yes", "partly", "no", "cannot_tell"],
};
const BOOLS = ["visible_damage", "stock_photo_suspected", "needs_human_review"] as const;

function Simulator() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [claims, setClaims] = useState<ClaimRow[]>([]);
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

  useEffect(() => {
    api.policies().then(setPolicies).catch((e) => setError(e.message));
    api.merchantClaims().then((r) => setClaims(r.filter((c) => c.damage_type))).catch(() => {});
  }, []);

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

  // Load batch cases when source changes
  useEffect(() => {
    if (batchSource === "benchmark") {
      setBatchCases(REPRESENTATIVE_BATCH);
      return;
    }
    if (!claims.length) {
      setBatchCases(REPRESENTATIVE_BATCH);
      return;
    }
    let cancelled = false;
    setBatchBusy(true);
    Promise.all(claims.slice(0, 8).map((c) => api.merchantClaim(c.id)))
      .then((fullClaims) => {
        if (cancelled) return;
        const loaded: BatchCase[] = fullClaims
          .filter((fc) => fc.assessment)
          .map((fc) => ({
            id: fc.id,
            title: `${fc.order.product.name} · Order #${fc.order.id} (${money(fc.order.amount_cents)})`,
            sourceLabel: `Recorded claim ${fc.id.slice(-6)}`,
            order_cents: fc.order.amount_cents,
            days_since_delivery: 3,
            prior_claims: 0,
            assessment: fc.assessment!.output,
          }));
        setBatchCases(loaded.length ? loaded : REPRESENTATIVE_BATCH);
      })
      .catch(() => {
        if (!cancelled) setBatchCases(REPRESENTATIVE_BATCH);
      })
      .finally(() => {
        if (!cancelled) setBatchBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [batchSource, claims]);

  // Run batch simulation across all policies for current batchCases
  useEffect(() => {
    if (!policies.length || !batchCases.length) return;
    let cancelled = false;
    setBatchBusy(true);
    Promise.all(
      batchCases.map(async (bc) => {
        const resList = await Promise.all(
          policies.map((p) =>
            api.simulate({
              policy_version: p.version,
              assessment: bc.assessment,
              order_cents: bc.order_cents,
              days_since_delivery: bc.days_since_delivery,
              prior_claims: bc.prior_claims,
            }),
          ),
        );
        return [bc.id, Object.fromEntries(resList.map((r) => [r.policy_version, r]))] as const;
      }),
    )
      .then((entries) => {
        if (!cancelled) setBatchMatrix(Object.fromEntries(entries));
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setBatchBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [policies, batchCases]);

  const batchSummary = useMemo(() => {
    return policies.map((p) => {
      let totalSuggested = 0;
      let reviewOrEvidenceCount = 0;
      let changedVsFirst = 0;
      const basePolicyVer = policies[0]?.version;

      for (const bc of batchCases) {
        const r = batchMatrix[bc.id]?.[p.version];
        const rBase = basePolicyVer ? batchMatrix[bc.id]?.[basePolicyVer] : undefined;
        if (!r) continue;
        totalSuggested += r.suggested_cents;
        if (r.outcome === "REVIEW" || r.outcome === "REQUEST_EVIDENCE") {
          reviewOrEvidenceCount += 1;
        }
        if (rBase && (r.outcome !== rBase.outcome || r.suggested_cents !== rBase.suggested_cents)) {
          changedVsFirst += 1;
        }
      }
      return {
        policy: p,
        totalSuggested,
        reviewRate: batchCases.length ? Math.round((reviewOrEvidenceCount / batchCases.length) * 100) : 0,
        reviewOrEvidenceCount,
        changedVsFirst,
      };
    });
  }, [policies, batchCases, batchMatrix]);

  async function loadClaim(id: string) {
    try {
      const c = await api.merchantClaim(id);
      if (c.assessment) setA(c.assessment.output);
      setOrderCents(c.order.amount_cents);
    } catch (e) {
      setError((e as Error).message);
    }
  }

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
            <select onChange={(e) => e.target.value && loadClaim(e.target.value)} defaultValue=""
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
          {" "}Source: <strong>{batchSource === "benchmark" ? "6 predefined representative scenarios" : `${batchCases.length} recorded demo claims`}</strong>.
        </p>

        {batchBusy && (
          <p className="mb-3 flex items-center gap-2 text-xs text-gray-500">
            <Spinner /> Replaying {batchCases.length} cases across {policies.length} policies…
          </p>
        )}

        <div className="mb-5 grid gap-4 sm:grid-cols-3">
          {batchSummary.map(({ policy: p, totalSuggested, reviewRate, reviewOrEvidenceCount, changedVsFirst }, idx) => (
            <div key={p.version} className="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-gray-900">{p.name}</span>
                <code className="rounded bg-white px-1.5 py-0.5 text-xs text-gray-600 ring-1 ring-gray-200">{p.version}</code>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <div>
                  <span className="block text-[11px] text-gray-500 uppercase">Total suggested</span>
                  <span className="text-xl font-semibold text-gray-900">{money(totalSuggested)}</span>
                </div>
                <div>
                  <span className="block text-[11px] text-gray-500 uppercase">Review / Evidence</span>
                  <span className="text-xl font-semibold text-gray-900">
                    {reviewRate}% <span className="text-xs font-normal text-gray-500">({reviewOrEvidenceCount}/{batchCases.length})</span>
                  </span>
                </div>
              </div>
              <p className="mt-2 text-xs text-gray-500">
                {idx === 0
                  ? "Baseline policy for comparison"
                  : `${changedVsFirst} of ${batchCases.length} cases change outcome or amount vs. ${policies[0]?.name}`}
              </p>
            </div>
          ))}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500 uppercase">
                <th className="py-2 pr-4 font-medium">Case &amp; source</th>
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
                        {bc.sourceLabel} · delivered {bc.days_since_delivery}d ago
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
      </Card>
    </div>
  );
}

export default function PolicyPage() {
  return (
    <TokenGate>
      <Simulator />
    </TokenGate>
  );
}

"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ClaimRow, EvidenceAssessment, Policy, SimulationResult } from "@/lib/types";
import { label, money } from "@/lib/format";
import TokenGate from "@/components/TokenGate";
import { Badge, Card, ErrorNote, OutcomeBadge } from "@/components/ui";

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
        <Card title="Evidence" className="lg:col-span-2">
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

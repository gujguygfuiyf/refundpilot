"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from "ag-grid-community";
import { api, ApiError, setMerchantToken } from "@/lib/api";
import type { ClaimRow, DisputeRow, Stats } from "@/lib/types";
import { ago, label, money } from "@/lib/format";
import ClaimDrawer from "@/components/ClaimDrawer";
import DisputeDrawer from "@/components/DisputeDrawer";
import TokenGate, { signOut } from "@/components/TokenGate";
import { Badge, Button, ErrorNote, FactChip, OutcomeBadge, Spinner, StatusBadge } from "@/components/ui";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withParams({
  accentColor: "#0070e0",
  headerFontWeight: 600,
  rowHeight: 56,
  fontFamily: "inherit",
});

function StatCard({ title, value, hint }: { title: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm ring-1 ring-slate-100">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</p>
      <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

type TabFilter = "open" | "all" | "disputes";

function Queue() {
  const params = useSearchParams();
  const [rows, setRows] = useState<ClaimRow[]>([]);
  const [disputes, setDisputes] = useState<DisputeRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<{ ok: boolean; paypal: string; assessor: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [reconcileFeedback, setReconcileFeedback] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(() => params.get("claim"));
  const [openDisputeId, setOpenDisputeId] = useState<string | null>(() => params.get("dispute"));
  const [previewDisputeClaimId, setPreviewDisputeClaimId] = useState<string | null>(null);
  const initialTab = params.get("tab");
  const [filter, setFilter] = useState<TabFilter>(
    initialTab === "all" ? "all" : initialTab === "disputes" ? "disputes" : "open",
  );

  const load = useCallback(async () => {
    api.health().then(setHealth).catch(() => {});
    try {
      const [r, s, d] = await Promise.all([
        api.merchantClaims(),
        api.stats(),
        api.merchantDisputes().catch(() => [] as DisputeRow[]),
      ]);
      setRows(r);
      setStats(s);
      setDisputes(d);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setMerchantToken("");
        setError("The merchant token was rejected. Reload and enter the correct token.");
      } else setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    const tab = params.get("tab");
    if (tab === "all" || tab === "disputes" || tab === "open") setFilter(tab);
    const cid = params.get("claim");
    if (cid) setOpenId(cid);
    const did = params.get("dispute");
    if (did) setOpenDisputeId(did);
  }, [params]);

  const openRows = useMemo(
    () => rows.filter((r) => !["COMPLETED", "REJECTED", "CANCELLED"].includes(r.status)),
    [rows],
  );

  const openCount = useMemo(() => {
    const fromRows = openRows.length;
    const fromStats = stats
      ? stats.pending_review + stats.evidence_requested + stats.processing
      : 0;
    return Math.max(fromRows, fromStats);
  }, [openRows.length, stats]);

  const allCount = useMemo(() => {
    const fromRows = rows.length;
    const fromStats = stats
      ? stats.pending_review + stats.evidence_requested + stats.processing + stats.completed
      : 0;
    return Math.max(fromRows, fromStats);
  }, [rows.length, stats]);

  const shown = useMemo(
    () => (filter === "all" ? rows : openRows),
    [rows, openRows, filter],
  );

  const showcaseCases = useMemo(() => {
    if (!rows.length) return [];
    const list: Array<{ id: string; label: string }> = [];

    const completed = rows.find((r) => r.status === "COMPLETED");
    if (completed) {
      list.push({
        id: completed.id,
        label: `Completed refund (${completed.product_name})`,
      });
    }

    const evidence = rows.find(
      (r) => (r.outcome === "REQUEST_EVIDENCE" || r.status === "EVIDENCE_REQUESTED") && r.id !== completed?.id,
    );
    if (evidence) {
      list.push({
        id: evidence.id,
        label: `Held: Request evidence (${evidence.rule_id || "R5-weak"})`,
      });
    }

    const review = rows.find(
      (r) => r.outcome === "REVIEW" && r.id !== completed?.id && r.id !== evidence?.id,
    );
    if (review) {
      list.push({
        id: review.id,
        label: `Held: Manual review (${review.rule_id || "Review"})`,
      });
    }

    return list;
  }, [rows]);

  const sampleDisputeClaim = useMemo(
    () => rows.find((r) => r.status === "COMPLETED") ?? rows[0] ?? null,
    [rows],
  );

  const columns = useMemo<ColDef<ClaimRow>[]>(
    () => [
      {
        headerName: "Item",
        field: "product_name",
        flex: 2,
        minWidth: 200,
        cellRenderer: (p: ICellRendererParams<ClaimRow>) =>
          p.data && (
            <div className="flex h-full items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={p.data.product_image_url} alt="" className="h-9 w-9 rounded object-cover" />
              <div className="min-w-0 leading-tight">
                <p className="truncate font-medium">{p.data.product_name}</p>
                <p className="text-xs text-gray-500">order #{p.data.order_id}</p>
              </div>
            </div>
          ),
      },
      { headerName: "Status", field: "status", width: 150, cellRenderer: (p: ICellRendererParams<ClaimRow>) => <StatusBadge status={p.value} /> },
      { headerName: "Damage", field: "damage_type", width: 120, valueFormatter: (p) => label(p.value), cellRenderer: (p: ICellRendererParams<ClaimRow>) => <FactChip value={p.value} /> },
      { headerName: "Evidence", field: "evidence_quality", width: 120, cellRenderer: (p: ICellRendererParams<ClaimRow>) => <FactChip value={p.value} /> },
      { headerName: "Match", field: "product_match", width: 110, cellRenderer: (p: ICellRendererParams<ClaimRow>) => <FactChip value={p.value} /> },
      { headerName: "Outcome", field: "outcome", width: 140, cellRenderer: (p: ICellRendererParams<ClaimRow>) => <OutcomeBadge outcome={p.value} /> },
      { headerName: "Rule", field: "rule_id", width: 115 },
      { headerName: "Suggested", field: "suggested_cents", width: 120, type: "rightAligned", valueFormatter: (p) => money(p.value) },
      { headerName: "Order", field: "amount_cents", width: 100, type: "rightAligned", valueFormatter: (p) => money(p.value) },
      { headerName: "Age", field: "created_at", width: 80, sort: "desc", valueFormatter: (p) => ago(p.value) },
    ],
    [],
  );

  const disputeColumns = useMemo<ColDef<DisputeRow>[]>(
    () => [
      {
        headerName: "Dispute / Item",
        field: "paypal_dispute_id",
        flex: 2,
        minWidth: 220,
        cellRenderer: (p: ICellRendererParams<DisputeRow>) =>
          p.data && (
            <div className="flex h-full items-center gap-3">
              {p.data.product_image_url ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img src={p.data.product_image_url} alt="" className="h-9 w-9 rounded object-cover" />
              ) : (
                <div className="flex h-9 w-9 items-center justify-center rounded bg-gray-100 text-xs font-medium text-gray-500">
                  PP
                </div>
              )}
              <div className="min-w-0 leading-tight">
                <p className="truncate font-medium">
                  {p.data.product_name ?? "Unmatched order"}{" "}
                  <span className="font-mono text-xs text-gray-500">({p.data.paypal_dispute_id})</span>
                </p>
                <p className="text-xs text-gray-500">
                  {p.data.order_id ? `order #${p.data.order_id} · capture ${p.data.capture_id}` : "no matching capture"}
                </p>
              </div>
            </div>
          ),
      },
      { headerName: "Status", field: "status", width: 175, cellRenderer: (p: ICellRendererParams<DisputeRow>) => <StatusBadge status={p.value} /> },
      { headerName: "Stage", field: "stage", width: 120, cellRenderer: (p: ICellRendererParams<DisputeRow>) => <Badge tone="violet">{label(p.value)}</Badge> },
      { headerName: "Reason", field: "reason", width: 180, valueFormatter: (p) => label(p.value) },
      { headerName: "Disputed", field: "dispute_amount_cents", width: 115, type: "rightAligned", valueFormatter: (p) => money(p.value, p.data?.currency) },
      { headerName: "Policy outcome", field: "outcome", width: 140, cellRenderer: (p: ICellRendererParams<DisputeRow>) => <OutcomeBadge outcome={p.value} /> },
      { headerName: "Rule", field: "rule_id", width: 110, valueFormatter: (p) => p.value ?? "—" },
      {
        headerName: "Suggested",
        field: "suggested_cents",
        width: 115,
        type: "rightAligned",
        valueFormatter: (p) => (p.data?.has_policy_decision ? money(p.value, p.data?.currency) : "—"),
      },
      { headerName: "Remaining", field: "remaining_cents", width: 110, type: "rightAligned", valueFormatter: (p) => (p.value !== null && p.value !== undefined ? money(p.value, p.data?.currency) : "—") },
      { headerName: "Age", field: "created_at", width: 80, sort: "desc", valueFormatter: (p) => ago(p.value) },
    ],
    [],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-2xl font-semibold">Claims &amp; Disputes queue</h1>
            {health && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 font-medium text-emerald-800 ring-1 ring-emerald-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  PayPal: {health.paypal === "PayPalClient" ? "Sandbox Live" : health.paypal}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-0.5 font-medium text-blue-800 ring-1 ring-blue-200">
                  AI Assessor: {health.assessor}
                </span>
              </div>
            )}
          </div>
          <p className="mt-1 text-sm text-gray-600">
            AI returns structured observations and uncertainty, your versioned policy calculates the suggestion, and you approve. PayPal executes.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            disabled={reconciling}
            onClick={async () => {
              setReconciling(true);
              setReconcileFeedback(null);
              setError(null);
              try {
                const res = await api.reconcile();
                await load();
                setReconcileFeedback(
                  `✓ Sync complete with PayPal Sandbox: ${res.touched} pending refund(s) resolved, ${res.disputes_synced ?? 0} dispute(s) synced, ${res.ledger?.orders_calibrated ?? 0} order(s) calibrated.`,
                );
                setTimeout(() => setReconcileFeedback(null), 5000);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setReconciling(false);
              }
            }}
          >
            {reconciling && <Spinner />}
            <span>{reconciling ? "Syncing PayPal Sandbox..." : "Reconcile & sync PayPal"}</span>
          </Button>
          <Button variant="ghost" onClick={signOut}>Sign out</Button>
        </div>
      </div>
      {reconcileFeedback && (
        <div className="rounded-lg bg-emerald-50 px-3.5 py-2 text-xs font-medium text-emerald-800 ring-1 ring-emerald-200">
          {reconcileFeedback}
        </div>
      )}
      <ErrorNote error={error} />
      {stats && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard title="Awaiting review" value={stats.pending_review} hint={`${money(stats.suggested_cents_pending)} suggested`} />
          <StatCard title="Evidence requested" value={stats.evidence_requested} />
          <StatCard title="In progress" value={stats.processing} hint="AI running or refund in flight" />
          <StatCard title="Refunded" value={money(stats.refunded_cents_total)} hint={`${stats.completed} claims completed`} />
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 text-sm">
          {(["open", "all", "disputes"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-lg px-3 py-1.5 transition ${
                filter === f
                  ? "bg-white font-medium shadow-sm ring-1 ring-slate-200"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {f === "open"
                ? `Open (${openCount})`
                : f === "all"
                  ? `All claims (${allCount})`
                  : `Disputes (${disputes.length})`}
            </button>
          ))}
        </div>
        {filter === "open" && openCount === 0 && allCount > 0 && (
          <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/80 px-3 py-1.5 text-xs text-blue-900 shadow-2xs">
            <span>No pending claims in the Open queue right now.</span>
            <button
              type="button"
              onClick={() => setFilter("all")}
              className="font-semibold text-brand-dark underline hover:text-brand"
            >
              View all {allCount} recorded claims &amp; hard cases →
            </button>
          </div>
        )}
      </div>

      {showcaseCases.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200/80 bg-slate-50/80 px-3 py-2 text-xs text-slate-700 shadow-2xs">
          <span className="font-semibold text-slate-900">Quick-inspect recorded cases:</span>
          {showcaseCases.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setOpenId(c.id)}
              className="inline-flex items-center gap-1.5 rounded-md bg-white px-2.5 py-1 text-xs font-medium text-slate-800 shadow-2xs ring-1 ring-slate-300 transition hover:bg-slate-50 hover:text-brand hover:ring-brand/40"
            >
              <span>{c.label}</span>
              <span className="text-slate-400">→</span>
            </button>
          ))}
          <span className="ml-auto text-[11px] text-slate-500">Click to open drawer with full PayPal audit trail</span>
        </div>
      )}

      {filter === "disputes" && disputes.length === 0 && sampleDisputeClaim !== null && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-violet-200 bg-violet-50/80 p-3 text-xs text-violet-950">
          <div className="flex items-center gap-2">
            <Badge tone="violet">Dispute Architecture Preview</Badge>
            <span>
              No active buyer disputes in PayPal Sandbox right now. Inspect how RefundPilot links a PayPal dispute to its prior claim,
              enforces the remaining-balance cap, and auto-builds a PROOF_OF_REFUND evidence package:
            </span>
          </div>
          <button
            type="button"
            onClick={() => setPreviewDisputeClaimId(sampleDisputeClaim.id)}
            className="inline-flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-violet-900 shadow-xs ring-1 ring-violet-300 transition hover:bg-violet-100/60"
          >
            Preview Dispute Drawer &amp; Evidence Package ({sampleDisputeClaim.product_name}) →
          </button>
        </div>
      )}

      <div style={{ height: 520 }}>
        {filter === "disputes" ? (
          <AgGridReact<DisputeRow>
            theme={theme}
            rowData={disputes}
            columnDefs={disputeColumns}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
            getRowId={(p) => p.data.id}
            onRowClicked={(e) => e.data && setOpenDisputeId(e.data.id)}
            rowClass="cursor-pointer"
            overlayNoRowsTemplate="No PayPal disputes found. Click 'Reconcile & sync PayPal' above to pull open disputes from the PayPal Disputes API."
          />
        ) : (
          <AgGridReact<ClaimRow>
            theme={theme}
            rowData={shown}
            columnDefs={columns}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
            getRowId={(p) => p.data.id}
            onRowClicked={(e) => e.data && setOpenId(e.data.id)}
            rowClass="cursor-pointer"
            overlayNoRowsTemplate="No open claims right now. Click 'All claims' above to inspect completed refunds and hard cases, or submit a new claim from the Buyer page."
          />
        )}
      </div>
      {openId && <ClaimDrawer id={openId} onClose={() => setOpenId(null)} onChanged={load} />}
      {openDisputeId && <DisputeDrawer id={openDisputeId} onClose={() => setOpenDisputeId(null)} onChanged={load} />}
      {previewDisputeClaimId && (
        <DisputeDrawer
          id="preview"
          previewClaimId={previewDisputeClaimId}
          onClose={() => setPreviewDisputeClaimId(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}

export default function MerchantPage() {
  return (
    <TokenGate>
      <Suspense fallback={<p className="text-sm text-gray-500">Loading queue…</p>}>
        <Queue />
      </Suspense>
    </TokenGate>
  );
}

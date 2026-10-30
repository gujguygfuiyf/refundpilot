"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from "ag-grid-community";
import { api, ApiError, setMerchantToken } from "@/lib/api";
import type { ClaimRow, Stats } from "@/lib/types";
import { ago, label, money } from "@/lib/format";
import ClaimDrawer from "@/components/ClaimDrawer";
import TokenGate, { signOut } from "@/components/TokenGate";
import { Button, ErrorNote, FactChip, OutcomeBadge, StatusBadge } from "@/components/ui";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withParams({
  accentColor: "#0070e0",
  headerFontWeight: 600,
  rowHeight: 56,
  fontFamily: "inherit",
});

function StatCard({ title, value, hint }: { title: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium text-gray-500 uppercase">{title}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {hint && <p className="text-xs text-gray-500">{hint}</p>}
    </div>
  );
}

function Queue() {
  const [rows, setRows] = useState<ClaimRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"open" | "all">("open");

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.merchantClaims(), api.stats()]);
      setRows(r);
      setStats(s);
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

  const shown = useMemo(
    () => (filter === "all" ? rows : rows.filter((r) => !["COMPLETED", "REJECTED", "CANCELLED"].includes(r.status))),
    [rows, filter],
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Claims queue</h1>
          <p className="mt-1 text-sm text-gray-600">AI describes the evidence, your policy suggests the amount, you approve. PayPal executes.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => api.reconcile().then(load).catch((e) => setError(e.message))}>Reconcile with PayPal</Button>
          <Button variant="ghost" onClick={signOut}>Sign out</Button>
        </div>
      </div>
      <ErrorNote error={error} />
      {stats && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard title="Awaiting review" value={stats.pending_review} hint={`${money(stats.suggested_cents_pending)} suggested`} />
          <StatCard title="Evidence requested" value={stats.evidence_requested} />
          <StatCard title="In progress" value={stats.processing} hint="AI running or refund in flight" />
          <StatCard title="Refunded" value={money(stats.refunded_cents_total)} hint={`${stats.completed} claims completed`} />
        </div>
      )}
      <div className="flex gap-1 text-sm">
        {(["open", "all"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`rounded-lg px-3 py-1.5 ${filter === f ? "bg-white font-medium shadow-sm ring-1 ring-gray-200" : "text-gray-600 hover:bg-gray-100"}`}>
            {f === "open" ? "Open" : "All claims"}
          </button>
        ))}
      </div>
      <div style={{ height: 520 }}>
        <AgGridReact<ClaimRow>
          theme={theme}
          rowData={shown}
          columnDefs={columns}
          defaultColDef={{ sortable: true, filter: true, resizable: true }}
          getRowId={(p) => p.data.id}
          onRowClicked={(e) => e.data && setOpenId(e.data.id)}
          rowClass="cursor-pointer"
          overlayNoRowsTemplate="No claims yet. Submit one from the buyer page."
        />
      </div>
      {openId && <ClaimDrawer id={openId} onClose={() => setOpenId(null)} onChanged={load} />}
    </div>
  );
}

export default function MerchantPage() {
  return (
    <TokenGate>
      <Queue />
    </TokenGate>
  );
}

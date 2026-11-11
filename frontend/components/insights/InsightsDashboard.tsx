"use client";

// The Insights page body: one AG Studio instance with RefundPilot's data, theme, custom widget and AI assistant.
// Studio owns the report; this component owns what surrounds it (page tabs, the view / ask / edit switch, reset) and
// drives Studio through its API. The layout a visitor edits is kept in their browser, so a judge can rearrange the
// dashboard, reload, and find it as they left it.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgStudioAiModule, type AgPanelsState, type AgReportState, type AgStudioApi } from "ag-studio";
import { AgStudio, AgStudioProvider, createWidgets } from "ag-studio-react";
import type { ClaimRow } from "@/lib/types";
import { buildAi } from "@/lib/insights/ai";
import { buildStudioData, EVAL_RUN_COUNT } from "@/lib/insights/data";
import { CLAIMS_PAGE, insightsReport, PAGE_TITLES } from "@/lib/insights/report";
import { insightsTheme } from "@/lib/insights/theme";
import { guardrailWidget, type InsightsRegistry } from "./GuardrailWidget";

// v2: the default report gained page filters and a new top row; a layout saved against v1 would hide both.
const STORAGE_KEY = "refundpilot.insights.report.v2";
const LICENSE_KEY = process.env.NEXT_PUBLIC_AG_STUDIO_LICENSE_KEY || undefined;

// Studio's built-in widgets plus ours, which gets its own group at the top of the widget picker.
const widgets = createWidgets<InsightsRegistry>({
  additionalTypes: [guardrailWidget],
  menu: [{ label: "RefundPilot", widgetIds: [guardrailWidget.id] }],
});

type Report = AgReportState<InsightsRegistry>;

type Mode = "view" | "ask" | "edit";

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "view", label: "View", hint: "Read the dashboard and filter it" },
  { id: "ask", label: "Ask the assistant", hint: "Chat with the AI assistant beside the dashboard" },
  { id: "edit", label: "Edit", hint: "Move, resize, add and configure widgets" },
];

// Which panels each mode opens. Ask and Edit are both Studio's edit mode, told apart by these presets; a
// collapsed panel stays one click away at the canvas edge. The AI panel only exists when the assistant is on.
function panelPreset(mode: Mode, assistant: boolean): Record<string, boolean> {
  if (mode === "view") return { filters: false };
  if (mode === "ask") return { ai: false, filters: true, edit: true, data: true };
  return { ...(assistant && { ai: true }), filters: true, edit: false, data: false };
}

// Studio keeps state only for panels it has rendered, so a preset applied before the new mode's panels exist
// is partly dropped; this tells the caller to try again on the next frame.
const presetApplied = (panels: AgPanelsState | undefined, preset: Record<string, boolean>) =>
  Object.entries(preset).every(([id, collapsed]) => (panels?.[id]?.collapsed === true) === collapsed);

const withPreset = (panels: AgPanelsState | undefined, preset: Record<string, boolean>): AgPanelsState => ({
  ...panels,
  ...Object.fromEntries(Object.entries(preset).map(([id, collapsed]) => [id, { ...panels?.[id], collapsed }])),
});

/** Key order and undefined members don't make two states differ. */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, (v as Record<string, unknown>)[k]]))
      : v,
  );
}

// What the reader made: the pages, their widgets, layout and filters. Which page is open, which panels are
// collapsed, the chart slice they clicked and the widget they selected are where they are looking, not a change.
const dashboardOf = (state: Report) => state.pages.map((page) => ({ ...page, crossFilter: undefined, selection: undefined }));
const DEFAULT_DASHBOARD = canonical(dashboardOf(insightsReport));
const isCustomised = (state: Report) => canonical(dashboardOf(state)) !== DEFAULT_DASHBOARD;

function savedReport(): Report | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Report) : null;
  } catch {
    return null;
  }
}

function saveReport(state: Report | null) {
  try {
    if (state) localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* private mode: the layout lasts for this page view only */
  }
}

export default function InsightsDashboard({ claims, assistant }: { claims: ClaimRow[]; assistant: boolean }) {
  const apiRef = useRef<AgStudioApi<InsightsRegistry> | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  // Studio reads initialState once, when it is created; afterwards its state is read from Studio.
  const [initialState] = useState<Report>(() => savedReport() ?? insightsReport);
  const [pageId, setPageId] = useState(initialState.selectedPageId);
  const [pageIds, setPageIds] = useState(() => initialState.pages.map((p) => p.id));
  const [customised, setCustomised] = useState(() => isCustomised(initialState));
  const [apiReady, setApiReady] = useState(false);

  const data = useMemo(() => buildStudioData(claims), [claims]);
  const ai = useMemo(() => (assistant ? buildAi() : undefined), [assistant]);
  const modes = assistant ? MODES : MODES.filter((m) => m.id !== "ask");

  // Runs after the render that handed Studio the new mode; Studio builds that mode's panels in its own render,
  // so the preset is retried for a few frames until every panel it names has taken it.
  useEffect(() => {
    if (!apiReady) return;
    const preset = panelPreset(mode, assistant);
    let frame = 0;
    let tries = 0;
    const apply = () => {
      const api = apiRef.current;
      if (!api) return;
      const state = api.getState();
      if (presetApplied(state.panels, preset)) return;
      api.setState({ ...state, panels: withPreset(state.panels, preset) });
      if (++tries < 20) frame = requestAnimationFrame(apply);
    };
    frame = requestAnimationFrame(apply);
    return () => cancelAnimationFrame(frame);
  }, [mode, assistant, apiReady]);

  const selectPage = useCallback((id: string) => {
    const api = apiRef.current;
    if (api) api.setState({ ...api.getState(), selectedPageId: id });
    setPageId(id);
  }, []);

  const reset = useCallback(() => {
    const api = apiRef.current;
    saveReport(null);
    // Keep the reader's panels where they are; only the dashboard itself goes back to the default.
    api?.setState({ ...insightsReport, panels: api.getState().panels });
    api?.clearHistory();
    setPageId(insightsReport.selectedPageId);
    setPageIds(insightsReport.pages.map((p) => p.id));
    setCustomised(false);
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 text-sm" role="tablist" aria-label="Dashboard pages">
          {pageIds.map((id, i) => (
            <button
              key={id}
              role="tab"
              aria-selected={pageId === id}
              onClick={() => selectPage(id)}
              className={`rounded-lg px-3 py-1.5 transition ${
                pageId === id ? "bg-white font-medium shadow-sm ring-1 ring-slate-200" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {PAGE_TITLES[id] ?? `Page ${i + 1}`}
              {id === "evaluation" && <span className="ml-1.5 text-xs text-slate-500">{EVAL_RUN_COUNT} runs</span>}
              {id === "claims" && <span className="ml-1.5 text-xs text-slate-500">{claims.length}</span>}
            </button>
          ))}
        </div>
        {pageId === CLAIMS_PAGE && claims.length === 0 && (
          <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/80 px-3 py-1.5 text-xs text-blue-900 shadow-2xs">
            <span>The live queue is empty, so this page has nothing to chart yet.</span>
            <Link href="/" className="font-semibold text-brand-dark underline hover:text-brand">
              File a claim in the Buyer Portal →
            </Link>
          </div>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2 text-sm">
          {customised && (
            <button
              onClick={reset}
              title="Go back to the dashboard as RefundPilot ships it"
              className="rounded-lg px-3 py-1.5 text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
            >
              Reset layout
            </button>
          )}
          <div className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5 ring-1 ring-slate-200" role="radiogroup" aria-label="Dashboard mode">
            {modes.map((m) => (
              <button
                key={m.id}
                role="radio"
                aria-checked={mode === m.id}
                title={m.hint}
                onClick={() => setMode(m.id)}
                className={`rounded-md px-3 py-1 transition ${
                  mode === m.id ? "bg-white font-medium text-brand-dark shadow-xs ring-1 ring-blue-200" : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="h-[calc(100vh-13.5rem)] min-h-[560px] overflow-hidden rounded-xl">
        <AgStudioProvider licenseKey={LICENSE_KEY} modules={ai ? [AgStudioAiModule] : undefined}>
          <AgStudio<InsightsRegistry>
            style={{ height: "100%", width: "100%" }}
            theme={insightsTheme}
            widgets={widgets}
            data={data}
            ai={ai}
            initialState={initialState}
            mode={mode === "view" ? "view" : "edit"}
            onApiReady={(event) => {
              // The event is typed for Studio's default widgets; this instance also has ours.
              apiRef.current = event.api as unknown as AgStudioApi<InsightsRegistry>;
              setApiReady(true);
            }}
            onStateUpdated={(event) => {
              const state = event.state as Report;
              const changed = isCustomised(state);
              // Only a real change is kept, so a reader who changed nothing gets the current default next time.
              saveReport(changed ? state : null);
              setCustomised(changed);
              setPageId(state.selectedPageId);
              setPageIds(state.pages.map((p) => p.id));
            }}
          />
        </AgStudioProvider>
      </div>
    </div>
  );
}

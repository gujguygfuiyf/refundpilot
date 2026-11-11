"use client";

// A custom AG Studio widget: one guardrail, checked against whatever the page is filtered to.
//
// It reads two mapped fields through the widget API: "violations" (a measure that should be zero, e.g. the sum
// of Overpaid) and "checked" (how many rows were looked at). Studio re-renders it with a new widgetApi whenever
// the data or a filter that touches it changes, so the tile always describes the current selection.

import { useEffect, useState } from "react";
import type { AgDefaultRegistry, AgWidgetData, AgWidgetDataFormat, AgWidgetFieldReference, AgWidgetParams } from "ag-studio";
import { AgIncompleteDataMappingOverlay, AgLoadingOverlay, type AgAiToolDisplay, type AgWidgetDefinition } from "ag-studio-react";

export const GUARDRAIL_WIDGET = "guardrail-check";

/** The widget's saved state: two mapped fields and the usual titles. */
export type GuardrailState = AgWidgetData<
  { violations: AgWidgetFieldReference[]; checked: AgWidgetFieldReference[] },
  AgWidgetDataFormat<unknown>
> & { type: typeof GUARDRAIL_WIDGET };

type GuardrailDefinition = AgWidgetDefinition<typeof GUARDRAIL_WIDGET, GuardrailState>;

/** Studio's own widgets plus ours. Passing this to AgStudio, createWidgets and the report state types them all. */
export interface InsightsRegistry extends Omit<AgDefaultRegistry, "widgets" | "aiToolDisplay"> {
  widgets: readonly (AgDefaultRegistry["widgets"][number] | GuardrailDefinition)[];
  aiToolDisplay: AgAiToolDisplay;
}

type Reading = { violations: number; checked: number };

function GuardrailWidget({ widgetApi, dataMapping }: AgWidgetParams<GuardrailState>) {
  const violationsField = dataMapping.violations?.[0];
  const checkedField = dataMapping.checked?.[0];
  const [reading, setReading] = useState<Reading | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!violationsField || !checkedField) return;
    let stale = false;
    widgetApi
      .getData({ fields: [violationsField, checkedField] })
      .then(({ results, replaced }) => {
        if (stale || replaced) return;
        const row = results.rows[0] ?? {};
        setReading({ violations: Number(row[violationsField.key] ?? 0), checked: Number(row[checkedField.key] ?? 0) });
        setError(null);
      })
      .catch((e: Error) => !stale && setError(e.message));
    return () => {
      stale = true;
    };
  }, [widgetApi, violationsField, checkedField]);

  if (!violationsField || !checkedField) return <AgIncompleteDataMappingOverlay />;
  if (error) return <p className="p-3 text-sm text-rose-700">Could not load this check: {error}</p>;
  if (!reading) return <AgLoadingOverlay />;

  const { violations, checked } = reading;
  const held = violations === 0;
  const empty = checked === 0;
  const cleanShare = empty ? 0 : Math.max(0, Math.min(1, (checked - violations) / checked));

  return (
    <div className="flex h-full min-h-0 flex-col justify-center gap-2 px-1" role="status">
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-lg font-bold text-white ${
            empty ? "bg-slate-400" : held ? "bg-emerald-600" : "bg-rose-600"
          }`}
        >
          {empty ? "–" : held ? "✓" : "!"}
        </span>
        <div className="min-w-0">
          <p className="truncate text-2xl font-bold leading-tight tracking-tight text-slate-900">
            {violations.toLocaleString("en-US")}
            <span className="text-base font-medium text-slate-500"> of {checked.toLocaleString("en-US")}</span>
          </p>
          <p className={`truncate text-xs font-medium ${empty ? "text-slate-500" : held ? "text-emerald-700" : "text-rose-700"}`}>
            {empty ? "Nothing in the current selection" : held ? "Holds for this selection" : "Broken: review these rows"}
          </p>
        </div>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200" aria-hidden>
        <div className={`h-full rounded-full ${held ? "bg-emerald-500" : "bg-rose-500"}`} style={{ width: `${cleanShare * 100}%` }} />
      </div>
    </div>
  );
}

// A shield with a tick, drawn like Studio's own picker icons: 32 px, filled with the theme's first chart colour.
const SHIELD_ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" fill="none" viewBox="0 0 32 32">' +
  '<path fill="var(--ag-chart-palette-fills-1-color, #5090DC)" d="M16 1.5 4 6v8.6c0 7.5 5.1 13.6 12 15.9 6.9-2.3 12-8.4 12-15.9V6z"/>' +
  '<path stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="m10.5 16.2 3.9 3.9 7.1-7.8"/>' +
  "</svg>";

export const guardrailWidget: GuardrailDefinition = {
  id: GUARDRAIL_WIDGET,
  label: "Guardrail check",
  icon: SHIELD_ICON,
  comp: GuardrailWidget,
  dataMapping: {
    violations: {
      type: "field",
      supportedRoles: ["numeric"],
      required: true,
      intent: "value",
      aiDescription: "A measure that should be zero when the guardrail holds, e.g. the sum of Overpaid.",
    },
    checked: {
      type: "field",
      supportedRoles: ["numeric", "category"],
      required: true,
      intent: "value",
      aiDescription: "How many rows were checked, e.g. the count of Run.",
    },
  },
  form: (params) =>
    params.createDefaults({
      dataMappingItems: [
        { key: "violations", label: "Violations (should be 0)" },
        { key: "checked", label: "Rows checked" },
      ],
    }),
  defaultSize: { width: 6, height: 6 },
  minSize: { width: 4, height: 5 },
  ai: {
    label: "Guardrail check",
    description: "A pass/fail tile: a tick and '0 of N' while a measure that must stay at zero is zero, a warning otherwise.",
    usage: "Use for safety or compliance checks such as overpayments. Not for ordinary KPIs; use the value widget for those.",
    configuration: "Map 'violations' to an aggregated numeric field (sum) and 'checked' to a count of rows. The title names the guardrail.",
  },
};

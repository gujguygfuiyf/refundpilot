// The built-in Insights report, as AG Studio report state: two pages, laid out on Studio's 24-column canvas.
// Field ids ("runs.suggested") and measures ("passRate") refer to the data sources in ./data.ts.
// In edit mode every widget here can be moved, resized, reconfigured or removed; "Reset" restores this state.

import type { AgPageState, AgReportState, AgWidgetLayoutState } from "ag-studio";
import { GUARDRAIL_WIDGET, type InsightsRegistry } from "@/components/insights/GuardrailWidget";

const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number): AgWidgetLayoutState => ({ xTrack, yTrack, xSpan, ySpan });
const title = (text: string, subtitle?: string) => ({
  title: { enabled: true, text },
  ...(subtitle && { subtitle: { enabled: true, text: subtitle } }),
});
const sum = (id: string) => ({ id, aggregation: "sum" as const });
const avg = (id: string) => ({ id, aggregation: "avg" as const });
const count = (id: string) => ({ id, aggregation: "count" as const });
const legend = { style: { theme: { common: { legend: { enabled: true } } } } };

// Page filters ready in the Filters panel, as Studio itself writes them: the first one open, the rest one click away.
// Each is a tick list of the column's values rather than a text search, so a reader sees what there is to pick.
const pageFilters = (...ids: string[]) => ({
  page: ids.map((id, i) => ({ field: { id }, view: { expanded: i === 0, viewTypeId: "selection" as const } })),
  widget: {},
});

const kpi = (text: string, value: { id: string; aggregation?: "sum" | "avg" | "count" }, subtitle?: string) => ({
  type: "value" as const,
  dataMapping: { value: [value] },
  format: title(text, subtitle),
});

export const EVALUATION_PAGE = "evaluation";
export const CLAIMS_PAGE = "claims";

export const PAGE_TITLES: Record<string, string> = {
  [EVALUATION_PAGE]: "AI evaluation",
  [CLAIMS_PAGE]: "Live claims",
};

const evaluation: AgPageState<InsightsRegistry> = {
  id: EVALUATION_PAGE,
  filter: pageFilters("runs.group", "runs.outcome", "runs.product"),
  widgets: {
    "test-set": {
      type: "button-filter",
      dataMapping: { value: [{ id: "runs.set" }] },
      // The button filter always labels itself "Filter by: Test set", so the tile title is off: with both, the
      // stacked buttons didn't fit the tile's height and "Held-out set" was cut off.
      format: { title: { enabled: false, text: "Test set" }, style: { dimensions: { height: 28, minWidth: 96 } } },
    },
    "guard-overpaid": {
      type: GUARDRAIL_WIDGET,
      dataMapping: { violations: [sum("runs.overpaid")], checked: [count("runs.run")] },
      format: title("Refunds above the cap"),
    },
    "kpi-pass": kpi("Pass rate", { id: "passRate" }, "expected outcome, within the cap"),
    "kpi-standard": kpi("Standard policy suggests", sum("runs.suggested"), "the policy that ran"),
    "kpi-goodwill": kpi("Goodwill policy would suggest", sum("runs.goodwill_suggested"), "same AI findings, replayed"),
    outcomes: {
      type: "donut-chart",
      dataMapping: { categoryKey: [{ id: "runs.outcome" }], valueKey: [count("runs.run")] },
      format: { ...title("Outcomes", "click a slice to filter the page"), ...legend },
    },
    "by-group": {
      type: "bar-chart-stacked",
      dataMapping: { categoryKey: [{ id: "runs.group" }], valueKey: [count("runs.run")], legendKey: [{ id: "runs.outcome" }] },
      format: { ...title("Outcome by scenario group", "prompt injection never changes an amount"), ...legend },
    },
    policies: {
      type: "column-chart-grouped",
      dataMapping: { categoryKey: [{ id: "runs.group" }], valueKey: [sum("runs.suggested"), sum("runs.goodwill_suggested")] },
      format: { ...title("One set of AI findings, two policies", "the model reports facts; the policy sets the amount"), ...legend },
    },
    latency: {
      type: "box-plot-chart",
      dataMapping: { categoryKey: [{ id: "runs.group" }], valueKey: [{ id: "runs.latency_s" }] },
      format: title("AI latency by scenario group (seconds)"),
    },
    runs: {
      type: "grid",
      dataMapping: {
        cols: [
          { id: "runs.run" },
          { id: "runs.group" },
          { id: "runs.scenario" },
          { id: "runs.product_match" },
          { id: "runs.damage_type" },
          { id: "runs.evidence_quality" },
          { id: "runs.outcome" },
          { id: "runs.rule" },
          sum("runs.suggested"),
          sum("runs.cap"),
          sum("runs.goodwill_suggested"),
          avg("runs.latency_s"),
        ],
      },
      format: title("Every run", "AI findings on the left, the policy's decision on the right"),
    },
  },
  widgetLayout: {
    "guard-overpaid": at(0, 0, 6, 8),
    "kpi-pass": at(6, 0, 4, 8),
    "kpi-standard": at(10, 0, 5, 8),
    "kpi-goodwill": at(15, 0, 5, 8),
    "test-set": at(20, 0, 4, 8),
    // The two scenario-group charts below run the full width, so their nine group names fit under the axis
    // without rotating; rotated, the first one was cut off at the tile's edge.
    outcomes: at(0, 8, 8, 17),
    "by-group": at(8, 8, 16, 17),
    policies: at(0, 25, 24, 18),
    latency: at(0, 43, 24, 15),
    runs: at(0, 58, 24, 20),
  },
};

const claims: AgPageState<InsightsRegistry> = {
  id: CLAIMS_PAGE,
  filter: pageFilters("claims.status", "claims.product"),
  widgets: {
    "kpi-claims": kpi("Claims", count("claims.claim"), "in the live queue"),
    "kpi-open": kpi("Waiting on the merchant", sum("claims.open")),
    "kpi-suggested": kpi("Suggested by policy", sum("claims.suggested")),
    "kpi-refunded": kpi("Refunded through PayPal", sum("claims.refunded")),
    status: {
      type: "donut-chart",
      dataMapping: { categoryKey: [{ id: "claims.status" }], valueKey: [count("claims.claim")] },
      format: { ...title("Claims by status"), ...legend },
    },
    "claim-outcomes": {
      type: "bar-chart-stacked",
      dataMapping: { categoryKey: [{ id: "claims.outcome" }], valueKey: [count("claims.claim")], legendKey: [{ id: "claims.rule" }] },
      format: { ...title("Policy outcome and the rule behind it"), ...legend },
    },
    "by-product": {
      type: "column-chart-grouped",
      dataMapping: { categoryKey: [{ id: "claims.product" }], valueKey: [sum("claims.suggested"), sum("claims.refunded")] },
      format: { ...title("Suggested and refunded by product"), ...legend },
    },
    "claim-grid": {
      type: "grid",
      dataMapping: {
        cols: [
          { id: "claims.created" },
          { id: "claims.order" },
          { id: "claims.product" },
          { id: "claims.status" },
          { id: "claims.damage_type" },
          { id: "claims.evidence_quality" },
          { id: "claims.outcome" },
          { id: "claims.rule" },
          sum("claims.order_amount"),
          sum("claims.suggested"),
          sum("claims.refunded"),
        ],
      },
      format: title("Live claims"),
    },
  },
  widgetLayout: {
    "kpi-claims": at(0, 0, 6, 7),
    "kpi-open": at(6, 0, 6, 7),
    "kpi-suggested": at(12, 0, 6, 7),
    "kpi-refunded": at(18, 0, 6, 7),
    status: at(0, 7, 7, 17),
    "claim-outcomes": at(7, 7, 9, 17),
    "by-product": at(16, 7, 8, 17),
    "claim-grid": at(0, 24, 24, 20),
  },
};

export const insightsReport: AgReportState<InsightsRegistry> = {
  selectedPageId: EVALUATION_PAGE,
  pages: [evaluation, claims],
};

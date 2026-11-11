"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import TokenGate from "@/components/TokenGate";
import { Badge, ErrorNote, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { ClaimRow } from "@/lib/types";

// AG Studio is a large, browser-only bundle: load it for this route alone, after the page shell is up.
const InsightsDashboard = dynamic(() => import("@/components/insights/InsightsDashboard"), {
  ssr: false,
  loading: () => (
    <p className="flex items-center gap-2 text-sm text-gray-500">
      <Spinner /> Loading the dashboard…
    </p>
  ),
});

function Insights() {
  const [claims, setClaims] = useState<ClaimRow[] | null>(null);
  const [assistant, setAssistant] = useState<{ enabled: boolean; model?: string; reason?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.merchantClaims().then(setClaims).catch((e) => setError(`Could not load the live claims: ${e.message}`));
    api.assistantStatus().then(setAssistant).catch(() => setAssistant({ enabled: false, reason: "The assistant endpoint did not answer." }));
  }, []);

  return (
    <div className="space-y-4 xl:-mx-16">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
            <Badge tone="blue">AG Studio</Badge>
            {assistant &&
              (assistant.enabled ? (
                <Badge tone="green">AI assistant: {assistant.model}</Badge>
              ) : (
                <span className="inline-flex items-center gap-2" title={assistant.reason}>
                  <Badge tone="gray">AI assistant off</Badge>
                  {assistant.reason && <span className="text-xs text-gray-500">{assistant.reason}</span>}
                </span>
              ))}
          </div>
          <p className="mt-1 max-w-4xl text-sm text-gray-600">
            How the AI and the policy behave, as a dashboard you can rearrange: 123 recorded model runs from the evaluation set and the
            live claim queue. Click a chart to cross-filter the page, switch to Edit to move, resize or add widgets
            {assistant?.enabled ? ", or ask the assistant to do it for you." : "."}
          </p>
        </div>
      </div>
      <ErrorNote error={error} />
      {claims && assistant ? (
        <InsightsDashboard claims={claims} assistant={assistant.enabled} />
      ) : (
        !error && (
          <p className="flex items-center gap-2 text-sm text-gray-500">
            <Spinner /> Loading data…
          </p>
        )
      )}
    </div>
  );
}

export default function InsightsPage() {
  return (
    <TokenGate>
      <Insights />
    </TokenGate>
  );
}

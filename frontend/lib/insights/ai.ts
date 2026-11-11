// The Insights assistant: AG Studio's five built-in agents (lead, data, page, planning, widget) plus one of
// our own, the refund policy analyst. All six run in Studio's own loop against the same adapter.
//
// The policy analyst is the part Studio can't know: how RefundPilot's versioned policies turn AI findings into
// a refund. Its tools call the real policy engine on the API, so an answer like "Goodwill would pay $39.50 here"
// is computed, not guessed. The lead agent can hand a question to it, and a reader can pick it directly.

import { createAiHarness, directLlmRunner, type AgAiAgentDefinition, type AgAiHarnessSetup, type AgAiPromptStarter, type AgAiTool, type AgStudioApi } from "ag-studio";
import { api as backend } from "@/lib/api";
import type { EvidenceAssessment } from "@/lib/types";
import { refundPilotAdapter } from "./gemini-adapter";

export const POLICY_AGENT_ID = "refund-policy";

const PROMPT_STARTERS: AgAiPromptStarter[] = [
  {
    label: "Summarise this page",
    prompt: "Summarise what this page shows: every widget, the fields it reads, and what stands out.",
  },
  {
    label: "Did prompt injection move money?",
    prompt: "Look at the evaluation runs in the 'Prompt injection' scenario group. Did any of them get more than its cap? Show the numbers.",
  },
  {
    label: "Add a latency chart",
    prompt: "Add a chart of average AI latency by scenario group to this page.",
  },
  {
    label: "Compare the refund policies",
    prompt: "Ask the refund policy analyst: for a $79 order with cosmetic damage, sufficient evidence and a confirmed product match, what does each policy suggest and why?",
  },
];

const PRODUCT_MATCH = ["confirmed", "likely", "unlikely", "mismatch", "cannot_tell"] as const;
const DAMAGE_TYPE = ["none_visible", "cosmetic", "broken", "shattered", "missing_part", "wrong_item", "not_as_described", "other"] as const;
const EVIDENCE = ["sufficient", "insufficient", "unclear"] as const;

/** The two tools only the policy analyst has. Both read from the API; neither changes anything. */
function policyTools(api: AgStudioApi): AgAiTool[] {
  const listPolicies = api.defineAiTool({
    name: "list_refund_policies",
    description:
      "List RefundPilot's current refund policies with their settings and ordered rules. The first rule whose 'when' " +
      "conditions all match the AI findings decides the outcome.",
    params: (s) => s.object({}),
    execute: async (_args, ctx) => {
      try {
        const policies = await backend.policies();
        return ctx.success(`${policies.length} policies`, policies.map((p) => ({ version: p.version, name: p.name, ...p.body })));
      } catch (e) {
        return ctx.error(`Could not load the policies: ${(e as Error).message}`);
      }
    },
  });

  const simulate = api.defineAiTool({
    name: "simulate_refund_policy",
    description:
      "Run RefundPilot's real policy engine on a hypothetical set of AI findings and return the outcome, the suggested " +
      "refund in dollars, the rule that matched and its reason. Pure computation: no claim is touched and no money moves. " +
      "Call it once per policy to compare policies.",
    params: (s) =>
      s.object({
        policy_version: s.string({ description: "A version from list_refund_policies, e.g. standard-1.2." }),
        order_amount: s.number({ description: "Order amount in US dollars." }),
        product_match: s.enum(PRODUCT_MATCH),
        damage_type: s.enum(DAMAGE_TYPE),
        evidence_quality: s.enum(EVIDENCE),
        stock_photo_suspected: s.boolean(),
        days_since_delivery: s.number({ description: "Days between delivery and the claim. Use 3 if the question doesn't say." }),
        prior_claims: s.number({ description: "Earlier claims on the same order. Use 0 if the question doesn't say." }),
      }),
    execute: async (args, ctx) => {
      const assessment: EvidenceAssessment = {
        product_match: args.product_match,
        damage_type: args.damage_type,
        severity: args.damage_type === "none_visible" ? "none" : "moderate",
        visible_damage: args.damage_type !== "none_visible",
        evidence_quality: args.evidence_quality,
        stock_photo_suspected: args.stock_photo_suspected,
        description_consistent: "yes",
        needs_human_review: false,
        confidence: 0.9,
        observations: [],
        limitations: [],
      };
      try {
        const result = await backend.simulate({
          policy_version: args.policy_version,
          assessment,
          order_cents: Math.round(args.order_amount * 100),
          days_since_delivery: Math.round(args.days_since_delivery),
          prior_claims: Math.round(args.prior_claims),
        });
        const dollars = (result.suggested_cents / 100).toFixed(2);
        return ctx.success(`${result.policy_version}: ${result.outcome}, $${dollars} (${result.rule_id})`, {
          policy_version: result.policy_version,
          outcome: result.outcome,
          suggested_refund_usd: Number(dollars),
          rule: result.rule_id,
          reason: result.reason,
          requires_human_approval: result.requires_approval,
        });
      } catch (e) {
        return ctx.error(`The policy engine rejected the request: ${(e as Error).message}`);
      }
    },
  });

  return [listPolicies, simulate];
}

const POLICY_INSTRUCTIONS = [
  "You are the refund policy analyst inside RefundPilot's Insights dashboard.",
  "RefundPilot works in four steps: an AI model describes the buyer's photos as facts, a versioned deterministic policy turns those facts into an outcome and an amount, a human approves, and PayPal executes the refund.",
  "You explain and compare the policies. Use list_refund_policies to read the rules and simulate_refund_policy to compute what a policy suggests. Never state an amount you did not get from simulate_refund_policy or from a query.",
  "The data has two tables: 'runs' (recorded evaluation runs) and 'claims' (the live queue). Use view_schema and execute_query when a question is about the recorded data.",
  "You cannot approve, change or refund anything, and you have no tool that could. Say so if asked.",
  "Answer in a few short sentences or a small table. Give amounts in US dollars.",
].join("\n");

export function buildAi(): AgAiHarnessSetup {
  const adapter = refundPilotAdapter();

  return (params) => {
    // The callback is typed against Studio's core API; in the browser it is always the full one.
    const api = params.api as AgStudioApi;
    return createAiHarness(api, ({ tools, builtIn }) => {
      const extra = policyTools(api);

      const policyAnalyst: AgAiAgentDefinition = {
        id: POLICY_AGENT_ID,
        name: "Refund policy analyst",
        description:
          "Explains and compares RefundPilot's refund policies and computes what a policy suggests for given AI findings " +
          "with the real policy engine. Hand it any question about refund rules, amounts, caps or policy differences.",
        instructions: () => POLICY_INSTRUCTIONS,
        tools: () => [...extra, tools.studio.viewSchema(), tools.studio.executeQuery()],
        maxTurns: 12,
      };

      // The lead keeps everything AG gave it and gains one more colleague to delegate to.
      const lead: AgAiAgentDefinition = {
        ...builtIn.lead,
        instructions: (ctx, params) =>
          `${builtIn.lead.instructions?.(ctx, params) ?? ""}\n\n` +
          `This dashboard belongs to RefundPilot, a refund-claim tool for PayPal merchants. For any question about refund ` +
          `policies, rules, caps or what a policy would pay, delegate to the '${POLICY_AGENT_ID}' agent.`,
        tools: (ctx, params) => [
          ...(builtIn.lead.tools?.(ctx, params) ?? []).filter((tool) => tool.name !== "delegate_to"),
          tools.delegateTo(["data", "page", "planning", "widget", POLICY_AGENT_ID]),
        ],
      };

      // Studio's default is 100 turns per agent. Every turn is a paid model call on the demo's key, and a model
      // that never calls complete_task once ran each delegate to that limit, so the caps bound one question's cost.
      return {
        agents: [
          directLlmRunner({ ...lead, maxTurns: 24, adapter }),
          directLlmRunner({ ...builtIn.data, maxTurns: 16, adapter }),
          directLlmRunner({ ...builtIn.page, maxTurns: 16, adapter }),
          directLlmRunner({ ...builtIn.planning, maxTurns: 16, adapter }),
          directLlmRunner({ ...builtIn.widget, maxTurns: 16, adapter }),
          directLlmRunner({ ...policyAnalyst, adapter }),
        ],
        primary: "lead",
        promptStarters: PROMPT_STARTERS,
      };
    });
  };
}

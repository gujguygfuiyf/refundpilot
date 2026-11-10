// The model behind the Insights assistant. AG Studio never calls a provider itself: it hands every turn to an
// adapter. This one posts the turn to RefundPilot's own API (POST /merchant/assistant/turn), which holds the
// Gemini key, translates the conversation and tool schemas to Gemini function calling, applies the demo's rate
// limits and answers in Studio's neutral format. No key and no provider wire format reaches the browser.

import type { AgAiEvent, AgLlmAdapter, AgLlmRequest, AgLlmResponse } from "ag-studio";
import { API_URL, getMerchantToken } from "@/lib/api";

const TURN_URL = `${API_URL}/merchant/assistant/turn`;
const UNREACHABLE = "The assistant could not be reached. Check your connection and try again; the dashboard itself keeps working.";
const UNAVAILABLE = "The assistant is unavailable right now. Try again in a minute; the dashboard itself keeps working.";

// A failed turn resolves as a response with status "failed" rather than rejecting `complete`: Studio's turn
// loop turns that status into an error with this message, which the chat shows as the run's error, and a
// promise that never rejects can't surface as an unhandled rejection when Studio stops reading the stream.
function failed(message: string, code = "assistant_error"): AgLlmResponse {
  return { id: `failed-${Date.now()}`, createdAt: Date.now(), output: [], status: "failed", error: { code, message } };
}

const cancelled = (): AgLlmResponse => ({ id: `cancelled-${Date.now()}`, createdAt: Date.now(), output: [], status: "cancelled" });

/** The API's errors carry a sentence written for the reader in `detail`; anything else gets a plain fallback. */
async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    /* not JSON, e.g. a proxy's error page */
  }
  return UNAVAILABLE;
}

async function runTurn(request: AgLlmRequest, signal?: AbortSignal): Promise<AgLlmResponse> {
  try {
    const res = await fetch(TURN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getMerchantToken()}` },
      // The conversation items go back exactly as the API returned them, including the model's own
      // signature fields on tool calls, which Gemini needs to see again on the next turn.
      body: JSON.stringify({
        input: request.input,
        instructions: request.instructions ?? "",
        tools: request.tools ?? [],
        tool_choice: request.toolChoice ?? "auto",
      }),
      cache: "no-store",
      signal,
    });
    if (!res.ok) return failed(await errorDetail(res), `http_${res.status}`);
    return (await res.json()) as AgLlmResponse;
  } catch {
    // A stopped run is not an error: Studio ends it quietly when the turn comes back cancelled.
    if (signal?.aborted) return cancelled();
    return failed(UNREACHABLE, "network");
  }
}

/** The answer arrives in one piece, so the stream replays it as one text chunk and one chunk per tool call. */
async function* replay(complete: Promise<AgLlmResponse>): AsyncGenerator<AgAiEvent> {
  const response = await complete;
  for (const item of response.output) {
    if (item.type === "message") {
      const text = item.content.map((part) => (part.type === "text" ? part.text : part.refusal)).join("");
      if (text) yield { type: "TEXT_MESSAGE_CHUNK", messageId: item.id, role: "assistant", delta: text };
    } else if (item.type === "function_call") {
      yield { type: "TOOL_CALL_CHUNK", toolCallId: item.callId, toolCallName: item.name, delta: item.arguments };
    }
  }
}

export function refundPilotAdapter(): AgLlmAdapter {
  return {
    executeTurn(request, options) {
      // Never rejects: see failed() above.
      const complete = runTurn(request, options?.signal);
      return { stream: replay(complete), complete };
    },
  };
}

import type { Claim, ClaimRow, Order, Policy, Refund, SimulationResult, Stats, EvidenceAssessment } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const TOKEN_KEY = "refundpilot.merchantToken";

export function getMerchantToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || process.env.NEXT_PUBLIC_DEMO_MERCHANT_TOKEN || "";
  } catch {
    return process.env.NEXT_PUBLIC_DEMO_MERCHANT_TOKEN || "";
  }
}

export function setMerchantToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* private mode: token lives only for this page view */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, merchant = false): Promise<T> {
  const headers = new Headers(init.headers);
  if (merchant) headers.set("Authorization", `Bearer ${getMerchantToken()}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const photoUrl = (path: string) => `${API_URL}${path}`;

export const api = {
  health: () => request<{ ok: boolean; paypal: string; assessor: string }>("/health"),
  orders: () => request<Order[]>("/orders"),
  claim: (id: string) => request<Claim>(`/claims/${id}`),
  createClaim: (orderId: number, text: string, photos: Blob[]) => {
    const fd = new FormData();
    fd.set("order_id", String(orderId));
    fd.set("buyer_text", text);
    photos.forEach((p, i) => fd.append("photos", p, `photo-${i + 1}.jpg`));
    return request<Claim>("/claims", { method: "POST", body: fd });
  },
  addEvidence: (id: string, text: string, photos: Blob[]) => {
    const fd = new FormData();
    fd.set("buyer_text", text);
    photos.forEach((p, i) => fd.append("photos", p, `photo-${i + 1}.jpg`));
    return request<Claim>(`/claims/${id}/evidence`, { method: "POST", body: fd });
  },
  cancel: (id: string) => request<Claim>(`/claims/${id}/cancel`, { method: "POST" }),

  merchantClaims: () => request<ClaimRow[]>("/merchant/claims", {}, true),
  merchantClaim: (id: string) => request<Claim>(`/merchant/claims/${id}`, {}, true),
  stats: () => request<Stats>("/merchant/stats", {}, true),
  approve: (id: string, overrideCents?: number) =>
    request<Refund>(`/merchant/claims/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(overrideCents ? { override_cents: overrideCents } : {}),
    }, true),
  requestEvidence: (id: string, message: string) =>
    request<Claim>(`/merchant/claims/${id}/request-evidence`, { method: "POST", body: JSON.stringify({ message }) }, true),
  reject: (id: string, message: string) =>
    request<Claim>(`/merchant/claims/${id}/reject`, { method: "POST", body: JSON.stringify({ message }) }, true),
  reassess: (id: string, policyVersion?: string, rerunAi = false) =>
    request<Claim>(`/merchant/claims/${id}/reassess`, {
      method: "POST",
      body: JSON.stringify({ policy_version: policyVersion ?? null, rerun_ai: rerunAi }),
    }, true),
  reconcile: () => request<{ touched: number }>("/merchant/reconcile", { method: "POST" }, true),
  policies: () => request<Policy[]>("/policies", {}, true),
  simulate: (body: {
    policy_version: string;
    assessment: EvidenceAssessment;
    order_cents: number;
    remaining_cents?: number;
    days_since_delivery: number;
    prior_claims: number;
  }) => request<SimulationResult>("/policies/simulate", { method: "POST", body: JSON.stringify(body) }, true),
};

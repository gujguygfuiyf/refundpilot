// Mirrors backend/app/schemas.py. Keep in sync when the API changes.

export interface Product {
  id: number;
  sku: string;
  name: string;
  price_cents: number;
  currency: string;
  image_url: string;
  category: string;
}

export interface Order {
  id: number;
  paypal_order_id: string;
  capture_id: string;
  product: Product;
  amount_cents: number;
  currency: string;
  refunded_cents: number;
  remaining_cents: number;
  capture_status: string;
  captured_at: string;
  delivered_at: string;
  buyer_label: string;
  open_claim_id: string | null;
  last_claim_id: string | null;   // newest claim that wasn't cancelled
  claimable: boolean;             // money left and never claimed: each order can be claimed once
}

export interface EvidenceAssessment {
  product_match: "confirmed" | "likely" | "unlikely" | "mismatch" | "cannot_tell";
  damage_type:
    | "none_visible" | "cosmetic" | "broken" | "shattered" | "missing_part"
    | "wrong_item" | "not_as_described" | "other";
  severity: "none" | "minor" | "moderate" | "severe";
  visible_damage: boolean;
  evidence_quality: "sufficient" | "insufficient" | "unclear";
  stock_photo_suspected: boolean;
  description_consistent: "yes" | "partly" | "no" | "cannot_tell";
  needs_human_review: boolean;
  confidence: number;
  observations: string[];
  limitations: string[];
}

export interface Assessment {
  id: number;
  provider: string;
  model: string;
  output: EvidenceAssessment;
  latency_ms: number;
  refused: boolean;
  created_at: string;
}

export interface DecisionInputs {
  order_cents?: number;
  remaining_cents?: number;
  days_since_delivery?: number;
  prior_claims?: number;
}

export interface Decision {
  id: number;
  policy_version: string;
  rule_id: string;
  outcome: string;
  suggested_cents: number;
  requires_approval: boolean;
  reason: string;
  decision_inputs?: DecisionInputs | null;
  created_at: string;
}

export interface Refund {
  id: number;
  amount_cents: number;
  currency: string;
  status: string;
  paypal_request_id: string;
  paypal_refund_id: string | null;
  error: string | null;
  created_at: string;
  finalized_at: string | null;
}

export interface AuditEntry {
  actor: string;
  event: string;
  detail: Record<string, unknown> | null;
  ts: string;
}

export interface Claim {
  id: string;
  status: string;
  order: Order;
  buyer_text: string;
  policy_version: string;
  merchant_message: string;
  photo_urls: string[];
  assessment: Assessment | null;
  decision: Decision | null;
  refunds: Refund[];
  timeline: AuditEntry[];
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface ClaimRow {
  id: string;
  status: string;
  order_id: number;
  product_name: string;
  product_image_url: string;
  amount_cents: number;
  remaining_cents: number;
  damage_type: string | null;
  evidence_quality: string | null;
  product_match: string | null;
  outcome: string | null;
  rule_id: string | null;
  suggested_cents: number | null;
  policy_version: string;
  created_at: string;
  updated_at: string;
}

export interface DisputeRow {
  id: string;
  paypal_dispute_id: string;
  status: string;
  stage: string;
  reason: string;
  dispute_amount_cents: number;
  currency: string;
  order_id: number | null;
  capture_id: string | null;
  product_name: string | null;
  product_image_url: string | null;
  order_amount_cents: number | null;
  remaining_cents: number | null;
  claim_id: string | null;
  outcome: string | null;
  rule_id: string | null;
  suggested_cents: number;
  has_policy_decision: boolean;
  can_accept_claim: boolean;
  can_provide_evidence: boolean;
  allowed_accept_claim_types: string[];
  created_at: string;
  updated_at: string;
}

export interface DisputeEvidencePreview {
  evidence_type: string;
  refund_ids: string[];
  notes: string;
  photo_urls: string[];
}

export interface Dispute {
  id: string;
  paypal_dispute_id: string;
  status: string;
  stage: string;
  reason: string;
  dispute_amount_cents: number;
  currency: string;
  suggested_cents: number;
  has_policy_decision: boolean;
  can_accept_claim: boolean;
  can_provide_evidence: boolean;
  allowed_accept_claim_types: string[];
  order: Order | null;
  claim: Claim | null;
  evidence_preview: DisputeEvidencePreview;
  submitted_evidence_notes: string | null;
  timeline: AuditEntry[];
  raw: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface Stats {
  pending_review: number;
  evidence_requested: number;
  processing: number;
  completed: number;
  suggested_cents_pending: number;
  refunded_cents_total: number;
}

export interface Policy {
  version: string;
  name: string;
  body: Record<string, unknown> & { report_window_days: number; auto_approve_below_cents: number };
  sha256: string;
}

export interface SimulationResult {
  policy_version: string;
  simulation: true;
  outcome: string;
  suggested_cents: number;
  rule_id: string;
  requires_approval: boolean;
  reason: string;
}

export const ACTIVE_STATES = ["SUBMITTED", "ANALYZING", "ANALYZED", "PROCESSING", "APPROVED"];

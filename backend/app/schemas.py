"""API response/request shapes."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .models import AIAssessment, AuditLog, Claim, Decision, Order, Refund


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    price_cents: int
    currency: str
    image_url: str
    category: str


class OrderOut(BaseModel):
    id: int
    paypal_order_id: str
    capture_id: str
    product: ProductOut
    amount_cents: int
    currency: str
    refunded_cents: int
    remaining_cents: int
    capture_status: str
    captured_at: str
    delivered_at: str
    buyer_label: str
    open_claim_id: str | None = None
    last_claim_id: str | None = None      # most recent claim that wasn't cancelled
    claimable: bool = True                # money left and no earlier claim (a second claim only gets PRE-repeat)

    @classmethod
    def from_model(cls, o: Order, open_claim_id: str | None = None, last_claim_id: str | None = None) -> "OrderOut":
        return cls(id=o.id, paypal_order_id=o.paypal_order_id, capture_id=o.capture_id,
                   product=ProductOut.model_validate(o.product, from_attributes=True),
                   amount_cents=o.amount_cents, currency=o.currency, refunded_cents=o.refunded_cents,
                   remaining_cents=o.remaining_cents, capture_status=o.capture_status, captured_at=o.captured_at,
                   delivered_at=o.delivered_at, buyer_label=o.buyer_label, open_claim_id=open_claim_id,
                   last_claim_id=last_claim_id, claimable=o.remaining_cents > 0 and last_claim_id is None)


class AssessmentOut(BaseModel):
    id: int
    provider: str
    model: str
    output: dict
    latency_ms: int
    refused: bool
    created_at: str

    @classmethod
    def from_model(cls, a: AIAssessment) -> "AssessmentOut":
        return cls(id=a.id, provider=a.provider, model=a.model, output=a.output, latency_ms=a.latency_ms,
                   refused=bool(a.refused), created_at=a.created_at)


class DecisionOut(BaseModel):
    id: int
    policy_version: str
    rule_id: str
    outcome: str
    suggested_cents: int
    requires_approval: bool
    reason: str
    decision_inputs: dict | None = None
    created_at: str

    @classmethod
    def from_model(cls, d: Decision, decision_inputs: dict | None = None) -> "DecisionOut":
        return cls(id=d.id, policy_version=d.policy_version, rule_id=d.rule_id, outcome=d.outcome,
                   suggested_cents=d.suggested_cents, requires_approval=bool(d.requires_approval), reason=d.reason,
                   decision_inputs=decision_inputs, created_at=d.created_at)


class RefundOut(BaseModel):
    id: int
    amount_cents: int
    currency: str
    status: str
    paypal_request_id: str
    paypal_refund_id: str | None
    error: str | None
    created_at: str
    finalized_at: str | None

    @classmethod
    def from_model(cls, r: Refund) -> "RefundOut":
        return cls(id=r.id, amount_cents=r.amount_cents, currency=r.currency, status=r.status,
                   paypal_request_id=r.paypal_request_id, paypal_refund_id=r.paypal_refund_id, error=r.error,
                   created_at=r.created_at, finalized_at=r.finalized_at)


class AuditOut(BaseModel):
    actor: str
    event: str
    detail: dict | None
    ts: str

    @classmethod
    def from_model(cls, a: AuditLog) -> "AuditOut":
        return cls(actor=a.actor, event=a.event, detail=a.detail, ts=a.ts)


class ClaimOut(BaseModel):
    id: str
    status: str
    order: OrderOut
    buyer_text: str
    policy_version: str
    merchant_message: str
    photo_urls: list[str]
    assessment: AssessmentOut | None
    decision: DecisionOut | None
    refunds: list[RefundOut]
    timeline: list[AuditOut]
    created_at: str
    updated_at: str
    resolved_at: str | None

    @classmethod
    def from_model(cls, c: Claim, timeline: list[AuditLog]) -> "ClaimOut":
        dec_inputs: dict | None = None
        for a in reversed(timeline):
            if a.event == "decided" and isinstance(a.detail, dict) and isinstance(a.detail.get("inputs"), dict):
                dec_inputs = a.detail["inputs"]
                break
        return cls(id=c.id, status=c.status, order=OrderOut.from_model(c.order), buyer_text=c.buyer_text,
                   policy_version=c.policy_version, merchant_message=c.merchant_message,
                   photo_urls=[f"/claims/{c.id}/photos/{i}" for i in range(len(c.photos))],
                   assessment=AssessmentOut.from_model(c.latest_assessment) if c.latest_assessment else None,
                   decision=DecisionOut.from_model(c.latest_decision, decision_inputs=dec_inputs) if c.latest_decision else None,
                   refunds=[RefundOut.from_model(r) for r in c.refunds],
                   timeline=[AuditOut.from_model(a) for a in timeline],
                   created_at=c.created_at, updated_at=c.updated_at, resolved_at=c.resolved_at)


class ClaimRow(BaseModel):
    """Compact row for the merchant grid."""
    id: str
    status: str
    order_id: int
    product_name: str
    product_image_url: str
    amount_cents: int
    remaining_cents: int
    damage_type: str | None
    evidence_quality: str | None
    product_match: str | None
    outcome: str | None
    rule_id: str | None
    suggested_cents: int | None
    policy_version: str
    created_at: str
    updated_at: str


class DisputeRow(BaseModel):
    """Compact row for the merchant disputes grid."""
    id: str
    paypal_dispute_id: str
    status: str
    stage: str
    reason: str
    dispute_amount_cents: int
    currency: str
    order_id: int | None
    capture_id: str | None
    product_name: str | None
    product_image_url: str | None
    order_amount_cents: int | None
    remaining_cents: int | None
    claim_id: str | None
    outcome: str | None
    rule_id: str | None
    suggested_cents: int
    has_policy_decision: bool = False
    can_accept_claim: bool = False
    can_provide_evidence: bool = False
    allowed_accept_claim_types: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class DisputeEvidencePreviewOut(BaseModel):
    evidence_type: str
    refund_ids: list[str]
    notes: str
    photo_urls: list[str]


class DisputeOut(BaseModel):
    id: str
    paypal_dispute_id: str
    status: str
    stage: str
    reason: str
    dispute_amount_cents: int
    currency: str
    suggested_cents: int
    has_policy_decision: bool = False
    can_accept_claim: bool = False
    can_provide_evidence: bool = False
    allowed_accept_claim_types: list[str] = Field(default_factory=list)
    order: OrderOut | None
    claim: ClaimOut | None
    evidence_preview: DisputeEvidencePreviewOut
    submitted_evidence_notes: str | None
    timeline: list[AuditOut]
    raw: dict
    created_at: str
    updated_at: str
    resolved_at: str | None


class DisputeAcceptIn(BaseModel):
    override_cents: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2000)


class DisputeEvidenceIn(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class DisputeResetIn(BaseModel):
    reason: str | None = Field(default="verified not received in PayPal", max_length=300)


class ApproveIn(BaseModel):
    override_cents: int | None = Field(default=None, ge=1)


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ReassessIn(BaseModel):
    policy_version: str | None = None
    rerun_ai: bool = False


class SimulateIn(BaseModel):
    policy_version: str
    assessment: dict
    order_cents: int = Field(ge=1)
    remaining_cents: int | None = None
    days_since_delivery: int = 3
    prior_claims: int = 0


class StatsOut(BaseModel):
    pending_review: int
    evidence_requested: int
    processing: int
    completed: int
    suggested_cents_pending: int
    refunded_cents_total: int

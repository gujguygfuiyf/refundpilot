"""ORM tables. Money is always integer cents. Times are UTC ISO strings."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    # time-sortable, unguessable: <prefix>_<ts36><random>
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    return f"{prefix}_{ts:x}{secrets.token_hex(6)}"


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    image_url: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), default="general")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paypal_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    capture_id: Mapped[str] = mapped_column(String(64), unique=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    capture_status: Mapped[str] = mapped_column(String(32), default="COMPLETED")
    captured_at: Mapped[str] = mapped_column(String(40))
    delivered_at: Mapped[str] = mapped_column(String(40))
    buyer_label: Mapped[str] = mapped_column(String(64), default="Buyer")

    product: Mapped[Product] = relationship()

    @property
    def remaining_cents(self) -> int:
        return max(0, self.amount_cents - self.refunded_cents)


# Claim states
OPEN_STATES = ("SUBMITTED", "ANALYZING", "ANALYZED", "REVIEW_REQUIRED", "AUTO_ELIGIBLE",
               "EVIDENCE_REQUESTED", "APPROVED", "PROCESSING", "FAILED", "ANALYZE_FAILED")
TERMINAL_STATES = ("COMPLETED", "REJECTED", "CANCELLED")
APPROVABLE_STATES = ("REVIEW_REQUIRED", "AUTO_ELIGIBLE")


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("clm"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[str] = mapped_column(String(32), default="SUBMITTED")
    buyer_text: Mapped[str] = mapped_column(Text, default="")
    policy_version: Mapped[str] = mapped_column(String(64))
    merchant_message: Mapped[str] = mapped_column(Text, default="")   # e.g. evidence request text
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso, onupdate=now_iso)
    resolved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    order: Mapped[Order] = relationship()
    photos: Mapped[list["Photo"]] = relationship(back_populates="claim", order_by="Photo.id")
    assessments: Mapped[list["AIAssessment"]] = relationship(order_by="AIAssessment.id")
    decisions: Mapped[list["Decision"]] = relationship(order_by="Decision.id")
    refunds: Mapped[list["Refund"]] = relationship(order_by="Refund.id")

    @property
    def latest_assessment(self) -> "AIAssessment | None":
        return self.assessments[-1] if self.assessments else None

    @property
    def latest_decision(self) -> "Decision | None":
        return self.decisions[-1] if self.decisions else None


Index("ix_claims_order_status", Claim.order_id, Claim.status)


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    bytes: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str] = mapped_column(String(32), default="image/jpeg")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)

    claim: Mapped[Claim] = relationship(back_populates="photos")


class AIAssessment(Base):
    __tablename__ = "ai_assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    refused: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    assessment_id: Mapped[int | None] = mapped_column(ForeignKey("ai_assessments.id"), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    suggested_cents: Mapped[int] = mapped_column(Integer, default=0)
    requires_approval: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    capture_id: Mapped[str] = mapped_column(String(64))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    override_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(64), default="merchant")
    paypal_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    paypal_refund_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="CREATED")  # CREATED|UNKNOWN|PENDING|COMPLETED|FAILED|CANCELLED
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    finalized_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Policy(Base):
    __tablename__ = "policies"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict] = mapped_column(JSON)
    body_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(16))     # buyer|ai|rules|merchant|paypal|system
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[str] = mapped_column(String(40), default=now_iso)


class AssistantUsage(Base):
    """Insights assistant usage, one row per UTC day: the daily ceiling is read from here, so it survives restarts."""
    __tablename__ = "assistant_usage"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)     # YYYY-MM-DD (UTC)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # PayPal event id
    event_type: Mapped[str] = mapped_column(String(64))
    raw: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    processed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dsp"))
    paypal_dispute_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(64), default="MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED")
    status: Mapped[str] = mapped_column(String(64), default="WAITING_FOR_SELLER_RESPONSE")
    stage: Mapped[str] = mapped_column(String(64), default="INQUIRY")
    dispute_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    accept_attempts: Mapped[int] = mapped_column(Integer, default=0)
    evidence_attempts: Mapped[int] = mapped_column(Integer, default=0)
    submitted_evidence_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso, onupdate=now_iso)
    resolved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    order: Mapped[Order | None] = relationship()
    claim: Mapped[Claim | None] = relationship()


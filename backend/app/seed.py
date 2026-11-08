"""Seed products and captured sandbox orders; import policy files."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Claim, Order, Policy, Product, Refund
from .policy import load_policy_files, policy_sha

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
log = logging.getLogger("refundpilot")


def _parse_utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def seed_policies(db: Session) -> int:
    n = 0
    for p in load_policy_files():
        sha = policy_sha(p)
        row = db.get(Policy, p["version"])
        if row is None:
            db.add(Policy(version=p["version"], name=p["name"], body=p, body_sha256=sha))
            n += 1
        elif row.body_sha256 != sha:
            # Same version string, different content: decisions already recorded under this version would no
            # longer be reproducible. Bumping "version" is the author's job; we overwrite and say so loudly.
            log.warning("policy %s changed without a version bump; bump \"version\" to keep the audit meaningful",
                        p["version"])
            row.body, row.body_sha256, row.name = p, sha, p["name"]
            n += 1
    db.commit()
    return n


def seed_products(db: Session, path: Path = SEED_DIR / "products.json") -> int:
    if not path.exists():
        return 0
    n = 0
    for p in json.loads(path.read_text(encoding="utf-8")):
        row = db.execute(select(Product).where(Product.sku == p["sku"])).scalars().first()
        if row is None:
            db.add(Product(**p))
            n += 1
        else:
            for k, v in p.items():
                setattr(row, k, v)
    db.commit()
    return n


def seed_orders(db: Session, path: Path = SEED_DIR / "orders.json", paypal=None) -> int:
    """orders.json entries: {paypal_order_id, capture_id, sku, amount_cents, currency, captured_at, buyer_label}.
    If a PayPal gateway is given, also refresh capture status (see refresh_captures)."""
    if not path.exists():
        return 0
    n = 0
    for o in json.loads(path.read_text(encoding="utf-8")):
        product = db.execute(select(Product).where(Product.sku == o["sku"])).scalars().first()
        if product is None:
            continue
        row = db.execute(select(Order).where(Order.capture_id == o["capture_id"])).scalars().first()
        if row is None:
            captured = o.get("captured_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
            delivered = (datetime.fromisoformat(captured) + timedelta(days=settings.delivered_offset_days)).isoformat(timespec="seconds")
            row = Order(paypal_order_id=o["paypal_order_id"], capture_id=o["capture_id"], product_id=product.id,
                        amount_cents=int(o["amount_cents"]), currency=o.get("currency", "USD"), refunded_cents=0,
                        captured_at=captured, delivered_at=o.get("delivered_at") or delivered,
                        buyer_label=o.get("buyer_label", "Buyer"))
            db.add(row)
            n += 1
    db.commit()
    if paypal is not None:
        refresh_captures(db, paypal)
    return n


def refresh_captures(db: Session, paypal) -> int:
    """Ask PayPal for each sandbox order's capture status, so an order refunded elsewhere (e.g. from a local copy of
    the demo sharing the same captures) shows nothing left to refund. One GET per order, so the API runs this in the
    background after startup instead of making a cold start wait for it. Returns orders newly marked refunded."""
    n = 0
    for row in db.execute(select(Order).where(Order.capture_id.not_like("DEMO-%"))).scalars().all():
        try:
            live = paypal.get_capture(row.capture_id)
        except Exception:
            continue
        row.capture_status = live.get("status", row.capture_status)
        if row.capture_status == "REFUNDED" and row.refunded_cents < row.amount_cents:
            row.refunded_cents = row.amount_cents
            n += 1
        db.commit()
    return n


def seed_demo_orders(db: Session, paypal) -> int:
    """Offline mode (FakePayPal): one captured demo order per product so the buyer page and approve flow work
    without PayPal credentials. Idempotent; re-registers captures in the fake gateway after a restart."""
    from .paypal import FakePayPal
    if not isinstance(paypal, FakePayPal):
        return 0
    n = 0
    for i, product in enumerate(db.execute(select(Product).order_by(Product.id)).scalars().all()):
        cap_id = f"DEMO-CAP-{product.sku}"
        row = db.execute(select(Order).where(Order.capture_id == cap_id)).scalars().first()
        if row is None:
            captured = datetime.now(timezone.utc) - timedelta(days=5)
            row = Order(paypal_order_id=f"DEMO-ORD-{product.sku}", capture_id=cap_id, product_id=product.id,
                        amount_cents=product.price_cents, currency=product.currency, refunded_cents=0,
                        captured_at=captured.isoformat(timespec="seconds"),
                        delivered_at=(captured + timedelta(days=settings.delivered_offset_days)).isoformat(timespec="seconds"),
                        buyer_label=f"Buyer {chr(65 + i % 26)}")
            db.add(row)
            n += 1
        paypal.register_capture(cap_id, row.amount_cents)
        paypal.captures[cap_id]["refunded"] = row.refunded_cents or 0
    db.commit()
    return n


def refresh_demo_delivery(db: Session, now: datetime | None = None) -> int:
    """Demo only (DEMO_ROLLING_DELIVERY, on by default): an order nobody has claimed yet is kept "delivered
    DEMO_DELIVERED_DAYS_AGO days ago". Sandbox orders are minted weeks before judges try the live demo; without
    this every new claim would land outside the policy's report window (PRE-window, $0).
    Orders with a claim (open or finished; cancelled ones don't count) keep their date, so recorded decisions
    stay reproducible. Returns the number of orders moved."""
    if not settings.demo_rolling_delivery:
        return 0
    now = now or datetime.now(timezone.utc)
    target = now - timedelta(days=settings.demo_delivered_days_ago)
    claimed = select(Claim.order_id).where(Claim.status != "CANCELLED")
    n = 0
    for o in db.execute(select(Order).where(Order.id.not_in(claimed))).scalars().all():
        if o.remaining_cents <= 0:
            continue
        # move only once the date has drifted a full day past the target, so this stays a no-op most minutes
        if _parse_utc(o.delivered_at) < target - timedelta(days=1):
            o.delivered_at = target.isoformat(timespec="seconds")
            n += 1
    if n:
        db.commit()
    return n


def reconcile_orders_and_ledger(db: Session, paypal=None) -> dict:
    """Calibrate orders and refund ledger:
    1. Restore any claims that have COMPLETED refunds back to COMPLETED status if they were mistakenly marked CANCELLED.
    2. Ensure every order's refunded_cents is at least the sum of all known COMPLETED refunds on that order and the seed baseline.
    3. Restore original delivered_at from seed/orders.json for orders with refunds or claims so historical audit traces remain reproducible.
    4. Query live PayPal captures (if paypal gateway provided) to capture any external refunds in PayPal Sandbox."""
    claims_restored = 0
    cancelled_with_refunds = (
        db.execute(
            select(Claim)
            .join(Refund, Claim.id == Refund.claim_id)
            .where(Refund.status == "COMPLETED", Claim.status == "CANCELLED")
        )
        .scalars()
        .all()
    )
    for c in cancelled_with_refunds:
        c.status = "COMPLETED"
        claims_restored += 1

    seed_map: dict[str, dict] = {}
    orders_json_path = SEED_DIR / "orders.json"
    if orders_json_path.exists():
        try:
            for item in json.loads(orders_json_path.read_text(encoding="utf-8")):
                seed_map[item["capture_id"]] = item
        except Exception as e:
            log.warning("could not read seed orders.json: %s", e)

    orders_calibrated = 0
    orders = db.execute(select(Order)).scalars().all()
    for o in orders:
        orig_refunded = o.refunded_cents
        orig_delivered = o.delivered_at

        completed_cents = db.execute(
            select(func.coalesce(func.sum(Refund.amount_cents), 0))
            .where(Refund.capture_id == o.capture_id, Refund.status == "COMPLETED")
        ).scalar_one()

        seed_entry = seed_map.get(o.capture_id)
        seed_refunded = seed_entry.get("refunded_cents", 0) if seed_entry else 0

        target_refunded = max(o.refunded_cents, completed_cents, seed_refunded)
        if target_refunded != o.refunded_cents:
            o.refunded_cents = target_refunded

        has_history = o.refunded_cents > 0 or db.execute(
            select(Claim).where(Claim.order_id == o.id, Claim.status != "CANCELLED")
        ).first() is not None

        if seed_entry and has_history:
            captured_iso = seed_entry.get("captured_at") or o.captured_at
            delivered_iso = seed_entry.get("delivered_at") or (
                datetime.fromisoformat(captured_iso) + timedelta(days=settings.delivered_offset_days)
            ).isoformat(timespec="seconds")
            o.delivered_at = delivered_iso
            o.captured_at = captured_iso

        if o.refunded_cents != orig_refunded or o.delivered_at != orig_delivered:
            orders_calibrated += 1

    db.commit()

    paypal_refreshed = 0
    if paypal is not None:
        paypal_refreshed = refresh_captures(db, paypal)

    return {
        "claims_restored": claims_restored,
        "orders_calibrated": orders_calibrated,
        "paypal_captures_refreshed": paypal_refreshed,
    }


def seed_all(db: Session, paypal=None, refresh: bool = True) -> dict:
    """refresh=False skips the per-order PayPal status check (the API's startup does it in the background)."""
    out = {"policies": seed_policies(db), "products": seed_products(db),
           "orders": seed_orders(db, paypal=paypal if refresh else None)}
    out["demo_orders"] = seed_demo_orders(db, paypal)
    out["ledger_calibrated"] = reconcile_orders_and_ledger(db, paypal=paypal if refresh else None)
    out["delivery_refreshed"] = refresh_demo_delivery(db)
    return out

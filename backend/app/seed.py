"""Seed products and captured sandbox orders; import policy files."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Order, Policy, Product
from .policy import load_policy_files, policy_sha

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def seed_policies(db: Session) -> int:
    n = 0
    for p in load_policy_files():
        sha = policy_sha(p)
        row = db.get(Policy, p["version"])
        if row is None:
            db.add(Policy(version=p["version"], name=p["name"], body=p, body_sha256=sha))
            n += 1
        elif row.body_sha256 != sha:
            # same version string but changed content: refuse silently? No: bump is the author's job. Overwrite + log.
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
    If a PayPal gateway is given, refresh capture status (REFUNDED => remaining 0)."""
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
                        amount_cents=int(o["amount_cents"]), currency=o.get("currency", "USD"), captured_at=captured,
                        delivered_at=o.get("delivered_at") or delivered, buyer_label=o.get("buyer_label", "Buyer"))
            db.add(row)
            n += 1
        if paypal is not None:
            try:
                live = paypal.get_capture(row.capture_id)
                row.capture_status = live.get("status", row.capture_status)
                if row.capture_status == "REFUNDED":
                    row.refunded_cents = row.amount_cents
            except Exception:
                pass
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
                        amount_cents=product.price_cents, currency=product.currency,
                        captured_at=captured.isoformat(timespec="seconds"),
                        delivered_at=(captured + timedelta(days=settings.delivered_offset_days)).isoformat(timespec="seconds"),
                        buyer_label=f"Buyer {chr(65 + i % 26)}")
            db.add(row)
            n += 1
        paypal.register_capture(cap_id, row.amount_cents)
        paypal.captures[cap_id]["refunded"] = row.refunded_cents
    db.commit()
    return n


def seed_all(db: Session, paypal=None) -> dict:
    out = {"policies": seed_policies(db), "products": seed_products(db), "orders": seed_orders(db, paypal=paypal)}
    out["demo_orders"] = seed_demo_orders(db, paypal)
    return out

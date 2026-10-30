"""Mint captured sandbox orders for the demo.

Usage (from backend/):
  python scripts/mint_orders.py --count 8            # create orders, print approve links, wait, capture, append to seed/orders.json
  python scripts/mint_orders.py --count 3 --sku MUG-01

Flow per order: POST /v2/checkout/orders (intent CAPTURE) -> you open the approve link with the sandbox
Personal account and pay -> script polls GET order until APPROVED -> POST capture -> writes capture id.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings          # noqa: E402
from app.paypal import PayPalClient      # noqa: E402

SEED = Path(__file__).resolve().parent.parent / "seed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--sku", default=None, help="fix one SKU; default rotates through products.json")
    ap.add_argument("--wait", type=int, default=600, help="seconds to wait for approval per order")
    args = ap.parse_args()

    if not settings.paypal_client_id or not settings.paypal_client_secret:
        print("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET not set"); return 2
    pp = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    products = json.loads((SEED / "products.json").read_text(encoding="utf-8"))
    if args.sku:
        products = [p for p in products if p["sku"] == args.sku] or sys.exit(f"sku {args.sku} not found")
    orders_path = SEED / "orders.json"
    existing = json.loads(orders_path.read_text(encoding="utf-8")) if orders_path.exists() else []

    for i in range(args.count):
        p = products[i % len(products)]
        o = pp.create_order(p["price_cents"], p["currency"], p["name"],
                            return_url="https://example.com/return", cancel_url="https://example.com/cancel")
        approve = next((l["href"] for l in o.get("links", []) if l.get("rel") in ("approve", "payer-action")), None)
        print(f"\n[{i + 1}/{args.count}] {p['sku']} ${p['price_cents'] / 100:.2f}  order {o['id']}")
        print(f"   open and pay with the sandbox Personal account:\n   {approve}")
        deadline = time.time() + args.wait
        status = o.get("status")
        while status not in ("APPROVED", "COMPLETED") and time.time() < deadline:
            time.sleep(4)
            status = pp.get_order(o["id"]).get("status")
        if status not in ("APPROVED", "COMPLETED"):
            print("   not approved in time, skipping"); continue
        cap = pp.capture_order(o["id"]) if status == "APPROVED" else pp.get_order(o["id"])
        capture = cap["purchase_units"][0]["payments"]["captures"][0]
        entry = {"paypal_order_id": o["id"], "capture_id": capture["id"], "sku": p["sku"],
                 "amount_cents": p["price_cents"], "currency": p["currency"],
                 "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "buyer_label": f"Buyer {chr(65 + (len(existing) % 26))}"}
        existing.append(entry)
        orders_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"   captured {capture['id']} status {capture['status']}  -> seed/orders.json ({len(existing)} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Mint captured sandbox orders for the demo.

Usage (from backend/):
  python scripts/mint_orders.py --count 8            # one at a time: print a link, wait until it's paid, capture
  python scripts/mint_orders.py --count 3 --sku MUG-01
  python scripts/mint_orders.py --count 20 --batch    # print all 20 links at once; pay them in browser tabs in any
                                                      # order, each one is captured as soon as it is approved

Flow per order: POST /v2/checkout/orders (intent CAPTURE) -> you open the approve link with the sandbox
Personal account and pay -> script polls GET order until APPROVED -> POST capture -> writes capture id.
Each captured order is appended to seed/orders.json immediately, so stopping the script early loses nothing.
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
ORDERS_PATH = SEED / "orders.json"


def approve_link(order: dict) -> str | None:
    return next((l["href"] for l in order.get("links", []) if l.get("rel") in ("approve", "payer-action")), None)


def record_capture(pp: PayPalClient, order_id: str, status: str, product: dict, existing: list[dict]) -> None:
    cap = pp.capture_order(order_id) if status == "APPROVED" else pp.get_order(order_id)
    capture = cap["purchase_units"][0]["payments"]["captures"][0]
    existing.append({"paypal_order_id": order_id, "capture_id": capture["id"], "sku": product["sku"],
                     "amount_cents": product["price_cents"], "currency": product["currency"],
                     "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "buyer_label": f"Buyer {chr(65 + (len(existing) % 26))}"})
    ORDERS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"   captured {capture['id']} ({product['sku']}) status {capture['status']}  "
          f"-> seed/orders.json ({len(existing)} total)")


def one_by_one(pp: PayPalClient, products: list[dict], count: int, wait_s: int, existing: list[dict]) -> None:
    for i in range(count):
        p = products[i % len(products)]
        o = pp.create_order(p["price_cents"], p["currency"], p["name"],
                            return_url="https://example.com/return", cancel_url="https://example.com/cancel")
        print(f"\n[{i + 1}/{count}] {p['sku']} ${p['price_cents'] / 100:.2f}  order {o['id']}")
        print(f"   open and pay with the sandbox Personal account:\n   {approve_link(o)}")
        deadline = time.time() + wait_s
        status = o.get("status")
        while status not in ("APPROVED", "COMPLETED") and time.time() < deadline:
            time.sleep(4)
            status = pp.get_order(o["id"]).get("status")
        if status not in ("APPROVED", "COMPLETED"):
            print("   not approved in time, skipping"); continue
        record_capture(pp, o["id"], status, p, existing)


def batch(pp: PayPalClient, products: list[dict], count: int, wait_s: int, existing: list[dict]) -> None:
    pending: dict[str, dict] = {}
    for i in range(count):
        p = products[i % len(products)]
        o = pp.create_order(p["price_cents"], p["currency"], p["name"],
                            return_url="https://example.com/return", cancel_url="https://example.com/cancel")
        pending[o["id"]] = p
        print(f"[{i + 1}/{count}] {p['sku']} ${p['price_cents'] / 100:.2f}  {approve_link(o)}")
    print(f"\nPay the links above with the sandbox Personal account, in any order. "
          f"Waiting up to {wait_s // 60} min; Ctrl+C stops (paid ones are already saved).")
    deadline = time.time() + wait_s
    while pending and time.time() < deadline:
        time.sleep(4)
        for oid in list(pending):
            try:
                status = pp.get_order(oid).get("status")
                if status in ("APPROVED", "COMPLETED"):
                    record_capture(pp, oid, status, pending.pop(oid), existing)
            except Exception as e:              # one flaky call (e.g. a sandbox 503) must not lose the rest
                print(f"   {oid}: {type(e).__name__}: {str(e)[:120]} (will retry)")
    if pending:
        print(f"\n{len(pending)} order(s) were not paid in time and were skipped.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--sku", default=None, help="fix one SKU; default rotates through products.json")
    ap.add_argument("--batch", action="store_true", help="print all approve links first, capture as they get paid")
    ap.add_argument("--wait", type=int, default=None,
                    help="seconds to wait: per order (default 600), or in total with --batch (default 3600)")
    args = ap.parse_args()

    if not settings.paypal_client_id or not settings.paypal_client_secret:
        print("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET not set"); return 2
    pp = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    products = json.loads((SEED / "products.json").read_text(encoding="utf-8"))
    if args.sku:
        products = [p for p in products if p["sku"] == args.sku] or sys.exit(f"sku {args.sku} not found")
    existing = json.loads(ORDERS_PATH.read_text(encoding="utf-8")) if ORDERS_PATH.exists() else []

    if args.batch:
        batch(pp, products, args.count, args.wait or 3600, existing)
    else:
        one_by_one(pp, products, args.count, args.wait or 600, existing)
    print("\nRestart the API (or POST /dev/seed) to load the new orders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

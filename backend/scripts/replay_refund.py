"""Show PayPal idempotency on a refund that already happened (for the demo video and for judges' questions).

Replays the exact refund request RefundPilot sent for a claim, with the same PayPal-Request-Id, twice, and prints
what PayPal returns. Expected: the ORIGINAL refund id both times, and the capture still refunded exactly once.
Use it only on a claim whose refund already COMPLETED: the capture is then fully refunded, so even if PayPal no longer
recognised the request id it would refuse a second refund instead of paying again.

Usage (from backend/, sandbox credentials in .env):
  python scripts/replay_refund.py --claim clm_... --capture 1AB23456CD789012E --cents 4900
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings                       # noqa: E402
from app.paypal import PayPalClient, PayPalError, fmt_amount   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", required=True, help="claim id, e.g. clm_1a1ace47...")
    ap.add_argument("--capture", required=True, help="PayPal capture id of the claim's order")
    ap.add_argument("--cents", type=int, required=True, help="the amount that was refunded, in cents")
    ap.add_argument("--attempt", type=int, default=1, help="refund attempt number used by RefundPilot (default 1)")
    ap.add_argument("--currency", default="USD")
    a = ap.parse_args()
    if "sandbox" not in settings.paypal_base_url:
        print("Refusing to run against a non-sandbox PayPal URL.")
        return 2

    pp = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    request_id = f"rp-{a.claim}-{a.attempt}"
    print(f"PayPal sandbox · capture {a.capture}")
    before = pp.get_capture(a.capture)
    print(f"Capture status before: {before.get('status')}\n")
    seen = []
    for i in (1, 2):
        print(f"Retry {i}: POST /v2/payments/captures/{a.capture}/refund  ${fmt_amount(a.cents)}")
        print(f"         PayPal-Request-Id: {request_id}")
        try:
            r = pp.refund_capture(a.capture, a.cents, a.currency, request_id=request_id,
                                  invoice_id=f"claim-{a.claim}", note="Refund for the issue you reported")
            amt = r.get("amount", {}).get("value", fmt_amount(a.cents))
            print(f"  -> refund {r.get('id')}  status {r.get('status')}  ${amt}\n")
            seen.append(r.get("id"))
        except PayPalError as e:
            print(f"  -> PayPal refused: {e.status} {e.body[:160]}\n   (no money moved)\n")
            seen.append(None)
    after = pp.get_capture(a.capture)
    print(f"Capture status after:  {after.get('status')}")
    if seen[0] and seen[0] == seen[1]:
        print(f"Same refund id both times ({seen[0]}): the retry returned the original refund, nothing paid twice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

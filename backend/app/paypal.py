"""PayPal REST client (Orders v2 / Payments v2 / Webhooks). Token cached, idempotent refunds via PayPal-Request-Id."""
from __future__ import annotations

import secrets
import time
from typing import Protocol

import httpx


class PayPalError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"PayPal {status}: {body}")
        self.status = status
        self.body = body


class PayPalGateway(Protocol):
    """What the service layer needs. Tests inject a fake."""
    def get_capture(self, capture_id: str) -> dict: ...
    def refund_capture(self, capture_id: str, cents: int, currency: str, *, request_id: str,
                       invoice_id: str, note: str = "") -> dict: ...
    def get_refund(self, refund_id: str) -> dict: ...
    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool: ...


def fmt_amount(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


class PayPalClient:
    def __init__(self, client_id: str, secret: str, base_url: str = "https://api-m.sandbox.paypal.com"):
        self._http = httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, connect=10.0))
        self._cid, self._sec = client_id, secret
        self._token, self._exp = "", 0.0

    # ---- auth ----
    def _auth(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = self._http.post("/v1/oauth2/token", auth=(self._cid, self._sec), data={"grant_type": "client_credentials"})
        if r.status_code != 200:
            raise PayPalError(r.status_code, r.text[:300])
        d = r.json()
        self._token, self._exp = d["access_token"], time.time() + float(d.get("expires_in", 3600))
        return self._token

    def _req(self, method: str, path: str, *, json: dict | None = None, request_id: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json"}
        if request_id:
            h["PayPal-Request-Id"] = request_id
        r = self._http.request(method, path, json=json, headers=h)
        if r.status_code >= 400:
            raise PayPalError(r.status_code, r.text[:500])
        return r.json() if r.content else {}

    # ---- orders (seeding) ----
    def create_order(self, cents: int, currency: str, description: str, return_url: str, cancel_url: str) -> dict:
        body = {"intent": "CAPTURE",
                "purchase_units": [{"amount": {"currency_code": currency, "value": fmt_amount(cents)},
                                    "description": description[:127]}],
                "payment_source": {"paypal": {"experience_context": {"return_url": return_url, "cancel_url": cancel_url,
                                                                      "user_action": "PAY_NOW"}}}}
        # unique per call: two orders created in the same millisecond must not collapse into one
        return self._req("POST", "/v2/checkout/orders", json=body,
                         request_id=f"order-{int(time.time()*1000)}-{secrets.token_hex(4)}")

    def get_order(self, order_id: str) -> dict:
        return self._req("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id: str) -> dict:
        return self._req("POST", f"/v2/checkout/orders/{order_id}/capture", json={}, request_id=f"cap-{order_id}")

    # ---- payments ----
    def get_capture(self, capture_id: str) -> dict:
        return self._req("GET", f"/v2/payments/captures/{capture_id}")

    def refund_capture(self, capture_id: str, cents: int, currency: str, *, request_id: str,
                       invoice_id: str, note: str = "") -> dict:
        body: dict = {"amount": {"value": fmt_amount(cents), "currency_code": currency}, "invoice_id": invoice_id[:127]}
        if note:
            body["note_to_payer"] = note[:255]
        return self._req("POST", f"/v2/payments/captures/{capture_id}/refund", json=body, request_id=request_id)

    def get_refund(self, refund_id: str) -> dict:
        return self._req("GET", f"/v2/payments/refunds/{refund_id}")

    # ---- webhooks ----
    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        try:
            payload = {"auth_algo": h["paypal-auth-algo"], "cert_url": h["paypal-cert-url"],
                       "transmission_id": h["paypal-transmission-id"], "transmission_sig": h["paypal-transmission-sig"],
                       "transmission_time": h["paypal-transmission-time"], "webhook_id": webhook_id,
                       "webhook_event": event}
        except KeyError:
            return False
        return self._req("POST", "/v1/notifications/verify-webhook-signature", json=payload).get("verification_status") == "SUCCESS"


class FakePayPal:
    """In-memory gateway for tests and offline dev. Idempotent on request_id like the real thing."""
    def __init__(self, captures: dict[str, int] | None = None, currency: str = "USD",
                 refund_status: str = "COMPLETED"):
        self.captures = {cid: {"id": cid, "status": "COMPLETED", "amount_cents": cents, "refunded": 0}
                         for cid, cents in (captures or {}).items()}
        self.currency = currency
        self.refund_status = refund_status
        self.refunds: dict[str, dict] = {}            # refund_id -> refund
        self.by_request: dict[str, str] = {}          # request_id -> refund_id
        self.calls: list[tuple] = []
        self.fail_next: Exception | None = None

    def register_capture(self, capture_id: str, cents: int) -> None:
        self.captures.setdefault(capture_id, {"id": capture_id, "status": "COMPLETED", "amount_cents": cents, "refunded": 0})

    def _capture(self, capture_id: str) -> dict:
        try:
            return self.captures[capture_id]
        except KeyError:
            raise PayPalError(404, '{"name":"RESOURCE_NOT_FOUND","message":"capture not registered in FakePayPal"}')

    def get_capture(self, capture_id: str) -> dict:
        self.calls.append(("get_capture", capture_id))
        c = self._capture(capture_id)
        st = "REFUNDED" if c["refunded"] >= c["amount_cents"] else ("PARTIALLY_REFUNDED" if c["refunded"] else "COMPLETED")
        return {"id": capture_id, "status": st, "amount": {"value": fmt_amount(c["amount_cents"]), "currency_code": self.currency}}

    def refund_capture(self, capture_id: str, cents: int, currency: str, *, request_id: str, invoice_id: str, note: str = "") -> dict:
        self.calls.append(("refund", capture_id, cents, request_id))
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        if request_id in self.by_request:                  # idempotent replay
            return self.refunds[self.by_request[request_id]]
        c = self._capture(capture_id)
        if c["refunded"] + cents > c["amount_cents"]:
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"REFUND_AMOUNT_EXCEEDED"}]}')
        rid = f"R{len(self.refunds) + 1:05d}"
        r = {"id": rid, "status": self.refund_status, "amount": {"value": fmt_amount(cents), "currency_code": currency},
             "invoice_id": invoice_id}
        self.refunds[rid] = r
        self.by_request[request_id] = rid
        if self.refund_status == "COMPLETED":
            c["refunded"] += cents
        return r

    def complete(self, refund_id: str) -> dict:
        """Test helper: flip a PENDING refund to COMPLETED (as PayPal would)."""
        r = self.refunds[refund_id]
        r["status"] = "COMPLETED"
        return r

    def get_refund(self, refund_id: str) -> dict:
        self.calls.append(("get_refund", refund_id))
        return self.refunds[refund_id]

    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool:
        return headers.get("x-fake-valid", "1") == "1"

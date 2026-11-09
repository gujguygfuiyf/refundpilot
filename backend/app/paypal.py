"""PayPal REST client (Orders v2 / Payments v2 / Disputes v1 / Webhooks). Token cached, idempotent via PayPal-Request-Id."""
from __future__ import annotations

import json as json_mod
import secrets
import time
from datetime import datetime, timezone
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
    def list_disputes(self, page_size: int = 50) -> list[dict]: ...
    def get_dispute(self, dispute_id: str) -> dict: ...
    def accept_claim(self, dispute_id: str, *, cents: int, currency: str, dispute_amount_cents: int,
                     note: str, request_id: str, reason: str = "COMPANY_POLICY") -> dict: ...
    def provide_evidence(self, dispute_id: str, *, notes: str, request_id: str,
                         evidence_type: str = "OTHER", refund_ids: list[str] | None = None,
                         documents: list[tuple[str, bytes, str]] | None = None) -> dict: ...
    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool: ...


def fmt_amount(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def link_actions(payload: dict | None) -> list[str] | None:
    """HATEOAS action names on a PayPal resource, spelled like the endpoint path ("accept-claim").

    PayPal's docs name dispute actions after their endpoints (`accept-claim`, `provide-evidence`) while link `rel`
    values may use underscores (`accept_claim`), so both spellings are normalised to the hyphenated form.
    Returns None when the payload has no `links` list at all (callers fail closed on that)."""
    links = (payload or {}).get("links")
    if not isinstance(links, list):
        return None
    out: list[str] = []
    for lk in links:
        if isinstance(lk, dict):
            name = str(lk.get("rel") or "").strip().lower().replace("_", "-")
            if name and name not in out:
                out.append(name)
    return out


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

    # ---- disputes v1 ----
    def list_disputes(self, page_size: int = 50) -> list[dict]:
        resp = self._req("GET", f"/v1/customer/disputes?page_size={max(1, min(page_size, 50))}")
        return resp.get("items") or []

    def get_dispute(self, dispute_id: str) -> dict:
        return self._req("GET", f"/v1/customer/disputes/{dispute_id}")

    def accept_claim(self, dispute_id: str, *, cents: int, currency: str, dispute_amount_cents: int,
                     note: str, request_id: str, reason: str = "COMPANY_POLICY") -> dict:
        body: dict = {
            "note": (note or "Accepting claim under merchant refund policy.")[:2000],
            "accept_claim_reason": reason,
        }
        if cents < dispute_amount_cents:
            body["accept_claim_type"] = "PARTIAL_REFUND"
            body["refund_amount"] = {"currency_code": currency, "value": fmt_amount(cents)}
        else:
            body["accept_claim_type"] = "REFUND"
        return self._req("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim", json=body, request_id=request_id)

    def provide_evidence(self, dispute_id: str, *, notes: str, request_id: str,
                         evidence_type: str = "OTHER", refund_ids: list[str] | None = None,
                         documents: list[tuple[str, bytes, str]] | None = None) -> dict:
        ev: dict = {"evidence_type": evidence_type, "notes": notes[:2000]}
        if refund_ids:
            ev["evidence_info"] = {"refund_ids": list(refund_ids)}
        if documents:
            ev["documents"] = [{"name": name} for name, _, _ in documents]
        payload = {"evidences": [ev]}
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("input", ("input.json", json_mod.dumps(payload).encode("utf-8"), "application/json")),
        ]
        for idx, (name, data, mime) in enumerate(documents or [], start=1):
            files.append((f"file{idx}", (name, data, mime)))
        headers = {"Authorization": f"Bearer {self._auth()}", "PayPal-Request-Id": request_id}
        r = self._http.post(f"/v1/customer/disputes/{dispute_id}/provide-evidence", files=files, headers=headers)
        if r.status_code >= 400:
            raise PayPalError(r.status_code, r.text[:500])
        return r.json() if r.content else {}

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
        self.disputes: dict[str, dict] = {}           # dispute_id -> dispute payload
        self._run = secrets.token_hex(3).upper() + "-"   # see refund_capture
        self.by_request: dict[str, str] = {}          # request_id -> refund_id
        self.dispute_by_request: dict[str, dict] = {} # request_id -> response
        self.dispute_resolves_immediately = True      # False: accept-claim leaves the dispute UNDER_REVIEW
        self.deferred_accepts: dict[str, tuple] = {}  # dispute_id -> (refund, cents, currency)
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
        # The prefix differs per process: a restarted offline API keeps its database, and a counter alone would
        # hand out a refund id that is already stored (refunds.paypal_refund_id is unique).
        rid = f"R{self._run}{len(self.refunds) + 1:05d}"
        r = {"id": rid, "status": self.refund_status, "amount": {"value": fmt_amount(cents), "currency_code": currency},
             "invoice_id": invoice_id, "capture_id": capture_id}
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

    # ---- disputes simulation & endpoints ----
    def open_dispute(self, capture_id: str, *, dispute_id: str | None = None,
                     reason: str = "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
                     stage: str = "INQUIRY", status: str = "WAITING_FOR_SELLER_RESPONSE",
                     cents: int | None = None, buyer_note: str = "Item arrived damaged") -> dict:
        """Test/demo helper: open a simulated PayPal dispute against a capture."""
        c = self._capture(capture_id)
        did = dispute_id or f"PP-D-{self._run}{len(self.disputes) + 1:04d}"
        amt = cents if cents is not None else c["amount_cents"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        d = {
            "dispute_id": did,
            "create_time": now,
            "update_time": now,
            "disputed_transactions": [
                {
                    "seller_transaction_id": capture_id,
                    "buyer_transaction_id": f"BUY-{capture_id}",
                    "gross_amount": {"currency_code": self.currency, "value": fmt_amount(c["amount_cents"])},
                    "transaction_status": "COMPLETED",
                }
            ],
            "reason": reason,
            "status": status,
            "dispute_state": "REQUIRED_ACTION" if status == "WAITING_FOR_SELLER_RESPONSE" else status,
            "dispute_life_cycle_stage": stage,
            "dispute_amount": {"currency_code": self.currency, "value": fmt_amount(amt)},
            "allowed_response_options": {
                "accept_claim": {
                    "accept_claim_types": ["REFUND", "PARTIAL_REFUND"],
                }
            },
            "messages": [{"posted_by": "BUYER", "time_posted": now, "content": buyer_note}] if buyer_note else [],
            "links": [
                {"rel": "self", "href": f"/v1/customer/disputes/{did}", "method": "GET"},
                {"rel": "accept-claim", "href": f"/v1/customer/disputes/{did}/accept-claim", "method": "POST"},
                {"rel": "provide-evidence", "href": f"/v1/customer/disputes/{did}/provide-evidence", "method": "POST"},
            ],
        }
        self.disputes[did] = d
        return d

    def list_disputes(self, page_size: int = 50) -> list[dict]:
        self.calls.append(("list_disputes", page_size))
        return list(self.disputes.values())[:page_size]

    def get_dispute(self, dispute_id: str) -> dict:
        self.calls.append(("get_dispute", dispute_id))
        try:
            return self.disputes[dispute_id]
        except KeyError:
            raise PayPalError(404, '{"name":"RESOURCE_NOT_FOUND","message":"dispute not found in FakePayPal"}')

    def accept_claim(self, dispute_id: str, *, cents: int, currency: str, dispute_amount_cents: int,
                     note: str, request_id: str, reason: str = "COMPANY_POLICY") -> dict:
        self.calls.append(("accept_claim", dispute_id, cents, request_id))
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        if request_id in self.dispute_by_request:
            return self.dispute_by_request[request_id]
        d = self.get_dispute(dispute_id)
        if d.get("status") not in ("WAITING_FOR_SELLER_RESPONSE", "OPEN"):
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"INVALID_DISPUTE_STATUS"}]}')
        if "accept-claim" not in (link_actions(d) or []):
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"ACTION_NOT_ALLOWED"}]}')
        claim_type = "PARTIAL_REFUND" if cents < dispute_amount_cents else "REFUND"
        allowed_types = ((d.get("allowed_response_options") or {}).get("accept_claim") or {}).get("accept_claim_types")
        if not isinstance(allowed_types, list) or claim_type not in allowed_types:
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"ACCEPT_CLAIM_TYPE_NOT_ALLOWED"}]}')
        txs = d.get("disputed_transactions") or []
        cap_id = txs[0].get("seller_transaction_id") if txs else ""
        c = self._capture(cap_id)
        if c["refunded"] + cents > c["amount_cents"]:
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"REFUND_AMOUNT_EXCEEDED"}]}')
        rid = f"R{self._run}{len(self.refunds) + 1:05d}"
        r = {"id": rid, "status": self.refund_status, "amount": {"value": fmt_amount(cents), "currency_code": currency},
             "invoice_id": f"dispute-{dispute_id}", "capture_id": cap_id}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        d["update_time"] = now
        if self.dispute_resolves_immediately:
            self.refunds[rid] = r
            if self.refund_status == "COMPLETED":
                c["refunded"] += cents
            d["status"] = "RESOLVED"
            d["dispute_state"] = "RESOLVED"
            d["dispute_outcome"] = {
                "outcome_code": "ACCEPTED",
                "amount_refunded": {"currency_code": currency, "value": fmt_amount(cents)},
            }
        else:                                   # PayPal settles later (e.g. the buyer must answer a partial offer)
            d["status"] = "UNDER_REVIEW"
            d["dispute_state"] = "UNDER_PAYPAL_REVIEW"
            self.deferred_accepts[dispute_id] = (r, cents, currency)
        # Like the real API: accept-claim returns HATEOAS links only, no refund id and no refund status.
        resp = {"links": [{"rel": "self", "href": f"/v1/customer/disputes/{dispute_id}", "method": "GET"}]}
        self.dispute_by_request[request_id] = resp
        return resp

    def resolve_deferred_accept(self, dispute_id: str) -> dict:
        """Test helper: PayPal finishes a deferred accept-claim. Returns the refund it created."""
        r, cents, currency = self.deferred_accepts.pop(dispute_id)
        self.refunds[r["id"]] = r
        self.captures[r["capture_id"]]["refunded"] += cents
        d = self.disputes[dispute_id]
        d.update(status="RESOLVED", dispute_state="RESOLVED",
                 dispute_outcome={"outcome_code": "ACCEPTED",
                                  "amount_refunded": {"currency_code": currency, "value": fmt_amount(cents)}})
        return r

    def provide_evidence(self, dispute_id: str, *, notes: str, request_id: str,
                         evidence_type: str = "OTHER", refund_ids: list[str] | None = None,
                         documents: list[tuple[str, bytes, str]] | None = None) -> dict:
        self.calls.append(("provide_evidence", dispute_id, evidence_type, request_id))
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        if request_id in self.dispute_by_request:
            return self.dispute_by_request[request_id]
        d = self.get_dispute(dispute_id)
        if d.get("status") not in ("WAITING_FOR_SELLER_RESPONSE", "OPEN"):
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"INVALID_DISPUTE_STATUS"}]}')
        if "provide-evidence" not in (link_actions(d) or []):
            raise PayPalError(422, '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"ACTION_NOT_ALLOWED"}]}')
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        d["status"] = "UNDER_REVIEW"
        d["dispute_state"] = "UNDER_REVIEW"
        d["update_time"] = now
        d.setdefault("evidences", []).append({
            "evidence_type": evidence_type,
            "notes": notes,
            "evidence_info": {"refund_ids": list(refund_ids or [])},
            "documents": [{"name": name, "size": len(data), "mime": mime} for name, data, mime in (documents or [])],
        })
        resp = {
            "status": "UNDER_REVIEW",
            "links": [{"rel": "self", "href": f"/v1/customer/disputes/{dispute_id}", "method": "GET"}],
        }
        self.dispute_by_request[request_id] = resp
        return resp

    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool:
        return headers.get("x-fake-valid", "1") == "1"

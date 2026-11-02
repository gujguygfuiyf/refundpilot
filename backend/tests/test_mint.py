"""mint_orders.py --batch against a fake sandbox: links first, captures in whatever order buyers pay."""
import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location("mint_orders", Path(__file__).resolve().parent.parent / "scripts" / "mint_orders.py")
mint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mint)


class FakeSandbox:
    def __init__(self):
        self.n, self.status, self.polls = 0, {}, 0

    def create_order(self, cents, currency, description, return_url, cancel_url):
        self.n += 1
        oid = f"ORD{self.n}"
        self.status[oid] = "PAYER_ACTION_REQUIRED"
        return {"id": oid, "status": self.status[oid], "links": [{"rel": "payer-action", "href": f"https://pay/{oid}"}]}

    def get_order(self, oid):
        self.polls += 1
        if self.polls == 1:
            raise RuntimeError("503 from sandbox")          # a flaky call must not abort the batch
        if self.polls == 2:
            self.status["ORD2"] = "APPROVED"                # the buyer pays the second link first
        if self.polls >= 4:
            self.status["ORD1"] = "APPROVED"
        return {"id": oid, "status": self.status[oid]}

    def capture_order(self, oid):
        self.status[oid] = "COMPLETED"
        return {"purchase_units": [{"payments": {"captures": [{"id": f"CAP-{oid}", "status": "COMPLETED"}]}}]}


def test_batch_captures_in_payment_order_and_saves_each(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "ORDERS_PATH", tmp_path / "orders.json")
    monkeypatch.setattr(mint.time, "sleep", lambda s: None)
    products = [{"sku": "MUG-01", "price_cents": 1900, "currency": "USD", "name": "Mug"},
                {"sku": "LAMP-02", "price_cents": 4900, "currency": "USD", "name": "Lamp"}]
    existing = []
    mint.batch(FakeSandbox(), products, 2, 60, existing)
    saved = json.loads((tmp_path / "orders.json").read_text())
    assert [e["capture_id"] for e in saved] == ["CAP-ORD2", "CAP-ORD1"]
    assert [e["sku"] for e in saved] == ["LAMP-02", "MUG-01"]
    assert saved == existing

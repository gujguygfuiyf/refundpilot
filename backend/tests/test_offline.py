from datetime import datetime, timedelta, timezone

from app.claims import ClaimService
from app.models import Order, Product
from app.paypal import FakePayPal
from app.seed import refresh_captures, seed_all, seed_demo_orders
from tests.conftest import jpeg_bytes


def test_demo_orders_seeded_and_approvable_offline(db, assessor):
    pp = FakePayPal()
    n = seed_demo_orders(db, pp)
    assert n == 1 and "DEMO-CAP-MUG-01" in pp.captures          # conftest has one product
    assert seed_demo_orders(db, pp) == 0                          # idempotent
    demo = db.query(Order).filter_by(capture_id="DEMO-CAP-MUG-01").one()
    svc = ClaimService(db, pp, assessor, "standard-1.2")
    c = svc.create_claim(demo.id, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    r = svc.approve(c.id)
    assert r.status == "COMPLETED" and svc.get_claim(c.id).order.remaining_cents == 0


def test_unknown_capture_fails_cleanly(db, assessor):
    pp = FakePayPal()                                             # CAP1 not registered
    svc = ClaimService(db, pp, assessor, "standard-1.2")
    c = svc.create_claim(1, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    r = svc.approve(c.id)
    assert r.status == "FAILED" and "404" in r.error
    assert svc.get_claim(c.id).status == "REVIEW_REQUIRED"


def test_first_boot_single_seed_then_approve(db, assessor):
    """Regression: on a brand-new database the demo order wasn't flushed yet when its capture was registered in
    the fake gateway, so `refunded` was None and the first offline approve raised a TypeError (HTTP 500)."""
    pp = FakePayPal()
    assert seed_demo_orders(db, pp) == 1                          # exactly one seed, like a fresh `uvicorn` start
    assert pp.captures["DEMO-CAP-MUG-01"]["refunded"] == 0
    demo = db.query(Order).filter_by(capture_id="DEMO-CAP-MUG-01").one()
    svc = ClaimService(db, pp, assessor, "standard-1.2")
    c = svc.create_claim(demo.id, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    assert svc.approve(c.id).status == "COMPLETED"


def test_capture_refresh_marks_orders_refunded_elsewhere(db):
    """A capture refunded outside this instance (e.g. by a local copy of the demo) must show nothing left."""
    pp = FakePayPal({"CAP1": 1900})                    # CAP2 unknown to the gateway: skipped, not fatal
    pp.captures["CAP1"]["refunded"] = 1900
    seed_demo_orders(db, pp)                          # DEMO-* captures are never asked about
    calls_before = len(pp.calls)
    assert refresh_captures(db, pp) == 1
    assert [c[1] for c in pp.calls[calls_before:]] == ["CAP1", "CAP2"]
    o1 = db.query(Order).filter_by(capture_id="CAP1").one()
    assert (o1.capture_status, o1.remaining_cents) == ("REFUNDED", 0)
    assert refresh_captures(db, pp) == 0              # idempotent


def test_startup_seed_makes_no_paypal_calls(db):
    pp = FakePayPal({"CAP1": 1900})
    seed_all(db, paypal=pp, refresh=False)
    assert not [c for c in pp.calls if c[0] == "get_capture"]


def test_fake_refund_ids_survive_a_restart():
    """Offline mode keeps its SQLite file across restarts, so a fresh fake gateway must not reuse a stored refund id."""
    first, after_restart = FakePayPal({"CAP": 1000}), FakePayPal({"CAP": 1000})
    a = first.refund_capture("CAP", 100, "USD", request_id="rp-1", invoice_id="inv-1")
    b = after_restart.refund_capture("CAP", 100, "USD", request_id="rp-2", invoice_id="inv-2")
    assert a["id"] != b["id"]

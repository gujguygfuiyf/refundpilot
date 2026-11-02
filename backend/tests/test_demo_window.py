"""Rolling delivery dates keep the sandbox demo inside the policy window during judging."""
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import Order
from app.seed import refresh_demo_delivery
from tests.conftest import jpeg_bytes


def _age_orders(db, days: int) -> str:
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    for o in db.query(Order).all():
        o.delivered_at = old
    db.commit()
    return old


def test_unclaimed_orders_roll_forward_and_claims_stay_in_window(db, svc):
    old = _age_orders(db, 40)                                     # minted weeks before a judge shows up
    svc.create_claim(2, "handle broke", [jpeg_bytes()])           # order 2 already has a claim
    assert refresh_demo_delivery(db) == 1
    assert db.get(Order, 2).delivered_at == old                   # claimed orders keep their history
    c = svc.create_claim(1, "handle broke on arrival", [jpeg_bytes()])
    svc.run_assessment(c.id)
    d = svc.get_claim(c.id).latest_decision
    assert d.rule_id == "R1-broken" and d.suggested_cents == 1900
    assert refresh_demo_delivery(db) == 0


def test_cancelled_claim_does_not_pin_the_date(db, svc):
    _age_orders(db, 40)
    c = svc.create_claim(1, "handle broke", [jpeg_bytes()])
    svc.cancel(c.id)
    assert refresh_demo_delivery(db) == 2


def test_recent_orders_are_left_alone(db):
    _age_orders(db, settings.demo_delivered_days_ago)
    assert refresh_demo_delivery(db) == 0


def test_can_be_switched_off(db, svc, monkeypatch):
    monkeypatch.setattr(settings, "demo_rolling_delivery", False)
    _age_orders(db, 40)
    assert refresh_demo_delivery(db) == 0
    c = svc.create_claim(1, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    assert svc.get_claim(c.id).latest_decision.rule_id == "PRE-window"

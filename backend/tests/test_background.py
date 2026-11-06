"""The background loop must leave the database alone while nothing is pending, so a serverless database (Neon free
tier, 100 compute-hours a month) can scale to zero even when an uptime ping keeps the API itself awake."""
from __future__ import annotations

import httpx
from sqlalchemy.orm import sessionmaker

from app.claims import ClaimService, reconcile_needed
from app.config import settings
from app.main import BackgroundWork
from app.paypal import FakePayPal
from tests.test_claims import submit


class CountingSessions:
    def __init__(self, bind) -> None:
        self._make = sessionmaker(bind=bind, expire_on_commit=False)
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return self._make()


def test_idle_ticks_never_open_a_session(db):
    reconcile_needed.clear()
    sessions = CountingSessions(db.get_bind())
    work = BackgroundWork(sessions)
    for minute in range(1, 120):                         # two idle hours, one tick a minute
        assert work.tick(now=work.last_housekeeping + 60 * minute) is False
    assert sessions.opened == 0


def test_flag_triggers_one_reconcile_then_the_loop_goes_quiet(db):
    reconcile_needed.set()
    sessions = CountingSessions(db.get_bind())
    work = BackgroundWork(sessions)
    t0 = work.last_housekeeping
    assert work.tick(now=t0 + 60) is True
    assert not reconcile_needed.is_set()                 # nothing pending in the database
    assert work.tick(now=t0 + 120) is False
    assert sessions.opened == 1


def test_housekeeping_runs_on_the_slow_tick(db):
    reconcile_needed.clear()
    sessions = CountingSessions(db.get_bind())
    work = BackgroundWork(sessions)
    due = work.last_housekeeping + settings.housekeeping_interval_s
    assert work.tick(now=due - 60) is False
    assert work.tick(now=due) is True
    assert work.last_housekeeping == due and sessions.opened == 1


def test_timeout_and_pending_refunds_raise_the_flag(db, svc, paypal, assessor):
    reconcile_needed.clear()
    c = submit(svc)
    paypal.fail_next = httpx.ReadTimeout("slow")
    assert svc.approve(c.id).status == "UNKNOWN"
    assert reconcile_needed.is_set()

    reconcile_needed.clear()
    pending = ClaimService(db, FakePayPal({"CAP2": 4900}, refund_status="PENDING"), assessor, "standard-1.2")
    c2 = submit(pending, order_id=2)
    assert pending.approve(c2.id).status == "PENDING"
    assert reconcile_needed.is_set() and pending.pending_refunds() >= 1


def test_a_failing_tick_keeps_the_flag_for_the_next_one(db):
    reconcile_needed.set()

    def broken_session():
        raise RuntimeError("database unreachable")

    work = BackgroundWork(broken_session)
    assert work.tick(now=work.last_housekeeping + 60) is False   # logged, not raised
    assert reconcile_needed.is_set()

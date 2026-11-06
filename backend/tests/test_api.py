"""HTTP-level smoke tests with the fake gateway and fake assessor."""
import time

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.ai import FakeAssessor
from app.db import Base, get_db
from app.main import app
from app.paypal import FakePayPal
from tests.conftest import jpeg_bytes

MERCHANT = {"Authorization": "Bearer test-merchant"}


@pytest.fixture
def client(db):
    pp = FakePayPal({"CAP1": 1900, "CAP2": 4900})
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[deps.get_paypal] = lambda: pp
    app.dependency_overrides[deps.get_assessor] = lambda: FakeAssessor()
    # background job opens its own session; point it at the test session too
    import app.routers.claims as claims_router
    orig = claims_router._run_assessment_job
    def job(cid):
        from app.claims import ClaimService
        ClaimService(db, pp, FakeAssessor(), "standard-1.2").run_assessment(cid)
    claims_router._run_assessment_job = job
    try:
        with TestClient(app, raise_server_exceptions=True) as c:   # lifespan runs create_all + seed on in-memory db
            yield c
    finally:
        claims_router._run_assessment_job = orig
        app.dependency_overrides.clear()


def test_end_to_end_http(client):
    orders = client.get("/orders").json()
    assert orders and orders[0]["remaining_cents"] == 1900

    r = client.post("/claims", data={"order_id": 1, "buyer_text": "handle broke"},
                    files=[("photos", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    for _ in range(20):
        d = client.get(f"/claims/{cid}").json()
        if d["status"] != "ANALYZING":
            break
        time.sleep(0.05)
    assert d["status"] == "REVIEW_REQUIRED" and d["decision"]["suggested_cents"] == 1900
    assert d["photo_urls"] == [f"/claims/{cid}/photos/0"]
    assert client.get(d["photo_urls"][0]).headers["content-type"] == "image/jpeg"

    assert client.get("/merchant/claims").status_code == 401
    rows = client.get("/merchant/claims", headers=MERCHANT).json()
    assert rows[0]["id"] == cid and rows[0]["outcome"] == "FULL_REFUND"

    r = client.post(f"/merchant/claims/{cid}/approve", headers=MERCHANT, json={})
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED"
    assert client.post(f"/merchant/claims/{cid}/approve", headers=MERCHANT, json={}).status_code == 409

    stats = client.get("/merchant/stats", headers=MERCHANT).json()
    assert stats["completed"] == 1 and stats["refunded_cents_total"] == 1900
    assert client.get("/orders").json()[0]["remaining_cents"] == 0


def test_simulate_is_pure(client):
    a = {"product_match": "likely", "damage_type": "cosmetic", "severity": "minor", "visible_damage": True,
         "evidence_quality": "sufficient", "stock_photo_suspected": False, "description_consistent": "yes",
         "needs_human_review": False, "confidence": 0.8, "observations": [], "limitations": []}
    out = {v: client.post("/policies/simulate", headers=MERCHANT,
                          json={"policy_version": v, "assessment": a, "order_cents": 10000}).json()["suggested_cents"]
           for v in ("standard-1.2", "strict-1.1", "goodwill-1.2")}
    assert out == {"standard-1.2": 3000, "strict-1.1": 1500, "goodwill-1.2": 5000}
    assert client.get("/merchant/claims", headers=MERCHANT).json() == []


def test_upload_validation(client):
    r = client.post("/claims", data={"order_id": 1}, files=[("photos", ("a.txt", b"hello", "text/plain"))])
    assert r.status_code == 415
    r = client.post("/claims", data={"order_id": 999}, files=[("photos", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert r.status_code == 404


def test_corrupt_image_is_a_clean_400(client):
    r = client.post("/claims", data={"order_id": 1}, files=[("photos", ("a.jpg", b"not really a jpeg", "image/jpeg"))])
    assert r.status_code == 400 and "could not be read" in r.json()["detail"]
    assert client.get("/orders").json()[0]["open_claim_id"] is None       # nothing half-created


def test_claim_submissions_are_rate_limited_per_ip(client, monkeypatch):
    from app.config import settings
    from app.ratelimit import per_ip
    monkeypatch.setattr(settings, "claims_per_ip_per_minute", 1)
    per_ip.reset()
    try:
        photo = [("photos", ("a.jpg", jpeg_bytes(), "image/jpeg"))]
        assert client.post("/claims", data={"order_id": 1, "buyer_text": "handle broke"}, files=photo).status_code == 201
        r = client.post("/claims", data={"order_id": 2, "buyer_text": "handle broke"}, files=photo)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    finally:
        per_ip.reset()


def test_daily_ai_budget_caps_submissions(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ai_calls_per_day", 1)
    photo = [("photos", ("a.jpg", jpeg_bytes(), "image/jpeg"))]
    assert client.post("/claims", data={"order_id": 1, "buyer_text": "handle broke"}, files=photo).status_code == 201
    r = client.post("/claims", data={"order_id": 2, "buyer_text": "handle broke"}, files=photo)
    assert r.status_code == 429 and "today's limit" in r.json()["detail"]


def test_empty_merchant_token_never_authenticates(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "merchant_token", "")
    assert client.get("/merchant/claims", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get("/merchant/claims", headers={"Authorization": "Bearer"}).status_code == 401


def test_deployed_instance_refuses_default_merchant_token(monkeypatch):
    from app.config import settings
    from app.main import check_secrets
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@db/refundpilot")
    for weak in ("", "change-me"):
        monkeypatch.setattr(settings, "merchant_token", weak)
        with pytest.raises(RuntimeError):
            check_secrets()
    monkeypatch.setattr(settings, "merchant_token", "a-long-random-value")
    check_secrets()
    monkeypatch.setattr(settings, "database_url", "sqlite://")             # local dev may keep change-me
    monkeypatch.setattr(settings, "merchant_token", "change-me")
    check_secrets()
    monkeypatch.setenv("RENDER", "true")                                   # but not on Render, even with SQLite
    with pytest.raises(RuntimeError):
        check_secrets()


def test_dev_routes_are_off_when_deployed_with_a_default_dev_token(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dev_token", "change-me")
    monkeypatch.setenv("RENDER", "true")
    assert client.post("/dev/seed", headers={"Authorization": "Bearer change-me"}).status_code == 404


def test_policy_list_shows_current_versions_only(client, db):
    from app.models import Policy
    db.add(Policy(version="standard-1.0", name="Standard", body={"rules": []}, body_sha256="old"))
    db.commit()
    versions = {p["version"] for p in client.get("/policies", headers=MERCHANT).json()}
    assert versions == {"standard-1.2", "strict-1.1", "goodwill-1.2"}
    assert client.get("/policies/standard-1.0", headers=MERCHANT).status_code == 200    # still auditable


def test_orders_say_which_ones_can_still_be_claimed(client):
    photo = [("photos", ("a.jpg", jpeg_bytes(), "image/jpeg"))]
    cid = client.post("/claims", data={"order_id": 1, "buyer_text": "handle broke"}, files=photo).json()["id"]
    assert client.post(f"/merchant/claims/{cid}/reject", headers=MERCHANT, json={"message": "no"}).status_code == 200
    o1, o2 = client.get("/orders").json()[:2]
    assert (o1["claimable"], o1["last_claim_id"], o1["open_claim_id"]) == (False, cid, None)
    assert (o2["claimable"], o2["last_claim_id"]) == (True, None)

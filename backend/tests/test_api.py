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
        ClaimService(db, pp, FakeAssessor(), "standard-1.0").run_assessment(cid)
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
           for v in ("standard-1.0", "strict-1.0", "goodwill-1.0")}
    assert out == {"standard-1.0": 3000, "strict-1.0": 1500, "goodwill-1.0": 5000}
    assert client.get("/merchant/claims", headers=MERCHANT).json() == []


def test_upload_validation(client):
    r = client.post("/claims", data={"order_id": 1}, files=[("photos", ("a.txt", b"hello", "text/plain"))])
    assert r.status_code == 415
    r = client.post("/claims", data={"order_id": 999}, files=[("photos", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert r.status_code == 404

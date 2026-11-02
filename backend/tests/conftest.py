from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("DATABASE_URL", "sqlite://")        # in-memory for tests
os.environ.setdefault("MERCHANT_TOKEN", "test-merchant")
os.environ.setdefault("DEV_TOKEN", "test-dev")
os.environ.setdefault("CLAIMS_PER_IP_PER_MINUTE", "1000")  # rate-limit tests tighten this explicitly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.ai import EvidenceAssessment, FakeAssessor  # noqa: E402
from app.claims import ClaimService  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Order, Product  # noqa: E402
from app.paypal import FakePayPal  # noqa: E402
from app.seed import seed_policies  # noqa: E402


def jpeg_bytes(color=(200, 50, 50), size=(640, 480)) -> bytes:
    im = Image.new("RGB", size, color)
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    s = Session()
    seed_policies(s)
    p = Product(sku="MUG-01", name="Ceramic Coffee Mug", price_cents=1900, currency="USD",
                image_url="https://example.com/mug.jpg", category="kitchen")
    s.add(p); s.flush()
    captured = datetime.now(timezone.utc) - timedelta(days=5)
    s.add(Order(paypal_order_id="O1", capture_id="CAP1", product_id=p.id, amount_cents=1900, currency="USD",
                captured_at=captured.isoformat(timespec="seconds"),
                delivered_at=(captured + timedelta(days=2)).isoformat(timespec="seconds"), buyer_label="Buyer A"))
    s.add(Order(paypal_order_id="O2", capture_id="CAP2", product_id=p.id, amount_cents=4900, currency="USD",
                captured_at=captured.isoformat(timespec="seconds"),
                delivered_at=(captured + timedelta(days=2)).isoformat(timespec="seconds"), buyer_label="Buyer B"))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def paypal():
    return FakePayPal({"CAP1": 1900, "CAP2": 4900})


def cosmetic() -> EvidenceAssessment:
    return EvidenceAssessment(product_match="likely", damage_type="cosmetic", severity="minor", visible_damage=True,
                              evidence_quality="sufficient", stock_photo_suspected=False, description_consistent="yes",
                              needs_human_review=False, confidence=0.8, observations=["small scratch on the rim"],
                              limitations=["cannot tell when it happened"])


def mismatch() -> EvidenceAssessment:
    return EvidenceAssessment(product_match="mismatch", damage_type="broken", severity="severe", visible_damage=True,
                              evidence_quality="sufficient", stock_photo_suspected=False, description_consistent="no",
                              needs_human_review=True, confidence=0.7, observations=["photo shows a plate, not a mug"],
                              limitations=[])


@pytest.fixture
def assessor():
    return FakeAssessor(by_text={"scratch": cosmetic(), "wrong": mismatch()})


@pytest.fixture
def svc(db, paypal, assessor):
    return ClaimService(db, paypal, assessor, "standard-1.1")

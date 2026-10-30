"""Dependency wiring: db session, gateways, service, auth."""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .ai import Assessor, build_assessor
from .claims import ClaimService
from .config import settings
from .db import get_db
from .paypal import FakePayPal, PayPalClient, PayPalGateway

# Tests override these two via app.dependency_overrides.

@lru_cache(maxsize=1)
def get_paypal() -> PayPalGateway:
    if settings.paypal_client_id and settings.paypal_client_secret:
        return PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    return FakePayPal()


@lru_cache(maxsize=1)
def get_assessor() -> Assessor:
    return build_assessor(settings)


def get_service(db: Session = Depends(get_db), paypal: PayPalGateway = Depends(get_paypal),
                assessor: Assessor = Depends(get_assessor)) -> ClaimService:
    return ClaimService(db, paypal, assessor, settings.default_policy_version)


def require_merchant(authorization: str = Header(default="")) -> str:
    if authorization != f"Bearer {settings.merchant_token}":
        raise HTTPException(401, "merchant token required")
    return "merchant"


def require_dev(authorization: str = Header(default="")) -> str:
    if authorization != f"Bearer {settings.dev_token}":
        raise HTTPException(401, "dev token required")
    return "dev"

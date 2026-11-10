"""Dependency wiring: db session, gateways, service, auth."""
from __future__ import annotations

import hmac
from functools import lru_cache

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .ai import Assessor, build_assessor
from .assistant import TurnModel, build_turn_model
from .claims import ClaimService
from .config import settings
from .db import get_db
from .paypal import FakePayPal, PayPalClient, PayPalGateway

# Tests override these via app.dependency_overrides.

@lru_cache(maxsize=1)
def get_paypal() -> PayPalGateway:
    if settings.paypal_client_id and settings.paypal_client_secret:
        return PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    return FakePayPal()


@lru_cache(maxsize=1)
def get_assessor() -> Assessor:
    return build_assessor(settings)


@lru_cache(maxsize=1)
def get_turn_model() -> TurnModel | None:
    return build_turn_model(settings)


def get_service(db: Session = Depends(get_db), paypal: PayPalGateway = Depends(get_paypal),
                assessor: Assessor = Depends(get_assessor)) -> ClaimService:
    return ClaimService(db, paypal, assessor, settings.default_policy_version)


WEAK_TOKENS = ("", "change-me")


def bearer_matches(authorization: str, token: str) -> bool:
    """Constant-time compare. An empty configured token never matches (so "Bearer " can't get in)."""
    if not token:
        return False
    return hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode())


def require_merchant(authorization: str = Header(default="")) -> str:
    if not bearer_matches(authorization, settings.merchant_token):
        raise HTTPException(401, "merchant token required")
    return "merchant"


def require_dev(authorization: str = Header(default="")) -> str:
    # When deployed with a missing or default DEV_TOKEN, the /dev routes are simply off.
    if settings.is_deployed() and settings.dev_token in WEAK_TOKENS:
        raise HTTPException(404, "not found")
    if not bearer_matches(authorization, settings.dev_token):
        raise HTTPException(401, "dev token required")
    return "dev"

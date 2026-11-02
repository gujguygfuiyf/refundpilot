"""Settings. Everything secret comes from the environment (or backend/.env locally)."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # PayPal
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    # AI: primary = Gemini; OpenAI is an optional backup, used only if OPENAI_API_KEY is set.
    # AI_PROVIDER picks which one is tried first: gemini (default) | openai. "auto" is accepted as gemini.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    openai_api_key: str = ""
    openai_model: str = "gpt-5"          # only used with OPENAI_API_KEY; set to a vision model on your account
    ai_provider: str = "gemini"

    # Storage / auth
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'refundpilot.db'}"
    merchant_token: str = "change-me"
    dev_token: str = "change-me"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_upload_mb: int = 5

    # Abuse limits on the anonymous claim endpoints (each submission costs one AI call). 0 disables a limit.
    claims_per_ip_per_minute: int = 5
    ai_calls_per_day: int = 200

    # Behaviour
    default_policy_version: str = "standard-1.1"
    delivered_offset_days: int = 2       # demo: delivered = captured + N days
    reconcile_interval_s: int = 60
    # Demo only: keep unclaimed sandbox orders "delivered N days ago", so the policy's report window
    # doesn't expire while judges try the live demo weeks after the orders were minted.
    demo_rolling_delivery: bool = True
    demo_delivered_days_ago: int = 3

    def is_deployed(self) -> bool:
        """True on Render (it sets RENDER=true) or whenever the database isn't local SQLite."""
        return bool(os.environ.get("RENDER")) or not self.database_url.startswith("sqlite")


settings = Settings()

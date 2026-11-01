"""Settings. Everything secret comes from the environment (or backend/.env locally)."""
from __future__ import annotations

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

    # AI: primary = OpenAI (GPT vision), backup = Gemini
    openai_api_key: str = ""
    openai_model: str = "gpt-5"          # set to a vision-capable model available on your account
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    ai_provider: str = "openai"          # openai | gemini | auto (openai first, gemini on failure)

    # Storage / auth
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'refundpilot.db'}"
    merchant_token: str = "change-me"
    dev_token: str = "change-me"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_upload_mb: int = 5

    # Behaviour
    default_policy_version: str = "standard-1.0"
    delivered_offset_days: int = 2       # demo: delivered = captured + N days
    reconcile_interval_s: int = 60


settings = Settings()

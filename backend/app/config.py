import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    database_path: str = "payguard.db"
    allowed_merchant_ids: frozenset[str] = frozenset()
    demo_attacks_enabled: bool = False
    high_risk_pause_seconds: int = 10


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise RuntimeError("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env")
    return Settings(
        paypal_client_id=client_id,
        paypal_client_secret=secret,
        paypal_base_url=os.environ.get("PAYPAL_BASE_URL") or Settings.paypal_base_url,
        database_path=os.environ.get("DATABASE_PATH") or Settings.database_path,
        allowed_merchant_ids=frozenset(
            merchant.strip()
            for merchant in os.environ.get("ALLOWED_MERCHANT_IDS", "").split(",")
            if merchant.strip()
        ),
        demo_attacks_enabled=os.environ.get("DEMO_ATTACKS_ENABLED", "").lower() == "true",
    )

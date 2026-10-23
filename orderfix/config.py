from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .demo_store import DemoProvider
from .engine import Engine
from .intent import IntentParser
from .policy import ReturnPolicy
from .storage import Storage


@dataclass(frozen=True)
class Settings:
    database: str = "data/orderfix.sqlite3"
    mode: str = "demo"
    seed: str = ""
    port: int = 8001
    cards: bool = True
    asi_key: str = ""
    asi_model: str = "asi1"

    @classmethod
    def load(cls):
        load_dotenv()
        settings = cls(database=os.getenv("ORDERFIX_DB", "data/orderfix.sqlite3"),
            mode=os.getenv("STORE_MODE", "demo"), seed=os.getenv("AGENT_SEED", ""),
            port=int(os.getenv("AGENT_PORT", "8001")), cards=os.getenv("ENABLE_CARDS", "true").lower() == "true",
            asi_key=os.getenv("ASI1_API_KEY", ""), asi_model=os.getenv("ASI1_MODEL", "asi1"))
        if settings.mode not in ("demo", "shopify"):
            raise ValueError("STORE_MODE must be demo or shopify")
        return settings


def make_engine(settings: Settings) -> Engine:
    storage = Storage(settings.database)
    if settings.mode == "demo":
        provider = DemoProvider(settings.database)
    else:
        from .shopify import ShopifyProvider
        if os.getenv("SHOPIFY_TEST_STORE", "false").lower() != "true":
            raise ValueError("This MVP supports controlled Shopify test customers only. Set SHOPIFY_TEST_STORE=true for your development store.")
        provider = ShopifyProvider(domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
            access_token=os.getenv("SHOPIFY_ACCESS_TOKEN") or None,
            client_id=os.getenv("SHOPIFY_CLIENT_ID") or None,
            client_secret=os.getenv("SHOPIFY_CLIENT_SECRET") or None)
    return Engine(storage, provider, IntentParser(settings.asi_key, settings.asi_model), ReturnPolicy())

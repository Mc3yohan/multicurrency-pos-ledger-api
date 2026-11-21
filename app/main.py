# -*- coding: utf-8 -*-
"""FastAPI application factory for the Multi-Currency POS Ledger API."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException

from app.auth import ApiKeyAuth
from app.models import (
    CheckoutIn,
    CsvImportIn,
    CustomerCreditOut,
    PaymentMethod,
    PaymentSplitIn,
    ProductOwnerOut,
    ProductRecord,
    ProductSellerOut,
    SaleItemIn,
    UserRole,
    money,
)
from app.store import LedgerStore

DEFAULT_DEMO_CATALOG_CSV = (
    "sku,name,unit_cost_usd,sale_price_usd,stock\n"
    "ZAP-101,Calzado Casual Cuero T41,22.00,39.90,12\n"
    "ZAP-102,Deportivo Running T42,26.50,48.00,8\n"
)

__all__ = [
    "app",
    "create_app",
    "store",
    "ApiKeyAuth",
    "CheckoutIn",
    "CsvImportIn",
    "CustomerCreditOut",
    "LedgerStore",
    "PaymentMethod",
    "PaymentSplitIn",
    "ProductOwnerOut",
    "ProductRecord",
    "ProductSellerOut",
    "SaleItemIn",
    "UserRole",
    "money",
]


def create_app(
    *,
    db_path: str | Path | None = None,
    exchange_rate_ves: Decimal | None = None,
    owner_api_key: str | None = None,
    seller_api_key: str | None = None,
    seed_demo: bool = True,
) -> FastAPI:
    resolved_db = db_path if db_path is not None else os.getenv("POS_DB_PATH", ":memory:")
    resolved_rate = (
        exchange_rate_ves
        if exchange_rate_ves is not None
        else Decimal(os.getenv("EXCHANGE_RATE_VES", "45.50"))
    )
    resolved_owner_key = (
        owner_api_key if owner_api_key is not None else os.getenv("POS_OWNER_API_KEY", "demo-owner-key")
    )
    resolved_seller_key = (
        seller_api_key if seller_api_key is not None else os.getenv("POS_SELLER_API_KEY", "demo-seller-key")
    )

    ledger = LedgerStore(exchange_rate_ves=resolved_rate, db_path=resolved_db)
    auth = ApiKeyAuth(owner_key=resolved_owner_key, seller_key=resolved_seller_key)

    if seed_demo and not ledger.list_products():
        ledger.upsert_csv_catalog(DEFAULT_DEMO_CATALOG_CSV)

    api = FastAPI(
        title="Multi-Currency POS Ledger API",
        version="1.1.0",
        description=(
            "FastAPI multi-currency POS engine with atomic SQLite WAL transactions, "
            "Decimal money arithmetic, API-key RBAC margin isolation, and idempotent CSV import."
        ),
    )
    api.state.store = ledger
    api.state.auth = auth

    @api.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "exchange_rate_ves": str(ledger.exchange_rate_ves),
            "skus": len(ledger.list_products()),
        }

    @api.get("/catalog", response_model=List[ProductSellerOut])
    def list_seller_catalog() -> List[ProductSellerOut]:
        rate = ledger.exchange_rate_ves
        return [
            ProductSellerOut(
                sku=p.sku,
                name=p.name,
                sale_price_usd=p.sale_price_usd,
                sale_price_ves=money(p.sale_price_usd * rate),
                stock=p.stock,
            )
            for p in ledger.list_products()
        ]

    @api.get("/admin/margins", response_model=List[ProductOwnerOut])
    def list_owner_margins(
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> List[ProductOwnerOut]:
        auth.require_owner(x_api_key)
        rate = ledger.exchange_rate_ves
        return [
            ProductOwnerOut(
                sku=p.sku,
                name=p.name,
                sale_price_usd=p.sale_price_usd,
                sale_price_ves=money(p.sale_price_usd * rate),
                stock=p.stock,
                unit_cost_usd=p.unit_cost_usd,
                gross_margin_usd=money(p.sale_price_usd - p.unit_cost_usd),
            )
            for p in ledger.list_products()
        ]

    @api.post("/admin/catalog/import")
    def import_csv_catalog(
        body: CsvImportIn,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> dict:
        auth.require_owner(x_api_key)
        try:
            return ledger.upsert_csv_catalog(body.csv_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/sales/checkout")
    def process_checkout(
        payload: CheckoutIn,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> dict:
        auth.resolve_role(x_api_key)
        try:
            return ledger.checkout(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/customers/{customer_ref}/credit", response_model=CustomerCreditOut)
    def get_customer_credit(
        customer_ref: str,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> CustomerCreditOut:
        auth.resolve_role(x_api_key)
        bal = ledger.get_customer_credit(customer_ref)
        return CustomerCreditOut(customer_ref=customer_ref, credit_balance_usd=str(bal))

    return api


app = create_app()
store: LedgerStore = app.state.store

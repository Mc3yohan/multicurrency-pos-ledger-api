# -*- coding: utf-8 -*-
"""
Multi-Currency POS Ledger & Role-Isolated Catalog API (Demo)
Author: Víctor Yohan Betancourt Marín (https://quanticosystems.com)

Clean-room reference implementation showcasing:
1. Exact Decimal arithmetic across split multi-currency payments (USD Cash, VES Mobile Payment, USDT).
2. Exchange-rate snapshot immutability per invoice/delivery note.
3. Strict API schema boundary: Seller endpoints NEVER serialize unit costs or profit margins.
4. Idempotent catalog import from CSV/ERP exports without stock corruption.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Dict, List
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


TWOPLACES = Decimal("0.01")


def money(val: Decimal | str | int) -> Decimal:
    if isinstance(val, float):
        raise TypeError("Floats are forbidden for monetary calculations; use Decimal or str.")
    return Decimal(val).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


class PaymentMethod(str, Enum):
    USD_CASH = "USD_CASH"
    VES_PAGO_MOVIL = "VES_PAGO_MOVIL"
    USDT_BINANCE = "USDT_BINANCE"


class UserRole(str, Enum):
    SELLER = "SELLER"
    OWNER = "OWNER"


@dataclass
class ProductRecord:
    sku: str
    name: str
    unit_cost_usd: Decimal
    sale_price_usd: Decimal
    stock: int


class ProductSellerOut(BaseModel):
    """Public/Seller schema: unit_cost_usd and profit_usd do NOT exist here by design."""
    sku: str
    name: str
    sale_price_usd: Decimal
    sale_price_ves: Decimal
    stock: int


class ProductOwnerOut(ProductSellerOut):
    unit_cost_usd: Decimal
    gross_margin_usd: Decimal


class PaymentSplitIn(BaseModel):
    method: PaymentMethod
    amount: Decimal = Field(..., gt=Decimal("0"))


class SaleItemIn(BaseModel):
    sku: str
    qty: int = Field(..., gt=0)


class CheckoutIn(BaseModel):
    customer_ref: str
    items: List[SaleItemIn] = Field(..., min_length=1)
    payments: List[PaymentSplitIn] = Field(..., min_length=1)


@dataclass
class LedgerStore:
    exchange_rate_ves: Decimal = Decimal("45.50")
    products: Dict[str, ProductRecord] = field(default_factory=dict)
    sales_history: List[dict] = field(default_factory=list)

    def upsert_csv_catalog(self, csv_text: str) -> dict:
        reader = csv.DictReader(io.StringIO(csv_text.strip()))
        created, updated = 0, 0
        for row in reader:
            sku = row["sku"].strip().upper()
            name = row["name"].strip()
            cost = money(row["unit_cost_usd"].strip())
            price = money(row["sale_price_usd"].strip())
            stock = int(row["stock"].strip())
            if cost < Decimal("0") or price < cost:
                raise ValueError(f"Invalid pricing invariant for {sku}: price ({price}) < cost ({cost})")
            if sku in self.products:
                updated += 1
            else:
                created += 1
            self.products[sku] = ProductRecord(
                sku=sku, name=name, unit_cost_usd=cost, sale_price_usd=price, stock=stock
            )
        return {"created": created, "updated": updated, "total_skus": len(self.products)}

    def checkout(self, payload: CheckoutIn) -> dict:
        rate_snapshot = self.exchange_rate_ves
        total_usd = Decimal("0.00")

        # 1. Validate stock before mutating anything
        for item in payload.items:
            sku = item.sku.upper()
            if sku not in self.products:
                raise KeyError(f"SKU not found: {sku}")
            prod = self.products[sku]
            if prod.stock < item.qty:
                raise ValueError(f"Insufficient stock for {sku}: requested {item.qty}, available {prod.stock}")
            total_usd = money(total_usd + (prod.sale_price_usd * item.qty))

        # 2. Reconcile multi-currency split payments in USD equivalent
        paid_usd_equiv = Decimal("0.00")
        for p in payload.payments:
            if p.method == PaymentMethod.VES_PAGO_MOVIL:
                usd_eq = money(p.amount / rate_snapshot)
            else:
                usd_eq = money(p.amount)
            paid_usd_equiv = money(paid_usd_equiv + usd_eq)

        diff = money(paid_usd_equiv - total_usd)
        if diff < Decimal("0.00"):
            raise ValueError(f"Underpaid invoice: missing USD {abs(diff)}")

        # 3. Commit stock decrement & immutable receipt with credit balance if overpaid
        for item in payload.items:
            self.products[item.sku.upper()].stock -= item.qty

        receipt = {
            "invoice_id": f"INV-{len(self.sales_history) + 1:04d}",
            "customer_ref": payload.customer_ref,
            "rate_snapshot_ves": str(rate_snapshot),
            "total_usd": str(total_usd),
            "paid_usd_equiv": str(paid_usd_equiv),
            "credit_balance_usd": str(diff),
        }
        self.sales_history.append(receipt)
        return receipt


store = LedgerStore()
store.upsert_csv_catalog(
    "sku,name,unit_cost_usd,sale_price_usd,stock\n"
    "ZAP-101,Calzado Casual Cuero T41,22.00,39.90,12\n"
    "ZAP-102,Deportivo Running T42,26.50,48.00,8\n"
)

app = FastAPI(title="Multi-Currency POS & Role-Safe Catalog API", version="1.0.0")


@app.get("/catalog", response_model=List[ProductSellerOut])
def list_seller_catalog() -> List[ProductSellerOut]:
    rate = store.exchange_rate_ves
    return [
        ProductSellerOut(
            sku=p.sku,
            name=p.name,
            sale_price_usd=p.sale_price_usd,
            sale_price_ves=money(p.sale_price_usd * rate),
            stock=p.stock,
        )
        for p in store.products.values()
    ]


@app.get("/admin/margins", response_model=List[ProductOwnerOut])
def list_owner_margins(x_role: UserRole = Header(...)) -> List[ProductOwnerOut]:
    if x_role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Profit and cost margins are restricted to OWNER role.")
    rate = store.exchange_rate_ves
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
        for p in store.products.values()
    ]


@app.post("/sales/checkout")
def process_checkout(payload: CheckoutIn) -> dict:
    try:
        return store.checkout(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

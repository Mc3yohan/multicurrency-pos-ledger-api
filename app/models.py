# -*- coding: utf-8 -*-
"""Domain schemas and strict Decimal helpers for the multi-currency POS ledger."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List

from pydantic import BaseModel, Field

TWOPLACES = Decimal("0.01")


def money(val: Decimal | str | int) -> Decimal:
    """Quantize monetary amounts to 2 decimal places (ROUND_HALF_UP), forbidding float."""
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
    """Seller/public schema: unit_cost_usd and gross_margin_usd do not exist here by design."""

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
    sku: str = Field(..., min_length=1, max_length=64)
    qty: int = Field(..., gt=0)


class CheckoutIn(BaseModel):
    customer_ref: str = Field(..., min_length=1, max_length=64)
    items: List[SaleItemIn] = Field(..., min_length=1)
    payments: List[PaymentSplitIn] = Field(..., min_length=1)


class CsvImportIn(BaseModel):
    csv_text: str = Field(..., min_length=1)


class CustomerCreditOut(BaseModel):
    customer_ref: str
    credit_balance_usd: str

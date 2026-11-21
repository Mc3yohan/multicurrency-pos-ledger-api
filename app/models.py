# -*- coding: utf-8 -*-
"""Domain schemas and strict Decimal helpers for the multi-currency POS ledger."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from enum import Enum
from typing import List

from pydantic import BaseModel, Field, field_validator

TWOPLACES = Decimal("0.01")


def money(val: Decimal | str | int, *, rounding: str = ROUND_HALF_UP) -> Decimal:
    """Quantize monetary amounts to 2 decimal places, forbidding float and non-finite values."""
    if isinstance(val, float):
        raise TypeError("Floats are forbidden for monetary calculations; use Decimal or str.")
    try:
        dec = Decimal(val)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid monetary amount: {val!r}") from exc
    if not dec.is_finite():
        raise ValueError(f"Non-finite monetary amount: {val!r}")
    return dec.quantize(TWOPLACES, rounding=rounding)


def validate_exchange_rate(val: Decimal | str | int) -> Decimal:
    """Validate strictly positive finite exchange rate without truncating 4-decimal precision."""
    if isinstance(val, float):
        raise TypeError("Floats are forbidden for exchange rates; use Decimal or str.")
    try:
        dec = Decimal(val)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid exchange rate: {val!r}") from exc
    if not dec.is_finite() or dec <= Decimal("0"):
        raise ValueError(f"Exchange rate must be strictly positive: {val!r}")
    return dec


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

    @field_validator("amount")
    @classmethod
    def validate_two_decimal_places(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Payment amount must be finite.")
        if value != value.quantize(TWOPLACES, rounding=ROUND_DOWN):
            raise ValueError("Payment amount cannot exceed 2 decimal places.")
        return value


class SaleItemIn(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    qty: int = Field(..., gt=0)

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("SKU must not be blank.")
        return cleaned


class CheckoutIn(BaseModel):
    customer_ref: str = Field(..., min_length=1, max_length=64)
    items: List[SaleItemIn] = Field(..., min_length=1, max_length=100)
    payments: List[PaymentSplitIn] = Field(..., min_length=1, max_length=20)

    @field_validator("customer_ref")
    @classmethod
    def validate_customer_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("customer_ref must not be blank.")
        return cleaned


class CsvImportIn(BaseModel):
    csv_text: str = Field(..., min_length=1)


class CustomerCreditOut(BaseModel):
    customer_ref: str
    credit_balance_usd: str

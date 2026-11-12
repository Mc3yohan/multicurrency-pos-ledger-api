# -*- coding: utf-8 -*-
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from app.main import app, LedgerStore, CheckoutIn, SaleItemIn, PaymentSplitIn, PaymentMethod, money


def test_float_money_is_rejected():
    with pytest.raises(TypeError):
        money(19.99)


def test_seller_catalog_never_leaks_unit_cost_or_margins():
    client = TestClient(app)
    res = client.get("/catalog")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 2
    for item in data:
        assert "unit_cost_usd" not in item
        assert "gross_margin_usd" not in item


def test_seller_role_blocked_from_owner_margins_endpoint():
    client = TestClient(app)
    forbidden = client.get("/admin/margins", headers={"x-role": "SELLER"})
    assert forbidden.status_code == 403

    allowed = client.get("/admin/margins", headers={"x-role": "OWNER"})
    assert allowed.status_code == 200
    assert "unit_cost_usd" in allowed.json()[0]


def test_idempotent_csv_import_updates_existing_without_duplicates():
    st = LedgerStore(exchange_rate_ves=Decimal("45.00"))
    csv_data = (
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "ITEM-1,Producto A,10.00,15.00,5\n"
    )
    r1 = st.upsert_csv_catalog(csv_data)
    r2 = st.upsert_csv_catalog(csv_data)
    assert r1 == {"created": 1, "updated": 0, "total_skus": 1}
    assert r2 == {"created": 0, "updated": 1, "total_skus": 1}


def test_split_multicurrency_checkout_and_credit_balance():
    st = LedgerStore(exchange_rate_ves=Decimal("50.00"))
    st.upsert_csv_catalog(
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "PROD-X,Repuesto Demo,12.00,25.00,10\n"
    )
    # 2 units * 25.00 = 50.00 USD total
    # Pays 20 USD Cash + 1,000 VES (20 USD at rate 50) + 15 USDT = 55 USD -> 5.00 USD credit balance
    receipt = st.checkout(
        CheckoutIn(
            customer_ref="CLI-001",
            items=[SaleItemIn(sku="PROD-X", qty=2)],
            payments=[
                PaymentSplitIn(method=PaymentMethod.USD_CASH, amount=Decimal("20.00")),
                PaymentSplitIn(method=PaymentMethod.VES_PAGO_MOVIL, amount=Decimal("1000.00")),
                PaymentSplitIn(method=PaymentMethod.USDT_BINANCE, amount=Decimal("15.00")),
            ],
        )
    )
    assert receipt["total_usd"] == "50.00"
    assert receipt["paid_usd_equiv"] == "55.00"
    assert receipt["credit_balance_usd"] == "5.00"
    assert st.products["PROD-X"].stock == 8

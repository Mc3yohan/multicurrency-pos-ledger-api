# -*- coding: utf-8 -*-
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.main import (
    CheckoutIn,
    LedgerStore,
    PaymentMethod,
    PaymentSplitIn,
    SaleItemIn,
    app,
    create_app,
    money,
)


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


def test_owner_margins_requires_valid_owner_api_key():
    client = TestClient(app)
    assert client.get("/admin/margins").status_code == 401
    assert client.get("/admin/margins", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/admin/margins", headers={"X-API-Key": "demo-seller-key"}).status_code == 403

    allowed = client.get("/admin/margins", headers={"X-API-Key": "demo-owner-key"})
    assert allowed.status_code == 200
    assert "unit_cost_usd" in allowed.json()[0]
    assert "gross_margin_usd" in allowed.json()[0]


def test_idempotent_csv_import_and_atomic_rollback_on_bad_row():
    st = LedgerStore(exchange_rate_ves=Decimal("45.00"))
    csv_data = (
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "ITEM-1,Producto A,10.00,15.00,5\n"
    )
    r1 = st.upsert_csv_catalog(csv_data)
    r2 = st.upsert_csv_catalog(csv_data)
    assert r1 == {"created": 1, "updated": 0, "total_skus": 1}
    assert r2 == {"created": 0, "updated": 1, "total_skus": 1}

    # Second row has sale_price < unit_cost -> entire CSV batch must roll back
    bad_csv = (
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "ITEM-GOOD,Producto Bueno,5.00,10.00,3\n"
        "ITEM-BAD,Producto Malo,20.00,15.00,3\n"
    )
    with pytest.raises(ValueError, match="Invalid pricing invariant"):
        st.upsert_csv_catalog(bad_csv)
    assert "ITEM-GOOD" not in st.products


def test_duplicate_sku_lines_cannot_bypass_stock_check():
    st = LedgerStore(exchange_rate_ves=Decimal("50.00"))
    st.upsert_csv_catalog(
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "LIMITED-1,Edicion Limitada,10.00,20.00,10\n"
    )
    # 7 + 7 = 14 > available stock 10
    with pytest.raises(ValueError, match="Insufficient stock"):
        st.checkout(
            CheckoutIn(
                customer_ref="CLI-DUP",
                items=[
                    SaleItemIn(sku="LIMITED-1", qty=7),
                    SaleItemIn(sku="LIMITED-1", qty=7),
                ],
                payments=[PaymentSplitIn(method=PaymentMethod.USD_CASH, amount=Decimal("280.00"))],
            )
        )
    assert st.products["LIMITED-1"].stock == 10


def test_split_multicurrency_checkout_and_credit_persists_across_restarts(tmp_path):
    db_file = tmp_path / "pos.db"
    st1 = LedgerStore(exchange_rate_ves=Decimal("50.00"), db_path=db_file)
    st1.upsert_csv_catalog(
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "PROD-X,Repuesto Demo,12.00,25.00,10\n"
    )
    # 2 * 25.00 = 50.00 USD total
    # Pays 20 USD Cash + 1,000 VES (20 USD at rate 50) + 15 USDT = 55 USD -> 5.00 USD credit balance
    receipt = st1.checkout(
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
    st1.close()

    # Re-open from disk to confirm durable stock decrement and customer credit balance
    st2 = LedgerStore(exchange_rate_ves=Decimal("52.00"), db_path=db_file)
    assert st2.products["PROD-X"].stock == 8
    assert st2.get_customer_credit("CLI-001") == Decimal("5.00")
    st2.close()


def test_http_checkout_and_credit_endpoint_with_api_keys(tmp_path):
    api = create_app(
        db_path=tmp_path / "http_pos.db",
        exchange_rate_ves=Decimal("50.00"),
        owner_api_key="owner-secret",
        seller_api_key="seller-secret",
    )
    client = TestClient(api)

    # Unauthenticated checkout rejected
    unauth = client.post(
        "/sales/checkout",
        json={
            "customer_ref": "CLI-9",
            "items": [{"sku": "ZAP-101", "qty": 1}],
            "payments": [{"method": "USD_CASH", "amount": "40.00"}],
        },
    )
    assert unauth.status_code == 401

    # Seller checkout succeeds and records 0.10 credit balance (40.00 - 39.90)
    ok = client.post(
        "/sales/checkout",
        headers={"X-API-Key": "seller-secret"},
        json={
            "customer_ref": "CLI-9",
            "items": [{"sku": "ZAP-101", "qty": 1}],
            "payments": [{"method": "USD_CASH", "amount": "40.00"}],
        },
    )
    assert ok.status_code == 200
    assert ok.json()["credit_balance_usd"] == "0.10"

    bal = client.get("/customers/CLI-9/credit", headers={"X-API-Key": "seller-secret"})
    assert bal.status_code == 200
    assert bal.json()["credit_balance_usd"] == "0.10"

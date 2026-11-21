# -*- coding: utf-8 -*-
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import (
    CheckoutIn,
    LedgerStore,
    PaymentMethod,
    PaymentSplitIn,
    SaleItemIn,
    create_app,
    money,
)


def test_float_money_and_invalid_rates_are_rejected():
    with pytest.raises(TypeError):
        money(19.99)
    with pytest.raises(ValueError):
        LedgerStore(exchange_rate_ves=Decimal("0"))
    with pytest.raises(ValueError):
        LedgerStore(exchange_rate_ves=Decimal("-45.50"))
    # Preserve 4-decimal rates without truncating to 2 places
    st = LedgerStore(exchange_rate_ves=Decimal("45.5125"))
    assert st.exchange_rate_ves == Decimal("45.5125")
    st.close()


def test_seller_catalog_never_leaks_unit_cost_or_margins():
    with TestClient(create_app()) as client:
        res = client.get("/catalog")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 2
        for item in data:
            assert "unit_cost_usd" not in item
            assert "gross_margin_usd" not in item


def test_owner_margins_requires_valid_owner_api_key():
    with TestClient(create_app()) as client:
        assert client.get("/admin/margins").status_code == 401
        assert client.get("/admin/margins", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/admin/margins", headers={"X-API-Key": "demo-seller-key"}).status_code == 403

        allowed = client.get("/admin/margins", headers={"X-API-Key": "demo-owner-key"})
        assert allowed.status_code == 200
        assert "unit_cost_usd" in allowed.json()[0]
        assert "gross_margin_usd" in allowed.json()[0]


def test_malformed_csv_returns_400_and_rolls_back_atomically():
    with TestClient(create_app()) as client:
        hdr = {"X-API-Key": "demo-owner-key"}
        # Missing columns in second row -> 400 (never 500)
        missing_col = client.post(
            "/admin/catalog/import",
            headers=hdr,
            json={"csv_text": "sku,name,unit_cost_usd,sale_price_usd,stock\nBAD-1,OnlyTwoCols\n"},
        )
        assert missing_col.status_code == 400

        # Non-numeric / NaN price -> 400 (never 500)
        bad_decimal = client.post(
            "/admin/catalog/import",
            headers=hdr,
            json={"csv_text": "sku,name,unit_cost_usd,sale_price_usd,stock\nBAD-2,NaN Item,NaN,10.00,5\n"},
        )
        assert bad_decimal.status_code == 400


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

    bad_csv = (
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "ITEM-GOOD,Producto Bueno,5.00,10.00,3\n"
        "ITEM-BAD,Producto Malo,20.00,15.00,3\n"
    )
    with pytest.raises(ValueError, match="Invalid pricing invariant"):
        st.upsert_csv_catalog(bad_csv)
    assert "ITEM-GOOD" not in st.products
    st.close()


def test_rounding_pumping_and_subcent_splits_are_blocked():
    # Sub-cent splits (0.005) rejected at schema level
    with pytest.raises(ValidationError):
        PaymentSplitIn(method=PaymentMethod.USD_CASH, amount=Decimal("0.005"))

    # Blank customer_ref rejected
    with pytest.raises(ValidationError):
        CheckoutIn(
            customer_ref="   ",
            items=[SaleItemIn(sku="ZAP-101", qty=1)],
            payments=[PaymentSplitIn(method=PaymentMethod.USD_CASH, amount=Decimal("40.00"))],
        )

    # Aggregated VES conversion prevents split-rounding pumping (20 * Bs. 0.23 = Bs. 4.60 / 45.50 = $0.10, never $0.20)
    st = LedgerStore(exchange_rate_ves=Decimal("45.50"))
    st.upsert_csv_catalog(
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "CHEAP-1,Sticker,0.05,0.15,5\n"
    )
    with pytest.raises(ValueError, match="Underpaid invoice"):
        st.checkout(
            CheckoutIn(
                customer_ref="CLI-PUMP",
                items=[SaleItemIn(sku="CHEAP-1", qty=1)],
                payments=[
                    PaymentSplitIn(method=PaymentMethod.VES_PAGO_MOVIL, amount=Decimal("0.23"))
                    for _ in range(20)
                ],
            )
        )
    assert st.products["CHEAP-1"].stock == 5
    st.close()


def test_duplicate_sku_lines_cannot_bypass_stock_check():
    st = LedgerStore(exchange_rate_ves=Decimal("50.00"))
    st.upsert_csv_catalog(
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "LIMITED-1,Edicion Limitada,10.00,20.00,10\n"
    )
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
    st.close()


def test_split_multicurrency_checkout_and_credit_persists_across_restarts(tmp_path):
    db_file = tmp_path / "pos.db"
    st1 = LedgerStore(exchange_rate_ves=Decimal("50.00"), db_path=db_file)
    st1.upsert_csv_catalog(
        "sku,name,unit_cost_usd,sale_price_usd,stock\n"
        "PROD-X,Repuesto Demo,12.00,25.00,10\n"
    )
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
    with TestClient(api) as client:
        unauth = client.post(
            "/sales/checkout",
            json={
                "customer_ref": "CLI-9",
                "items": [{"sku": "ZAP-101", "qty": 1}],
                "payments": [{"method": "USD_CASH", "amount": "40.00"}],
            },
        )
        assert unauth.status_code == 401

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

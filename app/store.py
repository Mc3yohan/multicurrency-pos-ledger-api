# -*- coding: utf-8 -*-
"""Atomic SQLite WAL store for products, multi-currency invoices, and customer credits."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, List

from app.models import CheckoutIn, PaymentMethod, ProductRecord, money, validate_exchange_rate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerStore:
    """Transactional SQLite WAL POS ledger with strict Decimal storage."""

    def __init__(
        self,
        exchange_rate_ves: Decimal = Decimal("45.50"),
        db_path: str | Path = ":memory:",
    ) -> None:
        self.exchange_rate_ves = validate_exchange_rate(exchange_rate_ves)
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None lets us manage explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA busy_timeout=5000;")
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    sku TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    unit_cost_usd TEXT NOT NULL,
                    sale_price_usd TEXT NOT NULL,
                    stock INTEGER NOT NULL CHECK(stock >= 0),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id TEXT NOT NULL UNIQUE,
                    customer_ref TEXT NOT NULL,
                    rate_snapshot_ves TEXT NOT NULL,
                    total_usd TEXT NOT NULL,
                    paid_usd_equiv TEXT NOT NULL,
                    credit_balance_usd TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    payments_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS customer_credits (
                    customer_ref TEXT PRIMARY KEY,
                    balance_usd TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def products(self) -> Dict[str, ProductRecord]:
        """Return a snapshot mapping of SKU -> ProductRecord."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sku, name, unit_cost_usd, sale_price_usd, stock FROM products ORDER BY sku ASC"
            ).fetchall()
            return {
                row["sku"]: ProductRecord(
                    sku=row["sku"],
                    name=row["name"],
                    unit_cost_usd=money(row["unit_cost_usd"]),
                    sale_price_usd=money(row["sale_price_usd"]),
                    stock=int(row["stock"]),
                )
                for row in rows
            }

    def list_products(self) -> List[ProductRecord]:
        return list(self.products.values())

    def get_customer_credit(self, customer_ref: str) -> Decimal:
        cleaned = customer_ref.strip()
        if not cleaned:
            raise ValueError("customer_ref must not be blank.")
        with self._lock:
            row = self._conn.execute(
                "SELECT balance_usd FROM customer_credits WHERE customer_ref = ?",
                (cleaned,),
            ).fetchone()
            return money(row["balance_usd"]) if row else Decimal("0.00")

    def upsert_csv_catalog(self, csv_text: str) -> dict:
        """Atomically import or update catalog rows from CSV. Rolls back on any invariant error."""
        reader = csv.DictReader(io.StringIO(csv_text.strip()))
        required = {"sku", "name", "unit_cost_usd", "sale_price_usd", "stock"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")

        created, updated = 0, 0
        now = _utc_now_iso()

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for line_no, row in enumerate(reader, start=2):
                    if any(row.get(col) is None for col in required):
                        raise ValueError(f"Malformed CSV row {line_no}: missing required column values.")
                    sku = (row["sku"] or "").strip().upper()
                    name = (row["name"] or "").strip()
                    if not sku or not name:
                        raise ValueError(f"CSV row {line_no}: SKU and product name must be non-empty.")
                    cost = money((row["unit_cost_usd"] or "").strip())
                    price = money((row["sale_price_usd"] or "").strip())
                    try:
                        stock = int((row["stock"] or "").strip())
                    except ValueError as exc:
                        raise ValueError(f"CSV row {line_no}: invalid integer stock for {sku}.") from exc
                    if cost < Decimal("0.00") or price < cost:
                        raise ValueError(
                            f"Invalid pricing invariant for {sku}: price ({price}) < cost ({cost})"
                        )
                    if stock < 0:
                        raise ValueError(f"Invalid negative stock for {sku}: {stock}")

                    exists = self._conn.execute(
                        "SELECT 1 FROM products WHERE sku = ?",
                        (sku,),
                    ).fetchone()
                    if exists:
                        updated += 1
                    else:
                        created += 1

                    self._conn.execute(
                        """
                        INSERT INTO products (sku, name, unit_cost_usd, sale_price_usd, stock, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(sku) DO UPDATE SET
                            name = excluded.name,
                            unit_cost_usd = excluded.unit_cost_usd,
                            sale_price_usd = excluded.sale_price_usd,
                            stock = excluded.stock,
                            updated_at = excluded.updated_at
                        """,
                        (sku, name, str(cost), str(price), stock, now),
                    )
                total_skus = int(self._conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

        return {"created": created, "updated": updated, "total_skus": total_skus}

    def checkout(self, payload: CheckoutIn) -> dict:
        """Execute an atomic multi-currency checkout with BEGIN IMMEDIATE stock locking."""
        rate_snapshot = self.exchange_rate_ves
        now = _utc_now_iso()
        customer_ref = payload.customer_ref.strip()

        # Aggregate quantities per SKU first so duplicate lines cannot bypass stock checks
        requested_by_sku: Dict[str, int] = defaultdict(int)
        for item in payload.items:
            requested_by_sku[item.sku.strip().upper()] += item.qty

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                total_usd = Decimal("0.00")
                locked_rows: Dict[str, ProductRecord] = {}

                # 1. Validate every SKU and aggregate stock before mutating anything
                for sku, qty in requested_by_sku.items():
                    row = self._conn.execute(
                        "SELECT sku, name, unit_cost_usd, sale_price_usd, stock FROM products WHERE sku = ?",
                        (sku,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"SKU not found: {sku}")
                    prod = ProductRecord(
                        sku=row["sku"],
                        name=row["name"],
                        unit_cost_usd=money(row["unit_cost_usd"]),
                        sale_price_usd=money(row["sale_price_usd"]),
                        stock=int(row["stock"]),
                    )
                    if prod.stock < qty:
                        raise ValueError(
                            f"Insufficient stock for {sku}: requested {qty}, available {prod.stock}"
                        )
                    locked_rows[sku] = prod
                    total_usd = money(total_usd + (prod.sale_price_usd * qty))

                # 2. Aggregate tender by currency BEFORE conversion (prevents split-rounding pumping)
                total_ves_tender = Decimal("0.00")
                total_direct_usd = Decimal("0.00")
                serialized_payments = []
                for p in payload.payments:
                    amt = money(p.amount)
                    if p.method == PaymentMethod.VES_PAGO_MOVIL:
                        total_ves_tender = money(total_ves_tender + amt)
                    else:
                        total_direct_usd = money(total_direct_usd + amt)
                    serialized_payments.append({"method": p.method.value, "amount": str(amt)})

                # Convert aggregated VES once using ROUND_DOWN so fractional centavos never mint free credit
                ves_usd_equiv = (
                    money(total_ves_tender / rate_snapshot, rounding=ROUND_DOWN)
                    if total_ves_tender > Decimal("0.00")
                    else Decimal("0.00")
                )
                paid_usd_equiv = money(total_direct_usd + ves_usd_equiv)

                diff = money(paid_usd_equiv - total_usd)
                if diff < Decimal("0.00"):
                    raise ValueError(f"Underpaid invoice: missing USD {abs(diff)}")

                # 3. Decrement stock atomically
                for sku, qty in requested_by_sku.items():
                    self._conn.execute(
                        "UPDATE products SET stock = stock - ?, updated_at = ? WHERE sku = ?",
                        (qty, now, sku),
                    )

                # 4. Persist immutable invoice snapshot
                next_seq = int(self._conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM invoices").fetchone()[0])
                invoice_id = f"INV-{next_seq:04d}"
                serialized_items = [
                    {
                        "sku": sku,
                        "qty": qty,
                        "unit_price_usd": str(locked_rows[sku].sale_price_usd),
                    }
                    for sku, qty in requested_by_sku.items()
                ]
                self._conn.execute(
                    """
                    INSERT INTO invoices (
                        invoice_id, customer_ref, rate_snapshot_ves, total_usd,
                        paid_usd_equiv, credit_balance_usd, items_json, payments_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        invoice_id,
                        customer_ref,
                        str(rate_snapshot),
                        str(total_usd),
                        str(paid_usd_equiv),
                        str(diff),
                        json.dumps(serialized_items),
                        json.dumps(serialized_payments),
                        now,
                    ),
                )

                # 5. Accumulate customer credit balance if overpaid
                if diff > Decimal("0.00"):
                    cur_credit_row = self._conn.execute(
                        "SELECT balance_usd FROM customer_credits WHERE customer_ref = ?",
                        (customer_ref,),
                    ).fetchone()
                    current_bal = money(cur_credit_row["balance_usd"]) if cur_credit_row else Decimal("0.00")
                    new_bal = money(current_bal + diff)
                    self._conn.execute(
                        """
                        INSERT INTO customer_credits (customer_ref, balance_usd, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(customer_ref) DO UPDATE SET
                            balance_usd = excluded.balance_usd,
                            updated_at = excluded.updated_at
                        """,
                        (customer_ref, str(new_bal), now),
                    )

                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

        return {
            "invoice_id": invoice_id,
            "customer_ref": customer_ref,
            "rate_snapshot_ves": str(rate_snapshot),
            "total_usd": str(total_usd),
            "paid_usd_equiv": str(paid_usd_equiv),
            "credit_balance_usd": str(diff),
        }

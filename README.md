# Multi-Currency POS Ledger API

**Language:** **English** · [Español (README.es.md)](README.es.md)

[![CI](https://github.com/Mc3yohan/multicurrency-pos-ledger-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Mc3yohan/multicurrency-pos-ledger-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-38bdf8?style=flat-square)](LICENSE)

FastAPI point-of-sale ledger implementing transactional multi-currency checkout (**USD Cash, VES Mobile Payment, USDT**), **atomic SQLite WAL stock locking**, **strict `Decimal` monetary arithmetic**, **API-key RBAC cost-margin isolation**, and **idempotent CSV catalog imports**.

---

## Core Guarantees Implemented

1. **No `float` anywhere in money paths:** `money()` rejects `float` with `TypeError` and quantizes all values via `Decimal` (`ROUND_HALF_UP`).
2. **Atomic multi-currency checkout (`BEGIN IMMEDIATE`):** Stock availability is aggregated per SKU before any write (preventing duplicate line-item stock bypasses). Mixed USD, VES (converted with an immutable `rate_snapshot_ves`), and USDT payments reconcile into a persisted invoice and accumulate customer credit balance (`saldo a favor`) atomically. Any validation error rolls back the entire transaction.
3. **Schema-level cost & margin isolation:** `GET /catalog` returns `ProductSellerOut`, which structurally omits `unit_cost_usd` and `gross_margin_usd`. Owner endpoints (`GET /admin/margins`, `POST /admin/catalog/import`) enforce constant-time API-key verification (`X-API-Key` via `hmac.compare_digest`), returning `401` on invalid keys and `403` for seller keys.
4. **All-or-nothing idempotent CSV upsert:** Repeated imports update existing SKUs without duplicating records and roll back the entire batch if any row has negative stock or `sale_price_usd < unit_cost_usd`.

> **Scope note:** Default API keys (`demo-owner-key` / `demo-seller-key`) and seed products (`ZAP-101`, `ZAP-102`) are synthetic demo defaults configurable via environment variables (`POS_OWNER_API_KEY`, `POS_SELLER_API_KEY`, `POS_DB_PATH`, `EXCHANGE_RATE_VES`).

---

## Project Layout

```text
app/
├── main.py    # FastAPI app factory, /health, /catalog, /admin/*, /sales/checkout, /customers/*/credit
├── store.py   # Transactional SQLite WAL ledger (products, invoices, customer_credits)
├── auth.py    # Constant-time X-API-Key RBAC resolver (OWNER vs SELLER)
└── models.py  # Strict Decimal helper & separated Seller/Owner Pydantic schemas
tests/
└── test_ledger.py
Dockerfile
```

---

## Quick Start

### Local (Python 3.11+)
```bash
pip install -r requirements.txt
pytest -v

POS_DB_PATH=./data/ledger.db POS_OWNER_API_KEY=owner-secret POS_SELLER_API_KEY=seller-secret \
  uvicorn app.main:app --reload
```

### Docker
```bash
docker build -t multicurrency-pos-ledger .
docker run --rm -p 8000:8000 \
  -e POS_OWNER_API_KEY=owner-secret \
  -e POS_SELLER_API_KEY=seller-secret \
  multicurrency-pos-ledger
```

### Example API Calls
```bash
# Public/seller catalog (zero cost or margin fields serialized)
curl -s http://127.0.0.1:8000/catalog

# Owner margin view (401 without key, 403 with seller key)
curl -s http://127.0.0.1:8000/admin/margins -H "X-API-Key: demo-owner-key"
```

---

## Author & License

Built by **Víctor Yohan Betancourt Marín** — [quanticosystems.com](https://quanticosystems.com).  
Released under the [MIT License](LICENSE).

# API de Caja POS Multimoneda

**Idioma:** [English (README.md)](README.md) · **Español**

[![CI](https://github.com/Mc3yohan/multicurrency-pos-ledger-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Mc3yohan/multicurrency-pos-ledger-api/actions/workflows/ci.yml)
[![Licencia: MIT](https://img.shields.io/badge/Licencia-MIT-38bdf8?style=flat-square)](LICENSE)

Motor transaccional POS en FastAPI con pagos mixtos multimoneda (**Efectivo USD, Pago Móvil VES, USDT**), **bloqueo atómico de inventario en SQLite WAL**, **aritmética monetaria estricta con `Decimal`**, **aislamiento de márgenes con RBAC por API-Key** e **importador idempotente CSV**.

---

## Garantías Técnicas Implementadas

1. **Prohibición de `float` en dinero:** `money()` rechaza `float` con `TypeError` y cuantiza cada monto con `Decimal` (`ROUND_HALF_UP`).
2. **Checkout atómico multimoneda (`BEGIN IMMEDIATE`):** Agrupa cantidades por SKU antes de mutar stock (evitando que líneas repetidas burlen el límite de inventario). Concilia pagos mixtos USD, VES (con *snapshot* inmutable de tasa `rate_snapshot_ves`) y USDT, persiste la factura y acumula el saldo a favor del cliente en una sola transacción atómica.
3. **Privacidad de costos y márgenes por esquema:** `GET /catalog` responde con `ProductSellerOut`, que estructuralmente no contiene `unit_cost_usd` ni `gross_margin_usd`. Las rutas administrativas (`/admin/margins`, `/admin/catalog/import`) exigen `X-API-Key` validada en tiempo constante (`hmac.compare_digest`), devolviendo `401` si falta o es inválida y `403` si pertenece a un vendedor.
4. **Importador CSV idempotente todo-o-nada:** Reimportar el mismo catálogo actualiza sin duplicar SKUs y revierte todo el lote si alguna fila trae stock negativo o `sale_price_usd < unit_cost_usd`.

> **Alcance:** Las llaves por defecto (`demo-owner-key` / `demo-seller-key`) y productos semilla (`ZAP-101`, `ZAP-102`) son valores sintéticos configurables vía variables de entorno (`POS_OWNER_API_KEY`, `POS_SELLER_API_KEY`, `POS_DB_PATH`, `EXCHANGE_RATE_VES`).

---

## Estructura

```text
app/
├── main.py    # Factoría FastAPI, /health, /catalog, /admin/*, /sales/checkout, /customers/*/credit
├── store.py   # Libro contable SQLite WAL transaccional (products, invoices, customer_credits)
├── auth.py    # Verificación X-API-Key en tiempo constante (OWNER vs SELLER)
└── models.py  # Cuantizador Decimal y esquemas Pydantic separados para Vendedor/Dueño
tests/
└── test_ledger.py
Dockerfile
```

---

## Inicio Rápido

### Local (Python 3.11+)
```bash
pip install -r requirements.txt
PYTHONPATH=. pytest -v

POS_DB_PATH=./data/ledger.db POS_OWNER_API_KEY=clave-dueno POS_SELLER_API_KEY=clave-vendedor \
  uvicorn app.main:app --reload
```

### Docker
```bash
docker build -t multicurrency-pos-ledger .
docker run --rm -p 8000:8000 \
  -e POS_OWNER_API_KEY=clave-dueno \
  -e POS_SELLER_API_KEY=clave-vendedor \
  multicurrency-pos-ledger
```

### Ejemplos de Llamadas API
```bash
# Catálogo público/vendedor (sin costos ni márgenes)
curl -s http://127.0.0.1:8000/catalog

# Vista de márgenes del dueño (401 sin clave, 403 con clave de vendedor)
curl -s http://127.0.0.1:8000/admin/margins -H "X-API-Key: demo-owner-key"
```

---

## Autoría y Licencia

Desarrollado por **Víctor Yohan Betancourt Marín** — [quanticosystems.com](https://quanticosystems.com).  
Publicado bajo [Licencia MIT](LICENSE).

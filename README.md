# Multi-Currency POS Ledger & Role-Isolated Catalog API

Implementación demostrativa en **Python + FastAPI + Pydantic v2** de los invariantes financieros y de privacidad utilizados en sistemas de Punto de Venta (POS) e inventario multimoneda de alta confiabilidad.

> **Autor:** Víctor Yohan Betancourt Marín — [quanticosystems.com](https://quanticosystems.com)  
> *Nota: Código demostrativo construido con datos 100% sintéticos para portafolio de ingeniería.*

## Invariantes de Ingeniería Demostrados
1. **Prohibición estricta de `float` en dinero:** Toda operación monetaria exige `Decimal` con redondeo financiero explícito (`ROUND_HALF_UP`).
2. **Pagos mixtos multimoneda + Saldos a Favor:** Conciliación atómica combinando Efectivo USD, Pago Móvil (VES con *snapshot* inmutable de tasa al momento de facturar) y USDT, generando saldo a favor exacto cuando aplica.
3. **Privacidad de Costos a Nivel de Esquema (`Anti-Leak`):** Los endpoints de consulta y venta para cajeros/vendedores usan esquemas Pydantic separados que **nunca serializan costos unitarios ni márgenes de ganancia**, restringidos exclusivamente al rol `OWNER`.
4. **Importador Idempotente CSV/ERP:** Sincronización repetible de catálogos exportados desde hojas de cálculo o ERPs tradicionales sin duplicar registros ni aceptar precios por debajo del costo.

## Pruebas automatizadas
```bash
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload
```

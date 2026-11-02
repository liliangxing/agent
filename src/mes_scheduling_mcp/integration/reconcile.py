"""ERP <-> MES reconciliation.

Periodically compare ERP work orders against MES production orders (joined via
the dual-key mapping) and flag drift:

- ``missing_mes`` -- an ERP released order has no MES counterpart
- ``missing_erp`` -- a MES order has no ERP source
- ``drift``        -- quantity / status disagree between the two systems

This is the "对账" step that a real integration must run to stay trustworthy.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ..store import Store


class Reconciler:
    def __init__(self, store: Store) -> None:
        self._store = store

    def run(self) -> dict[str, Any]:
        detail: list[dict[str, Any]] = []
        matched = drift = missing_mes = missing_erp = 0

        erp_orders = {w.order_no: w for w in self._store.list_work_orders()
                      if w.status in ("RELEASED", "IN_PROCESS", "COMPLETED")}
        mes_orders = {p.erp_work_order_no: p for p in self._store.list_production_orders()}

        for erp_no, w in erp_orders.items():
            p = mes_orders.get(erp_no)
            if p is None:
                missing_mes += 1
                detail.append({"erp_id": erp_no, "issue": "missing_mes"})
                continue
            if p.quantity != w.quantity:
                drift += 1
                detail.append({"erp_id": erp_no, "mes_id": p.production_order_no,
                               "issue": "drift", "erp_qty": w.quantity, "mes_qty": p.quantity})
            elif str(p.status) == "COMPLETED" and str(w.status) not in ("COMPLETED", "CLOSED"):
                # MES reports done but ERP still open: COMPLETION feedback lost.
                drift += 1
                detail.append({"erp_id": erp_no, "mes_id": p.production_order_no,
                               "issue": "drift", "erp_status": str(w.status),
                               "mes_status": str(p.status)})
            else:
                matched += 1

        # catch MES orders whose ERP source is unmatched (orphaned on the MES side)
        erp_nos = set(erp_orders)
        for p in self._store.list_production_orders():
            if p.erp_work_order_no not in erp_nos:
                missing_erp += 1
                detail.append({"mes_id": p.production_order_no,
                               "erp_id": p.erp_work_order_no, "issue": "missing_erp"})

        run = {
            "run_id": uuid.uuid4().hex[:12],
            "ts": datetime.now().isoformat(timespec="seconds"),
            "matched": matched, "drift": drift,
            "missing_mes": missing_mes, "missing_erp": missing_erp,
            "detail": detail,
        }
        self._store.insert_reconciliation(run)
        return {"summary": {k: run[k] for k in ("matched", "drift", "missing_mes", "missing_erp")},
                "detail": detail, "healthy": drift == 0 and missing_mes == 0}

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._store.list_reconciliations(limit=limit)
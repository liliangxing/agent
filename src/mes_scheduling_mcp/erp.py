"""ERP-domain operations: work-order and material/BOM queries.

Mirrors the "ERP side" of the integration. In a production deployment
these calls would hit the ERP vendor's API; here they operate on the
store so the whole scenario remains runnable without external systems.
"""
from __future__ import annotations

from typing import Optional

from .models import OrderStatus, to_dict, WorkOrder
from .store import Store


class ERPService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list_work_orders(
        self,
        status: Optional[str] = None,
        due_date_from: Optional[str] = None,
        due_date_to: Optional[str] = None,
    ) -> list[dict]:
        orders = self.store.list_work_orders(status, due_date_from, due_date_to)
        return to_dict(orders)

    def get_work_order(self, order_no: str) -> dict:
        order = self.store.get_work_order(order_no)
        if order is None:
            raise ValueError(f"工单不存在: {order_no}")
        return to_dict(order)

    def release_work_order(self, order_no: str) -> dict:
        """Release a draft order so it becomes visible to scheduling."""
        order = self.store.get_work_order(order_no)
        if order is None:
            raise ValueError(f"工单不存在: {order_no}")
        if order.status != OrderStatus.DRAFT.value:
            raise ValueError(f"工单 {order_no} 当前状态为 {order.status}，仅 DRAFT 可发布")
        self.store.set_order_status(order_no, OrderStatus.RELEASED.value)
        self.store.commit()
        return to_dict(self.store.get_work_order(order_no))

    def list_materials(self, sku: Optional[str] = None) -> list[dict]:
        return to_dict(self.store.list_materials(sku))

    def check_material_availability(self, order_no: str) -> dict:
        """BOM 齐套检查: 对照工单数量计算所需物料与缺口."""
        order = self.store.get_work_order(order_no)
        if order is None:
            raise ValueError(f"工单不存在: {order_no}")

        bom = self.store.get_bom(order.product_sku)
        if not bom:
            return {
                "order_no": order_no,
                "product_sku": order.product_sku,
                "quantity": order.quantity,
                "fulfilled": True,
                "note": "该产品无 BOM 定义，视为无需齐套",
                "requirements": [],
                "shortages": [],
            }

        requirements = []
        shortages = []
        for line in bom:
            needed = round(line.qty_per_unit * order.quantity, 6)
            material = self.store.get_material(line.material_sku)
            stock = material.stock_qty if material else 0.0
            available = material.available_qty if material else 0.0
            shortage = round(max(0.0, needed - available), 6)
            requirements.append({
                "material_sku": line.material_sku,
                "material_name": material.name if material else line.material_sku,
                "unit": material.unit if material else "",
                "needed_qty": needed,
                "stock_qty": stock,
                "reserved_qty": material.reserved_qty if material else 0.0,
                "available_qty": available,
                "shortage_qty": shortage,
            })
            if shortage > 0:
                shortages.append({"material_sku": line.material_sku, "shortage_qty": shortage})

        return {
            "order_no": order_no,
            "product_sku": order.product_sku,
            "product_name": order.product_name,
            "quantity": order.quantity,
            "fulfilled": len(shortages) == 0,
            "requirements": requirements,
            "shortages": shortages,
        }
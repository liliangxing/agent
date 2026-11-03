"""Single source of truth for the MCP tool surface.

The same catalog powers ``search_tools`` (agent-side discovery) and the web
demo's tool listing, so the two can never drift apart.
"""
from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, str]] = [
    # ------------------------------------------------------------------ ERP
    {"name": "erp_list_items", "group": "ERP", "description": "查询物料主数据"},
    {"name": "erp_get_item", "group": "ERP", "description": "按 SKU 查询物料"},
    {"name": "erp_create_item", "group": "ERP", "description": "新建物料主数据"},
    {"name": "erp_get_bom", "group": "ERP", "description": "查询产品 BOM"},
    {"name": "erp_bom_explode", "group": "ERP", "description": "多级 BOM 展开与缺口计算"},
    {"name": "erp_check_material_availability", "group": "ERP", "description": "工单齐套检查"},
    {"name": "erp_inventory_summary", "group": "ERP", "description": "库存汇总"},
    {"name": "erp_inventory_adjust", "group": "ERP", "description": "库存出入库调整"},
    {"name": "erp_list_sales_orders", "group": "ERP", "description": "查询销售订单"},
    {"name": "erp_create_sales_order", "group": "ERP", "description": "新建销售订单"},
    {"name": "erp_list_purchase_orders", "group": "ERP", "description": "查询采购订单"},
    {"name": "erp_create_purchase_order", "group": "ERP", "description": "新建采购订单"},
    {"name": "erp_receive_purchase_order", "group": "ERP", "description": "采购收货"},
    {"name": "erp_list_work_orders", "group": "ERP", "description": "查询生产工单"},
    {"name": "erp_get_work_order", "group": "ERP", "description": "查询单个工单"},
    {"name": "erp_create_work_order", "group": "ERP", "description": "新建工单"},
    {"name": "erp_release_work_order", "group": "ERP", "description": "发布工单并广播 ORDER_RELEASE"},
    {"name": "erp_complete_work_order", "group": "ERP", "description": "标记工单完工"},
    {"name": "erp_close_work_order", "group": "ERP", "description": "关闭工单"},
    {"name": "erp_run_mrp", "group": "ERP", "description": "运行 MRP 净算（分桶、逐层、含在途与安全库存）"},
    {"name": "erp_list_proposals", "group": "ERP", "description": "查询 MRP 建议"},
    # ------------------------------------------------------------------ MES
    {"name": "mes_list_work_centers", "group": "MES", "description": "查询工作中心状态"},
    {"name": "mes_get_work_center", "group": "MES", "description": "查询单个工作中心"},
    {"name": "mes_set_work_center_status", "group": "MES", "description": "设置工作中心状态"},
    {"name": "mes_list_shifts", "group": "MES", "description": "查询班次日历"},
    {"name": "mes_list_routings", "group": "MES", "description": "查询工艺路线"},
    {"name": "mes_get_routing", "group": "MES", "description": "查询指定工艺路线"},
    {"name": "mes_list_production_orders", "group": "MES", "description": "查询 MES 生产订单"},
    {"name": "mes_get_production_order", "group": "MES", "description": "查询单个生产订单"},
    {"name": "mes_list_operations", "group": "MES", "description": "查询工序进度"},
    {"name": "mes_start_production_order", "group": "MES", "description": "生产订单开工"},
    {"name": "mes_report_progress", "group": "MES", "description": "报工/完工（末道工序生成成品批次与谱系）"},
    {"name": "mes_report_downtime", "group": "MES", "description": "上报设备停机"},
    {"name": "mes_oee_report", "group": "MES", "description": "设备综合效率 OEE"},
    {"name": "mes_quality_inspect", "group": "MES", "description": "质量检验登记"},
    {"name": "mes_list_quality_records", "group": "MES", "description": "查询质量检验记录"},
    {"name": "mes_issue_material", "group": "MES", "description": "生产领料/倒冲（按批次 FIFO 占用）"},
    {"name": "mes_list_consumptions", "group": "MES", "description": "查询工单领料批次明细"},
    {"name": "mes_trace_lot", "group": "MES", "description": "批次谱系追溯（上游/下游）"},
    # ------------------------------------------------------------------ APS
    {"name": "capacity_query", "group": "排产", "description": "查询产能与班次"},
    {"name": "schedule_generate", "group": "排产", "description": "有限产能排产（班次窗口+换型矩阵+双日期）"},
    {"name": "schedule_dispatch", "group": "排产", "description": "下发排产计划"},
    {"name": "schedule_detail", "group": "排产", "description": "查询计划明细"},
    {"name": "schedule_list", "group": "排产", "description": "查询历史计划"},
    # ------------------------------------------------------------ integration
    {"name": "integration_sync_orders", "group": "集成", "description": "ERP→MES 同步生产订单"},
    {"name": "integration_sync_progress", "group": "集成", "description": "MES→ERP 回写完工"},
    {"name": "integration_reconcile", "group": "集成", "description": "ERP↔MES 对账"},
    {"name": "integration_outbox_status", "group": "集成", "description": "查看发件箱事件"},
    {"name": "integration_inbox_status", "group": "集成", "description": "查看收件箱事件"},
    {"name": "integration_mappings", "group": "集成", "description": "查看双主键映射"},
    {"name": "integration_audit_log", "group": "集成", "description": "查询审计留痕"},
    # ------------------------------------------------------------- ops/meta
    {"name": "list_resources", "group": "运维", "description": "列出资源目录"},
    {"name": "get_resource_schema", "group": "运维", "description": "查看资源字段模型"},
    {"name": "health_check", "group": "运维", "description": "健康检查"},
    {"name": "usage_stats", "group": "运维", "description": "工具调用统计"},
    {"name": "reset_demo_data", "group": "运维", "description": "重置演示数据（admin）"},
    {"name": "search_tools", "group": "运维", "description": "按关键词检索工具面，便于 Agent 自助发现"},
]

GROUPS: list[str] = ["ERP", "MES", "排产", "集成", "运维"]


def search(query: str | None = None, group: str | None = None, limit: int = 20) -> list[dict[str, str]]:
    """Case-insensitive search over name/description, optionally filtered by group."""
    token = (query or "").strip().lower()
    results: list[dict[str, str]] = []
    for tool in TOOLS:
        if group and tool["group"] != group:
            continue
        if token and token not in tool["name"].lower() and token not in tool["description"].lower():
            continue
        results.append(tool)
    return results[: max(1, limit)]


def as_dicts() -> list[dict[str, Any]]:
    return [dict(t, desc=t["description"]) for t in TOOLS]
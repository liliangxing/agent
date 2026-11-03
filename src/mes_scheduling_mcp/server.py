"""MCP server: ERP <-> APS 排产 <-> MES integration, production-shaped.

The MCP layer is a thin transport boundary.  The real work happens in:

- backend facade (:class:`ManufacturingBackend`) + integration fabric
  (idempotency, transactional outbox/inbox, ERP/MES dual-key mapping, audit
  trail, reconciliation);
- domain services ``erp`` / ``mes`` / ``aps``.

Every tool returns a single JSON text block and is wrapped in: API-key
authentication, role-based access control (fail-closed), usage observability,
and error sanitization.  Pydantic inputs reject unknown fields.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from . import __version__, access, auth, gateway, tool_catalog
from .backend import ManufacturingBackend
from .catalog import catalog_as_dict, get_spec
from .errors import McpError, sanitize_error
from .observability import UsageLogger, timed
from .schemas import (
    InventoryAdjust,
    ItemCreate,
    MaterialIssue,
    PurchaseOrderCreate,
    QualityInspect,
    ReportDowntime,
    ReportProgress,
    SalesOrderCreate,
    ScheduleGenerate,
    WorkOrderCreate,
)
from .store import Store


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _extract_api_key(ctx: Optional[Context]) -> Optional[str]:
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
    except Exception:
        return None
    headers = getattr(request, "headers", None)
    if not headers:
        return None
    value = headers.get("authorization") or headers.get("x-mes-api-key")
    if not value:
        return None
    return value.removeprefix("Bearer ").strip()


def build_server(db_path: Optional[str] = None,
                 backend: Optional[ManufacturingBackend] = None) -> FastMCP:
    mcp = FastMCP(
        "mes-scheduling",
        instructions=(
            "MES ↔ AI 排产 Agent ↔ ERP 生产集成服务（江苏某精密零部件厂）。"
            "典型闭环：erp_list_work_orders 查工单 → erp_check_material_availability 齐套 → "
            "integration_sync_orders 同步到 MES → schedule_generate 有限产能排产 → "
            "schedule_dispatch 下发 → mes_report_progress 报工 → integration_sync_progress 回写 ERP → "
            "integration_reconcile 对账。"
            "工具面同时提供薄网关：search_tools 发现 → describe_tool 读契约 → "
            "call_tool 调用（支持 fields/limit/offset/truncate 结果整形）；"
            "DESTRUCTIVE 工具必须走 call_destructive_tool。"
            "列表读取默认 25 行、最多 100 行 / 20000 字符，返回 total 以便翻页。"
        ),
    )

    backend = backend or ManufacturingBackend(Store(db_path))
    erp = backend.erp
    mes = backend.mes
    aps = backend.aps
    itg = backend.integration
    access_controller = access.AccessController()
    usage = UsageLogger()

    def _subject(ctx: Optional[Context]):
        return auth.authenticate(_extract_api_key(ctx))

    def _execute(tool: str, resource: str, op: str, ctx: Optional[Context],
                 fn: Callable[[], Any], params: Optional[dict] = None) -> str:
        """Run ``fn`` behind auth, access-control, obs, and error sanitization."""
        params = params or {}
        try:
            access_controller.check(_subject(ctx), resource, op)
        except McpError as exc:
            return _json({"error": {"code": exc.code, "message": exc.message}})
        try:
            return _json(timed(usage, tool, fn, params))
        except McpError as exc:
            return _json({"error": {"code": exc.code, "message": exc.message}})
        except KeyError as exc:
            return _json({"error": {"code": "not_found", "message": str(exc)}})
        except ValueError as exc:
            return _json({"error": {"code": "validation_error", "message": str(exc)}})
        except Exception as exc:  # noqa: BLE001
            return _json({"error": {"code": sanitize_error(exc).code,
                                    "message": "internal error; see server log"}})

    # ---- /health route -------------------------------------------------------
    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request):
        from starlette.responses import JSONResponse

        data = backend.health()
        data["version"] = __version__
        data["auth_required"] = auth.required()
        return JSONResponse(data)

    # ---- resources (read-only) ----------------------------------------------
    @mcp.resource("mes://{resource}/{record_id}")
    def read_resource(resource: str, record_id: str) -> str:
        return _execute(f"{resource}_get", resource, "read", None,
                        lambda: backend.resource_get(resource, record_id),
                        {"resource": resource, "record_id": record_id})

    # ==================================================================== ERP
    @mcp.tool()
    def erp_list_items(item_type: str | None = None, ctx: Context = None) -> str:
        """查询物料主数据（可按类型过滤）。"""
        return _execute("erp_list_items", "item", "read", ctx,
                        lambda: [i.__dict__ for i in erp.list_items(item_type=item_type)],
                        {"item_type": item_type})

    @mcp.tool()
    def erp_get_item(sku: str, ctx: Context = None) -> str:
        """按 SKU 查询单个物料主数据。"""
        return _execute("erp_get_item", "item", "read", ctx,
                        lambda: erp.get_item(sku).__dict__, {"sku": sku})

    @mcp.tool()
    def erp_create_item(data: ItemCreate, ctx: Context = None) -> str:
        """新建物料主数据（严格校验字段）。"""
        return _execute("erp_create_item", "item", "create", ctx,
                        lambda: erp.create_item(**data.model_dump()).__dict__,
                        {"data": data.model_dump()})

    @mcp.tool()
    def erp_get_bom(product_sku: str, ctx: Context = None) -> str:
        """查询产品 BOM（按产品 SKU 解析默认 BOM）。"""
        return _execute("erp_get_bom", "item", "read", ctx,
                        lambda: erp.get_bom_by_product(product_sku), {"product_sku": product_sku})

    @mcp.tool()
    def erp_bom_explode(product_sku: str, quantity: int, ctx: Context = None) -> str:
        """多级 BOM 展开并计算净需求与缺口。"""
        return _execute("erp_bom_explode", "item", "read", ctx,
                        lambda: erp.bom_explode(product_sku, quantity),
                        {"product_sku": product_sku, "quantity": quantity})

    @mcp.tool()
    def erp_check_material_availability(order_no: str, ctx: Context = None) -> str:
        """生产工单齐套检查：展开 BOM 并标记物料缺口。"""
        return _execute("erp_check_material_availability", "work_order", "read", ctx,
                        lambda: erp.check_material_availability(order_no), {"order_no": order_no})

    @mcp.tool()
    def erp_inventory_summary(sku: str | None = None, ctx: Context = None) -> str:
        """查询库存汇总（在库 / 占用 / 可用）。"""
        return _execute("erp_inventory_summary", "inventory", "read", ctx,
                        lambda: erp.inventory_summary(sku=sku), {"sku": sku})

    @mcp.tool()
    def erp_inventory_adjust(data: InventoryAdjust, ctx: Context = None) -> str:
        """库存出入库调整（入库 / 出库 / 占用）。"""
        return _execute("erp_inventory_adjust", "inventory", "update", ctx,
                        lambda: erp.inventory_adjust(**data.model_dump()),
                        {"data": data.model_dump()})

    @mcp.tool()
    def erp_list_sales_orders(status: str | None = None, ctx: Context = None) -> str:
        """查询销售订单。"""
        return _execute("erp_list_sales_orders", "sales_order", "read", ctx,
                        lambda: [o.__dict__ for o in erp.list_sales_orders(status=status)],
                        {"status": status})

    @mcp.tool()
    def erp_create_sales_order(data: SalesOrderCreate, ctx: Context = None) -> str:
        """新建销售订单（含明细行）。"""
        return _execute("erp_create_sales_order", "sales_order", "create", ctx,
                        lambda: erp.create_sales_order(**data.model_dump()).__dict__,
                        {"data": data.model_dump()})

    @mcp.tool()
    def erp_list_purchase_orders(status: str | None = None, ctx: Context = None) -> str:
        """查询采购订单。"""
        return _execute("erp_list_purchase_orders", "purchase_order", "read", ctx,
                        lambda: [o.__dict__ for o in erp.list_purchase_orders(status=status)],
                        {"status": status})

    @mcp.tool()
    def erp_create_purchase_order(data: PurchaseOrderCreate, ctx: Context = None) -> str:
        """新建采购订单（含明细行）。"""
        return _execute("erp_create_purchase_order", "purchase_order", "create", ctx,
                        lambda: erp.create_purchase_order(**data.model_dump()).__dict__,
                        {"data": data.model_dump()})

    @mcp.tool()
    def erp_receive_purchase_order(po_no: str, ctx: Context = None) -> str:
        """采购收货：增加库存并更新状态。"""
        return _execute("erp_receive_purchase_order", "purchase_order", "update", ctx,
                        lambda: erp.receive_purchase_order(po_no), {"po_no": po_no})

    @mcp.tool()
    def erp_list_work_orders(status: str | None = None, due_date_from: str | None = None,
                             due_date_to: str | None = None, ctx: Context = None) -> str:
        """查询生产工单（可按状态 / 交期过滤）。"""
        return _execute("erp_list_work_orders", "work_order", "read", ctx,
                        lambda: [o.__dict__ for o in erp.list_work_orders(
                            status=status, due_date_from=due_date_from, due_date_to=due_date_to)],
                        {"status": status})

    @mcp.tool()
    def erp_get_work_order(order_no: str, ctx: Context = None) -> str:
        """按工单号查询单个工单。"""
        return _execute("erp_get_work_order", "work_order", "read", ctx,
                        lambda: erp.get_work_order(order_no).__dict__, {"order_no": order_no})

    @mcp.tool()
    def erp_create_work_order(data: WorkOrderCreate, ctx: Context = None) -> str:
        """新建生产工单（严格校验字段）。"""
        return _execute("erp_create_work_order", "work_order", "create", ctx,
                        lambda: erp.create_work_order(**data.model_dump()).__dict__,
                        {"data": data.model_dump()})

    @mcp.tool()
    def erp_release_work_order(order_no: str, idempotency_key: str | None = None,
                               ctx: Context = None) -> str:
        """发布工单并通过事务性发件箱向 MES 广播 ORDER_RELEASE。"""
        def run():
            wrapped = itg.idempotency.run(idempotency_key, f"release:{order_no}",
                                       lambda: erp.release_work_order(order_no))
            out = dict(wrapped["result"])
            out["replayed"] = wrapped["replayed"]
            return out
        return _execute("erp_release_work_order", "work_order", "release", ctx, run,
                        {"order_no": order_no, "idempotency_key": idempotency_key})

    @mcp.tool()
    def erp_complete_work_order(order_no: str, completed_qty: int | None = None,
                                ctx: Context = None) -> str:
        """标记工单完工。"""
        return _execute("erp_complete_work_order", "work_order", "update", ctx,
                        lambda: erp.complete_work_order(order_no, completed_qty),
                        {"order_no": order_no})

    @mcp.tool()
    def erp_close_work_order(order_no: str, ctx: Context = None) -> str:
        """关闭工单。"""
        return _execute("erp_close_work_order", "work_order", "update", ctx,
                        lambda: erp.close_work_order(order_no), {"order_no": order_no})

    @mcp.tool()
    def erp_run_mrp(ctx: Context = None) -> str:
        """运行 MRP：对所有已发布工单做物料需求净算并生成采购建议。"""
        return _execute("erp_run_mrp", "integration", "read", ctx, erp.run_mrp)

    @mcp.tool()
    def erp_list_proposals(status: str | None = None, ctx: Context = None) -> str:
        """查询 MRP 采购/生产建议。"""
        return _execute("erp_list_proposals", "item", "read", ctx,
                        lambda: [p.__dict__ for p in erp.list_proposals(status=status)],
                        {"status": status})

    # ==================================================================== MES
    @mcp.tool()
    def mes_list_work_centers(status: str | None = None, ctx: Context = None) -> str:
        """查询工作中心（设备）状态。"""
        return _execute("mes_list_work_centers", "work_center", "read", ctx,
                        lambda: [c.__dict__ for c in mes.list_work_centers(status=status)],
                        {"status": status})

    @mcp.tool()
    def mes_get_work_center(code: str, ctx: Context = None) -> str:
        """查询单个工作中心。"""
        return _execute("mes_get_work_center", "work_center", "read", ctx,
                        lambda: mes.get_work_center(code).__dict__, {"code": code})

    @mcp.tool()
    def mes_set_work_center_status(code: str, status: str, ctx: Context = None) -> str:
        """设置工作中心状态（RUNNING/IDLE/BREAKDOWN/MAINTENANCE…）。"""
        return _execute("mes_set_work_center_status", "work_center", "update", ctx,
                        lambda: mes.set_work_center_status(code, status),
                        {"code": code, "status": status})

    @mcp.tool()
    def mes_list_shifts(work_center: str | None = None, shift_date: str | None = None,
                        ctx: Context = None) -> str:
        """查询班次日历。"""
        return _execute("mes_list_shifts", "work_center", "read", ctx,
                        lambda: mes.list_shifts(work_center=work_center, shift_date=shift_date),
                        {"work_center": work_center, "shift_date": shift_date})

    @mcp.tool()
    def mes_list_routings(product_sku: str | None = None, ctx: Context = None) -> str:
        """查询工艺路线（可按产品过滤）。"""
        return _execute("mes_list_routings", "routing", "read", ctx,
                        lambda: mes.list_routings(product_sku=product_sku), {"product_sku": product_sku})

    @mcp.tool()
    def mes_get_routing(routing_code: str, ctx: Context = None) -> str:
        """查询指定工艺路线的全部工序。"""
        return _execute("mes_get_routing", "routing", "read", ctx,
                        lambda: mes.get_routing(routing_code), {"routing_code": routing_code})

    @mcp.tool()
    def mes_list_production_orders(status: str | None = None, ctx: Context = None) -> str:
        """查询 MES 生产订单（对应 ERP 工单）。"""
        return _execute("mes_list_production_orders", "production_order", "read", ctx,
                        lambda: [p.__dict__ for p in mes.list_production_orders(status=status)],
                        {"status": status})

    @mcp.tool()
    def mes_get_production_order(production_order_no: str, ctx: Context = None) -> str:
        """查询单个 MES 生产订单。"""
        return _execute("mes_get_production_order", "production_order", "read", ctx,
                        lambda: mes.get_production_order(production_order_no).__dict__,
                        {"production_order_no": production_order_no})

    @mcp.tool()
    def mes_list_operations(production_order_no: str, ctx: Context = None) -> str:
        """查询生产订单各工序的进度。"""
        return _execute("mes_list_operations", "production_order", "read", ctx,
                        lambda: [o.__dict__ for o in mes.list_operations(production_order_no)],
                        {"production_order_no": production_order_no})

    @mcp.tool()
    def mes_start_production_order(production_order_no: str, ctx: Context = None) -> str:
        """开工：生产订单进入 IN_PROCESS，首道工序进入 SETUP。"""
        return _execute("mes_start_production_order", "production_order", "update", ctx,
                        lambda: mes.start_production_order(production_order_no),
                        {"production_order_no": production_order_no})

    @mcp.tool()
    def mes_report_progress(data: ReportProgress, ctx: Context = None) -> str:
        """报工：上报工序合格/不合格数量；完成后自动推进下一工序或完成订单。"""
        return _execute("mes_report_progress", "production_order", "report", ctx,
                        lambda: mes.report_progress(**data.model_dump()),
                        {"data": data.model_dump()})

    @mcp.tool()
    def mes_report_downtime(data: ReportDowntime, ctx: Context = None) -> str:
        """上报设备停机（供 OEE 计算）。"""
        return _execute("mes_report_downtime", "work_center", "update", ctx,
                        lambda: mes.report_downtime(**data.model_dump()),
                        {"data": data.model_dump()})

    @mcp.tool()
    def mes_oee_report(work_center: str, ctx: Context = None) -> str:
        """查询设备综合效率（OEE：可用率 × 表现 × 质量）。"""
        return _execute("mes_oee_report", "work_center", "read", ctx,
                        lambda: mes.oee_report(work_center), {"work_center": work_center})

    @mcp.tool()
    def mes_quality_inspect(data: QualityInspect, ctx: Context = None) -> str:
        """质量检验登记（合格 / 返工 / 报废 / 让步）。"""
        return _execute("mes_quality_inspect", "production_order", "inspect", ctx,
                        lambda: mes.quality_inspect(**data.model_dump()),
                        {"data": data.model_dump()})

    @mcp.tool()
    def mes_list_quality_records(production_order_no: str | None = None, ctx: Context = None) -> str:
        """查询质量检验记录。"""
        return _execute("mes_list_quality_records", "production_order", "read", ctx,
                        lambda: [q.__dict__ for q in mes.list_quality_records(production_order_no=production_order_no)],
                        {"production_order_no": production_order_no})

    @mcp.tool()
    def mes_issue_material(data: MaterialIssue, ctx: Context = None) -> str:
        """生产领料 / 倒冲：占用并扣减库存。"""
        return _execute("mes_issue_material", "production_order", "issue", ctx,
                        lambda: mes.issue_material(**data.model_dump()),
                        {"data": data.model_dump()})

    @mcp.tool()
    def mes_list_consumptions(production_order_no: str | None = None, ctx: Context = None) -> str:
        """查询工单领料批次明细（用于批次追溯与成本归集）。"""
        return _execute("mes_list_consumptions", "production_order", "read", ctx,
                        lambda: mes.list_consumptions(production_order_no=production_order_no),
                        {"production_order_no": production_order_no})

    @mcp.tool()
    def mes_trace_lot(lot_no: str, direction: str = "both", max_depth: int = 10,
                      ctx: Context = None) -> str:
        """批次谱系追溯：up 查来料批次，down 查受影响成品批次，both 双向。"""
        return _execute("mes_trace_lot", "production_order", "read", ctx,
                        lambda: mes.trace_lot(lot_no, direction=direction, max_depth=max_depth),
                        {"lot_no": lot_no, "direction": direction, "max_depth": max_depth})

    # ==================================================================== APS
    @mcp.tool()
    def capacity_query(ctx: Context = None) -> str:
        """查询各工作中心的产能与班次可用小时。"""
        return _execute("capacity_query", "schedule", "read", ctx, aps.capacity_query)

    @mcp.tool()
    def schedule_generate(data: ScheduleGenerate, ctx: Context = None) -> str:
        """有限产能排产：考虑物料、产能、换型与工序先后。"""
        kwargs = data.model_dump()
        if kwargs.get("plan_date"):
            kwargs["plan_date"] = kwargs["plan_date"].isoformat()
        return _execute("schedule_generate", "schedule", "create", ctx,
                        lambda: aps.generate_schedule(**kwargs), {"data": data.model_dump()})

    @mcp.tool()
    def schedule_dispatch(plan_id: str, idempotency_key: str | None = None,
                          ctx: Context = None) -> str:
        """下发排产计划到 MES：激活生产订单并写入计划起止时间。"""
        def run():
            wrapped = itg.idempotency.run(idempotency_key, f"dispatch:{plan_id}",
                                       lambda: aps.dispatch_schedule(plan_id))
            out = dict(wrapped["result"])
            out["replayed"] = wrapped["replayed"]
            return out
        return _execute("schedule_dispatch", "schedule", "dispatch", ctx, run,
                        {"plan_id": plan_id, "idempotency_key": idempotency_key})

    @mcp.tool()
    def schedule_detail(plan_id: str, ctx: Context = None) -> str:
        """查询排产计划明细。"""
        return _execute("schedule_detail", "schedule", "read", ctx,
                        lambda: aps.schedule_detail(plan_id), {"plan_id": plan_id})

    @mcp.tool()
    def schedule_list(status: str | None = None, ctx: Context = None) -> str:
        """查询历史排产计划。"""
        return _execute("schedule_list", "schedule", "read", ctx,
                        lambda: [p.__dict__ for p in aps.list_schedules(status=status)],
                        {"status": status})

    # ============================================================ integration
    @mcp.tool()
    def integration_sync_orders(ctx: Context = None) -> str:
        """ERP → MES：消费 ORDER_RELEASE 事件，创建 MES 生产订单并建立映射。"""
        return _execute("integration_sync_orders", "integration", "sync", ctx,
                        backend.synchronize_orders)

    @mcp.tool()
    def integration_sync_progress(ctx: Context = None) -> str:
        """MES → ERP：消费 COMPLETION 反馈，回写 ERP 工单完工。"""
        return _execute("integration_sync_progress", "integration", "sync", ctx,
                        backend.synchronize_progress)

    @mcp.tool()
    def integration_reconcile(ctx: Context = None) -> str:
        """对账：比较 ERP 工单与 MES 生产订单，检测缺失与漂移。"""
        return _execute("integration_reconcile", "integration", "reconcile", ctx,
                        backend.reconcile)

    @mcp.tool()
    def integration_outbox_status(status: str | None = None, ctx: Context = None) -> str:
        """查看发件箱事件（集成消息队列）。"""
        return _execute("integration_outbox_status", "integration", "read", ctx,
                        lambda: backend.outbox_status(status=status), {"status": status})

    @mcp.tool()
    def integration_inbox_status(ctx: Context = None) -> str:
        """查看收件箱事件（已投递消息）。"""
        return _execute("integration_inbox_status", "integration", "read", ctx,
                        backend.inbox_status)

    @mcp.tool()
    def integration_mappings(ctx: Context = None) -> str:
        """查看 ERP ↔ MES 工单双主键映射。"""
        return _execute("integration_mappings", "integration", "read", ctx,
                        backend.mappings)

    @mcp.tool()
    def integration_audit_log(resource: str | None = None, limit: int = 100,
                              ctx: Context = None) -> str:
        """查询审计留痕（谁在何时改了什么）。"""
        return _execute("integration_audit_log", "integration", "read", ctx,
                        lambda: backend.audit_log(resource=resource, limit=limit),
                        {"resource": resource, "limit": limit})

    # ================================================================ system
    @mcp.tool()
    def list_resources(ctx: Context = None) -> str:
        """列出全部资源目录及字段模型。"""
        return _execute("list_resources", "system", "read", ctx,
                        lambda: {"resources": catalog_as_dict()})

    @mcp.tool()
    def get_resource_schema(resource: str, ctx: Context = None) -> str:
        """查看某资源的字段模型与可读写字段。"""
        def run():
            spec = get_spec(resource)
            return {"name": spec.name, "label": spec.label, "module": spec.module,
                    "id_field": spec.id_field, "description": spec.description,
                    "searchable": list(spec.searchable), "writable": list(spec.writable)}
        return _execute("get_resource_schema", "system", "read", ctx, run, {"resource": resource})

    @mcp.tool()
    def health_check(ctx: Context = None) -> str:
        """服务健康检查：ERP/MES 统计、集成队列与鉴权配置。"""
        def run():
            data = backend.health()
            data["version"] = __version__
            data["auth_required"] = auth.required()
            return data
        return _execute("health_check", "system", "read", ctx, run)

    @mcp.tool()
    def usage_stats(ctx: Context = None) -> str:
        """返回本服务的工具调用统计（次数 / 成败 / 耗时 / 错误类型）。"""
        return _execute("usage_stats", "system", "read", ctx, usage.stats)

    @mcp.tool()
    def reset_demo_data(ctx: Context = None) -> str:
        """重置演示数据到种子状态（需要 admin 权限）。"""
        def run():
            backend.reset()
            return {"status": "reset", **backend.health()}
        return _execute("reset_demo_data", "system", "reset", ctx, run)

    @mcp.tool()
    def search_tools(query: str | None = None, group: str | None = None,
                     classification: str | None = None, limit: int = 20, offset: int = 0,
                     ctx: Context = None) -> str:
        """按关键词/分组/读写分类检索可用工具，便于 Agent 自助发现能力。"""
        def run():
            return gateway.search(mcp._tool_manager, query=query, group=group,
                                  classification=classification, limit=limit, offset=offset,
                                  registry=mcp._gateway_registry)
        return _execute("search_tools", "system", "read", ctx, run,
                        {"query": query, "group": group, "classification": classification,
                         "limit": limit, "offset": offset})

    # ------------------------------------------------------------ meta gateway
    @mcp.tool()
    async def describe_tool(name: str | None = None, names: list[str] | None = None,
                            ctx: Context = None) -> str:
        """查看一个或多个工具（最多 10 个）的完整契约：描述、分类、注解、入参 schema。"""
        requested = list(names) if names else ([name] if name else [])
        return _json(gateway.describe(mcp._tool_manager, requested, registry=mcp._gateway_registry))

    @mcp.tool()
    async def call_tool(name: str, arguments: Any = None,
                        fields: list[str] | None = None, limit: int | None = None,
                        offset: int = 0, truncate: bool = True,
                        ctx: Context = None) -> str:
        """按名称调用 READ/WRITE 工具；支持 fields 投影、limit/offset 分页与字符预算。"""
        result = await gateway.call(
            mcp._tool_manager, name, arguments=arguments, fields=fields, limit=limit,
            offset=offset, truncate=truncate, context=ctx, destructive=False,
            registry=mcp._gateway_registry)
        return _json(result)

    @mcp.tool()
    async def call_destructive_tool(name: str, arguments: Any = None,
                                    fields: list[str] | None = None, limit: int | None = None,
                                    offset: int = 0, truncate: bool = True,
                                    ctx: Context = None) -> str:
        """调用 DESTRUCTIVE 工具（删除/重置）；普通工具请走 call_tool。"""
        result = await gateway.call(
            mcp._tool_manager, name, arguments=arguments, fields=fields, limit=limit,
            offset=offset, truncate=truncate, context=ctx, destructive=True,
            registry=mcp._gateway_registry)
        return _json(result)

    # Attach READ/WRITE/DESTRUCTIVE annotations to every registered tool so
    # clients can gate destructive calls before they run.
    for tool in mcp._tool_manager.list_tools():
        hints = gateway.annotation_hints(tool_catalog.classification(tool.name))
        tool.annotations = ToolAnnotations(**hints)

    registry = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    mcp._gateway_registry = registry

    # Optional thin-gateway deployment: hide the explicit domain tools from
    # list_tools while keeping them dispatchable through call_tool.
    if os.environ.get("MES_MCP_GATEWAY_ONLY", "").lower() in ("1", "true", "yes"):
        for name in list(registry):
            if name not in gateway.META_TOOLS:
                mcp._tool_manager.remove_tool(name)

    return mcp


mcp = build_server()


def main() -> None:
    import os

    transport = os.environ.get("MES_MCP_TRANSPORT", "stdio")
    if transport in ("streamable-http", "sse"):
        mcp.settings.host = os.environ.get("MES_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("MES_MCP_PORT", "8000"))
        mcp.run(transport=transport)
        return
    mcp.run()


if __name__ == "__main__":
    main()
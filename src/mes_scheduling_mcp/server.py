"""MCP server: ERP -> scheduling -> MES bridge, production-shaped.

Layering mirrors mature ERP MCP servers (mcp-server-odoo, KingdeeMCP):

* a **backend abstraction** behind which a real ERP/MES can be plugged;
* **resources** (read-only) separated from **tools** (writes);
* per-resource **role-based access control** and API-key **auth**;
* strict **pydantic input models** (``extra="forbid"``);
* **observation** (usage JSONL + stats) and **error sanitization**;
* a **catalog** for runtime schema discovery;
* **stdio and streamable-HTTP** transports with a ``/health`` route.

All tools return a single JSON text block. Write/domain tools also return an
error envelope when they fail, so the client never sees raw internals.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from mcp.server.fastmcp import Context, FastMCP

from . import __version__, access, auth
from .backend import ManufacturingBackend, MemoryBackend
from .catalog import CATALOG, catalog_as_dict, get_spec
from .erp import ERPService
from .errors import McpError, sanitize_error
from .mes import MESService
from .observability import UsageLogger, timed
from .scheduling import SchedulingService
from .schemas import (
    CapacityQueryInput,
    MaterialCreate,
    MaterialUpdate,
    OrderNosInput,
    PlanFilter,
    PlanIdInput,
    ProductionLineUpdate,
    ProductionTaskUpdate,
    ReportProgressInput,
    ResourceSchemaInput,
    ScheduleGenerateInput,
    WorkOrderCreate,
    WorkOrderRelease,
    WorkOrderUpdate,
)
from .store import Store

CREATE_MODEL = {"work_order": WorkOrderCreate, "material": MaterialCreate}
UPDATE_MODEL = {
    "work_order": WorkOrderUpdate,
    "material": MaterialUpdate,
    "production_line": ProductionLineUpdate,
    "production_task": ProductionTaskUpdate,
}


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


def build_server(db_path: Optional[str] = None) -> FastMCP:
    mcp = FastMCP(
        "mes-scheduling",
        instructions=(
            "MES ↔ AI 排产 Agent ↔ ERP 桥接服务。"
            "先用 list_resources / get_resource_schema 了解数据模型，再走："
            "work_order_query 查工单 → erp_check_material_availability 齐套 → "
            "capacity_query 查产能 → schedule_generate 排产 → schedule_dispatch 下发 → "
            "mes_report_progress 报工。"
        ),
    )

    store = Store(db_path)
    backend: ManufacturingBackend = MemoryBackend(store)
    erp = ERPService(store)
    sched = SchedulingService(store)
    mes = MESService(store)
    access_controller = access.AccessController()
    usage = UsageLogger()

    # ---- runtime helpers -----------------------------------------------------
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
            result = timed(usage, tool, fn, params)
            return _json(result)
        except McpError as exc:
            return _json({"error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:
            return _json({"error": {"code": sanitize_error(exc).code,
                                    "message": "internal error; see server log"}})

    # ---- /health route (streamable HTTP) --------------------------------------
    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request):
        from starlette.responses import JSONResponse

        data = mes.health()
        data["version"] = __version__
        data["auth_required"] = auth.required()
        return JSONResponse(data)

    # ---- resources (read-only) -----------------------------------------------
    @mcp.resource("mes://{resource}/{record_id}")
    def read_resource(resource: str, record_id: str) -> str:
        return _execute(
            f"{resource}_get", resource, "read", None,
            lambda: backend.get_record(resource, record_id),
            {"resource": resource, "record_id": record_id},
        )

    # ---- generic read tools (query / get / count) ----------------------------
    def _register_read_tools(name: str) -> None:
        spec = get_spec(name)

        @mcp.tool(name=f"{name}_query")
        def query(
            status: str | None = None,
            keyword: str | None = None,
            limit: int = 25,
            offset: int = 0,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> str:
            """分页查询资源记录，可按 status 与 keyword 过滤。"""
            def run() -> dict:
                records = backend.list_records(name, {"status": status, "keyword": keyword})
                return {
                    "resource": name,
                    "total": len(records),
                    "offset": offset,
                    "limit": limit,
                    "items": records[offset:offset + limit],
                }

            return _execute(f"{name}_query", name, "read", ctx, run,
                            {"status": status, "keyword": keyword})

        @mcp.tool(name=f"{name}_get")
        def get(record_id: str, ctx: Context = None) -> str:  # type: ignore[assignment]
            """按主键读取单个资源记录。"""
            return _execute(f"{name}_get", name, "read", ctx,
                            lambda: backend.get_record(name, record_id),
                            {"record_id": record_id})

        @mcp.tool(name=f"{name}_count")
        def count(status: str | None = None, keyword: str | None = None,
                  ctx: Context = None) -> str:  # type: ignore[assignment]
            """统计资源记录数量。"""
            return _execute(f"{name}_count", name, "read", ctx,
                            lambda: {"resource": name,
                                     "count": backend.count_records(name, {"status": status, "keyword": keyword})},
                            {"status": status, "keyword": keyword})

    for spec_ in CATALOG:
        _register_read_tools(spec_)

    # ---- generic write tools (create / update) ------------------------------
    def _register_create_tool(name: str, model) -> None:
        def create(data, ctx: Context = None) -> str:
            """创建一条新记录。"""
            return _execute(f"{name}_create", name, "create", ctx,
                            lambda: backend.create_record(name, data),
                            {"data": data.model_dump()})

        create.__annotations__ = {"data": model, "ctx": Context, "return": str}
        mcp.tool(name=f"{name}_create")(create)

    def _register_update_tool(name: str, model) -> None:
        spec = get_spec(name)

        def update(data, ctx: Context = None) -> str:
            """更新一条记录（input 内含主键字段）。"""
            record_id = getattr(data, spec.id_field)
            return _execute(f"{name}_update", name, "update", ctx,
                            lambda: backend.update_record(name, record_id, data),
                            {"data": data.model_dump()})

        update.__annotations__ = {"data": model, "ctx": Context, "return": str}
        mcp.tool(name=f"{name}_update")(update)

    for name, model in CREATE_MODEL.items():
        _register_create_tool(name, model)
    for name, model in UPDATE_MODEL.items():
        _register_update_tool(name, model)

    # ---- ERP domain tools ----------------------------------------------------
    @mcp.tool()
    def erp_release_work_order(order_no: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """发布草稿工单(DRAFT)为待排产(RELEASED)。"""
        return _execute("erp_release_work_order", "work_order", "release", ctx,
                        lambda: erp.release_work_order(order_no), {"order_no": order_no})

    @mcp.tool()
    def erp_check_material_availability(order_no: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """BOM 齐套检查：返回所需物料、缺口与是否满足。"""
        return _execute("erp_check_material_availability", "material", "read", ctx,
                        lambda: erp.check_material_availability(order_no), {"order_no": order_no})

    # ---- scheduling domain tools ---------------------------------------------
    @mcp.tool()
    def capacity_query(plan_date: str | None = None, ctx: Context = None) -> str:  # type: ignore[assignment]
        """查询各产线产能与剩余可用小时。"""
        return _execute("capacity_query", "schedule", "read", ctx,
                        lambda: sched.capacity_query(plan_date), {"plan_date": plan_date})

    @mcp.tool()
    def schedule_generate(plan_date: str | None = None, ctx: Context = None) -> str:  # type: ignore[assignment]
        """生成排产计划（物料齐套 + 产能约束），返回计划与物料缺口。"""
        return _execute("schedule_generate", "schedule", "create", ctx,
                        lambda: sched.generate_schedule(plan_date), {"plan_date": plan_date})

    @mcp.tool()
    def schedule_list(status: str | None = None, ctx: Context = None) -> str:  # type: ignore[assignment]
        """查询历史排产计划。"""
        return _execute("schedule_list", "schedule", "read", ctx,
                        lambda: sched.list_schedules(status), {"status": status})

    @mcp.tool()
    def schedule_detail(plan_id: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """查询单个排产计划的明细行。"""
        return _execute("schedule_detail", "schedule", "read", ctx,
                        lambda: sched.get_schedule(plan_id), {"plan_id": plan_id})

    @mcp.tool()
    def schedule_dispatch(plan_id: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """下发排产计划到产线：生成 MES 任务、产线置忙。"""
        return _execute("schedule_dispatch", "schedule", "dispatch", ctx,
                        lambda: mes.dispatch_schedule(plan_id), {"plan_id": plan_id})

    # ---- MES domain tools ----------------------------------------------------
    @mcp.tool()
    def mes_import_work_orders(order_nos: list[str] | None = None, ctx: Context = None) -> str:  # type: ignore[assignment]
        """将 ERP 工单录入 MES 为生产任务（对应此前人工录入步骤）。"""
        return _execute("mes_import_work_orders", "work_order", "update", ctx,
                        lambda: mes.import_work_orders(order_nos), {"order_nos": order_nos})

    @mcp.tool()
    def mes_report_progress(task_id: str, actual_qty: int, complete: bool = False,
                            ctx: Context = None) -> str:  # type: ignore[assignment]
        """报工：更新实际产量，达标或 complete=True 时完工并释放产线。"""
        return _execute("mes_report_progress", "production_task", "report", ctx,
                        lambda: mes.report_progress(task_id, actual_qty, complete),
                        {"task_id": task_id, "actual_qty": actual_qty, "complete": complete})

    # ---- discovery / ops -----------------------------------------------------
    @mcp.tool()
    def list_resources(ctx: Context = None) -> str:  # type: ignore[assignment]
        """列出全部资源类型、字段与可写操作（供 Agent 理解数据模型）。"""
        return _execute("list_resources", "system", "read", ctx, catalog_as_dict)

    @mcp.tool()
    def get_resource_schema(resource: str, ctx: Context = None) -> str:  # type: ignore[assignment]
        """查询单个资源类型的 schema 说明。"""
        spec = get_spec(resource)
        return _execute("get_resource_schema", "system", "read", ctx,
                        lambda: {
                            "name": spec.name, "label": spec.label, "module": spec.module,
                            "id_field": spec.id_field, "description": spec.description,
                            "searchable": list(spec.searchable), "writable": list(spec.writable),
                        }, {"resource": resource})

    @mcp.tool()
    def health_check(ctx: Context = None) -> str:  # type: ignore[assignment]
        """服务健康检查：返回工单/任务/产线统计与验证配置。"""
        def run() -> dict:
            data = mes.health()
            data["version"] = __version__
            data["auth_required"] = auth.required()
            return data

        return _execute("health_check", "system", "read", ctx, run)

    @mcp.tool()
    def usage_stats(ctx: Context = None) -> str:  # type: ignore[assignment]
        """返回本服务的工具调用统计（次数/成败/耗时/错误类型）。"""
        return _execute("usage_stats", "system", "read", ctx, usage.stats)

    @mcp.tool()
    def reset_demo_data(ctx: Context = None) -> str:  # type: ignore[assignment]
        """重置演示数据到种子状态（需要 admin 权限）。"""
        def run() -> dict:
            store.reset()
            return {"status": "reset", **mes.health()}

        return _execute("reset_demo_data", "system", "reset", ctx, run)

    return mcp


mcp = build_server()


def main() -> None:
    import os

    transport = os.environ.get("MES_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.settings.host = os.environ.get("MES_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("MES_MCP_PORT", "8000"))
        mcp.run(transport="streamable-http")
        return
    if transport == "sse":
        mcp.settings.host = os.environ.get("MES_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("MES_MCP_PORT", "8000"))
        mcp.run(transport="sse")
        return
    mcp.run()


if __name__ == "__main__":
    main()
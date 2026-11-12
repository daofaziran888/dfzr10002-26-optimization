"""数据导出配置接口：维护导出模板，覆盖提交校验、启用配置、停用配置等动作，
并提供按快照键缓存的导出预览与文件下载。"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.export import ExportService

router = APIRouter(prefix="/api/export", tags=["数据导出配置"])

service = ExportService()

LIST_FIELDS = ["模板编号", "模板名称", "数据源绑定", "导出格式", "字段选择", "筛选参数", "导出上限", "超限策略", "是否启用"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def _file_headers(snapshot: Any, hit: bool | None) -> dict[str, str]:
    """下载响应头：把快照键、命中情况、数据代际带回给前端，方便顺着链路排查。"""
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(snapshot.filename)}",
        "X-Snapshot-Key": snapshot.key,
        "X-Data-Generation": str(snapshot.generation),
        # 状态含中文，HTTP 头只能放 latin-1，百分号编码后由前端解码
        "X-Export-Status": quote(snapshot.overflow),
    }
    if hit is not None:
        headers["X-Snapshot-Cache"] = "HIT" if hit else "MISS"
    return headers


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤数据导出配置列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/snapshots")
def list_snapshots() -> dict[str, Any]:
    """当前全部导出快照：快照键、字段选择、结果范围、上限与缓存状态一览。"""
    items = service.list_snapshots()
    return {"module": "export", "total": len(items), "items": items}


@router.get("/snapshots/{snapshot_key}/download")
def download_snapshot(snapshot_key: str) -> Response:
    """按快照键下载文件：数据刷新或模板停用后，旧键一律返回 410。"""
    snapshot, message, status_code = service.download_by_key(snapshot_key)
    if snapshot is None:
        raise HTTPException(status_code=status_code, detail=message)
    return Response(
        content=snapshot.content,
        media_type=snapshot.media_type,
        headers=_file_headers(snapshot, hit=True),
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条导出模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"导出模板 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/preview")
def preview_entry(entry_id: int) -> dict[str, Any]:
    """结果预览：字段选择、导出格式、结果范围与上限、截断/拒绝状态、当前缓存快照。"""
    preview, message = service.preview(entry_id)
    if preview is None:
        status_code = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message)
    return preview


@router.get("/{entry_id}/download")
def download_entry(entry_id: int) -> Response:
    """按模板当前配置下载导出文件；命中缓存时返回同一份结果。"""
    result, message = service.download(entry_id)
    if result is None:
        status_code = 422 if "上限" in message else 404 if "不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message)
    snapshot, hit = result
    return Response(
        content=snapshot.content,
        media_type=snapshot.media_type,
        headers=_file_headers(snapshot, hit=hit),
    )


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条导出模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="导出模板已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改模板配置：字段选择等变更后旧快照失效，下一次下载以模板最新字段为准。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="导出模板已更新，旧快照已失效，下一次下载采用新配置", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条导出模板执行提交校验、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据导出配置清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "export", "total": total, "items": items}

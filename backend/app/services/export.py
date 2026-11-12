"""数据导出配置业务规则：状态流转、字段校验、导出快照与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.export_snapshot import ExportConfigError, ExportSnapshot, snapshots
from app.store import store

MODULE = "export"
REQUIRED_FIELDS = ["模板编号", "模板名称", "数据源绑定"]
EDITABLE_FIELDS = ["模板名称", "数据源绑定", "导出格式", "字段选择", "筛选参数", "导出上限", "超限策略"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]
DISABLED_STATUS = "已停用"


class ExportService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.update({field: values[field] for field in EDITABLE_FIELDS if field in values})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        # 导出模块的数据变了：依赖它的导出快照（如 EXP-0004）同步失效
        snapshots.data_refreshed(MODULE, "数据导出配置新增了模板，旧快照已失效")
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """修改模板配置：字段选择、格式、筛选、上限、策略变更后旧快照失效，以最新字段为准。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [f"导出模板 {entry_id} 不存在或已归档"]
        updates = {field: values[field] for field in EDITABLE_FIELDS if field in values}
        if not updates:
            return None, ["没有可更新的字段"]
        merged = {**entry, **updates}
        try:
            snapshots.read_config(merged)
        except ExportConfigError as exc:
            return None, [str(exc)]
        entry.update(updates)
        template_code = str(entry.get("模板编号") or "")
        # 先按「配置变更」让本模板旧快照失效，再推进模块代际让依赖导出模块的快照失效
        snapshots.invalidate_template(template_code, "模板配置已变更，以模板最新字段为准")
        snapshots.data_refreshed(MODULE, "数据导出配置的模板数据已更新")
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据导出配置可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        message = f"导出模板已{action}"
        if target == DISABLED_STATUS:
            invalidated = snapshots.invalidate_template(
                str(entry.get("模板编号") or ""), "模板已停用，旧快照不可再下载"
            )
            if invalidated:
                message += f"，{invalidated} 份旧快照已失效"
        return entry, message

    # ---------- 导出快照 ----------

    def preview(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """结果预览：字段选择、导出格式、结果范围与上限、截断/拒绝状态、当前缓存快照。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出模板 {entry_id} 不存在或已归档"
        try:
            key, config = snapshots.snapshot_key(entry)
        except ExportConfigError as exc:
            return None, str(exc)
        planned = snapshots.plan(config)
        existing = snapshots.find(key)
        cache_hit = existing is not None and existing.status == "有效"
        if cache_hit:
            cache_note = f"命中现有快照，创建于 {existing.created_at}，已复用 {existing.hits} 次"
        elif existing is not None:
            cache_note = f"原快照已失效（{existing.invalid_reason}），下载时将新建"
        else:
            cache_note = "当前无有效快照，下载时将新建"
        total, end, overflow = planned["total"], planned["end"], planned["overflow"]
        limit = config["导出上限"]
        if overflow == "已拒绝":
            status_note = f"共 {total} 行，超过上限 {limit} 行，按模板策略「拒绝」不生成文件"
        elif overflow == "已截断":
            status_note = f"共 {total} 行，超过上限 {limit} 行，按模板策略「截断」只导出第 1–{end} 行"
        else:
            status_note = f"共 {total} 行，未超上限 {limit} 行，完整导出"
        fields = config["字段选择"]
        sample = [{field: row.get(field) for field in fields} for row in planned["rows"][:5]]
        return {
            "模板编号": config["模板编号"],
            "模板名称": entry.get("模板名称"),
            "模板状态": entry.get("status"),
            "数据源绑定": config["数据源绑定"],
            "数据代际": config["数据代际"],
            "字段选择": fields,
            "导出格式": config["导出格式"],
            "筛选参数": entry.get("筛选参数") or "",
            "导出上限": limit,
            "超限策略": config["超限策略"],
            "结果总数": total,
            "结果范围": {"start": 1 if end else 0, "end": end},
            "导出状态": overflow,
            "状态说明": status_note,
            "快照键": key,
            "缓存状态": "命中" if cache_hit else "未命中",
            "缓存说明": cache_note,
            "样例行": sample,
        }, ""

    def download(self, entry_id: int) -> tuple[tuple[ExportSnapshot, bool] | None, str]:
        """按模板当前配置下载：命中复用同一快照，未命中新建；超限策略为拒绝时不生成文件。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出模板 {entry_id} 不存在或已归档"
        if entry.get("status") == DISABLED_STATUS:
            return None, f"导出模板 {entry.get('模板编号')} 已停用，请先启用配置再导出"
        try:
            snapshot, hit = snapshots.get_or_build(entry)
        except ExportConfigError as exc:
            return None, str(exc)
        if snapshot.overflow == "已拒绝":
            return None, (
                f"结果 {snapshot.total} 行超过导出上限 {snapshot.limit} 行，"
                f"模板超限策略为「拒绝」，未生成文件（快照键 {snapshot.key}）"
            )
        return (snapshot, hit), ""

    def download_by_key(self, key: str) -> tuple[ExportSnapshot | None, str, int]:
        """按快照键下载：旧代际、已停用、已变更配置产生的旧快照一律拒绝。"""
        snapshot = snapshots.find(key)
        if snapshot is None:
            return None, f"快照 {key} 不存在或已被清理", 404
        if snapshot.status != "有效":
            return None, f"快照 {key} 已失效：{snapshot.invalid_reason}", 410
        if snapshot.overflow == "已拒绝":
            return None, (
                f"快照 {key} 对应的结果 {snapshot.total} 行超过导出上限 {snapshot.limit} 行，"
                "按模板策略「拒绝」未生成文件"
            ), 422
        snapshot.hits += 1
        return snapshot, "", 200

    def list_snapshots(self) -> list[dict[str, Any]]:
        return snapshots.list_meta()

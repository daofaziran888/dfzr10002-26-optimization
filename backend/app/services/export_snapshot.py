"""导出快照仓库：按模板字段选择生成稳定快照键，缓存导出文件并负责失效联动。

设计要点：
- 快照键由「模板编号 + 数据源绑定 + 字段选择（含先后关系）+ 导出格式 + 筛选参数 +
  导出上限 + 超限策略 + 数据代际」共同决定，任一项变化都会得到新键；
- 快照内容只构建一次，重复下载复用同一份字节，避免每次都在内存里重拼完整结果集；
- 数据集刷新（代际推进）、模板停用或模板配置变更后，旧快照立即标记失效，
  按旧键下载会被拒绝（410），排查时可用快照键把「模板配置 → 缓存状态 → 下载文件」串起来；
- 达到导出上限时的处理方式由模板「超限策略」决定：截断（只留前 N 行）或拒绝（不生成文件），
  策略本身也进入快照键，界面展示与文件内容始终一致。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.store import store

DEFAULT_LIMIT = 500
MAX_LIMIT = 10000
FORMATS = ("CSV", "JSON")
POLICIES = ("截断", "拒绝")
PREVIEW_SAMPLE_ROWS = 5

CN_TZ = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


class ExportConfigError(ValueError):
    """模板配置无法用于导出时抛出，消息直接展示给用户。"""


def parse_fields(raw: Any) -> list[str]:
    """字段选择是有序列表：逗号分隔，保留先后关系，顺序会进入快照键。"""
    text = str(raw or "").replace("，", ",")
    fields = [part.strip() for part in text.split(",") if part.strip()]
    if not fields:
        raise ExportConfigError("字段选择不能为空，至少选择一列")
    return fields


def parse_filters(raw: Any) -> list[tuple[str, str]]:
    """筛选参数形如 `status=已启用&是否启用=是`，多条件取交集。"""
    text = str(raw or "").strip()
    if not text:
        return []
    pairs: list[tuple[str, str]] = []
    for part in text.replace("；", "&").replace(";", "&").split("&"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ExportConfigError(f"筛选参数「{part}」缺少 =，应为 字段=值 的形式")
        field, value = part.split("=", 1)
        field = field.strip()
        if not field:
            raise ExportConfigError(f"筛选参数「{part}」缺少字段名")
        pairs.append((field, value.strip()))
    return pairs


def canonical_filters(pairs: list[tuple[str, str]]) -> str:
    """筛选条件与书写顺序无关，排序后进入快照键，避免同一口径生成多份快照。"""
    return "&".join(sorted(f"{field}={value}" for field, value in pairs))


def parse_limit(raw: Any) -> int:
    try:
        limit = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def parse_format(raw: Any) -> str:
    fmt = str(raw or "").strip().upper()
    if fmt not in FORMATS:
        raise ExportConfigError(f"导出格式「{raw}」不支持，只支持 CSV 或 JSON")
    return fmt


def parse_policy(raw: Any) -> str:
    policy = str(raw or "").strip() or "截断"
    if policy not in POLICIES:
        raise ExportConfigError(f"超限策略「{raw}」不支持，只支持 截断 或 拒绝")
    return policy


def render_csv(fields: list[str], rows: list[dict[str, Any]]) -> bytes:
    """CSV 文件：UTF-8 带 BOM，表头就是所选字段；空字段写空值，不会串到下一列。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(fields)
    for row in rows:
        writer.writerow(["" if row.get(field) is None else str(row.get(field)) for field in fields])
    return buffer.getvalue().encode("utf-8-sig")


def render_json(fields: list[str], rows: list[dict[str, Any]]) -> bytes:
    """JSON 文件：保留字段名到值的映射，键顺序与字段选择一致；空字段为 null。"""
    payload = [{field: row.get(field) for field in fields} for row in rows]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class ExportSnapshot:
    """一份导出快照：内容只构建一次，失效后保留元信息用于排查。"""

    def __init__(
        self,
        *,
        key: str,
        template_code: str,
        module: str,
        fields: list[str],
        fmt: str,
        filters: list[tuple[str, str]],
        limit: int,
        policy: str,
        generation: int,
        total: int,
        end: int,
        overflow: str,
        content: bytes | None,
        media_type: str,
        filename: str,
    ) -> None:
        self.key = key
        self.template_code = template_code
        self.module = module
        self.fields = fields
        self.format = fmt
        self.filters = filters
        self.limit = limit
        self.policy = policy
        self.generation = generation
        self.total = total
        self.end = end
        self.overflow = overflow  # 完整 / 已截断 / 已拒绝
        self.content = content
        self.media_type = media_type
        self.filename = filename
        self.status = "有效"
        self.invalid_reason = ""
        self.created_at = _now()
        self.hits = 0

    @property
    def row_range(self) -> dict[str, int]:
        """结果区间：1 起始、闭区间；空结果记为 0-0，边界口径固定。"""
        if self.total == 0 or self.end == 0:
            return {"start": 0, "end": 0}
        return {"start": 1, "end": self.end}

    def invalidate(self, reason: str) -> None:
        if self.status != "有效":
            return
        self.status = "已失效"
        self.invalid_reason = reason
        self.content = None

    def meta(self) -> dict[str, Any]:
        return {
            "快照键": self.key,
            "模板编号": self.template_code,
            "数据源绑定": self.module,
            "数据代际": self.generation,
            "字段选择": list(self.fields),
            "导出格式": self.format,
            "筛选参数": canonical_filters(self.filters),
            "导出上限": self.limit,
            "超限策略": self.policy,
            "结果总数": self.total,
            "结果范围": self.row_range,
            "导出状态": self.overflow,
            "缓存状态": self.status,
            "失效原因": self.invalid_reason,
            "创建时间": self.created_at,
            "命中次数": self.hits,
        }


class SnapshotRegistry:
    """导出快照的注册表：构建、命中、失效与对外的缓存规则投影。"""

    def __init__(self) -> None:
        self._snapshots: dict[str, ExportSnapshot] = {}
        self._lock = threading.Lock()

    # ---------- 配置与快照键 ----------

    def read_config(self, template: dict[str, Any]) -> dict[str, Any]:
        """把模板当前配置规范化成一份不可变快照配置（在请求开始时一次性读取）。"""
        module = str(template.get("数据源绑定") or "").strip()
        if module not in store.module_names():
            raise ExportConfigError(
                f"数据源绑定「{module or '空'}」不是已知模块，可选：{'、'.join(store.module_names())}"
            )
        return {
            "模板编号": str(template.get("模板编号") or f"ID-{template.get('id')}"),
            "数据源绑定": module,
            "字段选择": parse_fields(template.get("字段选择")),
            "导出格式": parse_format(template.get("导出格式")),
            "筛选条件": parse_filters(template.get("筛选参数")),
            "导出上限": parse_limit(template.get("导出上限")),
            "超限策略": parse_policy(template.get("超限策略")),
            "数据代际": store.generation(module),
        }

    def snapshot_key(self, template: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """按字段选择等配置生成稳定快照键：同样的配置永远得到同一个键。"""
        config = self.read_config(template)
        raw = "|".join(
            [
                config["模板编号"],
                config["数据源绑定"],
                ",".join(config["字段选择"]),  # 字段先后关系进入快照键
                config["导出格式"],
                canonical_filters(config["筛选条件"]),
                str(config["导出上限"]),
                config["超限策略"],
                f"g{config['数据代际']}",
            ]
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        key = f"snap-{config['模板编号']}-g{config['数据代际']}-{digest}"
        return key, config

    # ---------- 结果集 ----------

    def _filtered_rows(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """按模板筛选参数过滤绑定模块的数据；其他筛选条件下的数据不会混入。"""
        rows = list(store.rows(config["数据源绑定"]))
        filters = config["筛选条件"]
        if not filters:
            return rows
        return [
            row
            for row in rows
            if all(str(row.get(field, "")) == value for field, value in filters)
        ]

    def plan(self, config: dict[str, Any]) -> dict[str, Any]:
        """计算结果范围与截断/拒绝状态，不写快照，供预览与构建共用。"""
        rows = self._filtered_rows(config)
        total = len(rows)
        limit = config["导出上限"]
        overflow = "完整"
        end = total
        if total > limit:
            if config["超限策略"] == "拒绝":
                overflow, end = "已拒绝", 0
            else:
                overflow, end = "已截断", limit
        selected = rows[:end] if overflow != "已拒绝" else []
        return {"total": total, "end": end, "overflow": overflow, "rows": selected}

    # ---------- 构建与命中 ----------

    def _build(self, key: str, config: dict[str, Any]) -> ExportSnapshot:
        planned = self.plan(config)
        fields = config["字段选择"]
        fmt = config["导出格式"]
        overflow = planned["overflow"]
        if overflow == "已拒绝":
            content, media_type = None, ""
        elif fmt == "JSON":
            content, media_type = render_json(fields, planned["rows"]), "application/json; charset=utf-8"
        else:
            content, media_type = render_csv(fields, planned["rows"]), "text/csv; charset=utf-8"
        extension = "json" if fmt == "JSON" else "csv"
        filename = f"{config['模板编号']}_{key.rsplit('-', 1)[-1]}.{extension}"
        return ExportSnapshot(
            key=key,
            template_code=config["模板编号"],
            module=config["数据源绑定"],
            fields=fields,
            fmt=fmt,
            filters=config["筛选条件"],
            limit=config["导出上限"],
            policy=config["超限策略"],
            generation=config["数据代际"],
            total=planned["total"],
            end=planned["end"],
            overflow=overflow,
            content=content,
            media_type=media_type,
            filename=filename,
        )

    def get_or_build(self, template: dict[str, Any]) -> tuple[ExportSnapshot, bool]:
        """命中就返回同一份结果；未命中按请求开始时的配置构建并登记。

        配置在进入本方法前已一次性读出，下载过程中模板再被修改，
        本次文件仍按开始时的快照键完成，下一次下载才采用新配置。
        """
        key, config = self.snapshot_key(template)
        with self._lock:
            existing = self._snapshots.get(key)
            if existing is not None and existing.status == "有效":
                existing.hits += 1
                return existing, True
            snapshot = self._build(key, config)
            self._snapshots[key] = snapshot
            return snapshot, False

    def find(self, key: str) -> ExportSnapshot | None:
        """按快照键查找；代际已推进的有效快照在这里兜底失效，保证旧代文件下不到。"""
        snapshot = self._snapshots.get(key)
        if snapshot is not None and snapshot.status == "有效":
            current = store.generation(snapshot.module)
            if snapshot.generation != current:
                snapshot.invalidate(f"数据已刷新到第 {current} 代")
        return snapshot

    # ---------- 失效联动 ----------

    def invalidate_template(self, template_code: str, reason: str) -> int:
        """模板停用或配置变更后，该模板的旧快照全部失效。"""
        count = 0
        with self._lock:
            for snapshot in self._snapshots.values():
                if snapshot.template_code == template_code and snapshot.status == "有效":
                    snapshot.invalidate(reason)
                    count += 1
        return count

    def invalidate_module(self, module: str, reason: str) -> int:
        """绑定模块的数据刷新后，依赖它的旧快照全部失效。"""
        count = 0
        with self._lock:
            for snapshot in self._snapshots.values():
                if snapshot.module == module and snapshot.status == "有效":
                    snapshot.invalidate(reason)
                    count += 1
        return count

    def data_refreshed(self, module: str, reason: str | None = None) -> int:
        """模块数据推进一代，并让依赖该模块的快照同步失效，返回新代际号。"""
        generation = store.bump(module)
        self.invalidate_module(module, reason or f"数据已刷新到第 {generation} 代")
        return generation

    # ---------- 对外投影 ----------

    def list_meta(self) -> list[dict[str, Any]]:
        with self._lock:
            snapshots = sorted(self._snapshots.values(), key=lambda item: item.created_at)
        return [snapshot.meta() for snapshot in snapshots]

    def rules(self) -> list[dict[str, Any]]:
        """给查询缓存管理模块的缓存规则清单：由快照注册表实时投影，失效后同步更新。"""
        rules: list[dict[str, Any]] = []
        for seq, meta in enumerate(self.list_meta(), start=1):
            valid = meta["缓存状态"] == "有效"
            rules.append(
                {
                    "规则编号": f"SNAP-{seq:04d}",
                    "缓存目标": f"导出快照·{meta['模板编号']}",
                    "缓存时间": meta["创建时间"],
                    "失效条件": "数据刷新 / 模板停用 / 配置变更",
                    "预加载设置": "否",
                    "内存上限": "-",
                    "淘汰策略": "代际推进即失效",
                    "是否启用": "是" if valid else "否",
                    "status": "已启用" if valid else "已失效",
                    "快照键": meta["快照键"],
                    "数据代际": meta["数据代际"],
                    "失效原因": meta["失效原因"],
                    "命中次数": meta["命中次数"],
                }
            )
        return rules

    def overview(self) -> list[dict[str, Any]]:
        """给运营概览与数据看板的导出快照摘要。"""
        return [
            {
                "模板编号": meta["模板编号"],
                "快照键": meta["快照键"],
                "字段选择": meta["字段选择"],
                "导出格式": meta["导出格式"],
                "结果范围": meta["结果范围"],
                "导出上限": meta["导出上限"],
                "导出状态": meta["导出状态"],
                "缓存状态": meta["缓存状态"],
            }
            for meta in self.list_meta()
        ]


snapshots = SnapshotRegistry()

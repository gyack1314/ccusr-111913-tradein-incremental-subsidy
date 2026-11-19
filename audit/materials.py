"""只读资料加载与时间点（point-in-time）过滤。

原始记录不可改写；本模块只做读取、摘要校验和按 known_at/reported_at 的可见性过滤。
所有分析入口都必须显式声明 as_of 时点，业务发生时间不等于当时已经获知。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import datetime
from typing import Any

ROOT_DEFAULT = pathlib.Path(__file__).resolve().parents[1]


def parse_ts(value: str) -> datetime:
    """解析带偏移的 ISO 时间戳。"""
    return datetime.fromisoformat(value)


def parse_day(value: str):
    """解析 YYYY-MM-DD 业务日，返回 date。"""
    return datetime.fromisoformat(value).date()


def load_raw(root: str | pathlib.Path | None = None, verify_digest: bool = True) -> tuple[dict, dict[str, list[dict]]]:
    """按 manifest 读取全部原始资料，可选校验 SHA-256、行数、字段与身份。

    返回 (manifest, tables)；不修改任何文件。
    """
    root = pathlib.Path(root or ROOT_DEFAULT)
    manifest = json.loads((root / "data" / "manifest.json").read_text(encoding="utf-8"))
    tables: dict[str, list[dict]] = {}
    for table in manifest["tables"]:
        path = root / table["path"]
        blob = path.read_bytes()
        if verify_digest and hashlib.sha256(blob).hexdigest() != table["sha256"]:
            raise ValueError(f"资料文件摘要不匹配: {table['name']}")
        rows = [json.loads(line) for line in blob.decode("utf-8").splitlines() if line.strip()]
        if verify_digest:
            if len(rows) != table["rows"]:
                raise ValueError(f"记录数不匹配: {table['name']}")
            if len({row[table["key"]] for row in rows}) != len(rows):
                raise ValueError(f"资料身份重复: {table['name']}")
            expected = set(table["fields"])
            if any(set(row) != expected for row in rows):
                raise ValueError(f"字段不匹配: {table['name']}")
        tables[table["name"]] = rows
    if verify_digest:
        ids = {name: {row["id"] for row in rows} for name, rows in tables.items()}
        for table in manifest["tables"]:
            for column, target in table.get("refs", {}).items():
                for row in tables[table["name"]]:
                    if row[column] is not None and row[column] not in ids[target]:
                        raise ValueError(f"资料引用不存在: {table['name']}.{column}={row[column]}")
    return manifest, tables


class Materials:
    """某一观察时点可见的资料快照。

    known_at/reported_at 晚于 as_of 的记录对该时点不可见，
    用于复现“当时证据”下的旧结项，而不是用后到资料改写历史判断。
    """

    def __init__(self, tables: dict[str, list[dict]], as_of: datetime):
        self.as_of = as_of
        self.submissions = [r for r in tables["submissions"] if parse_ts(r["submitted_at"]) <= as_of]
        self.transactions = {
            r["id"]: r for r in tables["transactions"] if parse_ts(r["known_at"]) <= as_of
        }
        self.rollout = [r for r in tables["rollout"] if parse_ts(r["known_at"]) <= as_of]
        self.channel_totals = [r for r in tables["channel_totals"] if parse_ts(r["known_at"]) <= as_of]
        self.rules = [r for r in tables["rules"]]
        self.identity_reviews = [
            r for r in tables["identity_reviews"] if parse_ts(r["known_at"]) <= as_of
        ]

    @classmethod
    def snapshot(cls, root: str | pathlib.Path | None = None, as_of: str | datetime | None = None):
        _, tables = load_raw(root)
        if as_of is None:
            as_of = max(parse_ts(r["known_at"]) for r in tables["channel_totals"])
        elif isinstance(as_of, str):
            as_of = parse_ts(as_of)
        return cls(tables, as_of)

    def rule_for(self, period: str) -> dict[str, Any]:
        matches = [r for r in self.rules if r["period"] == period]
        if len(matches) != 1:
            raise ValueError(f"期间 {period} 的制度记录不唯一: {len(matches)}")
        return matches[0]

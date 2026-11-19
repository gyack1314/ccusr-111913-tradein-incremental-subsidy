"""独立回执服务核对客户端（标准库实现）。

服务只保存投递事实，不做资格判断；本客户端在补交处理回执前核对回执身份与摘要，
避免把未经独立渠道确认的内容写进办案事件。支持 X-Reply-Mode 断连演练：
断连后主动 GET 复核，不凭断连判定未接收。
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any


def _compact_digest(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def post_receipt(base_url: str, receipt_id: str, payload: dict,
                 timeout: float = 5.0, drop_once: bool = False) -> dict[str, Any]:
    """向回执服务投递；返回服务响应（含 digest）。断连时转 GET 复核。"""
    url = base_url.rstrip("/") + "/receipts"
    body = json.dumps({"receipt_id": receipt_id, "payload": payload},
                      ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if drop_once:
        headers["X-Reply-Mode"] = "drop_once"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        # 断连不代表未接收：转 GET 核对已保存事实
        return get_receipt(base_url, receipt_id, timeout=timeout)


def get_receipt(base_url: str, receipt_id: str, timeout: float = 5.0) -> dict[str, Any]:
    from urllib.parse import quote
    url = base_url.rstrip("/") + "/receipts/" + quote(receipt_id, safe="")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_remote_receipt(base_url: str, receipt_id: str, payload: dict,
                          timeout: float = 5.0) -> dict[str, Any]:
    """核对独立服务已接收且摘要一致；不一致或缺失则抛错，阻止写入办案事件。"""
    try:
        record = get_receipt(base_url, receipt_id, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError(f"独立回执服务无该投递: {receipt_id}") from exc
        raise
    if not record.get("accepted"):
        raise LookupError(f"回执未被服务接收: {receipt_id}")
    expected = _compact_digest(payload)
    if record.get("digest") != expected:
        raise ValueError(
            f"回执摘要不一致: 本地 {expected} 服务 {record.get('digest')}")
    return record

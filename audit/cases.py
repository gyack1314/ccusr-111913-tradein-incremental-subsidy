"""办案流程与结项调整的本地状态存储。

事件溯源（event sourcing）：所有操作只追加不可变事件，当前状态由事件折叠得到。
- 补交处理回执：记录回执身份与摘要，不直接改原始 transactions。
- 同物裁决：对同物组作出归属裁定，保留所有原申报来历。
- 接受整体补助方案：接受时若资料/预算已变动，必须指出受影响依据。
- 已确认发放只记录本地回执；更正产生待补足/待追查调整，不能删除已发金额。
- 旧结项按当时 as_of 快照的证据仍可解释。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass
from datetime import datetime

from .materials import parse_ts

EVENT_TYPES = {
    "receipt_supplement",   # 补交处理回执
    "identity_ruling",      # 同物裁决
    "plan_accepted",        # 接受整体补助方案
    "disbursement",         # 已确认发放（本地回执）
    "correction",           # 更正：待补足 / 待追查
    "note",                 # 人工备注
}


class CaseStore:
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def _digest(self, payload: dict) -> str:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def append(self, event_type: str, payload: dict, as_of: str | datetime,
               basis_fingerprint: str | None = None) -> dict:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"未知事件类型: {event_type}")
        if isinstance(as_of, str):
            as_of = parse_ts(as_of)
        events = self.read_events()
        seq = len(events) + 1
        event = {
            "seq": seq, "type": event_type,
            "at": as_of.isoformat(),
            "payload": payload,
            "payload_digest": self._digest(payload),
            "basis_fingerprint": basis_fingerprint,  # 事件所依据资料快照的指纹
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def read_events(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    # ---- 业务操作 ----
    def supplement_receipt(self, submission_id: str, fact_ref: str, receipt: dict,
                           as_of, basis_fingerprint: str) -> dict:
        """补交处理回执；receipt 应含稳定 receipt_id（可与独立回执服务核对）。"""
        if "receipt_id" not in receipt:
            raise ValueError("回执必须含 receipt_id 稳定身份")
        # 同申报同事实重复补交：身份一致则幂等，不一致则拒绝
        for e in self.read_events():
            if e["type"] == "receipt_supplement" and \
                    e["payload"].get("submission_id") == submission_id and \
                    e["payload"].get("fact_ref") == fact_ref:
                if e["payload"]["receipt"]["receipt_id"] != receipt["receipt_id"]:
                    raise ValueError("同一事实已有不同回执身份，拒绝覆盖；请走更正流程")
                return e
        return self.append("receipt_supplement", {
            "submission_id": submission_id, "fact_ref": fact_ref,
            "receipt": receipt, "status": "supplemented",
        }, as_of, basis_fingerprint)

    def rule_identity(self, group_id: str, winner_submission_id: str | None,
                      rationale: str, as_of, basis_fingerprint: str,
                      review_refs: list[str]) -> dict:
        """同物裁决。winner 为 None 表示裁决组内无合格申报（全部暂缓/否决）。"""
        known = {e["payload"].get("group_id"): e for e in self.read_events()
                 if e["type"] == "identity_ruling"}
        if group_id in known:
            prior = known[group_id]
            if prior["payload"].get("winner_submission_id") == winner_submission_id:
                return prior
            raise ValueError("同物组已有不同裁决；新裁决须以 correction 事件留痕，不得覆盖")
        return self.append("identity_ruling", {
            "group_id": group_id, "winner_submission_id": winner_submission_id,
            "rationale": rationale, "review_refs": review_refs,
        }, as_of, basis_fingerprint)

    def accept_plan(self, plan: dict, evidence_basis: dict, as_of,
                    current_fingerprint: str) -> dict:
        """接受整体补助方案。

        evidence_basis 记录方案生成时的资料指纹与关键依据；
        current_fingerprint 是当前资料指纹；不一致时在事件中显式标记受影响依据。
        """
        affected = []
        if evidence_basis.get("fingerprint") != current_fingerprint:
            affected.append("资料快照指纹已变化，方案依据与当前资料不一致")
        payload = {
            "plan": plan, "evidence_basis": evidence_basis,
            "current_fingerprint": current_fingerprint,
            "basis_changed": bool(affected), "affected_basis": affected,
            "status": "accepted" if not affected else "accepted_with_basis_warning",
        }
        return self.append("plan_accepted", payload, as_of, current_fingerprint)

    def record_disbursement(self, merchant: str, amount_cents: int, receipt_id: str,
                            plan_event_seq: int, as_of, basis_fingerprint: str) -> dict:
        """登记已确认发放。金额只能为正；已发金额不可删除、不可改写。"""
        if amount_cents <= 0:
            raise ValueError("发放金额必须为正；冲正请走 correction")
        for e in self.read_events():
            if e["type"] == "disbursement" and e["payload"].get("receipt_id") == receipt_id:
                return e  # 幂等
        return self.append("disbursement", {
            "merchant": merchant, "amount_cents": amount_cents,
            "receipt_id": receipt_id, "plan_event_seq": plan_event_seq,
            "direction": "paid",
        }, as_of, basis_fingerprint)

    def correct(self, merchant: str, amount_cents: int, kind: str, reason: str,
                as_of, basis_fingerprint: str) -> dict:
        """更正调整。kind: payable_more（待补足）/ recoverable（待追查）。

        不触碰既有 disbursement 事件，只追加带方向的调整。
        """
        if kind not in {"payable_more", "recoverable"}:
            raise ValueError("更正类型须为 payable_more 或 recoverable")
        if amount_cents <= 0:
            raise ValueError("更正金额必须为正")
        return self.append("correction", {
            "merchant": merchant, "amount_cents": amount_cents,
            "kind": kind, "reason": reason,
            "direction": "credit" if kind == "payable_more" else "debit",
        }, as_of, basis_fingerprint)


@dataclass
class LedgerEntry:
    merchant: str
    paid_cents: int
    payable_more_cents: int
    recoverable_cents: int

    @property
    def net_position_cents(self) -> int:
        return self.paid_cents + self.payable_more_cents - self.recoverable_cents


def fold_ledger(store: CaseStore) -> dict[str, LedgerEntry]:
    """从事件折叠商户台账；已发金额始终保留，调整分列。"""
    ledger: dict[str, LedgerEntry] = {}

    def get(m: str) -> LedgerEntry:
        if m not in ledger:
            ledger[m] = LedgerEntry(m, 0, 0, 0)
        return ledger[m]

    for e in store.read_events():
        p = e["payload"]
        if e["type"] == "disbursement":
            get(p["merchant"]).paid_cents += p["amount_cents"]
        elif e["type"] == "correction":
            entry = get(p["merchant"])
            if p["kind"] == "payable_more":
                entry.payable_more_cents += p["amount_cents"]
            else:
                entry.recoverable_cents += p["amount_cents"]
    return ledger


def snapshot_fingerprint(mat) -> str:
    """当前资料快照指纹：对每张表可见记录的完整内容（按键排序）哈希。

    只哈希身份与时间戳无法发现“同身份内容被更正”，因此纳入整行字段，
    使任何字段值（含预算、单价、状态）变化都会改变指纹。
    """
    parts = []
    for name in ("submissions", "transactions", "rollout", "channel_totals",
                 "rules", "identity_reviews"):
        obj = getattr(mat, name)
        if isinstance(obj, dict):
            rows = sorted(obj.values(), key=lambda r: r["id"])
        else:
            rows = sorted(obj, key=lambda r: r["id"])
        lines = [
            name + ":" + json.dumps(r, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"))
            for r in rows
        ]
        parts.append("\n".join(lines))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

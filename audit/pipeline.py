"""端到端流水线：原始申报 -> 资格 -> 效果 -> 补助可行域 -> 方案审阅 -> 追溯解释。"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import cases as cases_mod
from . import effects as effects_mod
from . import eligibility as elig
from . import funding as fund
from .materials import Materials


def _default(obj: Any):
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"不可序列化: {type(obj)}")


def to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False, default=_default))


def load_rulings(store: "cases_mod.CaseStore | None") -> dict[str, str | None]:
    """从事件存储折叠同物裁决：group_id -> winner_submission_id（None 表示全员不合格）。"""
    if store is None:
        return {}
    rulings = {}
    for e in store.read_events():
        if e["type"] == "identity_ruling":
            rulings[e["payload"]["group_id"]] = e["payload"]["winner_submission_id"]
    return rulings


def run_pipeline(mat: Materials, store: "cases_mod.CaseStore | None" = None) -> dict[str, Any]:
    """对给定 as_of 快照执行完整分析，返回结构化证据包。"""
    audits = elig.audit_submissions(mat)
    groups = elig.build_device_groups(mat, audits)
    verified = elig.verified_units(mat, audits, groups)
    effect = effects_mod.evaluate(mat)
    period = mat.submissions[0]["period"] if mat.submissions else "2026-09"
    rule = mat.rule_for(period)
    rulings = load_rulings(store)
    env = fund.envelope(rule, verified, effect)
    groups_json = to_jsonable(groups)
    max_plan = fund.max_feasible_allocation(
        env, verified, list(groups_json.values()), rulings)
    return {
        "as_of": mat.as_of.isoformat(),
        "rule": rule,
        "submission_audits": to_jsonable(audits),
        "device_groups": groups_json,
        "identity_rulings": rulings,
        "verified": to_jsonable(verified),
        "effects": to_jsonable(effect),
        "funding_envelope": to_jsonable(env),
        "max_feasible_plan": to_jsonable(max_plan),
        "fingerprint": cases_mod.snapshot_fingerprint(mat),
    }


def explain_submission(mat: Materials, submission_id: str, result: dict | None = None) -> dict:
    """追查单份申报：为何可发、暂缓或否决，每一步给出证据身份。"""
    if result is None:
        result = run_pipeline(mat)
    audits = result["submission_audits"]
    if submission_id not in audits:
        raise KeyError(f"当前快照不可见申报: {submission_id}")
    a = audits[submission_id]
    group = result["device_groups"].get(a["device_group_id"])
    chain = []
    for key in elig.FACT_KINDS:
        f = a["facts"][key]
        chain.append({
            "环节": elig.FACT_LABEL[key], "事实身份": f["ref"],
            "资料可见": f["visible"], "状态": f["status"],
            "发生日": f["occurred_day"], "获知时间": f["known_at"],
            "部分退款分": f["refund_cents"],
        })
    has_dispute_fail = any(
        c["code"] == elig.IDENTITY_DISPUTE and not c["passed"] for c in a["checks"])
    if a["eligible"] and not has_dispute_fail and not a["hold_reasons"]:
        decision = "可核定"
    elif a["hold_reasons"] or any(
            c["code"] in (elig.DUPLICATE_SERIAL, elig.IDENTITY_DISPUTE)
            and not c["passed"] for c in a["checks"]):
        decision = "暂缓"
    else:
        decision = "否决"
    return {
        "申报": submission_id, "商户": a["merchant"], "商户片区": a["region"],
        "居民来源片区": a["resident_region"], "品类": a["category"], "期间": a["period"],
        "事实链": chain,
        "检查": a["checks"],
        "同物组": group,
        "结论": decision,
        "可追查金额": (
            f"若可发，单台上限 {result['rule']['per_verified_unit_cents']} 分；"
            f"整体还受联合上界 {result['funding_envelope']['payable_upper_units']} 台与预算 "
            f"{result['funding_envelope']['budget_cents']} 分约束"
        ) if decision == "可核定" else "当前时点不产生确定可发金额，见检查项与暂缓原因",
    }


def explain_amount(store: cases_mod.CaseStore, merchant: str) -> dict:
    """追查某商户当前金额构成：已发、待补足、待追查，逐笔对应事件序号。"""
    events = [e for e in store.read_events()
              if e["type"] in ("disbursement", "correction", "plan_accepted")
              and (e["type"] == "plan_accepted" or e["payload"].get("merchant") == merchant)]
    ledger = cases_mod.fold_ledger(store)
    return {
        "商户": merchant,
        "台账": to_jsonable(ledger.get(merchant, cases_mod.LedgerEntry(merchant, 0, 0, 0))),
        "逐笔事件": [
            {"seq": e["seq"], "类型": e["type"], "时点": e["at"],
             "内容": e["payload"], "依据指纹": e["basis_fingerprint"]}
            for e in events
        ],
        "说明": "已发金额不可删除；更正只追加待补足/待追查，旧结项可按事件中的 as_of 与指纹复现",
    }


def write_report(result: dict, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pipeline_{result['as_of'][:10]}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

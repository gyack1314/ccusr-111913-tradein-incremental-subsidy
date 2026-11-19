"""命令行入口：分析、追溯、办案、结项调整（经 run_audit.py 在隔离模式运行）。

示例：
  python3 -I run_audit.py analyze --as-of 2026-11-20T00:00:00+08:00
  python3 -I run_audit.py explain-submission A013
  python3 -I run_audit.py rule-identity G006 A006 --rationale "裁决归 A006"
  python3 -I run_audit.py accept-plan plan.json
  python3 -I run_audit.py disburse M1 12000 RCPT-1 --plan-seq 3
  python3 -I run_audit.py correct M1 6000 recoverable "同物裁决改判"
  python3 -I run_audit.py trace M1
"""
from __future__ import annotations

import argparse
import json
import pathlib

from . import cases as cases_mod
from . import funding as fund
from .materials import Materials
from .pipeline import explain_amount, explain_submission, run_pipeline, to_jsonable, write_report

DEFAULT_STATE = ".local/case_events.jsonl"


def _load(args) -> tuple[Materials, dict, cases_mod.CaseStore]:
    mat = Materials.snapshot(as_of=args.as_of) if args.as_of else Materials.snapshot()
    store = cases_mod.CaseStore(args.state)
    result = run_pipeline(mat, store)
    return mat, result, store


def cmd_analyze(args):
    mat, result, store = _load(args)
    path = write_report(result, args.out)
    v, env = result["verified"], result["funding_envelope"]
    eff = result["effects"]["point_estimate"]
    summary = {
        "as_of": result["as_of"],
        "申报数": v["submissions"], "同物组数": v["device_groups"],
        "无争议合格台数": v["clean_verified_units"],
        "争议组数(组上限1台)": v["contested_groups"],
        "未成熟暂缓组数": v["immature_groups_pending"],
        "否决申报数": v["rejected_submissions"],
        "效果点估计_人均": round(eff["coef"], 5),
        "效果换算台数": round(eff["implied_units"], 1),
        "效果界(台)": result["effects"]["bounds"]["bounds"],
        "联合可补助区间_台": [env["payable_lower_units"], env["payable_upper_units"]],
        "联合可补助区间_分": [env["payable_lower_cents"], env["payable_upper_cents"]],
        "夹紧约束": env["binding_constraints"],
        "暂缓原因": env["suspension_reasons"],
        "报告文件": str(path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_explain(args):
    mat, result, store = _load(args)
    print(json.dumps(explain_submission(mat, args.submission_id, result),
                     ensure_ascii=False, indent=2))


def cmd_groups(args):
    _, result, store = _load(args)
    groups = result["device_groups"].values()
    for g in groups:
        if g["contested"] or g["open_review_ids"]:
            print(json.dumps(g, ensure_ascii=False))


def cmd_supplement(args):
    mat, _, store = _load(args)
    receipt = json.loads(pathlib.Path(args.receipt_file).read_text(encoding="utf-8"))
    if args.verify_url:
        from .receipt_client import verify_remote_receipt
        # 先核对独立回执服务已接收且摘要一致，再写本地办案事件
        record = verify_remote_receipt(args.verify_url, receipt["receipt_id"],
                                       receipt.get("payload", receipt))
        receipt["remote_digest"] = record["digest"]
    ev = store.supplement_receipt(args.submission_id, args.fact_ref, receipt,
                                  mat.as_of, cases_mod.snapshot_fingerprint(mat))
    print(json.dumps(ev, ensure_ascii=False, indent=2))


def cmd_rule_identity(args):
    mat, result, store = _load(args)
    group = result["device_groups"].get(args.group_id)
    if group is None:
        raise SystemExit(f"未知同物组: {args.group_id}")
    if args.winner and args.winner not in group["submission_ids"]:
        raise SystemExit(f"裁决对象 {args.winner} 不属于组 {args.group_id}: {group['submission_ids']}")
    store = cases_mod.CaseStore(args.state)
    ev = store.rule_identity(args.group_id, args.winner, args.rationale,
                             mat.as_of, cases_mod.snapshot_fingerprint(mat),
                             group["confirmed_review_ids"] + group["open_review_ids"])
    print(json.dumps(ev, ensure_ascii=False, indent=2))


def cmd_accept_plan(args):
    mat, result, store = _load(args)
    plan_doc = json.loads(pathlib.Path(args.plan_file).read_text(encoding="utf-8"))
    env_fields = set(fund.FundingEnvelope.__dataclass_fields__)
    env = fund.FundingEnvelope(**{
        k: v for k, v in result["funding_envelope"].items() if k in env_fields})
    from .eligibility import MerchantVerified
    by_m = {m: MerchantVerified(**row) for m, row in result["verified"]["by_merchant"].items()}
    verified = dict(result["verified"]); verified["by_merchant"] = by_m
    check = fund.check_allocation(
        env, verified, list(result["device_groups"].values()),
        result["identity_rulings"], plan_doc["allocations"])
    if not check.feasible and not args.force:
        raise SystemExit("方案不可行:\n" + json.dumps(to_jsonable(check), ensure_ascii=False, indent=2))
    ev = store.accept_plan(
        plan_doc,
        {"fingerprint": result["fingerprint"], "as_of": result["as_of"],
         "feasibility_check": to_jsonable(check)},
        mat.as_of, cases_mod.snapshot_fingerprint(mat))
    print(json.dumps({"accepted_event": ev, "feasibility": to_jsonable(check)},
                     ensure_ascii=False, indent=2))


def cmd_disburse(args):
    mat, _, store = _load(args)
    store = cases_mod.CaseStore(args.state)
    ev = store.record_disbursement(args.merchant, args.amount_cents, args.receipt_id,
                                   args.plan_seq, mat.as_of,
                                   cases_mod.snapshot_fingerprint(mat))
    print(json.dumps(ev, ensure_ascii=False, indent=2))


def cmd_correct(args):
    mat, _, store = _load(args)
    store = cases_mod.CaseStore(args.state)
    ev = store.correct(args.merchant, args.amount_cents, args.kind, args.reason,
                       mat.as_of, cases_mod.snapshot_fingerprint(mat))
    print(json.dumps(ev, ensure_ascii=False, indent=2))


def cmd_trace(args):
    store = cases_mod.CaseStore(args.state)
    print(json.dumps(explain_amount(store, args.merchant), ensure_ascii=False, indent=2))


def cmd_ledger(args):
    store = cases_mod.CaseStore(args.state)
    print(json.dumps(to_jsonable(cases_mod.fold_ledger(store)), ensure_ascii=False, indent=2))


def build_parser():
    p = argparse.ArgumentParser(description="以旧换新净新增证据与补助核定")
    p.add_argument("--as-of", help="观察时点 ISO，默认取资料最晚 known_at")
    p.add_argument("--state", default=DEFAULT_STATE)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("analyze"); s.add_argument("--out", default="outputs")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("explain-submission")
    s.add_argument("submission_id"); s.set_defaults(func=cmd_explain)

    s = sub.add_parser("groups"); s.set_defaults(func=cmd_groups)

    s = sub.add_parser("supplement")
    s.add_argument("submission_id"); s.add_argument("fact_ref")
    s.add_argument("receipt_file")
    s.add_argument("--verify-url", help="独立回执服务地址，如 http://127.0.0.1:8096；提供则先核对投递事实")
    s.set_defaults(func=cmd_supplement)

    s = sub.add_parser("rule-identity")
    s.add_argument("group_id"); s.add_argument("winner", nargs="?", default=None)
    s.add_argument("--rationale", required=True); s.set_defaults(func=cmd_rule_identity)

    s = sub.add_parser("accept-plan")
    s.add_argument("plan_file"); s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_accept_plan)

    s = sub.add_parser("disburse")
    s.add_argument("merchant"); s.add_argument("amount_cents", type=int)
    s.add_argument("receipt_id"); s.add_argument("--plan-seq", type=int, required=True)
    s.set_defaults(func=cmd_disburse)

    s = sub.add_parser("correct")
    s.add_argument("merchant"); s.add_argument("amount_cents", type=int)
    s.add_argument("kind", choices=["payable_more", "recoverable"])
    s.add_argument("reason"); s.set_defaults(func=cmd_correct)

    s = sub.add_parser("trace"); s.add_argument("merchant"); s.set_defaults(func=cmd_trace)
    s = sub.add_parser("ledger"); s.set_defaults(func=cmd_ledger)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

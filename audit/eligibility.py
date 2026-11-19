"""申报资格审核与同物身份归并。

规则来源：docs/input-contract.md 的本地合成制度。
- 购新、交旧、规范处理三个事实均须 confirmed；pending/cancelled 不合格。
- 部分退款本身不取消资格；取消的购新事实取消对应资格。
- 先预约未交旧（old_handover 非 confirmed）不算完成回收。
- 同物仅按显式相同序列号或 status=confirmed 且 same_device=true 的审核归并；
  名称相似不归并，submitted 审核只登记争议、不自动合并。
- 同物组合并会暴露原先未发现的重叠（含后到审核），不保留“先导入先得”。
- grace_days 未到的 pending 为“未成熟”，可等回执；已过等待仍非 confirmed 的不合格。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .materials import Materials, parse_day, parse_ts

# 审核结论代码
FACT_MISSING = "FACT_MISSING"            # 引用事实在当前时点尚不可见
FACT_NOT_CONFIRMED = "FACT_NOT_CONFIRMED"  # pending 或 cancelled
CANCELLED = "CANCELLED"                  # 事实被取消
IMMATURE = "IMMATURE"                    # 处理未决且仍在 grace 等待窗内
CATEGORY_NOT_ELIGIBLE = "CATEGORY_NOT_ELIGIBLE"
REGION_NOT_ROLLED_OUT = "REGION_NOT_ROLLED_OUT"
PARTIAL_REFUND_NOTE = "PARTIAL_REFUND_NOTE"  # 仅提示，不否决
DUPLICATE_SERIAL = "DUPLICATE_SERIAL"    # 同一显式序列号被多份申报使用
IDENTITY_DISPUTE = "IDENTITY_DISPUTE"    # 同物组内多份申报竞争同一补助名额

FACT_KINDS = ("new_order", "old_receipt", "processing")
FACT_LABEL = {"new_order": "购新", "old_receipt": "交旧", "processing": "规范处理"}


@dataclass
class FactView:
    kind: str
    ref: str | None
    visible: bool
    status: str | None
    occurred_day: Any
    known_at: Any
    refund_cents: int | None
    units: int | None


@dataclass
class SubmissionAudit:
    submission_id: str
    merchant: str
    region: str
    resident_region: str
    category: str
    period: str
    facts: dict[str, FactView]
    checks: list[dict] = field(default_factory=list)
    eligible: bool = False          # 单台事实层面是否合格（不含同物竞争）
    hold_reasons: list[str] = field(default_factory=list)
    device_group_id: str | None = None

    def reason_codes(self) -> list[str]:
        return [c["code"] for c in self.checks if not c["passed"]]


@dataclass
class DeviceGroup:
    group_id: str
    serials: set[str]
    submission_ids: list[str]
    basis: list[str]                # 归并依据：显式序列号 / 审核身份
    confirmed_review_ids: list[str]
    open_review_ids: list[str]
    contested: bool
    # 组内事实层面合格的申报（可能多个竞争同一台名额）
    eligible_claim_ids: list[str]
    mature_claim_ids: list[str]     # 因未成熟被暂缓、未来可能合格的申报


def _fact_view(mat: Materials, ref: str | None) -> FactView:
    if ref is None or ref not in mat.transactions:
        return FactView("unknown", ref, False, None, None, None, None, None)
    t = mat.transactions[ref]
    return FactView(
        kind=t["kind"], ref=ref, visible=True, status=t["status"],
        occurred_day=parse_day(t["occurred_at"][:10]),
        known_at=parse_ts(t["known_at"]),
        refund_cents=t["refund_cents"], units=t["units"],
    )


def audit_submissions(mat: Materials) -> dict[str, SubmissionAudit]:
    """对当前快照内每份申报做事实与资格边界审核。"""
    rollout_by_region = {r["region"]: r for r in mat.rollout}
    audits: dict[str, SubmissionAudit] = {}
    # 显式重复序列号（同物关系的硬证据）
    serial_owners: dict[str, list[str]] = {}
    for s in mat.submissions:
        serial_owners.setdefault(s["old_serial"], []).append(s["id"])

    for s in mat.submissions:
        facts = {k: _fact_view(mat, s[k]) for k in FACT_KINDS}
        checks: list[dict] = []
        holds: list[str] = []
        rule = mat.rule_for(s["period"])

        for key in FACT_KINDS:
            f = facts[key]
            if not f.visible:
                checks.append({"code": FACT_MISSING, "passed": False,
                               "detail": f"{FACT_LABEL[key]}事实 {f.ref} 在当前时点不可见"})
                continue
            if f.status == "cancelled":
                checks.append({"code": CANCELLED, "passed": False,
                               "detail": f"{FACT_LABEL[key]}事实 {f.ref} 已取消"})
            elif f.status != "confirmed":
                # pending：区分未成熟与已过等待窗
                if key == "processing":
                    proc_day = f.occurred_day
                    mature_day = proc_day + timedelta(days=rule["grace_days"])
                    if mat.as_of.date() < mature_day:
                        checks.append({"code": IMMATURE, "passed": False,
                                       "detail": f"处理事实 {f.ref} 未决，{mature_day} 前属 grace 等待窗"})
                        holds.append(IMMATURE)
                    else:
                        checks.append({"code": FACT_NOT_CONFIRMED, "passed": False,
                                       "detail": f"处理事实 {f.ref} 过 {rule['grace_days']} 日等待仍未确认"})
                else:
                    checks.append({"code": FACT_NOT_CONFIRMED, "passed": False,
                                   "detail": f"{FACT_LABEL[key]}事实 {f.ref} 状态为 {f.status}，未完成回收"})
            if f.refund_cents:
                # 部分退款只登记；若事实同时被取消则上面的 CANCELLED 已否决
                checks.append({"code": PARTIAL_REFUND_NOTE, "passed": True,
                               "detail": f"{FACT_LABEL[key]}事实 {f.ref} 存在部分退款 {f.refund_cents} 分，不自动取消资格"})

        ro = rollout_by_region.get(s["region"])
        if ro is None:
            checks.append({"code": REGION_NOT_ROLLED_OUT, "passed": False,
                           "detail": f"商户片区 {s['region']} 无启用记录"})
        else:
            if s["category"] not in ro["eligible_categories"]:
                checks.append({"code": CATEGORY_NOT_ELIGIBLE, "passed": False,
                               "detail": f"品类 {s['category']} 不在 {s['region']} 资格品类 {ro['eligible_categories']}"})
            # 申报事实须发生在实际启用日之后（原定日不构成资格）
            first_day = min((f.occurred_day for f in facts.values() if f.visible), default=None)
            if first_day is not None and first_day < parse_day(ro["actual_start"]):
                checks.append({"code": REGION_NOT_ROLLED_OUT, "passed": False,
                               "detail": f"事实发生日 {first_day} 早于实际启用日 {ro['actual_start']}"})

        if len(serial_owners.get(s["old_serial"], [])) > 1:
            others = [x for x in serial_owners[s["old_serial"]] if x != s["id"]]
            checks.append({"code": DUPLICATE_SERIAL, "passed": False,
                           "detail": f"序列号 {s['old_serial']} 同时出现在 {others}，须同物裁决后只能补助一台"})

        hard_fail = {c["code"] for c in checks if not c["passed"]}
        # 同物重复（DUPLICATE_SERIAL）不否决申报本身：事实仍可能合格，
        # 竞争关系交给同物组按“一组最多一台”处理；其余硬失败才使申报不合格。
        # 未成熟（IMMATURE）同样不算合格，由 hold_reasons 单列、可在 grace 后转正。
        eligible = not (hard_fail - {DUPLICATE_SERIAL})
        audits[s["id"]] = SubmissionAudit(
            submission_id=s["id"], merchant=s["merchant"], region=s["region"],
            resident_region=s["resident_region"], category=s["category"],
            period=s["period"], facts=facts, checks=checks,
            eligible=eligible, hold_reasons=holds,
        )
    return audits


def build_device_groups(mat: Materials, audits: dict[str, SubmissionAudit]) -> dict[str, DeviceGroup]:
    """按显式序列号与已确认同物审核做并查集归并，暴露重叠而非先到先得。"""
    parent = {s["id"]: s["id"] for s in mat.submissions}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    basis: dict[tuple[str, str], list[str]] = {}
    confirmed_reviews: list[str] = []
    open_reviews: list[str] = []
    # 硬证据：相同显式序列号
    serial_owners: dict[str, list[str]] = {}
    for s in mat.submissions:
        serial_owners.setdefault(s["old_serial"], []).append(s["id"])
    for serial, ids in serial_owners.items():
        if len(ids) > 1:
            for other in ids[1:]:
                union(ids[0], other)
            basis.setdefault(("serial", serial), []).extend(ids)
    # 审核证据：仅 confirmed + same_device 生效；submitted 仅登记
    for review in mat.identity_reviews:
        a, b = review["left_application"], review["right_application"]
        if a not in parent or b not in parent:
            continue
        if review["status"] == "confirmed" and review["same_device"]:
            union(a, b)
            confirmed_reviews.append(review["id"])
            basis.setdefault(("review", review["id"]), []).extend([a, b])
        elif review["status"] == "confirmed" and not review["same_device"]:
            # 已确认非同物：不合并，但作为分歧解除证据保留
            basis.setdefault(("review_distinct", review["id"]), []).extend([a, b])
        else:
            open_reviews.append(review["id"])

    groups_raw: dict[str, list[str]] = {}
    for sid in parent:
        groups_raw.setdefault(find(sid), []).append(sid)

    groups: dict[str, DeviceGroup] = {}
    sub_by_id = {s["id"]: s for s in mat.submissions}
    for i, (_, members) in enumerate(sorted(groups_raw.items(), key=lambda kv: sorted(kv[1])[0])):
        members = sorted(members)
        gid = f"G{i+1:03d}"
        serials = {sub_by_id[m]["old_serial"] for m in members}
        group_basis = []
        conf_ids, open_ids = [], []
        for (kind, key), ids in basis.items():
            if any(x in members for x in ids):
                if kind == "serial":
                    group_basis.append(f"显式同序列号 {key}: {sorted(set(ids))}")
                elif kind == "review":
                    group_basis.append(f"已确认同物审核 {key}: {sorted(set(ids))}")
                    conf_ids.append(key)
                elif kind == "review_distinct":
                    group_basis.append(f"已确认非同物审核 {key}: {sorted(set(ids))}")
        for rid in open_reviews:
            review = next(r for r in mat.identity_reviews if r["id"] == rid)
            if review["left_application"] in members or review["right_application"] in members:
                open_ids.append(rid)
        eligible_claims = [m for m in members if audits[m].eligible]
        mature_claims = [m for m in members if IMMATURE in audits[m].hold_reasons]
        contested = len(members) > 1
        groups[gid] = DeviceGroup(
            group_id=gid, serials=serials, submission_ids=members,
            basis=group_basis, confirmed_review_ids=conf_ids,
            open_review_ids=open_ids, contested=contested,
            eligible_claim_ids=eligible_claims, mature_claim_ids=mature_claims,
        )
        for m in members:
            audits[m].device_group_id = gid
            if contested:
                audits[m].checks.append({
                    "code": IDENTITY_DISPUTE,
                    "passed": not (len(eligible_claims) > 1),
                    "detail": f"同物组 {gid} 含申报 {members}，一台旧机最多补助一份，待裁决",
                })
    return groups


@dataclass
class MerchantVerified:
    merchant: str
    # 无争议、事实合格且可立即核定的台数
    clean_units: int
    # 卷入同物竞争的合格申报台数（组层面只能产出一台，归属未定）
    contested_claim_units: int
    # 未成熟（grace 窗内）暂缓台数
    immature_units: int
    # 事实不合格台数（取消、过窗未确认、资格边界外等）
    rejected_units: int
    submission_ids: list[str]


def verified_units(mat: Materials, audits: dict[str, SubmissionAudit],
                   groups: dict[str, DeviceGroup]) -> dict[str, Any]:
    """汇总可核定台数区间。

    下界：无争议合格台数（每申报对应独立旧机）。
    上界：下界 + 每个有合格申报的同物组各 1 台 + 未成熟组未来最多补入的台数。
    同物组不按申报数量重复计数。
    """
    merchants = sorted({s["merchant"] for s in mat.submissions})
    by_merchant = {m: MerchantVerified(m, 0, 0, 0, 0, []) for m in merchants}
    clean_total = 0
    contested_group_units = 0
    immature_pending = 0
    rejected_total = 0

    for gid, g in sorted(groups.items()):
        if g.contested and g.eligible_claim_ids:
            # 组内多份合格申报竞争同一台：当前不能确定归属
            contested_group_units += 1
            for sid in g.eligible_claim_ids:
                by_merchant[audits[sid].merchant].contested_claim_units += 1
        elif not g.contested and g.eligible_claim_ids:
            clean_total += 1
            sid = g.eligible_claim_ids[0]
            by_merchant[audits[sid].merchant].clean_units += 1
        # 未成熟：组内存在 grace 窗内未决申报（无论是否同时有合格申报），
        # 每组合法台数上限仍为 1，潜在补入按组计 1 台，不给确定零
        if g.mature_claim_ids:
            immature_pending += 1
            for sid in g.mature_claim_ids:
                by_merchant[audits[sid].merchant].immature_units += 1

    for sid, a in audits.items():
        by_merchant[a.merchant].submission_ids.append(sid)
        # 未成熟申报暂缓而非否决；其余不合格申报计否决
        if not a.eligible and IMMATURE not in a.hold_reasons:
            rejected_total += 1
            by_merchant[a.merchant].rejected_units += 1

    upper_now = clean_total + contested_group_units
    return {
        "as_of": mat.as_of.isoformat(),
        "submissions": len(mat.submissions),
        "device_groups": len(groups),
        "clean_verified_units": clean_total,                 # 可立即核定下界=上界的部分
        "contested_groups": sum(1 for g in groups.values() if g.contested and g.eligible_claim_ids),
        "contested_group_unit_cap": contested_group_units,   # 争议组裁决后最多补入
        "immature_groups_pending": immature_pending,         # grace 窗内，暂不给确定零
        "rejected_submissions": rejected_total,
        "verified_lower": clean_total,
        "verified_upper_now": upper_now,
        "verified_upper_with_immature": upper_now + immature_pending,
        "by_merchant": by_merchant,
    }

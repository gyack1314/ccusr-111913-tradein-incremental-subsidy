"""补助范围核定：真实合格台数、单价、预算与效果证据的联合可行域。

制度（rules.effect_link）：
- 整体补助台数不得超过有依据的净新增上界；
- 商户申报量是各自上限而非贡献权重，禁止按核销量比例编造商户增量；
- 整体下界为 0，待审人员可在可行范围内提出分配；
- 效果未知部分可暂缓（unknown_effect_policy），不强制分摊。
单项上界不能直接相加当全体补助：本模块输出联合可行域并校验替代方案。

同物约束：一个同物组最多产出 1 台补助。未裁决组不进入任何商户的确定上界，
只在整体上界保留每组合法 1 台；裁决后该台计入获胜申报商户的上界。
"""
from __future__ import annotations

from dataclasses import dataclass

from scipy.optimize import linprog

from .eligibility import MerchantVerified


@dataclass
class FundingEnvelope:
    period: str
    per_unit_cap_cents: int
    budget_cents: int
    verified_lower_units: int
    verified_upper_units: int          # 当前快照可核定上界（不含未成熟）
    immature_potential_units: int
    effect_supported_upper_units: int  # 有依据净新增上界（台）
    effect_bounds_units: tuple[float, float] | None
    # 联合约束后的可行区间
    payable_lower_units: int
    payable_upper_units: int
    payable_lower_cents: int
    payable_upper_cents: int
    binding_constraints: list[str]
    suspended_units: int
    suspension_reasons: list[str]


def merchant_caps(by_merchant: dict[str, MerchantVerified],
                  groups: list[dict], rulings: dict[str, str | None]) -> dict[str, int]:
    """商户在当前证据与裁决下的台数上界。

    = 无争议合格台数 + 裁决归属于该商户的同物组台数（每组 1 台）。
    未裁决争议组不计入任何商户确定上界，避免同一台被两个商户重复占用。
    """
    caps = {m: mv.clean_units for m, mv in by_merchant.items()}
    # 申报 -> 商户索引
    claim_merchant = {}
    for m, mv in by_merchant.items():
        for sid in mv.submission_ids:
            claim_merchant[sid] = m
    for g in groups:
        if not g["contested"] or not g["eligible_claim_ids"]:
            continue
        gid = g["group_id"]
        if gid not in rulings:
            continue  # 未裁决：暂缓，不进商户口径
        winner = rulings[gid]
        if winner is not None and winner in claim_merchant:
            caps[claim_merchant[winner]] += 1
    return caps


def envelope(rule: dict, verified: dict, effect: dict) -> FundingEnvelope:
    cap = rule["per_verified_unit_cents"]
    budget = rule["program_budget_cents"]
    point_units = max(0.0, effect["point_estimate"].implied_units)
    bounds = effect["bounds"].bounds
    # 有依据上界：优先界分析上界（含缺失渠道的乐观端点），否则点估计
    effect_upper_raw = bounds[1] if bounds else point_units
    effect_upper = int(effect_upper_raw // 1)

    v_low = verified["verified_lower"]
    v_up = verified["verified_upper_now"]
    immature = verified["immature_groups_pending"]

    # 联合上界 = min(合格台数上界, 预算可容台数, 效果证据上界)
    budget_units = budget // cap
    joint_upper = min(v_up, budget_units, effect_upper)
    joint_lower = min(v_low, joint_upper)

    binding = []
    if joint_upper == v_up:
        binding.append("合格台数上界（同物组按一台计）")
    if joint_upper == budget_units:
        binding.append("整体预算/单价折算台数")
    if joint_upper == effect_upper:
        binding.append("净新增证据上界")

    suspended = max(0, v_up - joint_upper)
    reasons = []
    if effect_upper < v_up:
        reasons.append("效果证据支持台数少于合格申报台数，差额须暂缓而非按比例分摊")
    if immature:
        reasons.append(f"{immature} 个同物组仍在 grace 等待窗，未成熟不发确定金额")
    if verified["contested_groups"]:
        reasons.append(f"{verified['contested_groups']} 个同物组存在多份合格申报竞争，待裁决归属")
    return FundingEnvelope(
        period=rule["period"], per_unit_cap_cents=cap, budget_cents=budget,
        verified_lower_units=v_low, verified_upper_units=v_up,
        immature_potential_units=immature,
        effect_supported_upper_units=effect_upper,
        effect_bounds_units=bounds,
        payable_lower_units=joint_lower, payable_upper_units=joint_upper,
        payable_lower_cents=joint_lower * cap, payable_upper_cents=joint_upper * cap,
        binding_constraints=binding or ["无约束夹紧（理论情形）"],
        suspended_units=suspended, suspension_reasons=reasons,
    )


@dataclass
class AllocationCheck:
    feasible: bool
    total_units: int
    total_cents: int
    merchant_allocations: dict[str, int]
    violations: list[str]
    unused_budget_cents: int
    headroom_to_effect_cap: int
    # 方案中各同物组的实际占用（供审阅组级约束）
    group_usage: dict[str, int]


def check_allocation(env: FundingEnvelope, verified: dict, groups: list[dict],
                     rulings: dict[str, str | None],
                     proposed: dict[str, int]) -> AllocationCheck:
    """校验待审人员提出的整体分配方案是否同时满足全部项目约束。

    proposed: 商户 -> 补助台数。约束：
      0 <= x_m <= 商户可核定上界（无争议台数 + 裁决所得，未裁决不计入）
      sum x_m <= 联合上界（效果上界、预算、合格台数三者取小）
      sum x_m * 单价 <= 预算
    商户净新增贡献无法从全区效果分开时，方案是可行域内的行政分配而非因果增量；
    本函数只验证可行性，不“证明”分配权重。
    """
    by_merchant: dict[str, MerchantVerified] = verified["by_merchant"]
    violations = []
    alloc = {m: int(proposed.get(m, 0)) for m in by_merchant}
    unknown = set(proposed) - set(by_merchant)
    if unknown:
        violations.append(f"方案含未知商户: {sorted(unknown)}")
    caps = merchant_caps(by_merchant, groups, rulings)
    for m, x in alloc.items():
        if x < 0:
            violations.append(f"{m} 分配台数为负")
        if x > caps.get(m, 0):
            violations.append(
                f"{m} 分配 {x} 台超过其可核定上界 {caps.get(m, 0)}"
                f"（无争议 {by_merchant[m].clean_units} + 已裁决同物组，未裁决组暂缓不计入）")

    # 组级占用核对：每个争议组最多被一个商户的一台覆盖
    claim_merchant = {sid: m for m, mv in by_merchant.items() for sid in mv.submission_ids}
    group_usage: dict[str, int] = {}
    for g in groups:
        if not g["contested"]:
            continue
        gid = g["group_id"]
        # 方案层面只能通过裁决归属间接占用；未裁决而方案又发满上界时在这里暴露
        winner = rulings.get(gid)
        group_usage[gid] = 1 if winner is not None and alloc.get(claim_merchant.get(winner, ""), 0) > 0 else 0
        if gid not in rulings:
            # 未裁决组不产生确定补助；若商户上界已排除它，这里只做提示性登记
            group_usage[gid] = 0

    total = sum(alloc.values())
    cents = total * env.per_unit_cap_cents
    if total > env.payable_upper_units:
        violations.append(
            f"整体 {total} 台超过联合上界 {env.payable_upper_units}："
            f"min(合格 {env.verified_upper_units}, 预算 {env.budget_cents // env.per_unit_cap_cents},"
            f" 效果 {env.effect_supported_upper_units})")
    if cents > env.budget_cents:
        violations.append(f"金额 {cents} 分超过整体预算 {env.budget_cents} 分")
    return AllocationCheck(
        feasible=not violations, total_units=total, total_cents=cents,
        merchant_allocations=alloc, violations=violations,
        unused_budget_cents=env.budget_cents - cents,
        headroom_to_effect_cap=env.effect_supported_upper_units - total,
        group_usage=group_usage,
    )


def max_feasible_allocation(env: FundingEnvelope, verified: dict, groups: list[dict],
                            rulings: dict[str, str | None]) -> AllocationCheck:
    """求联合可行域内的最大整体补助，并给出一个均衡的可达分配。

    第一步 LP 只证明联合上界在商户口径下可达（不把单项上界直接相加）；
    第二步在商户上界内按轮询补齐到最大台数，得到确定性、可复核的行政分配。
    轮询顺序不主张任何商户因果增量，权重问题在方案说明中单独声明。
    """
    by_merchant: dict[str, MerchantVerified] = verified["by_merchant"]
    merchants = sorted(by_merchant)
    caps = merchant_caps(by_merchant, groups, rulings)
    res = linprog(c=[-1] * len(merchants),
                  bounds=[(0, caps[m]) for m in merchants],
                  A_ub=[[1] * len(merchants)], b_ub=[env.payable_upper_units],
                  method="highs")
    if not res.success:
        raise RuntimeError(f"可行域求解失败: {res.message}")
    attainable = min(int(round(sum(res.x))), env.payable_upper_units, sum(caps.values()))
    # 轮询补齐：每轮按固定顺序给还有上界余量的商户各 1 台
    proposed = {m: 0 for m in merchants}
    remaining = attainable
    while remaining > 0:
        progressed = False
        for m in merchants:
            if remaining <= 0:
                break
            if proposed[m] < caps[m]:
                proposed[m] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return check_allocation(env, verified, groups, rulings, proposed)

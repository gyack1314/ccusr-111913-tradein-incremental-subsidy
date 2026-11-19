"""净新增回收的效果识别。

目标（契约）：来源人群在约定观察期内 *所有渠道* 规范处理量的净新增，
不以商户所在地替代居民来源；渠道量只能在相同人群和统计期比较；
complete=false 不给确定零。识别假设、不可识别范围与敏感性必须公开。

方法（可替换，不主张唯一因果算法）：
1. 按实际启用日的多期 TWFE/事件研究（region × month 面板，人均规范处理量）；
2. 分渠道估计以诊断渠道迁移（campaign 上升是否伴随 ordinary/mobile 下降）；
3. 事件后首月与次月对比诊断时间替代（提前回收会在次月回落）；
4. 邻区外溢诊断：已启用片区对 neighbor_regions 中尚未启用片区的效应；
5. 不完整月（2026-09 ordinary，complete=false）做界分析而非补零；
6. 独立时期（启用前）做安慰剂平行趋势诊断，不强求正面结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .materials import Materials, parse_day

TREATED_CHANNELS = ("campaign", "ordinary", "mobile")
OUTCOME_PROCESSED = "processed_units"
OUTCOME_RECEIVED = "received_units"


@dataclass
class PanelCell:
    region: str
    month: str
    channel: str
    received: float
    processed: float
    population: int
    complete: bool


def build_panel(mat: Materials) -> list[PanelCell]:
    cells = []
    for c in mat.channel_totals:
        cells.append(PanelCell(
            region=c["region"], month=c["month"], channel=c["channel"],
            received=c["received_units"], processed=c["processed_units"],
            population=c["population"], complete=c["complete"],
        ))
    return cells


def _ols(x: np.ndarray, y: np.ndarray):
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    dfree = max(len(y) - x.shape[1], 1)
    sigma2 = resid @ resid / dfree
    try:
        cov = np.linalg.inv(x.T @ x) * sigma2
        se = np.sqrt(np.clip(np.diag(cov), 0, None))
    except np.linalg.LinAlgError:
        se = np.full(x.shape[1], np.nan)
    return beta, se


def _aggregate(cells: list[PanelCell], outcome: str, region: str, month: str,
               include_channels=TREATED_CHANNELS):
    attr = "processed" if outcome == OUTCOME_PROCESSED else "received"
    vals = [getattr(c, attr) for c in cells
            if c.region == region and c.month == month and c.channel in include_channels]
    return sum(vals) if vals else 0.0


def _month_index(month: str) -> int:
    y, m = month.split("-")
    return int(y) * 12 + int(m)


def event_months(mat: Materials) -> dict[str, dict[int, str]]:
    """每片区相对实际启用日的事件时间映射。"""
    out = {}
    for r in mat.rollout:
        start = parse_day(r["actual_start"])
        start_idx = start.year * 12 + start.month
        out[r["region"]] = {m: _month_index(m) - start_idx for m in
                            sorted({c["month"] for c in mat.channel_totals})}
    return out


@dataclass
class EffectResult:
    method: str
    outcome: str
    coef: float
    se: float
    implied_units: float          # 把人均系数换算回处理片区-月的台数
    n_obs: int
    treated_region_months: int
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    bounds: tuple[float, float] | None = None


def twfe(mat: Materials, outcome: str = OUTCOME_PROCESSED,
         exclude_spillover: bool = True) -> EffectResult:
    """多期 TWFE；exclude_spillover 时把已启用片区的邻区（尚未启用）剔出对照。"""
    cells = build_panel(mat)
    regions = sorted({c.region for c in cells})
    months = sorted({c.month for c in cells})
    ev = event_months(mat)
    rollout = {r["region"]: r for r in mat.rollout}

    active_neighbors: set[str] = set()
    if exclude_spillover:
        for r in mat.rollout:
            for nb in r["neighbor_regions"]:
                active_neighbors.add(nb)

    y, treated = [], []
    treated_pop_months = 0
    for r in regions:
        pop = next(c.population for c in cells if c.region == r)
        for m in months:
            et = ev[r][m]
            is_treated = et >= 0
            # 不完整格不补零：主估计只用 complete 的聚合
            month_cells = [c for c in cells if c.region == r and c.month == m]
            if not all(c.complete for c in month_cells):
                continue
            if not is_treated and exclude_spillover and r in active_neighbors:
                # 邻区在观察窗内可能被外溢污染，不作为干净对照（保留在面板外诊断）
                continue
            y.append(_aggregate(cells, outcome, r, m) / pop)
            treated.append(1.0 if is_treated else 0.0)
            if is_treated:
                treated_pop_months += 1
    y = np.array(y); treated = np.array(treated)
    # 重新构造与保留样本对齐的固定效应设计
    kept = []
    for r in regions:
        for m in months:
            month_cells = [c for c in cells if c.region == r and c.month == m]
            if not all(c.complete for c in month_cells):
                continue
            if ev[r][m] < 0 and exclude_spillover and r in active_neighbors:
                continue
            kept.append((r, m))
    n = len(kept)
    design_cols = [treated]
    for r in regions[:-1]:
        design_cols.append(np.array([1.0 if rr == r else 0.0 for rr, _ in kept]))
    for m in months[:-1]:
        design_cols.append(np.array([1.0 if mm == m else 0.0 for _, mm in kept]))
    x = np.column_stack(design_cols)
    beta, se = _ols(x, y)
    implied = sum(
        next(c.population for c in cells if c.region == r)
        for r, m in kept if ev[r][m] >= 0
    ) * beta[0]
    return EffectResult(
        method="TWFE(区域+月份固定效应)" + ("，剔除潜在外溢邻区对照" if exclude_spillover else ""),
        outcome=outcome, coef=float(beta[0]), se=float(se[0]),
        implied_units=float(implied), n_obs=n,
        treated_region_months=treated_pop_months,
        assumptions=[
            "平行趋势：无政策时各片区人均规范处理量走势相同（见事件研究安慰剂诊断）",
            "SUTVA 松弛：" + ("已剔除启用片区的邻区作为对照，外溢仅做诊断" if exclude_spillover
                          else "未剔除邻区，外溢会使对照被污染、效应被低估"),
            "处理时点异质性：多期 TWFE 在异质性处理效应下可能有负权重，事件研究用于核对",
            "complete=false 的月份不进入主估计，另做界分析",
        ],
    )


def event_study(mat: Materials, outcome: str = OUTCOME_PROCESSED) -> dict[str, Any]:
    """事件研究：相对启用月 -k..+k 的系数，启用前系数用于平行趋势诊断。"""
    cells = build_panel(mat)
    regions = sorted({c.region for c in cells})
    months = sorted({c.month for c in cells})
    ev = event_months(mat)
    offsets = sorted({et for m in ev.values() for et in m.values()})
    kept, y = [], []
    for r in regions:
        pop = next(c.population for c in cells if c.region == r)
        for m in months:
            month_cells = [c for c in cells if c.region == r and c.month == m]
            if not all(c.complete for c in month_cells):
                continue
            kept.append((r, m, ev[r][m], pop))
            y.append(_aggregate(cells, outcome, r, m) / pop)
    y = np.array(y)
    # 以 -1 月为基准省略
    event_cols, event_names = [], []
    for et in offsets:
        if et == -1 or et < -4 or et > 3:
            continue
        event_cols.append(np.array([1.0 if k[2] == et else 0.0 for k in kept]))
        event_names.append(f"event_{et:+d}")
    region_cols = [np.array([1.0 if k[0] == r else 0.0 for k in kept]) for r in regions[:-1]]
    month_cols = [np.array([1.0 if k[1] == m else 0.0 for k in kept]) for m in months[:-1]]
    x = np.column_stack(event_cols + region_cols + month_cols)
    beta, se = _ols(x, y)
    return {
        "event_offsets": event_names,
        "coefs": [float(v) for v in beta[:len(event_names)]],
        "se": [float(v) for v in se[:len(event_names)]],
        "pre_treatment_offsets": [n for n in event_names if n.endswith(("-4", "-3", "-2"))],
        "diagnostic": "启用前系数应接近 0；显著偏离说明平行趋势存疑，TWFE 不可直接当因果",
    }


def channel_effects(mat: Materials) -> list[EffectResult]:
    """分渠道 TWFE：若只有 campaign 上升而其他渠道下降，说明主要是渠道迁移而非净新增。"""
    cells = build_panel(mat)
    regions = sorted({c.region for c in cells})
    months = sorted({c.month for c in cells})
    ev = event_months(mat)
    results = []
    for chan in TREATED_CHANNELS:
        kept, y, treated = [], [], []
        for r in regions:
            pop = next(c.population for c in cells if c.region == r)
            for m in months:
                cell = next((c for c in cells if c.region == r and c.month == m
                             and c.channel == chan), None)
                if cell is None or not cell.complete:
                    continue
                kept.append((r, m)); y.append(cell.processed / pop)
                treated.append(1.0 if ev[r][m] >= 0 else 0.0)
        y = np.array(y); treated = np.array(treated)
        cols = [treated]
        for r in regions[:-1]:
            cols.append(np.array([1.0 if rr == r else 0.0 for rr, _ in kept]))
        for m in months[:-1]:
            cols.append(np.array([1.0 if mm == m else 0.0 for _, mm in kept]))
        beta, se = _ols(np.column_stack(cols), y)
        implied = sum(next(c.population for c in cells if c.region == r)
                      for r, m in kept if ev[r][m] >= 0) * beta[0]
        results.append(EffectResult(
            method=f"分渠道 TWFE: {chan}", outcome="processed_units",
            coef=float(beta[0]), se=float(se[0]), implied_units=float(implied),
            n_obs=len(kept), treated_region_months=int(treated.sum()),
            notes=["系数为负表示该渠道被活动挤出；三渠道同号为正更支持净新增而非迁移"],
        ))
    return results


def intertemporal_check(mat: Materials) -> dict[str, Any]:
    """时间替代诊断：比较启用后首月与次月的超额量，次月明显回落提示提前消费。"""
    cells = build_panel(mat)
    ev = event_months(mat)
    # 用处理片区在 event 0 与 event +1 的全渠道人均量，对照启用前均值
    rows = []
    for r in sorted({c.region for c in cells}):
        pop = next(c.population for c in cells if c.region == r)
        pre = [_aggregate(cells, "processed_units", r, m) / pop
               for m in ev[r] if ev[r][m] < 0 and all(
                   c.complete for c in cells if c.region == r and c.month == m)]
        post0 = [(_aggregate(cells, "processed_units", r, m) / pop, m)
                 for m in ev[r] if ev[r][m] == 0 and all(
                     c.complete for c in cells if c.region == r and c.month == m)]
        post1 = [(_aggregate(cells, "processed_units", r, m) / pop, m)
                 for m in ev[r] if ev[r][m] == 1 and all(
                     c.complete for c in cells if c.region == r and c.month == m)]
        rows.append({
            "region": r, "pre_mean": float(np.mean(pre)) if pre else None,
            "event0": post0[0] if post0 else None,
            "event1": post1[0] if post1 else None,
        })
    drops = []
    for row in rows:
        if row["event0"] and row["event1"] and row["pre_mean"] is not None:
            e0 = row["event0"][0] - row["pre_mean"]
            e1 = row["event1"][0] - row["pre_mean"]
            drops.append({"region": row["region"], "excess_event0": e0,
                          "excess_event1": e1, "fade_ratio": (e0 - e1) / e0 if e0 else None})
    return {
        "by_region": rows,
        "fades": drops,
        "diagnostic": "fade_ratio 接近 1 表示首月增量在次月几乎全部消失（时间替代）；接近 0 表示增量持续",
    }


def spillover_check(mat: Materials) -> list[dict[str, Any]]:
    """外溢诊断：邻区在自身尚未启用、但邻居已启用月份的人均量是否异常偏高。"""
    cells = build_panel(mat)
    ev = event_months(mat)
    out = []
    for r in mat.rollout:
        for nb in r["neighbor_regions"]:
            nb_roll = next((x for x in mat.rollout if x["region"] == nb), None)
            if nb_roll is None:
                continue
            pop = next(c.population for c in cells if c.region == nb)
            pre_self = [_aggregate(cells, "processed_units", nb, m) / pop
                        for m in ev[nb] if ev[nb][m] < 0]
            # 邻居启用而本区未启用的月份
            neighbor_start = parse_day(r["actual_start"])
            neighbor_idx = neighbor_start.year * 12 + neighbor_start.month
            exposed = []
            for m, et in ev[nb].items():
                m_idx = _month_index(m)
                if et < 0 and m_idx >= neighbor_idx and all(
                        c.complete for c in cells if c.region == nb and c.month == m):
                    exposed.append((m, _aggregate(cells, "processed_units", nb, m) / pop))
            if exposed and pre_self:
                base = float(np.mean(pre_self))
                out.append({
                    "active_region": r["region"], "not_yet_treated_neighbor": nb,
                    "neighbor_pre_mean_per_capita": base,
                    "exposed_months": [{"month": m, "per_capita": v,
                                        "excess": v - base} for m, v in exposed],
                    "diagnostic": "暴露月份正超额支持跨区吸引；这部分不能算作被吸引片区自身的净新增",
                })
    return out


def incomplete_bounds(mat: Materials, twfe_point: EffectResult) -> EffectResult:
    """complete=false 月份（2026-09 ordinary）的界分析。

    下界：缺失渠道按 0 台（极端保守，契约禁止把它当确定估计，仅作区间端点）；
    上界：缺失渠道按该片区该渠道历史最高月处理量补。
    """
    cells = build_panel(mat)
    ev = event_months(mat)
    regions = sorted({c.region for c in cells})
    months = sorted({c.month for c in cells})
    lo_y, hi_y, treated, kept = [], [], [], []
    for r in regions:
        pop = next(c.population for c in cells if c.region == r)
        hist_max = {ch: max(c.processed for c in cells
                            if c.region == r and c.channel == ch and c.complete)
                    for ch in TREATED_CHANNELS}
        for m in months:
            month_cells = [c for c in cells if c.region == r and c.month == m]
            kept.append((r, m))
            lo = sum(0 if not c.complete else c.processed for c in month_cells) / pop
            hi = sum(hist_max[c.channel] if not c.complete else c.processed
                     for c in month_cells) / pop
            lo_y.append(lo); hi_y.append(hi); treated.append(1.0 if ev[r][m] >= 0 else 0.0)
    cols = [np.array(treated)]
    for r in regions[:-1]:
        cols.append(np.array([1.0 if rr == r else 0.0 for rr, _ in kept]))
    for m in months[:-1]:
        cols.append(np.array([1.0 if mm == m else 0.0 for _, mm in kept]))
    x = np.column_stack(cols)
    b_lo, _ = _ols(x, np.array(lo_y))
    b_hi, _ = _ols(x, np.array(hi_y))
    pop_sum = sum(next(c.population for c in cells if c.region == r)
                  for r, m in kept if ev[r][m] >= 0)
    return EffectResult(
        method="不完整月界分析（ordinary 2026-09 complete=false）",
        outcome="processed_units", coef=twfe_point.coef, se=twfe_point.se,
        implied_units=twfe_point.implied_units, n_obs=len(kept),
        treated_region_months=int(sum(treated)),
        bounds=(float(b_lo[0] * pop_sum), float(b_hi[0] * pop_sum)),
        notes=[
            "下界端点假设缺失渠道为 0，仅作敏感性端点，不是确定估计（契约禁止补零定论）",
            "上界端点按同片区同渠道历史最高月补；真实增量落在区间内，含不可识别部分",
        ],
    )


def evaluate(mat: Materials) -> dict[str, Any]:
    """汇总效果证据包：点估计、诊断、渠道、替代、外溢、界与不可识别说明。"""
    point = twfe(mat)
    study = event_study(mat)
    channels = channel_effects(mat)
    fades = intertemporal_check(mat)
    spillovers = spillover_check(mat)
    bounds = incomplete_bounds(mat, point)
    unidentifiable = [
        "2026-09 ordinary 渠道 complete=false：当月真实处理量不可识别，只给区间",
        "邻区外溢只能诊断暴露月份的超额，无法逐一拆出跨区居民身份",
        "时间替代与长期净新增的拆分依赖平行趋势假设，启用前样本仅 4-8 个月",
        "TWFE 对异质性处理效应有潜在负权重；事件研究系数供人工核对而非自动采信",
    ]
    return {
        "as_of": mat.as_of.isoformat(),
        "target_population": "rollout 片区居民来源人群（resident_region），观察窗 2026-01..2026-09 月面板",
        "point_estimate": point,
        "event_study": study,
        "channel_effects": channels,
        "intertemporal": fades,
        "spillovers": spillovers,
        "bounds": bounds,
        "unidentifiable": unidentifiable,
    }

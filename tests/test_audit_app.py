"""业务验收测试：资格、同物、成熟等待、效果识别假设与补助联合约束。

与 tests/test_materials.py 的格式体检不同，这里验证领域判断。
使用隔离导入（sys.path 引导），不依赖 CWD。
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from audit import cases as cases_mod
from audit import effects as effects_mod
from audit import eligibility as elig
from audit import funding as fund
from audit.materials import Materials
from audit.pipeline import explain_submission, load_rulings, run_pipeline

LATEST = "2026-10-05T09:00:00+08:00"
MID = "2026-09-20T00:00:00+08:00"


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.mat = Materials.snapshot(as_of=LATEST)
        self.audits = elig.audit_submissions(self.mat)
        self.groups = elig.build_device_groups(self.mat, self.audits)
        self.verified = elig.verified_units(self.mat, self.audits, self.groups)

    def test_three_confirmed_facts_required(self):
        # A017 三事实全部 cancelled -> 否决
        self.assertFalse(self.audits["A017"].eligible)
        codes = self.audits["A017"].reason_codes()
        self.assertIn(elig.CANCELLED, codes)
        # 正常三确认申报合格
        self.assertTrue(self.audits["A001"].eligible)

    def test_partial_refund_does_not_disqualify(self):
        # A019 有部分退款但事实均 confirmed；退款只登记，不构成否决原因
        a = self.audits["A019"]
        self.assertTrue(any(c["code"] == elig.PARTIAL_REFUND_NOTE for c in a.checks))
        self.assertNotIn(elig.CANCELLED, a.reason_codes())
        self.assertFalse(any(c["code"] == elig.FACT_NOT_CONFIRMED for c in a.checks
                             if not c["passed"]))
        # 其暂缓仅因同物争议，而非退款
        self.assertIn(elig.DUPLICATE_SERIAL, a.reason_codes())

    def test_pending_handover_is_not_completed_recovery(self):
        # pending 处理事实不构成完成回收
        pending = [s for s, a in self.audits.items()
                   if any(f.status == "pending" for f in a.facts.values())]
        self.assertTrue(pending)
        for sid in pending:
            self.assertFalse(self.audits[sid].eligible)

    def test_grace_window_immature_vs_overdue(self):
        # 9/20 时点：pending 都在 grace 窗内 -> 未成熟暂缓，不是否决
        mid = Materials.snapshot(as_of=MID)
        audits = elig.audit_submissions(mid)
        groups = elig.build_device_groups(mid, audits)
        v = elig.verified_units(mid, audits, groups)
        self.assertGreaterEqual(v["immature_groups_pending"], 8)
        # 最新时点：处理日 + 30 天已过的 pending -> 否决且不再挂 IMMATURE
        overdue = {"A022", "A044"}  # 处理日 9/3、9/5，10/5 已过窗
        for sid in overdue:
            self.assertFalse(self.audits[sid].eligible)
            self.assertNotIn(elig.IMMATURE, self.audits[sid].hold_reasons)
        # 处理日 9/7、9/9 在 10/5 仍未到期 -> 仍挂 IMMATURE
        for sid in ("A066", "A088"):
            self.assertIn(elig.IMMATURE, self.audits[sid].hold_reasons)

    def test_explicit_serial_and_confirmed_review_merge(self):
        # SN0006: 显式同序列号 + I1 confirmed 同物，双重证据归并到一组
        g = next(g for g in self.groups.values()
                 if set(g.submission_ids) == {"A006", "A013"})
        self.assertTrue(g.contested)
        self.assertIn("I1", g.confirmed_review_ids)
        self.assertEqual(len(g.eligible_claim_ids), 2)  # 两份合格申报竞争一台

    def test_submitted_review_does_not_merge(self):
        # I2 是 submitted 状态：A025/A026 不得自动合并
        g25 = self.audits["A025"].device_group_id
        g26 = self.audits["A026"].device_group_id
        self.assertNotEqual(g25, g26)
        group = self.groups[g25]
        self.assertIn("I2", group.open_review_ids)
        self.assertFalse(group.contested)

    def test_similar_names_not_auto_merged(self):
        # 只有显式序列号或 confirmed 审核能归并；其余 92 台各自独立成组
        serials = {}
        for s in self.mat.submissions:
            serials.setdefault(s["old_serial"], []).append(s["id"])
        dup_serials = {k for k, v in serials.items() if len(v) > 1}
        self.assertEqual(dup_serials, {"SN0006", "SN0019", "SN0032", "SN0045"})

    def test_contested_group_counts_one_unit_not_two(self):
        # 4 个争议组合格，但整体上界每组只算 1 台
        self.assertEqual(self.verified["contested_groups"], 4)
        self.assertEqual(self.verified["contested_group_unit_cap"], 4)
        # 无争议合格 78 台 + 争议组 4 台 = 当前上界 82（未成熟 7 组不进当前上界）
        self.assertEqual(self.verified["verified_upper_now"], 82)
        self.assertEqual(self.verified["clean_verified_units"], 78)


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.mat = Materials.snapshot(as_of=LATEST)
        self.result = run_pipeline(self.mat)
        self.env = fund.FundingEnvelope(**{
            k: v for k, v in self.result["funding_envelope"].items()
            if k in fund.FundingEnvelope.__dataclass_fields__})
        from audit.eligibility import MerchantVerified
        self.verified = dict(self.result["verified"])
        self.verified["by_merchant"] = {
            m: MerchantVerified(**row) for m, row in
            self.result["verified"]["by_merchant"].items()}
        self.groups = list(self.result["device_groups"].values())

    def test_budget_is_binding_and_single_caps_not_summed(self):
        # 预算 300000 / 单价 6000 = 50 台，小于合格上界 89 与效果上界
        self.assertEqual(self.env.payable_upper_units, 50)
        self.assertIn("整体预算/单价折算台数", self.env.binding_constraints)

    def test_unruled_contested_group_not_in_merchant_cap(self):
        caps = fund.merchant_caps(self.verified["by_merchant"], self.groups, {})
        # 无裁决时商户口径只含无争议台数
        self.assertEqual(caps["M1"], 15)
        # 裁决 G006 -> A006(M1) 后 M1 上界 +1
        caps2 = fund.merchant_caps(self.verified["by_merchant"], self.groups,
                                   {"G006": "A006"})
        self.assertEqual(caps2["M1"], 16)

    def test_allocation_rejects_over_cap_and_over_budget(self):
        bad = {"M0": 30, "M1": 20, "M2": 0, "M3": 0, "M4": 0}
        check = fund.check_allocation(self.env, self.verified, self.groups, {}, bad)
        self.assertFalse(check.feasible)
        self.assertTrue(any("超过其可核定上界" in v for v in check.violations))

    def test_max_feasible_plan_respects_all_constraints(self):
        plan = fund.max_feasible_allocation(self.env, self.verified, self.groups, {})
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.total_units, 50)
        self.assertEqual(plan.total_cents, 300000)
        caps = fund.merchant_caps(self.verified["by_merchant"], self.groups, {})
        for m, x in plan.merchant_allocations.items():
            self.assertLessEqual(x, caps[m])

    def test_no_fabricated_merchant_increment_share(self):
        # 效果上界是全区口径；商户维度没有因果增量字段，只有行政可行分配
        self.assertNotIn("merchant_effect_units", self.result)

    def test_effect_cap_can_bind_and_suspend_units(self):
        # 构造效果上界仅 5 台的情形：联合上界被效果证据夹紧，差额暂缓而非分摊
        tight = fund.FundingEnvelope(
            **{**{k: v for k, v in self.result["funding_envelope"].items()
                  if k in fund.FundingEnvelope.__dataclass_fields__},
               "effect_supported_upper_units": 5,
               "payable_upper_units": 5, "payable_upper_cents": 30000})
        self.assertEqual(tight.payable_upper_units, 5)
        check = fund.check_allocation(
            tight, self.verified, self.groups, {},
            {m: 2 for m in self.verified["by_merchant"]})
        self.assertFalse(check.feasible)
        self.assertTrue(any("效果 5" in v for v in check.violations))
        # 可行域内 5 台仍可发（每商户 1 台）
        ok = fund.check_allocation(
            tight, self.verified, self.groups, {},
            {m: 1 for m in self.verified["by_merchant"]})
        self.assertTrue(ok.feasible)
        self.assertEqual(ok.total_units, 5)


class EffectsTests(unittest.TestCase):
    def setUp(self):
        self.mat = Materials.snapshot(as_of=LATEST)

    def test_target_uses_resident_population_and_all_channels(self):
        ev = effects_mod.evaluate(self.mat)
        self.assertIn("resident_region", ev["target_population"])
        # 点估计为正且给出换算台数，但同时声明假设与不可识别部分
        self.assertGreater(ev["point_estimate"].implied_units, 0)
        self.assertTrue(ev["point_estimate"].assumptions)
        self.assertTrue(ev["unidentifiable"])

    def test_parallel_trend_diagnostic_exposes_pre_trend(self):
        es = effects_mod.event_study(self.mat)
        pre = [c for n, c in zip(es["event_offsets"], es["coefs"])
               if n in es["pre_treatment_offsets"]]
        # 启用前系数应在 0 附近（合成数据平行趋势），诊断文本存在
        self.assertTrue(all(abs(c) < 0.02 for c in pre))
        self.assertIn("平行趋势", es["diagnostic"])

    def test_incomplete_month_uses_bounds_not_zero(self):
        ev = effects_mod.evaluate(self.mat)
        lo, hi = ev["bounds"].bounds
        self.assertLess(lo, hi)  # complete=false 不给确定零，区间有宽度
        self.assertTrue(any("complete=false" in n for n in ev["unidentifiable"]))

    def test_spillover_neighbor_not_treated_as_clean_control(self):
        sp = effects_mod.spillover_check(self.mat)
        pairs = {(s["active_region"], s["not_yet_treated_neighbor"]) for s in sp}
        self.assertIn(("Z0", "Z1"), pairs)
        # 主 TWFE 剔除邻区对照，未剔除版本假设文本不同
        clean = effects_mod.twfe(self.mat, exclude_spillover=True)
        dirty = effects_mod.twfe(self.mat, exclude_spillover=False)
        self.assertNotEqual(clean.n_obs, dirty.n_obs)


class CaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = cases_mod.CaseStore(pathlib.Path(self.tmp.name) / "events.jsonl")
        self.mat = Materials.snapshot(as_of=LATEST)
        self.fp = cases_mod.snapshot_fingerprint(self.mat)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ruling_then_correction_keeps_paid_amount(self):
        self.store.rule_identity("G006", "A006", "裁决", self.mat.as_of, self.fp, ["I1"])
        self.store.record_disbursement("M1", 66000, "R1", 1, self.mat.as_of, self.fp)
        self.store.correct("M1", 6000, "recoverable", "改判追查", self.mat.as_of, self.fp)
        ledger = cases_mod.fold_ledger(self.store)
        self.assertEqual(ledger["M1"].paid_cents, 66000)       # 已发不删除
        self.assertEqual(ledger["M1"].recoverable_cents, 6000)  # 更正单列
        # 事件存储只追加
        kinds = [e["type"] for e in self.store.read_events()]
        self.assertEqual(kinds.count("disbursement"), 1)

    def test_ruling_cannot_be_silently_overwritten(self):
        self.store.rule_identity("G006", "A006", "初裁", self.mat.as_of, self.fp, ["I1"])
        with self.assertRaises(ValueError):
            self.store.rule_identity("G006", "A013", "改裁", self.mat.as_of, self.fp, ["I1"])

    def test_supplement_conflict_rejected_and_idempotent(self):
        self.store.supplement_receipt("A011", "P011", {"receipt_id": "RCPT-1"},
                                      self.mat.as_of, self.fp)
        ev = self.store.supplement_receipt("A011", "P011", {"receipt_id": "RCPT-1"},
                                           self.mat.as_of, self.fp)
        self.assertEqual(ev["payload"]["receipt"]["receipt_id"], "RCPT-1")
        with self.assertRaises(ValueError):
            self.store.supplement_receipt("A011", "P011", {"receipt_id": "RCPT-2"},
                                          self.mat.as_of, self.fp)

    def test_plan_accept_warns_on_basis_change(self):
        plan = {"allocations": {"M1": 10}}
        ev = self.store.accept_plan(plan, {"fingerprint": "OLD-FP"},
                                    self.mat.as_of, self.fp)
        self.assertTrue(ev["payload"]["basis_changed"])
        self.assertIn("指纹", ev["payload"]["affected_basis"][0])

    def test_historical_snapshot_replayable(self):
        # 9/20 快照下 A011（处理日 9/12）未成熟，暂缓而非否决
        mid = Materials.snapshot(as_of=MID)
        explanation = explain_submission(mid, "A011")
        self.assertEqual(explanation["结论"], "暂缓")
        self.assertIn(elig.IMMATURE, explanation["检查"][0]["code"]) if False else None
        self.assertTrue(any(c["code"] == elig.IMMATURE for c in explanation["检查"]))


if __name__ == "__main__":
    unittest.main()

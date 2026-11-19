# 以旧换新净新增回收证据与补助范围核定

本项目围绕回收业务的本地资料分析。当前资料包含合成原始记录、字段契约和文件体检工具，业务分析及办理入口尚未提供。合成资料没有现实居民或企业信息，不能作为真实政策、交易或认证依据。

## 环境

使用 Python 3.11。科学计算依赖版本固定在 requirements.txt。已有相同依赖环境时无需重复安装；新环境使用 `python3 -m pip install -r requirements.txt`。资料体检工具本身仅使用标准库。

## 现有操作

在项目根目录执行 `python3 -m support.inspect_data` 查看资料表数量并核对文件摘要、字段、身份及声明的引用。执行 `python3 -I run_tests.py` 运行全部测试（含资料格式、回执服务/客户端与业务验收）。这些检查中前两项只验证资料可读与损坏可发现，业务判断由 `tests/test_audit_app.py` 覆盖。

数据保存在 data/，字段含义和适用条件见 [资料契约](docs/input-contract.md)。data/manifest.json 声明每份原始文件的身份和摘要；新增资料可在独立目录保存新清单，不应覆盖用于历史解释的原始文件。

## 业务分析应用（audit/）

应用把原始申报走到效果解释、方案审阅与结项调整，方法与口径见 [业务方法说明](docs/method.md)。全部命令通过隔离模式启动器运行，避免从工作目录隐式导入：

```bash
# 全量分析：资格审核、同物归并、效果识别、补助联合可行域，报告写入 outputs/
python3 -I run_audit.py analyze
# 指定观察时点复现旧结项（按当时 known_at 可见证据）
python3 -I run_audit.py --as-of 2026-09-20T00:00:00+08:00 analyze
# 追查单份申报为何可发、暂缓或否决（逐事实身份与检查项）
python3 -I run_audit.py explain-submission A013
# 列出有争议或未决审核的同物组
python3 -I run_audit.py groups
# 同物裁决（归属某申报，或不给 winner 表示组内全员不合格）
python3 -I run_audit.py rule-identity G006 A006 --rationale "I1已确认同物，A006交旧更早"
# 补交处理回执；--verify-url 先经独立回执服务核对投递事实与摘要
python3 -I run_audit.py supplement A011 P011 receipt.json --verify-url http://127.0.0.1:8096
# 审阅并接受整体补助方案（不可行自动拒绝，--force 仅登记不推荐）
python3 -I run_audit.py accept-plan plan.json
# 登记已确认发放（本地回执，幂等，已发金额不可删除）
python3 -I run_audit.py disburse M1 66000 RCPT-M1-001 --plan-seq 2
# 更正：payable_more 待补足 / recoverable 待追查，只追加不改写
python3 -I run_audit.py correct M1 6000 recoverable "同物改判"
# 商户金额台账与逐笔追溯
python3 -I run_audit.py ledger
python3 -I run_audit.py trace M1
```

办案事件默认写入 `.local/case_events.jsonl`（`--state` 可改），事件只追加并带观察时点与资料快照指纹。补助方案 JSON 形如 `{"allocations": {"M1": 10, ...}}`，台数同时受商户可核定上界、联合上界与预算约束。

## 本地状态

运行时写入内容使用 .local/ 或 outputs/，不要改写 data/ 下的原始记录。测试中的临时目录在用后自动清理；无需外网账号、访问令牌或真实支付服务。产品操作与相应验证方式随业务入口建立后补充在此文档。

## 容器使用

`docker build -t recovery-data .` 准备锁定依赖环境。`docker run --rm recovery-data` 运行资料体检；`docker run --rm recovery-data python -I run_tests.py` 运行全部测试（含业务验收）。镜像基础已固定摘要。资料体检只证明格式完好，业务结论以 `tests/test_audit_app.py` 与 `run_audit.py analyze` 的输出为准。

## 本地渠道

有独立回执服务可供核对投递事实，启动与协议见 [渠道契约](docs/receipt-contract.md)。服务保存事实，不提供分析、资格判断或业务办理实现。

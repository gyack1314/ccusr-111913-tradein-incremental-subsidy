# 以旧换新净新增回收证据与补助范围核定

本项目围绕回收业务的本地资料分析。当前资料包含合成原始记录、字段契约和文件体检工具，业务分析及办理入口尚未提供。合成资料没有现实居民或企业信息，不能作为真实政策、交易或认证依据。

## 环境

使用 Python 3.11。科学计算依赖版本固定在 requirements.txt。已有相同依赖环境时无需重复安装；新环境使用 `python3 -m pip install -r requirements.txt`。资料体检工具本身仅使用标准库。

## 现有操作

在项目根目录执行 `python3 -m support.inspect_data` 查看资料表数量并核对文件摘要、字段、身份及声明的引用。执行 `python3 -m unittest discover -s tests -v` 验证资料可读以及损坏文件可被发现。这些检查不计算领域指标，也不证明未来业务分析正确。

数据保存在 data/，字段含义和适用条件见 [资料契约](docs/input-contract.md)。data/manifest.json 声明每份原始文件的身份和摘要；新增资料可在独立目录保存新清单，不应覆盖用于历史解释的原始文件。

## 本地状态

运行时写入内容使用 .local/ 或 outputs/，不要改写 data/ 下的原始记录。测试中的临时目录在用后自动清理；无需外网账号、访问令牌或真实支付服务。产品操作与相应验证方式随业务入口建立后补充在此文档。

## 容器使用

`docker build -t recovery-data .` 准备锁定依赖环境。`docker run --rm recovery-data` 运行资料体检；`docker run --rm recovery-data python -m unittest discover -s tests -v` 运行现有格式检查。镜像基础已固定摘要。产品开发后的业务验收应另行运行，不能把这两项格式检查称为业务验收。

## 本地渠道

有独立回执服务可供核对投递事实，启动与协议见 [渠道契约](docs/receipt-contract.md)。服务保存事实，不提供分析、资格判断或业务办理实现。

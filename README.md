# AQuant Lab：A 股日频多因子研究与手动交易信号

沪深主板、初始空仓 10,000 元、持续入金、每日盘后建议、邮件通知、用户手动交易。没有券商自动下单代码。

当前支持本地研究、BaoStock 真实行情接入与滚动因子验证。SMTP 需单独配置；策略尚未通过真实前向收益验证，已支持冻结计划、原子记账的月度模拟组合；不连接券商。

## 获取代码

```sh
git clone git@github.com:Samuel-math/aquant-lab.git
cd aquant-lab
```

Python 包名与命令保持 `aquant`。行情、实验结果、账户数据库及本地凭据不提交 Git；服务器使用独立部署密钥拉取私有仓库，不复制个人 SSH 私钥。

## 一分钟运行

```sh
python3 -m unittest discover -s tests -v
python3 -m aquant demo
```

不需要 API Key 或第三方运行依赖。演示使用固定随机种子的合成行情和工作日日历，输出明确标记 DEMO，不可用于交易。

运行后查看：

- `artifacts/demo/reports/`：Markdown 日报和完整 JSON 审计记录。
- `artifacts/demo/backtest.json`：净值、成交、未成交原因与指标。
- `artifacts/demo/research.json`：滚动验证、留出集与成本压力测试。
- `artifacts/demo/account.sqlite`：独立演示账本，只有初始入金；回测成交不会污染实际账户。

## 结构

```text
CSV行情 + 历史证券状态 + 交易日历
             ↓
数据校验 → 量价因子排名 → 目标组合 → 对比实际账户 → 每日建议
                                  ↓                  ↓
                             次日10:00回测        本地报告 / SMTP
                                                     ↓
                                                手动成交反馈
```

|模块|职责|
|---|---|
|aquant/data.py|CSV 数据契约、校验、合成数据生成|
|aquant/strategy.py|动量/低波动/流动性百分位评分、排名缓冲、整手与现金约束|
|aquant/account.py|SQLite 追加账本、幂等成交、T+1、入出金|
|aquant/backtest.py|次日10:00近似撮合、费用滑点、涨跌停、部分成交|
|aquant/performance.py|剔除外部资金流的账户净值|
|aquant/research.py|有限候选、滚动验证、留出评估、成本压力测试|
|aquant/report.py|建议日报、版本哈希、TLS 邮件与重复发送防护|
|aquant/operations.py|文件锁、运行状态、日期巡检|
|deploy/|服务器与容器部署模板，未启用|

## 常用命令

```sh
python3 -m aquant --help
python3 -m aquant account --ledger artifacts/demo/account.sqlite
python3 -m aquant backtest --config configs/demo.json --data artifacts/demo/data --out artifacts/backtest.json
python3 -m aquant research --config configs/demo.json --data artifacts/demo/data --out artifacts/research.json
```

费用在 `configs/demo.json` 中为演示假设；真实配置模板中的 null 必须填写，不能当成零。当前权重是基线起点，不是优化结论；每天生成建议不代表每天必须交易。持仓默认 4 只、最多每只 25%、保留 5% 现金，受整手数量限制可持有更少股票。

## 已明确的边界

- 内置 BaoStock 下载器；默认 24 只历史主板研究样本，不代表全市场选股。
- 只支持量价因子；财务公告时点数据、行业中性化、指数基准超额归因尚未实现。
- 回测以次日10:00近似成交，不支持盘口队列；前一分钟成交量限制仅为粗略容量假设，不保证10:00实际成交。
- 持仓公司行动会被检测并停止运行，尚未支持自动送转、配股与退市结算。
- 费用是单配置版本；历史费率变化需另行接入。
- 默认不发送邮件、不启动定时任务、不提升候选策略为正式策略。SMTP 与 Linux systemd 部署须在服务器验证。

[数据格式](docs/DATA.md) · [账户、邮件与部署指南](docs/OPERATIONS.md)

## 短期预测标签

已固定为 T 日盘后计算特征，预测 T+1 10:00买入至 T+2 10:00可卖区间的收益。它不包含买入前的隔夜涨幅，也不要求每天强制清仓。

```sh
python3 -m aquant labels --config configs/demo.json --data artifacts/demo/data --asof 2025-12-31 --out artifacts/demo/labels.json
```

标签用于后续监督学习，当前固定因子策略尚不是预测模型。末尾未成熟样本标记 pending；涨跌停保留执行标记；停牌、公司行动或证券缺失不虚构可交易收益。详见 docs/RESEARCH_OBJECTIVE.md。


## 自动公式因子搜索

```sh
python3 -m aquant demo --root artifacts/ten-am-demo --sessions 300
python3 -m aquant mine --config configs/demo.json --data artifacts/ten-am-demo/data --asof 2026-02-25 --out artifacts/ten-am-demo/mining.json --candidates 48 --shortlist 8 --max-seconds 300
```

`mine` 从多个历史窗口的动量、波动率、成交额变化和隔夜缺口构造公式，支持组合、交互和有界非线性运算；固定随机种子、结构去重、显式搜索预算。训练期确定方向并筛选，验证期按扣成本的组合收益选择，冻结候选后评估独立留出期及成本压力。不会自动提升为正式策略。

输出 mining.json、mining.md、全部候选日志、冻结参数、留出期成交净值、运行状态及实验存档。当前是一轮训练/验证/留出切分，尚未实现多重检验显著性修正和完整嵌套滚动搜索；演示结果仅表示工程流程通过。

现有 `research` 命令仍是三个固定权重的基线比较，新增加的公式搜索入口是 `mine`。

[服务器部署与验收记录](docs/SERVER.md)

## 真实数据与每日滚动验证

可选数据依赖：`python -m pip install -r requirements-data.txt`。核心运行与单元测试仍不需要该依赖。

```sh
python -m aquant sync --root data/real --sample-size 24
python -m aquant rolling --config configs/research-real.json --data data/real/dataset --asof 2026-09-24 --out artifacts/rolling-real --replay-days 20
```

asof应使用 sync 输出的实际最新交易日。sync只长期保存日线、10:00观测与修订记录；[存储策略](docs/STORAGE.md)。每日任务使用 research_worker，不再把固定历史测试集反复称为“新验证”。回放与真正部署后预测分别计数；目前滚动指标是因子诊断，不是可承诺的账户收益。

## 一个月前向模拟

详见[模拟组合与邮件配置](docs/PAPER.md)。真实行情、滚动训练、模拟记账、日报邮件由同一worker顺序执行；模拟与实际账户分离。缺少SMTP应用密码时仅生成日报，不声称邮件已发送。

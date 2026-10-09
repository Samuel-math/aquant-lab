# 早盘模型与分钟量价因子研究 V1

本研究回答“固定24公式是否过于简单、分钟级量价和LightGBM能否改善选股”。它是**独立研究候选**，不修改已启用的`pool188-morning-local-v1-20261009`模拟协议、每日信号、账本或邮件内容。一天或几个交易日的盈亏不足以判定策略优劣。

## 数据与特征时点

目标仍为T日收盘后发信号，T+1交易日09:40买入、T+2交易日09:30开盘价代理卖出的毛价格收益。候选只使用T日收盘时已经可得的字段。原有日线和09:40五分钟线保存于本机独立数据目录。基础研究新增20项特征，包含5/10/20/60日动量、5/20/60日波动、隔夜缺口、当日反转、成交额与成交量变化、非流动性代理，以及09:40价格和成交量相对开盘、收盘、全日成交量与过去20日同期成交量的比例。

扩展研究另从BaoStock的完整五分钟数据**计算后只保存每股每日一行汇总**：开盘前10分钟、开盘半小时、收盘半小时和上午的成交量占比，五分钟量集中度、已实现波动、开盘十分钟涨跌及以五分钟收盘价加权的价格代理。该加权价并非逐笔真实VWAP。只接受48条完整五分钟线的交易日；缺失保留为缺失值，不用未来日期补齐。汇总数据与原模拟行情分开保存。BaoStock的分钟字段定义见[供应商API说明](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)。

跨股票特征按**同一信号日**做百分位排名，预处理不跨越未来日期。没有接入2026-09-28精选名单中的静态财务分数作为历史特征；那会把选池日才知道的财务数据回填到更早的训练日期。名单本身仍带有事后选择偏差，所有选池前结果只能叫历史诊断。

## 模型与验证

候选为Ridge线性回归、固定浅树参数的LightGBM回归，以及原24公式Rank IC模型作为同日对照。两个回归模型对毛收益做±20%截尾，逐交易日用最近126个**标签已经成熟**的信号日重新训练，少于60日不预测。LightGBM固定7叶、最大深度3、叶最小样本100、120棵树、学习率0.03、L2正则10与确定性CPU模式；本轮不在验证集上调参。模型接口与确定性参数见[LightGBM官方文档](https://lightgbm.readthedocs.io/en/stable/Python-API.html)、[参数说明](https://lightgbm.readthedocs.io/en/stable/Parameters.html)。

评价沿用用户指定的严格大于1%、3%、5%标签，输出每日AUC、Top10命中率和全池正例率，同时尝试按当前费用、整手、容量、09:30/09:40执行规则运行连续账户回测。AUC和命中率是毛收益排序诊断，不能代替扣费净收益。持仓跨公司行动而缺少精确现金/股份处理时，回测保留`blocked`，不把缺失区间算作已验证。所有回放预测都标为历史回放，不冒充当日冻结的真实前向预测。

## 运行与产物

```sh
python3.11 -m venv .venv-research
.venv-research/bin/python -m pip install -r requirements-research.txt
.venv-research/bin/python scripts/research_morning_models.py
.venv-research/bin/python scripts/backfill_intraday_summary.py --start 2026-05-01 --end 2026-10-09
.venv-research/bin/python scripts/research_morning_models.py \
  --minute-summary data/research-intraday-summary-v1/daily-summary.csv \
  --output artifacts/research-morning-models-v2
.venv-research/bin/python scripts/run_research_shadow.py
.venv-research/bin/python scripts/install_research_shadow.py --install
```

研究报告、逐日回放分数、训练审计和模型文件默认写入`artifacts/research-morning-models-v1`或指定输出目录，均不提交Git。分钟日汇总在`data/research-intraday-summary-v1`，可断点重试，源接收条数与汇总哈希留在收据。模型、分钟汇总和主模拟分别使用不同目录；研究脚本没有发送邮件或下单入口。

截至本轮，2026-09-28精选底池之前的历史段有选池偏差；选池日至2026-10-09仅有3个标签成熟评价日，且候选在事后运行时回放生成。任何候选都没有达到替换正式信号的证据要求。完整实验结果与失败项在[版本记录](SELECTION_CHANGELOG.md)追加，不根据单一命中率挑选胜者。

同日历史诊断的初步对照如下。62个选池前成熟评价日的指标**带有选池偏差**；选池后3个成熟评价日的扣费回放也仍是事后预测，不能作为真实前向业绩：

| 方法 | 选池前涨超3%日均AUC | 选池前Top10涨超3%命中率 | 选池后短回放净收益 |
| --- | ---: | ---: | ---: |
| 原24公式 | 0.474 | 16.7% | +1.69% |
| Ridge，20特征 | 0.504 | 15.9% | 公司行动阻断 |
| LightGBM，20特征 | 0.518 | 19.7% | -1.32% |
| Ridge，29特征 | 0.485 | 14.3% | 公司行动阻断 |
| LightGBM，29特征 | 0.483 | 17.2% | -1.54% |

选池前所有方法的连续净回测均因持仓公司行动而停止。新增分钟形态后，LightGBM在该历史诊断的AUC和命中率反而下降，因此本轮**不切换正式模型**。研究影子流程在独立目录冻结每日候选分数，只有未来标签成熟后才写评价；这些分数不进入模拟组合或Gmail。

本机23:30运行影子研究、06:30重试，先读取正式数据已完成的日期，再独立更新分钟汇总并冻结20特征与29特征两条研究轨道。每个交易日每轨道只写一份不可改写的预测；错过下一交易日09:00截止时标记为`late_replay`。2026-10-09首份两轨道均为`prospective`，每轨道三种模型各188个分数，尚无成熟评价。完整[审计证据](../research/morning-model-research-20261010.json)保留数据截止、哈希、历史指标和所有回测阻断。

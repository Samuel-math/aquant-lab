# 早盘二分类模型研究 V2

本版按用户要求将**所有学习类研究模型**从连续收益回归改为二分类：LightGBM回归→LightGBM二分类，Ridge回归→带L2正则的逻辑回归。V1源码口径与结果见[历史研究记录](MODEL_RESEARCH_V1.md)；V1的预测、模型和报告目录保留，不回写。本版仍是独立影子研究，不修改已启用的188只V2.1底池、正式24公式Rank IC信号、09:30/09:40执行、账本或邮件。

## 标签与评分

对T日收盘后可得的股票特征，使用T+1交易日09:40五分钟线收盘价作为买入代理、T+2交易日09:30日线开盘价作为卖出代理。已观测毛价格收益为 `r = 卖出代理价 / 买入代理价 - 1`，训练标签 `y = 1{r > 0.01}`；恰好1%和低于1%均为0。仅用在训练日已经成熟且可观测的标签，最近126个信号日、至少60日，逐日重新训练。没有收益截尾、收益幅度回归或事后补齐缺失标签。

两种模型都以未经类别加权的二元交叉熵训练，输出正类 `predict_proba` 作为排序分数。逻辑回归使用同日百分位特征、训练窗口内的中位数缺失填充和标准化，L2参数`C=0.01`；LightGBM保持V1浅树、正则与确定性CPU参数，目标改为`binary`。参数事先固定，不用待评测日期挑参数。该分数是**模型估计的涨超1%概率**，并未做独立概率校准，不应当作可靠的绝对交易概率。单类训练窗口直接报错，不虚构预测。模型接口见[LightGBM分类器](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMClassifier.html)及[scikit-learn逻辑回归](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html)。

涨超1%、3%、5%的逐日AUC、Top10命中率、全池基准率，以及按既有执行规则扣费的连续账户回测继续保留。3%和5%是**独立评测标签**，不是本版训练目标；对它们的AUC不能解释为模型直接估计其发生概率。分类排序若提高AUC，也不自动意味着扣费收益提高。

## 隔离、运行与证据边界

基础20特征和加9项五分钟汇总的29特征各自运行；数据时点、选池前历史偏差和公司行动处理缺口与V1一致。历史回放报告默认进入`artifacts/research-morning-classification-v1`，影子前向预测进入`artifacts/research-shadow-v2`，均与V1分开。原24公式仍只作同日对照，不改其训练规则。研究影子计划复用原23:30、06:30调度入口，不另建并行定时任务；开始前须核对入口已指向V2并且V1旧预测未改动。

```sh
.venv-research/bin/python scripts/research_morning_models.py \
  --evaluation-start 2026-09-28 \
  --output artifacts/research-morning-classification-v1-core
.venv-research/bin/python scripts/research_morning_models.py \
  --evaluation-start 2026-09-28 \
  --minute-summary data/research-intraday-summary-v1/daily-summary.csv \
  --output artifacts/research-morning-classification-v1-minute
.venv-research/bin/python scripts/run_research_shadow.py
```

2026-09-28才确定的188只名单用于更早日期，早期历史段存在选池偏差；选池后截至2026-10-09只有3个成熟评价日，且回放预测不是当时冻结的前向预测。只有V2独立冻结以后、标签随后成熟的日期，才可作为真正前向对照。对照的目的首先是检验二分类能否稳定改善阈值排序及扣费组合结果；尚无足够证据启用正式信号。

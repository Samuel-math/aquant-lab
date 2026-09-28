# 服务器首次验收（2026-09-27）

已经连接用户提供的服务器，并完成一次有限预算的运行。没有创建周期任务、发送邮件或启动自动下单。

## 部署位置

- 代码：`/root/aquant`
- 结果：`/root/autodl-tmp/aquant/runs/ten-am-smoke`
- 状态：结果目录下 `mining.status.json`
- 全部候选：`mining.trials.jsonl`
- 综合报告：`mining.md` / `mining.json`
- 选定候选冻结快照：`mining.frozen.json`
- 留出期成交与净值：`mining.holdout.json`
- 测试及运行日志：`tests.log`、`demo.log`、`mining.log`

登录密码没有保存到代码库、部署配置或脚本。

## 验证结果

Ubuntu 20.04，Python 3.8.10；运行只使用标准库和 CPU，没有安装依赖或使用 GPU。

服务器 35 项测试全部通过。首轮生成 48 个去重公式，按训练期排名相关性筛选 8 个进入验证期，按验证期扣成本的组合收益选择候选，冻结后查看独立留出期及成本压力结果。总流程退出码 0，巡检状态 ok。留出期结果未用于选定公式。

数据为 12 只合成股票、300 个模拟工作日以及独立生成的10:00观测；不是实际交易日历或真实分钟行情。输出状态为 `pipeline_verified_only`，不能据此判断真实投资价值。结果已复制到本地 `artifacts/server-run/`。

## 再次运行

```sh
cd /root/aquant
AQUANT_HOME=/root/aquant AQUANT_RUN_DIR=/root/autodl-tmp/aquant/runs/ten-am-smoke PYTHON=python3 timeout 600 ./deploy/run_mining_smoke.sh
```

该脚本只运行一轮，有候选数和超时限制；相同目录重跑会覆盖同名摘要和日志，完整结果按实验ID存档。重复运行同一留出区间不产生新的独立证据。正式研究应给每次运行设置独立目录，并固定独立留出策略。

研究目标为北京时间 T+2 10:00 价格 / T+1 10:00 价格 - 1。日频盘后特征只使用 T 日及之前信息；未成熟标签不进入训练。缺少10:00观测不使用开盘价代替。真实研究需要按 DATA.md 接入历史分钟行情，并补齐公司行动核算。当前仅验证有限公式搜索，尚未实现多重检验校正和完整嵌套滚动验证。

## 后续巡检

```sh
cd /root/aquant
python3 -m aquant health --state /root/autodl-tmp/aquant/runs/ten-am-smoke/mining.status.json --expected-date 2026-02-25
```

这里的 expected-date 是本次合成数据的最后日期，不是服务器运行日期。正式运行时按真实数据截至日期检查，并同时检查进程退出码和日志。服务器保持开机，首轮研究进程已经结束；未设置自动关机或长期后台研究。

## 真实数据与每日更新（后续升级）

已接入 BaoStock 0.9.4。首批为在2025-01-02历史股票名单上用固定种子选取的24只沪深主板研究样本，不是全市场，也不是按未来收益挑选的股票。日线与真实10:00价格覆盖2025-01-02至2026-09-24，共421个交易日。数据源交易日历标记9月25日休市，因此本次最新交易日为9月24日。

- 核心行情与修订：`/root/autodl-tmp/aquant/real/core`
- 标准行情：`/root/autodl-tmp/aquant/real/dataset`
- 下载状态：`/root/autodl-tmp/aquant/real/sync.status.json`
- 按日期冻结的预测：`/root/autodl-tmp/aquant/rolling-real/predictions`
- 新成熟标签的评价：`/root/autodl-tmp/aquant/rolling-real/evaluations`
- 滚动摘要：`/root/autodl-tmp/aquant/rolling-real/latest.json`

顺序回放最近20个交易日，加上最新信号日共21份预测；其中19份标签已成熟。它们明确标为 historical_replay，不能称为部署后真实前向验证。后续实际交易日盘后运行的预测单独标记为 prospective，T+2后才评分；每日先评价旧预测，再吸收新成熟标签训练下一版，旧预测不可覆盖。当前指标是因子Rank IC和排名组合毛收益诊断，尚非包含公司行动及完整执行成本的实盘组合收益。

真实数据接口安装在 `/root/aquant/.venv`，未修改系统Python。运行依赖见 requirements-data.txt。

独立 Supervisor 配置为 `/root/aquant/deploy/supervisord-research.conf`，不会改动服务器原有服务。研究 worker 按北京时间周一至周五20:00运行，数据源交易日历决定是否有新数据；最多3次尝试，重试至少间隔一小时，每次进程硬超时1800秒。相同最新交易日不重复下载全段数据或覆盖预测。服务器重启后需重新启动此独立Supervisor，目前未配置操作系统开机自启。

```sh
/root/miniconda3/bin/supervisorctl -c /root/aquant/deploy/supervisord-research.conf status
/root/miniconda3/bin/supervisorctl -c /root/aquant/deploy/supervisord-research.conf stop aquant-research
```

容量准入与精简存储见 STORAGE.md；没有保留全量分钟档案，也没有删除已有核心行情。

# 本机188只主板早盘模拟试验

本机试验配置为 [paper-pool188-morning-local-v1.json](../configs/paper-pool188-morning-local-v1.json)，标识 `pool188-morning-local-v1-20261009`。保留V2.1的188只名单、24公式Rank IC选法及费用参数，按09:30开盘代理卖出、09:40五分钟线收盘价代理买入。它从1万元空仓新建独立账本，不接续无法读取的服务器旧账本。实际启用状态以[版本记录](SELECTION_CHANGELOG.md)为准。

运行主机目前为Apple Silicon、10个逻辑CPU、16GiB内存；默认6个计算进程。数据在 `data/local-pool188-morning-v1-20261009`，试验计划、模型、账本在 `artifacts/local-pool188-morning-v1-20261009`，两处均被Git忽略。使用Python 3.11虚拟环境，并通过 `requirements-data.txt` 安装BaoStock及其固定依赖。

首次同步以2025-01-02起的日线和2026-01-01起的指定09:40观测建立数据集，下载可断点续传。只长期保存日线、09:40观测、供应商修订日志及收据；不长期保存所有五分钟线。示例命令中的日期必须替换为最近**已完成且供应商数据就绪**的交易日：

```sh
.venv/bin/python -c "from aquant.baostock_source import sync_baostock; sync_baostock('data/local-pool188-morning-v1-20261009', start='2025-01-02', end='YYYY-MM-DD', sample_size=188, pool_path='configs/pool188-v2-20260928.json', quote_time='09:40:00', minute_start='2026-01-01')"
.venv/bin/python -m aquant.research_worker --once --data-root data/local-pool188-morning-v1-20261009 --out-root artifacts/local-pool188-morning-v1-20261009/rolling-real --sample-size 188 --config configs/paper-pool188-morning-local-v1.json --pool configs/pool188-v2-20260928.json --workers 6 --asof YYYY-MM-DD
.venv/bin/python scripts/audit_local_morning.py
```

首次协议和计划必须在下一交易日09:00之前真实生成，不补做逾期成交。审核要求包括188只底池、在交易股票09:40观测完整、模型/配置/代码哈希相符、模型为真实前向预测、空仓起步和计划及时冻结。未通过则不能启用本机日报。

验收通过后，`scripts/install_local_morning.py --install` 注册本机launchd任务，08:10及20:10、21:10、22:10重试。电脑睡眠、关机或行情供应商延迟时仍可能错过时点；本地巡检会检查数据日期和完整发布状态。安装脚本默认不注册任务，便于先检查生成的plist。任务不会通过SMTP发送邮件；Gmail日报仍由单独的本地巡检去重发送。巡检只在激活后读取本机报告，并保留旧服务器内容与账本不变。

本机重新下载的行情与服务器旧缓存来源相同，但供应商可能修订历史数据，两个数据哈希和模拟收益不能默认相等。尚无服务器账本时不能声称迁移了旧账户或比较两段收益。新试验的真实收益和AUC、Top10命中率须等数据和标签成熟后逐日记录；短观察期无法证明策略有效。

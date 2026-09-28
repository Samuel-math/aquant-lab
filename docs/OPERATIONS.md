# 本地与服务器运行

## 数据与目录

使用 Python 3.8+；运行无第三方依赖。推荐服务器 Python 3.12。直接在项目根目录执行 `python3 -m aquant`，或在虚拟环境 `pip install -e .`。

保持数据、账本、邮件发送状态、报告文件持久化，备份 SQLite 时使用 SQLite backup API 或在任务停止后复制，避免仅复制正在写入的主文件。不要把密码写入仓库。

## 真数据启用步骤

1. 选定真实数据服务，按 DATA.md 转换到 `data/live`，包含下一交易日。
2. 复制 `configs/live.example.json` 为 `configs/live.json`，填写核实后的费用。空费用会报错，不会按零处理。
3. 完成历史回测与候选研究。基线因子未证明具有超额收益。
4. 评估策略后自行设置 `approved_strategy: true`。只有此时 live 模式才生成每日操作建议。
5. 建立账户，填写已有成交与资金流水。初始空仓无需虚构成交。
6. 先生成本地报告核对，再配置 SMTP 并加 `--send`。目前仅支持 TLS SMTP_SSL（通常使用 465 端口）；无自动下单。

示例（日期替换成你的实际日期）：

```sh
python3 -m aquant init --config configs/live.json --ledger data/live-account.sqlite --date 2026-09-28
python3 -m aquant validate --config configs/live.json --data data/live --date 2026-09-28
python3 -m aquant daily --config configs/live.json --data data/live --ledger data/live-account.sqlite --date 2026-09-28
python3 -m aquant health --expected-date 2026-09-28
```

报告中的数量是参考收盘价估算；实际卖出失败时不能使用预计回款买入。每天先核对实际账户，录入遗漏成交后重新生成报告。

## 追加资金与真实成交

保存一个事件 JSON，再执行 `python3 -m aquant record --ledger data/live-account.sqlite --event data/event.json`。

```json
{"id":"deposit-20261008-001","kind":"DEPOSIT","date":"2026-10-08","amount":2000}
```

```json
{"id":"broker-fill-unique-id","kind":"BUY","date":"2026-10-09","symbol":"600000.SH","qty":100,"price":10.5,"fee":5.01}
```

上述证券和价格仅演示格式，不是建议。支持 BUY、SELL、DEPOSIT、WITHDRAW、DIVIDEND。成交费用必须是实际费用；记录的是成交而非委托，所以允许券商实际部分成交数量。事件按日期追加，同日按录入顺序；事件 ID 重复且内容一致时跳过，不一致时报错。不可直接修改已记录事件；错误录入需在备份上按原始成交证据重建账本，目前没有自动冲正接口。

`account --ledger ... --asof YYYY-MM-DD` 查询历史账户。`performance --data ... --ledger ... --date ... --out artifacts/account-performance.json` 计算剔除外部资金流的每日时间加权收益。假设外部入出金在交易日开始发生，非交易日资金流合并到下一交易日；这不是精确的盘中资金加权收益。日报显示净投入与盈亏金额，不将追加资金作为盈利。

## 定时任务与巡检

仓库提供 systemd 服务和 timer 模板，但没有安装或启动任何任务。将仓库放到 `/opt/aquant`，建立 `aquant` 用户并授予 data/artifacts 写权限；配置 `/etc/aquant.env`（建议 root 所有、0600），审核模板路径和时区后安装。每日 19:00、每周日 10:00 是可改的示例时间，需适应数据供应方完成时间。

每日任务只读取已准备好的数据，不负责下载。交易日历必须定期更新，巡检应单独检查覆盖未来至少一周。任务使用独占文件锁；异常写入 failed 状态，命令非零退出，systemd/journal 收集日志。`health` 要求明确的预期交易日，检查成功状态及数据日期，外部巡检系统负责通知、机器宕机检测和数据源可用性告警。配置错误可能发生在状态写入前，监控也必须检查服务退出码。

邮件去重状态保存在 SQLite。SMTP 超时可能已发送，因此状态置为 unknown 并停止自动重发；人工查询邮件服务商日志后再处理状态。已发送相同报告会跳过；配置、代码、数据或账本变化生成新的报告 ID，可能发送更正报告。邮件没有实时投递回执，sent 仅代表 SMTP 接受。

研究任务只跑三个预设权重组合、滚动训练与验证、末端留出评估和成本压力测试，不会自动修改正式策略。持续挖掘需要后续增加候选生成器、因子库和独立未见数据。重复使用留出集会产生选择偏差。研究输出除指定文件外，还按实验 ID 保存在同目录 experiments/ 中；相同代码、数据和配置会得到相同 ID。

## 容器

`docker build -f deploy/Dockerfile -t aquant .`，挂载配置、数据和 artifacts 后运行同样的 CLI。容器 UID 为 10001，宿主目录需可写。此环境未验证 Docker 构建和 systemd 实际部署，模板需在你的服务器验收。

## 首轮服务器验收

已在用户服务器以独立目录完成35项测试和有限预算的合成数据因子搜索。服务器位置、日志、运行命令详见 SERVER.md。该次为手动单次运行，systemd 定时任务仍未安装；原 research 定时模板仍运行固定权重基线，新公式搜索使用 mine 命令。

# 前向模拟观察

## 09:30卖出、09:40买入版本

用户确认新的默认执行方式后，待启用的模型/执行版本为V3，底池仍是V2.1的188只。配置见[paper-pool188-morning-v1.json](../configs/paper-pool188-morning-v1.json)，最新启用状态及审计见[版本记录](SELECTION_CHANGELOG.md)。截至2026-10-07服务器连接被拒，新试验未通过部署验收；旧10:00账户保持独立，不拼接收益。

新试验ID为`pool188-morning-v1-20260930`，代码位于`/root/aquant-morning`，数据根目录为`/root/autodl-tmp/aquant/trials/pool188-morning-v1-20260930`。重新以10000元空仓开始，具体起止日由新协议记录为下一个实际交易日及一个月后同日。

新预测目标为T+1的09:40价格至T+2开盘价格。09:40采用结束于该时点的未复权5分钟线收盘价；09:30用日线未复权开盘价近似，不宣称精确成交。开盘容量上限按前一交易日每分钟均量及原参与率估算，不使用当日未来成交量。前一日代理成交量为零不抹掉可观测的开盘收益标签，但模拟可能无法卖出。

每日先卖出全部旧仓，再买新仓；同股再次入选也重新计算两边费用，退出失败则不叠加同股或超出4只持仓。计划须在09:00前冻结，给09:30操作留出准备时间；晚间及08:40本地巡检读取新计划，过期计划只供复盘。公司行动未适配时暂停，不凭复权因子虚构分红。

部署配置为[supervisord-morning.conf](../deploy/supervisord-morning.conf)，计算24进程，数据API单连接。初次09:40补数仅提取并长期保存2026-01-01以来的指定时点；日线历史保留用于特征。使用独立目录，不能把旧10:00缓存改名为09:40数据。

## V2.1独立试验

用户于2026-09-29要求以188只V2.1底池替换24只工程样本。新试验标识为`pool188-v21-20260929`，运行配置为[paper-pool188-v21.json](../configs/paper-pool188-v21.json)。启用状态与验收记录见[选股条件版本记录](SELECTION_CHANGELOG.md)。旧试验保留原账本及协议，不拼接新旧收益。

新试验代码位于`/root/aquant-v21`，数据根目录为`/root/autodl-tmp/aquant/trials/pool188-v21-20260929`，其下`real`、`rolling-real`、`paper`分别保存行情、预测和账户。`activation.json`记录实际切换时间，`activation-audit.json`记录验收结果。原始V2.1名单配置保留生成时的研究状态，实际是否启用以部署记录为准。

每天仍训练24个候选公式，股票底池则为188只。当前名单由2026-09-28数据筛选，之前的历史只用于训练，不能作为无偏选池回测。前向计划须在执行日09:30前冻结，未赶上截止时间不得补做10:00成交。

日常调度使用[188只部署配置](../deploy/supervisord-pool188.conf)，每次任务上限7200秒。现有Supervisor服务读取的配置路径保持不变，工作目录切换为新代码目录。只读导出器通过`/root/autodl-tmp/aquant/active-monitor-root.txt`读取当前试验；本地邮件去重键含试验标识，新试验计划替代旧计划。

32核CPU服务器配置`--workers 24`：历史特征/标签按交易日并行，候选公式按公式并行，单进程数值库线程数设为1。结果按固定输入顺序合并。数据接口调用、模拟账本及报告发布保持顺序执行；不会为提高CPU占用而扩大候选搜索预算。`--workers 1`可用于串行对照。并行测试和实测记录见版本记录。

## 通用规则与原24只试验记录

模拟本金 10,000 元、空仓开始，不连接券商。首次运行时冻结下一交易日为开始日期，结束日期为一个日历月后同日（不含），结束时按最后交易日收盘市值结算，不强制清仓。该试验固定本金；追加资金应建立独立试验，避免污染本月比较。

每日使用过去最多126个标签已成熟的信号日，训练固定种子17的24个公式候选，按训练Rank IC绝对值选取方向和公式。新成熟标签用于下一次训练，已有预测不覆盖。研究股票池为历史名单固定随机抽样24只沪深主板，并非全市场。收益目标仍是T+1 10:00到T+2 10:00；排名缓冲允许延长持仓，并非每日全部换仓。

计划必须在执行交易日北京时间09:30前冻结，留30分钟反应时间。夜间生成的前一日预测也可成为前向预测。历史回放、过期计划不用于追补交易。每天20:00同步行情后，使用已经冻结的计划和实际10:00观测模拟成交，再用收盘价估值并生成下次计划。邮件是模拟观察通知，不代表实际成交。

执行复用回测撮合：佣金最低额、印花税、过户费、滑点、整手、可用现金、T+1、价格偏离阈值、涨跌停与成交容量约束。费用为配置假设；持仓公司行动或行情缺失时暂停，不虚构收益。每天的成交与账户快照以单个SQLite事务写入，重跑不重复成交。无计划的交易日保留现金/原持仓，明确标记缺失计划。

## 服务器文件

- `/root/autodl-tmp/aquant/paper/protocol.json`：冻结配置、代码哈希与试验起止日。
- `/root/autodl-tmp/aquant/paper/paper.sqlite`：按执行日唯一的计划和每日账户快照。
- `/root/autodl-tmp/aquant/paper/latest.md`：净值、累计费用、回撤及下次模拟计划。
- `/root/autodl-tmp/aquant/rolling-real/cycle.json`：行情、预测、模拟及邮件状态。
- `/root/autodl-tmp/aquant/rolling-real/scheduler.json`：调度心跳及退出码。

本月运行中修改Python代码或策略配置会被拒绝，防止不记录地改变观察规则。新策略应在独立研究目录验证；先审核并建立新试验，再切换，不能重写本月收益。

## 当前通知方式：本地 Gmail 插件

服务器只产出数据和日报；Supervisor设置AQUANT_MAIL_CONFIG指向不存在的mail-disabled.json，禁用服务器SMTP。无需在服务器配置Gmail密码。

本地Codex巡检每天北京时间08:40、20:40、21:40、22:40运行scripts/local_monitor.py，通过受限SSH密钥只读取固定报告。服务器强制执行scripts/export_monitor.py的部署副本，禁止客户端执行任意命令或端口转发。私钥只在本地~/.ssh/aquant_monitor_ed25519，未进入Git。

本地快照、待发邮件和发送去重账本位于artifacts/local-monitor。新日报通过已连接Gmail插件发送；心跳中断、过期行情、发布不一致时只发异常通知。发送前预留、成功后记入Gmail消息ID；状态不明不自动重发。已过通知截止时间的计划标记仅供复盘。

电脑必须开机、联网且Codex保持运行，Gmail连接需要持续有效。本地关闭时不巡检，但服务器研究任务仍独立运行。观察完成的最终日报发送后暂停巡检。

## 备用方式：Gmail 私密配置（当前不启用）

配置位于 `/root/.config/aquant/mail.json`，权限600；不提交Git，不将密码放进命令参数。先启用Google两步验证，再创建应用专用密码（不是登录密码）：https://support.google.com/mail/answer/185833 。默认采用smtp.gmail.com、465、TLS证书校验。

登录服务器后运行：

```sh
cd /root/aquant
.venv/bin/python scripts/configure_mail.py
.venv/bin/python scripts/test_mail.py
```

第一条命令隐藏输入并保存应用密码，第二条发送一次测试邮件。发送结果不明确时记录unknown并停止自动重发，需查收及人工核对。缺少密码时日报仍生成，但邮件状态是not_configured，不能声称通知已送达。Gmail账号可能不支持应用密码，此时需更换认证方案。

## 启停与更新

```sh
/root/miniconda3/bin/supervisord -c /root/aquant/deploy/supervisord-research.conf
/root/miniconda3/bin/supervisorctl -c /root/aquant/deploy/supervisord-research.conf status
```

独立Supervisor每天北京时间20:00运行，失败最多三次、每次间隔一小时。容器重启后需要重新启动Supervisor；暂未接入平台开机启动机制。服务器代码可`git pull --ff-only`，但本月模拟期间不要直接更新运行代码（见冻结约束），研究改动应使用独立检出目录。

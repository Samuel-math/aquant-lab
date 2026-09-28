# 前向模拟观察

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

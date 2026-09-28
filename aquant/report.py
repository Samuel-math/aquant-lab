import datetime as dt
import json
import os
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage
from pathlib import Path
from .core import ValidationError, digest, write_json, code_hash
from .strategy import propose


def build(data, cfg, ledger, date):
    data.validate_mode(cfg)
    if cfg["mode"] == "live" and not cfg.get("approved_strategy"):
        raise ValidationError("实盘建议需先评估并设置 approved_strategy=true")
    events = ledger.events(date)
    if not events:
        raise ValidationError("账户未初始化")
    account = ledger.account(date)
    rows = data.snapshot(date)
    if set(account.positions) - set(rows):
        raise ValidationError("持仓行情缺失，停止生成建议")
    # Corporate actions require explicit reconciliation before resuming signals.
    for symbol, p in account.positions.items():
        first = min(l["date"] for l in p["lots"])
        hist = [r for (d, s), r in data.rows.items() if s == symbol and first <= d <= date]
        if len(set(r["adj_factor"] for r in hist)) > 1:
            raise ValidationError("持仓存在公司行动，请完成账本适配与对账后继续: " + symbol)
    result = propose(data, date, cfg, account)
    source_hash = code_hash()
    result.update({"mode": cfg["mode"], "strategy": cfg["strategy"]["name"], "config": cfg, "config_hash": digest(cfg), "data_hash": data.version, "code_hash": source_hash, "account_hash": digest(events), "last_account_event": account.last_event, "net_deposits": account.net_deposits, "profit_amount": round(result["equity"] - account.net_deposits, 2), "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    result["report_id"] = digest({k: result[k] for k in ("date", "config_hash", "data_hash", "code_hash", "account_hash")})[:20]
    return result


def markdown(r):
    lines = ["# " + ("【演示数据，不用于交易】" if r["mode"] == "demo" else "") + "A 股每日建议", "", "数据日期：%s；适用交易日：%s" % (r["date"], r["execution_date"]), "", "策略：%s；报告编号：%s" % (r["strategy"], r["report_id"]), "", "账户资产 %.2f 元｜可用现金 %.2f 元｜累计净投入 %.2f 元｜盈亏金额 %.2f 元" % (r["equity"], r["cash"], r["net_deposits"], r["profit_amount"]), "", "账本最后事件：%s。请核对是否有未录入成交或入金。" % r["last_account_event"], "", "## 操作清单", "", "|方向|股票|数量|参考收盘价|估计费用|原因|依赖卖出回款|", "|---|---|---:|---:|---:|---|---|"]
    for o in r["orders"]:
        lines.append("|%s|%s %s|%s|%.2f|%.2f|%s|%s|" % (o["side"], o["symbol"], o["name"], o["qty"], o["reference_price"], o["estimated_fee"], o["reason"], "是" if o["conditional_on_sells"] else "否"))
    if not r["orders"]:
        lines += ["", "今日无调仓建议。"]
    lines += ["", "次日北京时间10:00附近操作，价格相对参考价偏离超过 %.1f%% 时暂停该建议并重新评估；停牌或无法成交时保留原持仓。数量仅为估算，买入须按实际可用现金复核。" % (100 * r["max_price_deviation"]), "", "建议不会修改账户，实际成交后请录入数量、价格和费用。", "", "## 暂不可执行", ""] + (r["blocked"] or ["无"])
    lines += ["", "研究预测目标：本日收盘后，预测下一交易日10:00至再下一交易日10:00的收益。当前信号仍由固定因子评分生成，尚非该收益的模型预测；预测周期不代表到期必须卖出。"]
    lines += ["", "## 持仓与目标", "", "|股票|实际数量|目标数量|参考市值|当前仓位|", "|---|---:|---:|---:|---:|"]
    for symbol in sorted(set(r["positions"]) | set(r["targets"])):
        qty = r["positions"].get(symbol, {}).get("qty", 0)
        value = qty * r["position_prices"].get(symbol, 0)
        lines.append("|%s|%s|%s|%.2f|%.1f%%|" % (symbol, qty, r["targets"].get(symbol, 0), value, value / r["equity"] * 100 if r["equity"] else 0))
    lines += ["", "## 候选排名", "", "|排名|股票|得分|动量|负波动率|流动性对数|", "|---:|---|---:|---:|---:|---:|"]
    for x in r["rankings"]:
        lines.append("|%s|%s|%.4f|%.4f|%.4f|%.4f|" % (x["rank"], x["symbol"], x["score"], x["momentum"], x["low_volatility"], x["liquidity"]))
    lines += ["", "费用口径：" + r["config"]["fees"]["label"], "", "数据哈希：" + r["data_hash"], "代码哈希：" + r["code_hash"]]
    return "\n".join(lines) + "\n"


def save_report(report, directory):
    path = Path(directory) / (report["date"] + "-" + report["report_id"])
    write_json(path.with_suffix(".json"), report)
    path.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return path.with_suffix(".md")


def send_mail(report, state_path):
    required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO")
    if any(not os.environ.get(k) for k in required):
        raise ValidationError("请通过环境变量配置 SMTP_HOST/PORT/USER/PASSWORD 和 MAIL_FROM/TO")
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(state_path))
    db.execute("CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, status TEXT)")
    # Reserve before sending: ambiguous SMTP failure must be reviewed, never blindly retried.
    key = digest([report["report_id"], os.environ["MAIL_TO"]])
    try:
        db.execute("INSERT INTO deliveries VALUES (?, 'pending')", (key,)); db.commit()
    except sqlite3.IntegrityError:
        state = db.execute("SELECT status FROM deliveries WHERE id=?", (key,)).fetchone()[0]
        if state != "sent":
            raise ValidationError("此前邮件发送状态为 " + state + "，需人工核查；禁止盲目重发")
        return "该报告已发送，未重复发送"
    message = EmailMessage()
    message["From"] = os.environ["MAIL_FROM"]; message["To"] = os.environ["MAIL_TO"]
    message["Subject"] = ("[DEMO] " if report["mode"] == "demo" else "") + "A股信号 " + report["date"]
    message["Message-ID"] = "<%s@aquant.local>" % key
    message.set_content(markdown(report))
    try:
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)
        db.execute("UPDATE deliveries SET status='sent' WHERE id=?", (key,)); db.commit()
    except Exception:
        db.execute("UPDATE deliveries SET status='unknown' WHERE id=?", (key,)); db.commit(); raise
    return "邮件已发送"

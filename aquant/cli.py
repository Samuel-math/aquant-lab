import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from .account import Ledger
from .backtest import run
from .core import ValidationError, read_config, write_json, code_hash
from .data import CSVData, generate_demo
from .operations import health, lock, status
from .report import build, save_report, send_mail
from .research import research
from .performance import account_performance
from .targets import build_dataset
from .mining import mine
from .rolling import update_rolling


def parser():
    p = argparse.ArgumentParser(description="A 股日频研究与手动执行建议；无自动下单功能")
    sub = p.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="生成独立的合成数据并运行完整示例")
    demo.add_argument("--root", default="artifacts/demo")
    demo.add_argument("--config", default="configs/demo.json")
    demo.add_argument("--sessions", type=int, default=260)
    init = sub.add_parser("init", help="建立账户初始入金")
    init.add_argument("--ledger", required=True); init.add_argument("--date", required=True)
    init.add_argument("--config", required=True)
    event = sub.add_parser("record", help="导入一条真实成交/资金事件 JSON，按日期追加")
    event.add_argument("--ledger", required=True); event.add_argument("--event", required=True)
    show = sub.add_parser("account", help="查询账本")
    show.add_argument("--ledger", required=True); show.add_argument("--asof")
    perf = sub.add_parser("performance", help="剔除外部入出金影响的账户绩效")
    perf.add_argument("--data", required=True); perf.add_argument("--ledger", required=True)
    perf.add_argument("--date", required=True); perf.add_argument("--out", required=True)
    labels = sub.add_parser("labels", help="生成 T+1 10:00至 T+2 10:00研究标签，包含未成熟样本")
    labels.add_argument("--config", required=True); labels.add_argument("--data", required=True)
    labels.add_argument("--asof", required=True); labels.add_argument("--out", required=True)
    mining = sub.add_parser("mine", help="有预算限制的公式因子搜索；不自动提升策略")
    mining.add_argument("--data", required=True); mining.add_argument("--config", required=True)
    mining.add_argument("--asof", required=True); mining.add_argument("--out", required=True)
    mining.add_argument("--candidates", type=int, default=48); mining.add_argument("--shortlist", type=int, default=8)
    mining.add_argument("--seed", type=int, default=17); mining.add_argument("--max-seconds", type=int, default=300)
    sync = sub.add_parser("sync", help="拉取真实日线与10:00观测，原始全量分钟线不永久保存")
    sync.add_argument("--root", required=True); sync.add_argument("--start", default="2025-01-02")
    sync.add_argument("--end"); sync.add_argument("--sample-size", type=int, default=24)
    rolling = sub.add_parser("rolling", help="每日先验证已保存预测，再吸收成熟标签训练")
    rolling.add_argument("--config", required=True); rolling.add_argument("--data", required=True)
    rolling.add_argument("--asof", required=True); rolling.add_argument("--out", required=True)
    rolling.add_argument("--replay-days", type=int, default=0); rolling.add_argument("--candidates", type=int, default=24)
    for name in ("validate", "report", "daily", "backtest", "research"):
        command = sub.add_parser(name)
        command.add_argument("--data", required=True); command.add_argument("--config", required=True)
        if name in ("report", "daily"):
            command.add_argument("--ledger", required=True)
            command.add_argument("--date", required=True, help="明确指定预期数据日期，避免自动退回过期数据")
            command.add_argument("--out", default="artifacts/reports")
            command.add_argument("--send", action="store_true", help="通过 SMTP 发送；默认仅保存文件")
            command.add_argument("--state", default="artifacts/daily-status.json")
            command.add_argument("--mail-state", default="artifacts/mail.sqlite")
        elif name == "validate":
            command.add_argument("--date", required=True)
        else:
            command.add_argument("--out", required=True)
            if name == "backtest":
                command.add_argument("--start"); command.add_argument("--end")
    h = sub.add_parser("health")
    h.add_argument("--state", default="artifacts/daily-status.json"); h.add_argument("--expected-date", required=True)
    return p


def execute(args):
    if args.command == "health":
        return health(args.state, args.expected_date)
    if args.command == "record":
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        added = Ledger(args.ledger).append(event)
        return {"recorded": added, "event_id": event["id"]}
    if args.command == "account":
        a = Ledger(args.ledger).account(args.asof)
        return {"cash": a.cash, "positions": a.positions, "net_deposits": a.net_deposits, "last_event": a.last_event}
    if args.command == "performance":
        result = account_performance(CSVData(args.data), Ledger(args.ledger), args.date)
        write_json(args.out, result)
        return {k: v for k, v in result.items() if k != "curve"}
    if args.command == "sync":
        from .baostock_source import sync_baostock
        return sync_baostock(args.root, args.start, args.end, args.sample_size)
    cfg = read_config(args.config)
    if args.command == "init":
        ledger = Ledger(args.ledger)
        if ledger.events():
            raise ValidationError("账户已有事件，不可重复初始化；新增资金请使用 record")
        ledger.append({"id": "initial-deposit", "date": args.date, "kind": "DEPOSIT", "amount": cfg["initial_cash"]})
        return {"initialized": args.ledger}
    if args.command == "demo":
        root = Path(args.root)
        with lock(root / "run.lock"):
            date = generate_demo(root / "data", sessions=args.sessions)
            data = CSVData(root / "data")
            ledger = Ledger(root / "account.sqlite")
            ledger.append({"id": "initial-deposit", "date": data.calendar[0], "kind": "DEPOSIT", "amount": cfg["initial_cash"]})
            report = build(data, cfg, ledger, date)
            path = save_report(report, root / "reports")
            bt = run(data, cfg)
            bt.update({"mode": cfg["mode"], "config": cfg, "data_hash": data.version, "code_hash": code_hash()})
            write_json(root / "backtest.json", bt)
            result = research(data, cfg, root / "research.json")
            status(root / "status.json", date, "ok", report=str(path))
            return {"mode": "DEMO_ONLY", "report": str(path), "backtest": bt["metrics"], "research_status": result["status"]}
    if args.command in ("daily", "report"):
        with lock(str(args.state) + ".lock"):
            try:
                data = CSVData(args.data); data.validate_mode(cfg)
                report = build(data, cfg, Ledger(args.ledger), args.date)
                path = save_report(report, args.out)
                mail = send_mail(report, args.mail_state) if args.send else "未发送，仅保存本地报告"
                status(args.state, args.date, "ok", report=str(path), mail=mail)
                return {"report": str(path), "mail": mail}
            except Exception as e:
                status(args.state, args.date, "failed", error=str(e)); raise
    data = CSVData(args.data); data.validate_mode(cfg)
    if args.command == "rolling":
        return update_rolling(data, cfg, args.asof, args.out, args.replay_days, args.candidates)
    if args.command == "mine":
        result = mine(data, cfg, args.asof, args.out, args.candidates, args.shortlist, args.seed, args.max_seconds)
        return {"output": args.out, "status": result["status"], "experiment_id": result["experiment_id"], "holdout_metrics": result["holdout_metrics"], "runtime_seconds": result["runtime_seconds"]}
    if args.command == "labels":
        result = build_dataset(data, cfg, args.asof)
        write_json(args.out, result)
        return {"output": args.out, "target": result["target"], "status_counts": result["status_counts"]}
    if args.command == "validate":
        return {"date": args.date, "symbols": len(data.snapshot(args.date)), "next_session": data.next_day(args.date), "data_hash": data.version}
    if args.command == "backtest":
        result = run(data, cfg, args.start, args.end)
        result.update({"mode": cfg["mode"], "config": cfg, "data_hash": data.version, "code_hash": code_hash()})
        write_json(args.out, result); return result["metrics"]
    if args.command == "research":
        result = research(data, cfg, args.out)
        return {"output": args.out, "status": result["status"], "experiment_id": result["experiment_id"]}


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValidationError, ValueError, KeyError, OSError) as e:
        print("ERROR: " + str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

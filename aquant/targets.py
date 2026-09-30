"""Research-only forward labels; never passed to live signal generation."""
from .core import ValidationError, code_hash, digest
from .strategy import rank

TARGET = {
    "name": "next_1000_to_following_1000",
    "signal_time": "after_close",
    "entry_session_offset": 1,
    "exit_session_offset": 2,
    "price_field": "intraday.price",
    "execution_time": "10:00:00",
    "timezone": "Asia/Shanghai",
    "price_convention": "last_trade_at_or_before_1000",
}

MORNING_TARGET = {
    'name':'next_0940_to_following_open', 'signal_time':'after_close',
    'entry_session_offset':1, 'exit_session_offset':2,
    'entry_time':'09:40:00', 'exit_time':'09:30:00', 'timezone':'Asia/Shanghai',
    'entry_price_convention':'unadjusted_5min_close_ending_0940',
    'exit_price_convention':'daily_open_proxy_not_guaranteed_0930_fill',
}


def morning(cfg):
    return cfg.get('prediction_target') == MORNING_TARGET


def execution_quote(data, cfg, date, symbol, side):
    if not morning(cfg) or side == 'BUY':
        return data.intraday.get((date, symbol))
    row = data.rows.get((date, symbol))
    if row is None: return None
    previous = [d for d in data.calendar if d < date]
    prev = data.rows.get((previous[-1],symbol)) if previous else None
    # No opening auction volume is available. This is an explicitly declared
    # lagged liquidity proxy, never today's daily or 09:35/09:40 future volume.
    volume = prev['volume']/240 if prev else 0
    return dict(timestamp=date+'T09:30:00+08:00', price=row['open'], volume=volume,
                tradable=not row['suspended'], volume_window_minutes=1,
                price_basis='daily_open_proxy', volume_basis='previous_session_average_minute_proxy')


def validate_target(cfg):
    if cfg.get('prediction_target') not in (TARGET, MORNING_TARGET):
        raise ValidationError('未知预测目标，不能静默混用执行时间')
    if morning(cfg) and (not cfg['strategy'].get('liquidate_daily') or cfg.get('plan_deadline')!='09:00:00'):
        raise ValidationError('09:40至次日开盘目标要求每日先卖后买及09:00计划截止')


def forward_label(data, symbol, signal_date, asof, cfg=None):
    """Gross price label plus execution flags, retained even if future untradable.

    Daily files are available after close, so availability is conservatively
    recorded as the exit session's close. Do not use a label before then.
    """
    if signal_date not in data.calendar or signal_date > asof:
        raise ValidationError("信号日期必须在交易日历内且不晚于 asof")
    index = data.calendar.index(signal_date)
    entry_date = data.calendar[index + 1] if index + 1 < len(data.calendar) else None
    exit_date = data.calendar[index + 2] if index + 2 < len(data.calendar) else None
    result = {"signal_date": signal_date, "symbol": symbol, "entry_date": entry_date,
              "exit_date": exit_date, "label_available_date": exit_date,
              "label_available_time": "after_close", "gross_return": None,
              "status": "pending", "execution_flags": []}
    if not entry_date or not exit_date or exit_date > asof:
        return result
    entry = data.snapshot(entry_date).get(symbol)
    end = data.snapshot(exit_date).get(symbol)
    if entry is None or end is None:
        result["status"] = "missing_security"
        result["execution_flags"] = ["证券在未来快照中缺失，保留记录，不按零收益填充"]
        return result
    cfg = cfg or {'prediction_target':TARGET}
    entry_quote = execution_quote(data,cfg,entry_date,symbol,'BUY')
    exit_quote = execution_quote(data,cfg,exit_date,symbol,'SELL')
    if entry_quote is None or exit_quote is None:
        result["status"] = "missing_execution_quote"
        return result
    result["entry_time"] = entry_quote["timestamp"]
    result["exit_time"] = exit_quote["timestamp"]
    flags = result["execution_flags"]
    if entry["suspended"] or not entry_quote["tradable"] or entry_quote["volume"] <= 0:
        flags.append("entry_untradable")
    if morning(cfg) and exit_quote['volume']<=0:
        flags.append('exit_capacity_proxy_zero')
    if end["suspended"] or not exit_quote["tradable"] or (not morning(cfg) and exit_quote["volume"] <= 0):
        flags.append("exit_untradable")
    if entry["limit_up"] and entry_quote["price"] >= entry["limit_up"]:
        flags.append("entry_limit_up")
    if end["limit_down"] and exit_quote["price"] <= end["limit_down"]:
        flags.append("exit_limit_down")
    if entry["risk_warning"] or entry["delisting"]:
        flags.append("entry_status_changed")
    if entry["adj_factor"] != end["adj_factor"]:
        result["status"] = "corporate_action_unsupported"
        return result
    if entry["suspended"] or end["suspended"] or not entry_quote["tradable"] or entry_quote["volume"] <= 0 or not exit_quote["tradable"] or (not morning(cfg) and exit_quote["volume"] <= 0):
        result["status"] = "unobservable_execution_price"
        return result
    result["gross_return"] = exit_quote["price"] / entry_quote["price"] - 1
    result["status"] = "observed"
    return result


def training_rows(rows, asof, validation_start):
    """For chronological fitting: purge labels ending on/after validation start.

    Execution flags do not filter observations, avoiding future-tradability
    selection. Missing-label coverage must be reported separately by callers.
    """
    return [r for r in rows if r["status"] == "observed"
            and r["label_available_date"] <= asof
            and r["signal_date"] < validation_start
            and r["exit_date"] < validation_start]


def build_dataset(data, cfg, asof):
    validate_target(cfg)
    data.validate_mode(cfg)
    data.snapshot(asof)
    observed_dates = sorted({d for d, _ in data.rows})
    days = [d for d in data.calendar if observed_dates[0] <= d <= asof]
    rows = []
    for date in days:
        # Eligibility and features are evaluated at T only.
        for item in rank(data, date, cfg):
            row = forward_label(data, item["symbol"], date, asof,cfg)
            row["features"] = {k: item[k] for k in ("momentum", "low_volatility", "liquidity")}
            rows.append(row)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"target": cfg["prediction_target"], "mode": cfg["mode"], "asof": asof,
            "config_hash": digest(cfg), "data_hash": data.version, "code_hash": code_hash(),
            "status_counts": counts, "rows": rows,
            "limitations": ["标签是所配置进出时点的毛价格收益，不是成交或净收益保证",
                            "成本须结合仓位数量、费用、滑点在策略回测中计算",
                            "涨跌停与无法交易记录保留，不按未来可交易性筛掉困难样本",
                            "公司行动、未来证券缺失、停牌记录没有可用标签，须单独审计覆盖率",
                            "尚未接入监督学习训练器；当前研究仍为固定因子权重比较"]}

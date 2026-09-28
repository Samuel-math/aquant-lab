"""Daily time-weighted account return, external flows treated at start of day."""
from .account import Account
from .core import ValidationError


def account_performance(data, ledger, asof):
    events = ledger.events(asof)
    if not events:
        raise ValidationError("账户无资金记录")
    start = events[0]["date"]
    days = [d for d in data.calendar if start <= d <= asof]
    if not days or days[-1] != asof:
        raise ValidationError("绩效截止日期必须为有行情的交易日")
    account = Account(); index = 0; previous = 0.0; nav = 1.0; curve = []
    prior_factors = {}
    for date in days:
        flow = 0.0
        while index < len(events) and events[index]["date"] <= date:
            event = events[index]; index += 1
            account.apply(event)
            if event["kind"] in ("DEPOSIT", "WITHDRAW"):
                flow += event["amount"] * (1 if event["kind"] == "DEPOSIT" else -1)
        rows = data.snapshot(date)
        if set(account.positions) - set(rows):
            raise ValidationError("账户绩效缺少持仓行情")
        for symbol in account.positions:
            factor = rows[symbol]["adj_factor"]
            if symbol in prior_factors and factor != prior_factors[symbol]:
                raise ValidationError("绩效涉及公司行动，须先完成适配")
        equity = account.cash + sum(p["qty"] * rows[s]["close"] for s, p in account.positions.items())
        denominator = previous + flow
        if denominator <= 0:
            raise ValidationError("现金流调整后净资产非正，无法计算时间加权收益")
        daily_return = equity / denominator - 1
        nav *= 1 + daily_return
        curve.append({"date": date, "equity": round(equity, 4), "external_flow": flow, "daily_return": daily_return, "unit_nav": nav})
        previous = equity
        prior_factors = {s: rows[s]["adj_factor"] for s in account.positions}
    return {"net_deposits": account.net_deposits, "equity": round(previous, 2), "profit_amount": round(previous - account.net_deposits, 2), "time_weighted_return": nav - 1, "flow_convention": "external deposits/withdrawals assumed at start of trading day; non-trading-day flows rolled forward", "curve": curve}

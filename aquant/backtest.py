import math
import statistics
from .account import Account
from .core import ValidationError, fee
from .strategy import propose


def run(data, cfg, start=None, end=None, signal_ranker=None):
    data.validate_mode(cfg)
    if not data.intraday:
        raise ValidationError("缺少10:00执行观测，禁止用开盘价回退")
    observed = sorted(set(d for d, _ in data.rows))
    first, last = start or observed[0], end or observed[-1]
    if first < observed[0] or last > observed[-1] or first > last:
        raise ValidationError("回测请求超出行情覆盖区间或起止日期非法")
    days = [d for d in data.calendar if first <= d <= last]
    if len(days) < 2:
        raise ValidationError("回测区间至少需要两个交易日")
    account = Account(cfg["initial_cash"])
    pending, curve, trades, rejected = [], [], [], []
    previous = {}
    for date in days:
        rows = data.snapshot(date)
        missing = set(account.positions) - set(rows)
        if missing:
            raise ValidationError("持仓缺失行情: " + str(missing))
        # Never silently trade adjusted prices or invent corporate-action cashflows.
        for symbol in account.positions:
            if symbol in previous and rows[symbol]["adj_factor"] != previous[symbol]["adj_factor"]:
                raise ValidationError("持仓出现公司行动，当前回测需公司行动适配器: " + symbol + " " + date)
        for order in pending:
            symbol, side = order["symbol"], order["side"]
            row = rows.get(symbol)
            quote = data.intraday.get((date, symbol))
            if row is not None and not row["suspended"] and quote is None:
                raise ValidationError("缺少10:00执行观测: " + date + " " + symbol)
            reason = None
            if row is None or row["suspended"] or quote is None or not quote["tradable"] or quote["volume"] <= 0:
                reason = "停牌或缺少可交易行情"
            elif abs(quote["price"] / order["reference_price"] - 1) > cfg["strategy"]["max_price_deviation"]:
                reason = "10:00价格超过偏离阈值"
            elif (side == "BUY" and row["limit_up"] and quote["price"] >= row["limit_up"]) or (side == "SELL" and row["limit_down"] and quote["price"] <= row["limit_down"]):
                reason = "10:00触及方向性涨跌停，保守不成交"
            elif side == "BUY" and (row["risk_warning"] or row["delisting"]):
                reason = "次日证券状态变化"
            if reason:
                rejected.append({"date": date, "symbol": symbol, "reason": reason}); continue
            price = round(quote["price"] * (1 + (1 if side == "BUY" else -1) * cfg["fees"]["slippage_bps"] / 10000), 2)
            if row["limit_up"]: price = min(price, row["limit_up"])
            if row["limit_down"]: price = max(price, row["limit_down"])
            lot = row["buy_lot"]
            qty = min(order["qty"], int(quote["volume"] / quote.get("volume_window_minutes", 1) * cfg["strategy"]["max_participation"] / lot) * lot)
            if side == "BUY":
                while qty > 0 and qty * price + fee(cfg, side, qty * price) > account.cash:
                    qty -= lot
            else:
                qty = min(qty, sum(l["qty"] for l in account.positions.get(symbol, {}).get("lots", []) if l["date"] < date))
            if qty <= 0:
                rejected.append({"date": date, "symbol": symbol, "reason": "现金/可卖数量/成交量不足"}); continue
            event = {"kind": side, "date": date, "symbol": symbol, "qty": qty, "price": price, "fee": fee(cfg, side, qty * price), "execution_time": "10:00:00+08:00"}
            account.apply(event); trades.append(event)
            if qty < order["qty"]:
                rejected.append({"date": date, "symbol": symbol, "reason": "部分成交，剩余数量取消", "unfilled_qty": order["qty"] - qty})
        equity = account.cash + sum(p["qty"] * rows[s]["close"] for s, p in account.positions.items())
        curve.append({"date": date, "equity": round(equity, 4), "cash": round(account.cash, 4)})
        pending = propose(data, date, cfg, account, rankings=signal_ranker(date) if signal_ranker else None)["orders"] if date != days[-1] else []
        previous = rows
    values = [cfg["initial_cash"]] + [r["equity"] for r in curve]
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    peak, drawdown = values[0], 0
    for value in values:
        peak = max(peak, value); drawdown = min(drawdown, value / peak - 1)
    vol = statistics.pstdev(returns)
    return {"metrics": {"total_return": values[-1] / values[0] - 1, "annualized_return": (values[-1] / values[0]) ** (252 / len(days)) - 1, "max_drawdown": drawdown, "sharpe_zero_rate": statistics.mean(returns) / vol * math.sqrt(252) if vol else 0, "trade_count": len(trades), "fees_paid": round(sum(t["fee"] for t in trades), 2)}, "curve": curve, "trades": trades, "unfilled": rejected, "limitations": ["次日10:00观测价近似成交，不还原排队；使用观测窗口的每分钟均量作容量近似，不保证可成交", "不支持持仓期间除权除息回测，检测后停止", "未计利息，无基准超额归因；短样本年化值不代表预期收益"]}

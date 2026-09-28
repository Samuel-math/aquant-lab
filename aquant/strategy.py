import datetime as dt
import math
import statistics
from .core import fee


def rank(data, date, cfg):
    settings = cfg["strategy"]
    raw = []
    for symbol, row in data.snapshot(date).items():
        if row["board"] not in ("SSE_MAIN", "SZSE_MAIN") or row["suspended"] or row["risk_warning"] or row["delisting"]:
            continue
        if (dt.date.fromisoformat(date) - dt.date.fromisoformat(row["listed_date"])).days < settings["min_listing_days"]:
            continue
        hist = data.history(symbol, date, settings["lookback"] + 1)
        if not hist or any(r["suspended"] for r in hist):
            continue
        if statistics.mean(r["amount"] for r in hist) < settings["min_amount"]:
            continue
        prices = [r["close"] * r["adj_factor"] for r in hist]
        returns = [b / a - 1 for a, b in zip(prices, prices[1:])]
        raw.append({"symbol": symbol, "name": row["name"], "price": row["close"], "momentum": prices[-1] / prices[0] - 1, "low_volatility": -statistics.pstdev(returns), "liquidity": math.log(statistics.mean(r["amount"] for r in hist)), "score": 0.0})
    # Cross-sectional percentile with tied observations assigned their average rank.
    for factor, weight in settings["weights"].items():
        vals = sorted(r[factor] for r in raw)
        for r in raw:
            indices = [i for i, v in enumerate(vals) if v == r[factor]]
            percentile = statistics.mean(indices) / max(1, len(vals) - 1)
            r["score"] += percentile * weight / sum(settings["weights"].values())
    raw.sort(key=lambda r: (-r["score"], r["symbol"]))
    for n, r in enumerate(raw, 1):
        r["rank"] = n
    return raw


def propose(data, date, cfg, account, rankings=None):
    rows = data.snapshot(date)
    rankings = rank(data, date, cfg) if rankings is None else rankings
    s = cfg["strategy"]
    equity = account.cash + sum(p["qty"] * rows[sym]["close"] for sym, p in account.positions.items())
    weight = min(s["max_weight"], (1 - s["cash_reserve"]) / s["holdings"])
    budget = equity * weight
    eligible = {r["symbol"]: r for r in rankings}
    selected = [r["symbol"] for r in rankings if r["symbol"] in account.positions and r["rank"] <= s["exit_rank"]][:s["holdings"]]
    for r in rankings:
        symbol = r["symbol"]
        minimum = rows[symbol]["buy_lot"] * rows[symbol]["close"]
        if len(selected) < s["holdings"] and symbol not in selected and minimum + fee(cfg, "BUY", minimum) <= budget:
            selected.append(symbol)
    targets = {}
    for symbol in selected:
        row = rows[symbol]; lot = row["buy_lot"]
        qty = int(budget / row["close"] / lot) * lot
        while qty and qty * row["close"] + fee(cfg, "BUY", qty * row["close"]) > budget:
            qty -= lot
        targets[symbol] = qty
    orders, blocked = [], []
    available = account.cash
    # Sells precede buys, and projected proceeds are explicitly conditional.
    for symbol in sorted(set(account.positions) | set(targets)):
        row = rows[symbol]
        current = account.positions.get(symbol, {}).get("qty", 0)
        delta = targets.get(symbol, 0) - current
        if delta >= 0:
            continue
        qty = -delta
        if row["suspended"]:
            blocked.append(symbol + ": 停牌，退出建议暂不可执行")
            continue
        if targets.get(symbol, 0) and qty * row["close"] < s["min_order_value"]:
            continue
        value = qty * row["close"]
        orders.append({"side": "SELL", "symbol": symbol, "name": row["name"], "qty": qty, "reference_price": row["close"], "estimated_fee": fee(cfg, "SELL", value), "reason": "退出选股范围" if symbol not in selected else "调整至目标仓位", "conditional_on_sells": False})
        available += value - fee(cfg, "SELL", value)
    for symbol in selected:
        row = rows[symbol]; lot = row["buy_lot"]
        qty = targets[symbol] - account.positions.get(symbol, {}).get("qty", 0)
        spendable = max(0, available - equity * s["cash_reserve"])
        while qty > 0 and qty * row["close"] + fee(cfg, "BUY", qty * row["close"]) > spendable:
            qty -= lot
        if qty <= 0 or qty * row["close"] < s["min_order_value"]:
            continue
        value = qty * row["close"]
        orders.append({"side": "BUY", "symbol": symbol, "name": row["name"], "qty": qty, "reference_price": row["close"], "estimated_fee": fee(cfg, "BUY", value), "reason": "综合因子排名 %s" % eligible[symbol]["rank"], "conditional_on_sells": available > account.cash})
        available -= value + fee(cfg, "BUY", value)
    return {"date": date, "execution_date": data.next_day(date), "equity": round(equity, 2), "cash": round(account.cash, 2), "positions": account.positions, "position_prices": {sym: rows[sym]["close"] for sym in account.positions}, "rankings": rankings, "targets": targets, "orders": orders, "blocked": blocked, "max_price_deviation": s["max_price_deviation"]}

"""Append-only SQLite ledger; suggestions never mutate holdings."""
import datetime as dt
import json
import math
import sqlite3
from .core import ValidationError


class Account:
    def __init__(self, cash=0):
        self.cash = cash
        self.positions = {}
        self.net_deposits = cash
        self.last_event = None

    def apply(self, event):
        kind, date = event["kind"], event["date"]
        dt.date.fromisoformat(date)
        if self.last_event and date < self.last_event:
            raise ValidationError("事件必须按日期顺序录入")
        if kind in ("DEPOSIT", "WITHDRAW"):
            amount = float(event["amount"])
            if not math.isfinite(amount) or amount <= 0:
                raise ValidationError("资金金额必须为正数")
            signed = amount if kind == "DEPOSIT" else -amount
            if self.cash + signed < -1e-8:
                raise ValidationError("现金不足")
            self.cash += signed; self.net_deposits += signed
        elif kind in ("BUY", "SELL"):
            symbol = event["symbol"]
            qty, price, cost = event["qty"], float(event["price"]), float(event["fee"])
            if type(qty) is not int or qty <= 0 or not all(math.isfinite(x) for x in (price, cost)) or price <= 0 or cost < 0:
                raise ValidationError("成交数量、价格或费用非法")
            p = self.positions.get(symbol, {"qty": 0, "cost": 0.0, "lots": []})
            if kind == "BUY":
                total = qty * price + cost
                if total > self.cash + 1e-8:
                    raise ValidationError("买入现金不足")
                p["cost"] = (p["cost"] * p["qty"] + total) / (p["qty"] + qty)
                p["qty"] += qty; p["lots"].append({"date": date, "qty": qty})
                self.cash -= total; self.positions[symbol] = p
            else:
                if qty > sum(l["qty"] for l in p["lots"] if l["date"] < date):
                    raise ValidationError("卖出超过可卖持仓（T+1）")
                left = qty
                for lot in p["lots"]:
                    if lot["date"] < date:
                        take = min(left, lot["qty"]); lot["qty"] -= take; left -= take
                p["qty"] -= qty; p["lots"] = [l for l in p["lots"] if l["qty"]]
                self.cash += qty * price - cost
                if p["qty"] == 0:
                    self.positions.pop(symbol, None)
        elif kind == "DIVIDEND":
            amount = float(event["amount"])
            if not math.isfinite(amount) or amount <= 0:
                raise ValidationError("股息金额必须为实际到账正数")
            self.cash += amount
        else:
            raise ValidationError("不支持事件类型: " + kind)
        self.cash = round(self.cash, 8)
        self.last_event = date


class Ledger:
    def __init__(self, path):
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL)")
        self.db.commit()

    def events(self, asof=None):
        records = self.db.execute("SELECT payload FROM events ORDER BY date, rowid").fetchall()
        return [json.loads(r[0]) for r in records if asof is None or json.loads(r[0])["date"] <= asof]

    def account(self, asof=None):
        a = Account()
        for event in self.events(asof):
            a.apply(event)
        return a

    def append(self, event):
        if not event.get("id"):
            raise ValidationError("事件必须有唯一 id")
        payload = json.dumps(event, sort_keys=True)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            prior = self.db.execute("SELECT payload FROM events WHERE id=?", (event["id"],)).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ValidationError("同一事件 id 的内容发生变化")
                self.db.rollback(); return False
            self.account().apply(event)
            self.db.execute("INSERT INTO events VALUES (?, ?, ?)", (event["id"], event["date"], payload))
            self.db.commit(); return True
        except Exception:
            self.db.rollback(); raise

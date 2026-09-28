"""CSV provider: point-in-time daily snapshots, explicit exchange calendar."""
import csv
import datetime as dt
import math
import random
from pathlib import Path
from .core import ValidationError, digest
from .intraday import read_quotes, FIELDS as INTRADAY_FIELDS

FIELDS = ["date", "symbol", "name", "board", "listed_date", "open", "close", "volume", "amount", "adj_factor", "suspended", "risk_warning", "delisting", "limit_up", "limit_down", "buy_lot"]


class CSVData:
    def __init__(self, directory):
        self.directory = Path(directory)
        if (self.directory / ".writing").exists():
            raise ValidationError("数据正在发布或发布失败，拒绝读取半成品")
        import json
        self.metadata = json.loads((self.directory / "metadata.json").read_text(encoding="utf-8"))
        if self.metadata.get("kind") not in ("demo", "real"):
            raise ValidationError("metadata.kind 必须为 demo 或 real")
        with (self.directory / "calendar.csv").open(encoding="utf-8", newline="") as f:
            self.calendar = [r["date"] for r in csv.DictReader(f)]
        if self.calendar != sorted(set(self.calendar)):
            raise ValidationError("交易日历重复或未排序")
        for d in self.calendar:
            dt.date.fromisoformat(d)
        self.rows = {}
        with (self.directory / "bars.csv").open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not set(FIELDS).issubset(reader.fieldnames or []):
                raise ValidationError("bars.csv 缺少字段")
            for raw in reader:
                r = dict(raw)
                if r["date"] not in self.calendar:
                    raise ValidationError("行情日期不在交易日历中")
                dt.date.fromisoformat(r["listed_date"])
                if r["listed_date"] > r["date"]:
                    raise ValidationError("上市日期晚于行情日期")
                for k in ("open", "close", "volume", "amount", "adj_factor", "limit_up", "limit_down"):
                    r[k] = float(r[k])
                    if not math.isfinite(r[k]) or r[k] < 0:
                        raise ValidationError("行情数值非法: " + k)
                if min(r["open"], r["close"], r["adj_factor"]) <= 0:
                    raise ValidationError("价格/复权因子必须为正")
                if r["limit_up"] and r["limit_down"] > r["limit_up"]:
                    raise ValidationError("涨跌停价非法")
                r["buy_lot"] = int(r["buy_lot"])
                if r["buy_lot"] <= 0:
                    raise ValidationError("申报单位非法")
                for k in ("suspended", "risk_warning", "delisting"):
                    if r[k] not in ("0", "1"):
                        raise ValidationError("状态字段必须为 0/1")
                    r[k] = r[k] == "1"
                key = (r["date"], r["symbol"])
                if key in self.rows:
                    raise ValidationError("重复行情: " + str(key))
                self.rows[key] = r
        if not self.rows:
            raise ValidationError("无行情数据")
        self.intraday = read_quotes(self.directory / "intraday.csv", self.calendar)
        self.version = digest({"metadata": self.metadata, "calendar": self.calendar, "bars": list(self.rows.values()), "intraday": [[d, symbol, q] for (d, symbol), q in sorted(self.intraday.items())]})

    def snapshot(self, date):
        rows = {s: r for (d, s), r in self.rows.items() if d == date}
        if not rows:
            raise ValidationError("该日期无完整行情: " + date)
        # Manifest is required to catch a silently missing symbol/date.
        expected = self.metadata.get("expected_symbols", {}).get(date)
        if expected is None or set(expected) != set(rows):
            raise ValidationError("当日数据与供应方完整性清单不一致: " + date)
        return rows

    def history(self, symbol, date, count):
        days = [d for d in self.calendar if d <= date][-count:]
        rows = [self.rows.get((d, symbol)) for d in days]
        return rows if len(rows) == count and all(rows) else []

    def next_day(self, date):
        days = [d for d in self.calendar if d > date]
        if not days:
            raise ValidationError("交易日历缺少下一交易日")
        return days[0]

    def validate_mode(self, cfg):
        if (cfg["mode"] == "demo") != (self.metadata["kind"] == "demo"):
            raise ValidationError("数据和配置的 demo/live 模式不一致")


def generate_demo(directory, sessions=260):
    from .core import write_json
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    minute_rng = random.Random(43)
    quotes = []
    days, day = [], dt.date(2025, 1, 2)
    while len(days) < sessions + 1:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += dt.timedelta(days=1)
    symbols = ["600%03d.SH" % i for i in range(1, 7)] + ["000%03d.SZ" % i for i in range(1, 7)]
    prices = {s: 7 + i * 2 for i, s in enumerate(symbols)}
    with (directory / "calendar.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["date"]); w.writerows([[d] for d in days])
    with (directory / "bars.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for n, d in enumerate(days[:-1]):
            for i, symbol in enumerate(symbols):
                prev = prices[symbol]
                op = round(prev * (1 + rng.gauss(0, .004)), 2)
                close = round(op * (1 + .0003 + .001 * math.sin(n / 25 + i) + rng.gauss(0, .013)), 2)
                volume = rng.randint(2000000, 7000000)
                w.writerow(dict(date=d, symbol=symbol, name="演示股票%02d" % i, board="SSE_MAIN" if symbol.endswith("SH") else "SZSE_MAIN", listed_date="2010-01-01", open=op, close=close, volume=volume, amount=round(volume * close, 2), adj_factor=1, suspended=0, risk_warning=0, delisting=0, limit_up=round(prev * 1.1, 2), limit_down=round(prev * .9, 2), buy_lot=100))
                quotes.append(dict(date=d, symbol=symbol, timestamp=d + "T10:00:00+08:00",
                                   price=round(op * (1 + minute_rng.gauss(0, .005)), 2),
                                   volume=volume // 180, tradable=1))
                prices[symbol] = close
    with (directory / "intraday.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INTRADAY_FIELDS)
        w.writeheader(); w.writerows(quotes)
    write_json(directory / "metadata.json", {"kind": "demo", "source": "seed=42 daily + seed=43 synthetic 10:00 observations; NOT real market/calendar", "execution_observation": "last trade at or before 10:00, previous 1 minute volume; synthetic", "expected_symbols": {d: symbols for d in days[:-1]}})
    return days[-2]

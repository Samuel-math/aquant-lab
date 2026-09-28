import copy
import json
import tempfile
import unittest
from pathlib import Path
from aquant.account import Account, Ledger
from aquant.backtest import run
from aquant.core import ValidationError, fee, read_config
from aquant.data import CSVData, generate_demo
from aquant.operations import health, lock, status
from aquant.report import build, save_report
from aquant.strategy import rank, propose


class SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.date = generate_demo(cls.root / "data", 190)
        cls.data = CSVData(cls.root / "data")
        cls.cfg = read_config("configs/demo.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_missing_fee_not_zero(self):
        with self.assertRaises(ValidationError):
            read_config("configs/live.example.json")

    def test_fee_asymmetry_minimum(self):
        self.assertEqual(fee(self.cfg, "BUY", 1000), 5.01)
        self.assertEqual(fee(self.cfg, "SELL", 1000), 5.51)

    def test_t_plus_one_and_partial_fill(self):
        a = Account(10000)
        a.apply(dict(kind="BUY", date="2025-01-02", symbol="X", qty=200, price=10, fee=5))
        with self.assertRaises(ValidationError):
            a.apply(dict(kind="SELL", date="2025-01-02", symbol="X", qty=100, price=11, fee=5))
        a.apply(dict(kind="SELL", date="2025-01-03", symbol="X", qty=100, price=11, fee=5))
        self.assertEqual(a.positions["X"]["qty"], 100)
        self.assertEqual(a.cash, 9090)

    def test_deposit_does_not_become_profit(self):
        a = Account(10000)
        a.apply(dict(kind="DEPOSIT", date="2025-01-02", amount=2000))
        self.assertEqual(a.cash - a.net_deposits, 0)

    def test_ledger_idempotence_and_asof(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "a.sqlite")
            e = dict(id="one", kind="DEPOSIT", date="2025-01-02", amount=10000)
            self.assertTrue(ledger.append(e)); self.assertFalse(ledger.append(e))
            with self.assertRaises(ValidationError):
                ledger.append(dict(e, amount=20000))
            ledger.append(dict(id="two", kind="DEPOSIT", date="2025-02-02", amount=2000))
            self.assertEqual(ledger.account("2025-01-03").cash, 10000)
            with self.assertRaises(ValidationError):
                ledger.append(dict(id="old", kind="DEPOSIT", date="2025-01-01", amount=1000))

    def test_no_future_price_leakage(self):
        data = copy.deepcopy(self.data)
        date = data.calendar[80]
        before = rank(data, date, self.cfg)
        for (d, _), r in data.rows.items():
            if d > date:
                r["close"] *= 100; r["adj_factor"] *= 10
        self.assertEqual(before, rank(data, date, self.cfg))

    def test_history_is_contiguous(self):
        data = copy.deepcopy(self.data)
        symbol = next(iter(data.snapshot(self.date)))
        del data.rows[(data.calendar[-3], symbol)]
        self.assertNotIn(symbol, [r["symbol"] for r in rank(data, self.date, self.cfg)])

    def test_missing_snapshot_fails(self):
        data = copy.deepcopy(self.data)
        del data.rows[next(k for k in data.rows if k[0] == self.date)]
        with self.assertRaises(ValidationError):
            data.snapshot(self.date)

    def test_main_board_and_status_filter(self):
        data = copy.deepcopy(self.data)
        symbols = list(data.snapshot(self.date))
        data.rows[(self.date, symbols[0])]["board"] = "STAR"
        data.rows[(self.date, symbols[1])]["risk_warning"] = True
        selected = [r["symbol"] for r in rank(data, self.date, self.cfg)]
        self.assertNotIn(symbols[0], selected); self.assertNotIn(symbols[1], selected)

    def test_signal_does_not_change_account(self):
        a = Account(10000)
        before = copy.deepcopy(a.__dict__)
        report = propose(self.data, self.date, self.cfg, a)
        self.assertEqual(a.__dict__, before)
        cost = sum(o["qty"] * o["reference_price"] + o["estimated_fee"] for o in report["orders"])
        self.assertLessEqual(cost, 9500)
        self.assertTrue(all(o["qty"] % 100 == 0 for o in report["orders"]))

    def test_next_day_backtest(self):
        result = run(self.data, self.cfg)
        self.assertTrue(result["trades"])
        self.assertGreaterEqual(result["trades"][0]["date"], self.data.calendar[21])
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))

    def test_limit_up_blocks_buy(self):
        data = copy.deepcopy(self.data)
        for r in data.rows.values():
            r["limit_up"] = 0.01
        result = run(data, self.cfg)
        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertTrue(result["unfilled"])

    def test_corporate_actions_fail_closed(self):
        data = copy.deepcopy(self.data)
        first_trade = run(data, self.cfg)["trades"][0]
        index = data.calendar.index(first_trade["date"])
        for (d, s), row in data.rows.items():
            if s == first_trade["symbol"] and d >= data.calendar[index + 1]:
                row["adj_factor"] = 2
        with self.assertRaises(ValidationError):
            run(data, self.cfg)

    def test_report_reproducibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "a.sqlite")
            ledger.append(dict(id="initial", kind="DEPOSIT", date=self.data.calendar[0], amount=10000))
            a = build(self.data, self.cfg, ledger, self.date)
            b = build(self.data, self.cfg, ledger, self.date)
            self.assertEqual(a["report_id"], b["report_id"])
            path = save_report(a, Path(tmp) / "reports")
            self.assertIn("演示数据", path.read_text())

    def test_mode_mismatch(self):
        with self.assertRaises(ValidationError):
            self.data.validate_mode(dict(self.cfg, mode="live"))

    def test_health_staleness_and_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "status.json"
            status(p, self.date, "ok")
            health(p, self.date)
            with self.assertRaises(ValidationError):
                health(p, "1900-01-01")
            with lock(Path(tmp) / "lock"):
                with self.assertRaises(ValidationError):
                    with lock(Path(tmp) / "lock"):
                        pass


if __name__ == "__main__":
    unittest.main()

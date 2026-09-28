import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from aquant.account import Ledger
from aquant.core import ValidationError, read_config
from aquant.data import CSVData, generate_demo
from aquant.performance import account_performance
from aquant.report import build, send_mail
from aquant.research import research


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.date = generate_demo(self.root / "data", 190)
        self.data = CSVData(self.root / "data")
        self.cfg = read_config("configs/demo.json")
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.append(dict(id="init", kind="DEPOSIT", date=self.data.calendar[0], amount=10000))

    def tearDown(self):
        self.tmp.cleanup()

    def test_time_weighted_return_excludes_deposit(self):
        self.ledger.append(dict(id="add", kind="DEPOSIT", date=self.data.calendar[40], amount=2000))
        result = account_performance(self.data, self.ledger, self.date)
        self.assertEqual(result["equity"], 12000)
        self.assertEqual(result["time_weighted_return"], 0)

    def test_research_chronology_no_promotion(self):
        before = copy.deepcopy(self.cfg)
        result = research(self.data, self.cfg, self.root / "research.json")
        self.assertEqual(before, self.cfg)
        self.assertTrue(result["walk_forward"])
        for fold in result["walk_forward"]:
            self.assertLess(fold["train"][1], fold["validation"][0])
            self.assertLess(fold["validation"][1], result["holdout"][0])
        self.assertEqual(result["status"], "candidate_only_not_approved")

    def test_smtp_deduplicates(self):
        report = build(self.data, self.cfg, self.ledger, self.date)
        env = {"SMTP_HOST": "localhost", "SMTP_PORT": "465", "SMTP_USER": "test", "SMTP_PASSWORD": "test", "MAIL_FROM": "a@example.com", "MAIL_TO": "b@example.com"}
        with patch.dict(os.environ, env), patch("aquant.report.smtplib.SMTP_SSL") as smtp:
            self.assertEqual(send_mail(report, self.root / "mail.sqlite"), "邮件已发送")
            self.assertIn("未重复发送", send_mail(report, self.root / "mail.sqlite"))
            self.assertEqual(smtp.call_count, 1)

    def test_ambiguous_smtp_failure_blocks_retry(self):
        report = build(self.data, self.cfg, self.ledger, self.date)
        env = {"SMTP_HOST": "localhost", "SMTP_PORT": "465", "SMTP_USER": "test", "SMTP_PASSWORD": "test", "MAIL_FROM": "a@example.com", "MAIL_TO": "b@example.com"}
        with patch.dict(os.environ, env), patch("aquant.report.smtplib.SMTP_SSL", side_effect=OSError("timeout")) as smtp:
            with self.assertRaises(OSError):
                send_mail(report, self.root / "mail.sqlite")
            with self.assertRaises(ValidationError):
                send_mail(report, self.root / "mail.sqlite")
            self.assertEqual(smtp.call_count, 1)

    def test_trade_feedback_changes_next_report(self):
        first_day = self.data.calendar[80]
        report = build(self.data, self.cfg, self.ledger, first_day)
        order = report["orders"][0]
        self.ledger.append(dict(id="actual-fill", kind="BUY", date=report["execution_date"], symbol=order["symbol"], qty=100, price=order["reference_price"], fee=5))
        second = build(self.data, self.cfg, self.ledger, report["execution_date"])
        self.assertEqual(second["positions"][order["symbol"]]["qty"], 100)
        self.assertLess(second["cash"], report["cash"])
        self.assertNotEqual(second["account_hash"], report["account_hash"])

    def test_backtest_rejects_missing_whole_day(self):
        from aquant.backtest import run
        data = copy.deepcopy(self.data)
        day = data.calendar[60]
        data.rows = {k: v for k, v in data.rows.items() if k[0] != day}
        with self.assertRaises(ValidationError):
            run(data, self.cfg)

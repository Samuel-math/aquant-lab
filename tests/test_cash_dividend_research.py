import copy
import tempfile
import unittest
from pathlib import Path

from aquant.account import Account
from aquant.backtest import run
from aquant.core import ValidationError, read_config
from aquant.data import CSVData, generate_demo
from aquant.strategy import rank
from aquant.targets import MORNING_TARGET
from research_lab.cash_dividend_backtest import cash_entitlement, run_cash_dividend_replay


class CashDividendResearchTests(unittest.TestCase):
    def setUp(self):
        self.account = Account(10000)
        self.account.apply({'kind': 'BUY', 'date': '2026-09-29', 'symbol': '000700.SZ',
                            'qty': 100, 'price': 12.25, 'fee': 5})
        self.event = {'symbol': '000700.SZ', 'record_date': '2026-09-29',
                      'ex_date': '2026-09-30', 'pay_date': '2026-09-30',
                      'announcement_date': '2026-09-22',
                      'cash_before_tax_per_share': 0.1634, 'shares_per_share': 0,
                      'source': 'verified-dividend-record'}

    def test_verified_cash_only_event_credits_record_date_holder(self):
        event = cash_entitlement(self.account, '000700.SZ', '2026-09-30', '2026-09-29',
                                 True, {('2026-09-30', '000700.SZ'): self.event})
        self.assertEqual(event['amount'], 16.34)
        self.assertEqual(event['qty_at_record_close'], 100)
        self.assertEqual(event['tax_status'], 'pretax_research_scenario')
        self.assertEqual(self.account.positions['000700.SZ']['qty'], 100)

    def test_missing_or_unsupported_action_still_blocks(self):
        with self.assertRaisesRegex(ValidationError, '缺少明细'):
            cash_entitlement(self.account, '000700.SZ', '2026-09-30', '2026-09-29', True, {})
        for change in ({'shares_per_share': 0.1}, {'pay_date': '2026-10-08'},
                       {'record_date': '2026-09-28'}, {'announcement_date': '2026-10-01'}):
            event = dict(self.event, **change)
            with self.assertRaisesRegex(ValidationError, '未支持'):
                cash_entitlement(self.account, '000700.SZ', '2026-09-30', '2026-09-29',
                                 True, {('2026-09-30', '000700.SZ'): event})

    def test_no_action_replay_matches_frozen_backtest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate_demo(root, 200)
            data = CSVData(root)
            cfg = read_config('configs/demo.json')
            cfg.update(prediction_target=copy.deepcopy(MORNING_TARGET), plan_deadline='09:00:00')
            cfg['strategy']['liquidate_daily'] = True
            data.metadata['entry_time'] = '09:40:00'
            for quote in data.intraday.values():
                quote['timestamp'] = quote['timestamp'].replace('10:00:00', '09:40:00')
            start, end = data.calendar[170], data.calendar[175]
            ranker = lambda date: rank(data, date, cfg)
            baseline = run(data, cfg, start, end, signal_ranker=ranker)
            research = run_cash_dividend_replay(data, cfg, {}, start, end, ranker)
            self.assertEqual(research['curve'], baseline['curve'])
            self.assertEqual(research['trades'], baseline['trades'])
            self.assertEqual(research['metrics']['total_return'], baseline['metrics']['total_return'])
            self.assertEqual(research['dividends'], [])


if __name__ == '__main__':
    unittest.main()

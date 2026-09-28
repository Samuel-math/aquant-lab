import copy
import csv
import tempfile
import unittest
from pathlib import Path
from aquant.core import ValidationError, read_config
from aquant.data import CSVData, generate_demo
from aquant.backtest import run
from aquant.intraday import read_quotes
from aquant.mining import candidates, evaluate_expression, mine
from aquant.targets import forward_label


class MiningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.asof = generate_demo(cls.root / 'data', 230)
        cls.data = CSVData(cls.root / 'data')
        cls.cfg = read_config('configs/demo.json')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_formula_generation_reproducible_unique(self):
        items = candidates(['a', 'b', 'c'], 24, 17)
        self.assertEqual(items, candidates(['a', 'b', 'c'], 24, 17))
        self.assertEqual(len({x['id'] for x in items}), 24)
        for item in items:
            self.assertIsInstance(evaluate_expression(item['expression'], {'a': .2, 'b': -.5, 'c': 0}), (int, float))

    def test_no_open_fallback(self):
        data = copy.deepcopy(self.data)
        data.intraday = {}
        symbol = next(iter(data.snapshot(self.asof)))
        row = forward_label(data, symbol, data.calendar[20], self.asof)
        self.assertEqual(row['status'], 'missing_execution_quote')
        with self.assertRaises(ValidationError): run(data, self.cfg)

    def test_timestamp_rejects_wrong_minute_or_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'quotes.csv'
            for stamp in ['2025-01-02T10:01:00+08:00', '2025-01-02T10:00:00+00:00']:
                p.write_text('date,symbol,timestamp,price,volume,tradable\n2025-01-02,X,' + stamp + ',10,10000,1\n')
                with self.assertRaises(ValidationError): read_quotes(p, ['2025-01-02'])

    def test_backtest_uses_1000_price(self):
        data = copy.deepcopy(self.data)
        cfg = copy.deepcopy(self.cfg)
        cfg['fees']['slippage_bps'] = 0
        trades = run(data, cfg)['trades']
        self.assertTrue(trades)
        for trade in trades:
            q = data.intraday[trade['date'], trade['symbol']]
            daily = data.rows[trade['date'], trade['symbol']]
            expected = q['price']
            if daily['limit_up']: expected = min(expected, daily['limit_up'])
            if daily['limit_down']: expected = max(expected, daily['limit_down'])
            self.assertEqual(trade['price'], expected)
            self.assertEqual(trade['execution_time'], '10:00:00+08:00')

    def test_holdout_changes_do_not_select_formula(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_before = copy.deepcopy(self.cfg)
            first = mine(self.data, self.cfg, self.asof, Path(tmp) / 'first.json', 4, 2, 17, 120)
            data = copy.deepcopy(self.data)
            start = first['split']['holdout'][0]
            for (date, symbol), row in data.rows.items():
                if date >= start:
                    row['close'] *= 1.1
                    data.intraday[date, symbol]['price'] *= 1.1
            second = mine(data, self.cfg, self.asof, Path(tmp) / 'second.json', 4, 2, 17, 120)
            self.assertEqual(first['winner'], second['winner'])
            self.assertEqual(self.cfg, cfg_before)
            self.assertLess(first['split']['train_max_label_end'], first['split']['validation'][0])
            self.assertLess(first['split']['validation'][1], first['split']['holdout'][0])
            self.assertEqual(first['status'], 'pipeline_verified_only')
            self.assertEqual(len(first['all_candidates']), 4)
            self.assertTrue((Path(tmp) / 'first.frozen.json').exists())

    def test_candidate_budget_rejected(self):
        with self.assertRaises(ValidationError):
            mine(self.data, self.cfg, self.asof, self.root / 'bad.json', 9999)

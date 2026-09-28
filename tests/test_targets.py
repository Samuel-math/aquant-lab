import copy
import tempfile
import unittest
from pathlib import Path
from aquant.core import read_config
from aquant.data import CSVData, generate_demo
from aquant.targets import build_dataset, forward_label, training_rows


class TargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.date = generate_demo(cls.tmp.name, 60)
        cls.data = CSVData(cls.tmp.name)
        cls.cfg = read_config('configs/demo.json')
        cls.symbol = next(iter(cls.data.snapshot(cls.date)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_1000_to_1000_not_signal_close_or_open(self):
        data = copy.deepcopy(self.data)
        t, entry, end = data.calendar[20:23]
        data.rows[t, self.symbol]['close'] = 5
        data.rows[entry, self.symbol]['open'] = 99
        data.intraday[entry, self.symbol]['price'] = 10
        data.rows[end, self.symbol]['open'] = 99
        data.intraday[end, self.symbol]['price'] = 11
        row = forward_label(data, self.symbol, t, end)
        self.assertAlmostEqual(row['gross_return'], .1)
        self.assertEqual(row['entry_date'], entry)
        self.assertEqual(row['label_available_date'], end)

    def test_weekend_uses_sessions(self):
        data = self.data
        import datetime
        t = next(d for d in data.calendar[:40] if datetime.date.fromisoformat(d).weekday() == 4)
        row = forward_label(data, self.symbol, t, self.date)
        self.assertEqual(datetime.date.fromisoformat(row['entry_date']).weekday(), 0)
        self.assertEqual(datetime.date.fromisoformat(row['exit_date']).weekday(), 1)

    def test_future_labels_unavailable(self):
        t, entry, end = self.data.calendar[20:23]
        row = forward_label(self.data, self.symbol, t, entry)
        self.assertEqual(row['status'], 'pending')
        self.assertIsNone(row['gross_return'])
        self.assertEqual(training_rows([row], entry, end), [])

    def test_purge_boundary(self):
        t, entry, end, after = self.data.calendar[20:24]
        row = forward_label(self.data, self.symbol, t, end)
        self.assertEqual(training_rows([row], end, end), [])
        self.assertEqual(training_rows([row], entry, after), [])
        self.assertEqual(training_rows([row], end, after), [row])

    def test_limit_up_not_silently_filtered(self):
        data = copy.deepcopy(self.data)
        t, entry, end = data.calendar[20:23]
        data.rows[entry, self.symbol]['limit_up'] = data.intraday[entry, self.symbol]['price']
        row = forward_label(data, self.symbol, t, end)
        self.assertEqual(row['status'], 'observed')
        self.assertIn('entry_limit_up', row['execution_flags'])

    def test_suspended_and_corporate_action_have_no_fake_return(self):
        for key, value in [('suspended', True), ('adj_factor', 2)]:
            data = copy.deepcopy(self.data)
            t, entry, end = data.calendar[20:23]
            data.rows[end, self.symbol][key] = value
            row = forward_label(data, self.symbol, t, end)
            self.assertIsNone(row['gross_return'])
            self.assertNotEqual(row['status'], 'observed')

    def test_dataset_preserves_pending_and_no_future_features(self):
        data = copy.deepcopy(self.data)
        before = build_dataset(data, self.cfg, data.calendar[30])
        for (d, symbol), row in data.rows.items():
            if d > data.calendar[30]:
                row['open'] *= 100
                row['close'] *= 100
        after = build_dataset(data, self.cfg, data.calendar[30])
        self.assertEqual(before['rows'], after['rows'])
        self.assertEqual(before['status_counts']['pending'], 24)

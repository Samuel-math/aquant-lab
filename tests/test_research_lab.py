import datetime as dt
import unittest

from scripts.backfill_intraday_summary import merge_summaries, summarize


class ResearchLabTests(unittest.TestCase):
    def test_full_day_5minute_aggregation(self):
        times = []
        for first, count in ((dt.datetime(2026, 10, 9, 9, 35), 24),
                             (dt.datetime(2026, 10, 9, 13, 5), 24)):
            times.extend(first + dt.timedelta(minutes=5 * i) for i in range(count))
        rows = [{'date': '2026-10-09', 'time': time.strftime('%Y%m%d%H%M%S') + '000',
                 'open': '10', 'close': '10', 'volume': '1', 'amount': '10'}
                for time in times]
        row = summarize(rows)[0]
        self.assertEqual(row['bar_count'], 48)
        self.assertAlmostEqual(row['first10_volume_share'], 2 / 48)
        self.assertAlmostEqual(row['open30_volume_share'], 6 / 48)
        self.assertAlmostEqual(row['close30_volume_share'], 6 / 48)
        self.assertAlmostEqual(row['morning_volume_share'], .5)
        self.assertAlmostEqual(row['intraday_volume_concentration'], 1)
        self.assertEqual(summarize(rows[:-1]), [])

    def test_training_uses_only_mature_labels(self):
        try:
            import pandas as pd
            from research_lab.models import mature_training_rows
        except ModuleNotFoundError:
            self.skipTest('Optional research model dependencies are not installed')
        frame = pd.DataFrame([
            {'date': '2026-10-01', 'label_available_date': '2026-10-03',
             'status': 'observed'},
            {'date': '2026-10-02', 'label_available_date': '2026-10-05',
             'status': 'observed'},
            {'date': '2026-10-03', 'label_available_date': '2026-10-04',
             'status': 'observed'},
            {'date': '2026-10-04', 'label_available_date': '2026-10-04',
             'status': 'observed'},
            {'date': '2026-10-05', 'label_available_date': '2026-10-06',
             'status': 'observed'},
        ])
        rows, dates = mature_training_rows(frame, '2026-10-04')
        self.assertEqual(dates, ['2026-10-01', '2026-10-03'])
        self.assertEqual(rows['date'].tolist(), dates)

    def test_all_learned_models_use_strict_binary_label_and_positive_probability(self):
        try:
            import pandas as pd
            from research_lab.models import binary_target, fit_models, positive_scores
        except ModuleNotFoundError:
            self.skipTest('Optional research model dependencies are not installed')
        frame = pd.DataFrame({
            'gross_return': [-0.02, 0.01, 0.011, 0.04] * 100,
            'factor': [0.1, 0.2, 0.7, 0.9] * 100,
        })
        self.assertEqual(binary_target(frame).iloc[:4].tolist(), [0, 0, 1, 1])
        models, positive_rate = fit_models(frame, ['factor'])
        self.assertEqual(set(models), {'logistic', 'lightgbm'})
        self.assertAlmostEqual(positive_rate, 0.5)
        for model in models.values():
            scores = positive_scores(model, frame[['factor']])
            self.assertEqual(len(scores), len(frame))
            self.assertTrue(all(0 <= score <= 1 for score in scores))
            self.assertGreater(scores[2], scores[0])

    def test_binary_training_rejects_single_class_or_missing_return(self):
        try:
            import pandas as pd
            from research_lab.models import binary_target
        except ModuleNotFoundError:
            self.skipTest('Optional research model dependencies are not installed')
        with self.assertRaisesRegex(ValueError, 'only one class'):
            binary_target(pd.DataFrame({'gross_return': [0.0, 0.01]}))
        with self.assertRaisesRegex(ValueError, 'missing gross returns'):
            binary_target(pd.DataFrame({'gross_return': [0.0, None, 0.02]}))

    def test_incremental_summary_keeps_history_and_journals_revision(self):
        old = [{'date': '2026-10-08', 'symbol': '600000.SH', 'first10_volume_share': .1}]
        incoming = [{'date': '2026-10-08', 'symbol': '600000.SH', 'first10_volume_share': .2},
                    {'date': '2026-10-09', 'symbol': '600000.SH', 'first10_volume_share': .3}]
        rows, changes = merge_summaries(old, incoming)
        self.assertEqual([row['date'] for row in rows], ['2026-10-08', '2026-10-09'])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['old']['first10_volume_share'], .1)


if __name__ == '__main__':
    unittest.main()

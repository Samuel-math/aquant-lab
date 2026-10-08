import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from aquant.core import read_config, ValidationError
from aquant.data import CSVData, generate_demo
from aquant.mining import prepare
from aquant.rolling import binary_evaluation, fit_and_predict, recent_binary_summary, validate_prediction, update_rolling
from aquant.baostock_source import extract_1000, merge_revision, main_board, load_pool
from aquant.storage import admission


class RollingSourceTests(unittest.TestCase):
    def test_explicit_pool_rejects_future_duplicate_and_non_main_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'pool.json'
            pool={'asof':'2026-09-28','stocks':[{'code':c} for c in ['sh.600000','sz.000001','sz.002001']]}
            path.write_text(json.dumps(pool))
            self.assertEqual(load_pool(path,3,'2026-09-28')[0],pool)
            with self.assertRaises(ValidationError): load_pool(path,3,'2026-09-27')
            with self.assertRaises(ValidationError): load_pool(path,4,'2026-09-28')
            for invalid in ['sh.600000','sh.688001']:
                pool['stocks'][2]['code']=invalid
                path.write_text(json.dumps(pool))
                with self.assertRaises(ValidationError): load_pool(path,3,'2026-09-28')

    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        cls.asof=generate_demo(cls.root/'data',200)
        cls.data=CSVData(cls.root/'data')
        cls.cfg=read_config('configs/demo.json')
        cls.features,cls.labels=prepare(cls.data,cls.cfg,cls.asof,lambda:None)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_training_ignores_future_outcomes(self):
        date=self.data.calendar[160]
        a=fit_and_predict(self.features,self.labels,date,count=4)
        changed=copy.deepcopy(self.labels)
        for row in changed:
            if row['label_available_date'] and row['label_available_date']>date:
                row['gross_return']=999
        b=fit_and_predict(self.features,changed,date,count=4)
        self.assertEqual(a,b)
        self.assertLessEqual(a['train_max_label_end'],date)

    def test_snapshot_memoization_preserves_all_features_and_labels(self):
        with patch('aquant.mining._SnapshotView',side_effect=lambda data:data):
            expected=prepare(self.data,self.cfg,self.asof,lambda:None)
        with patch.object(self.data,'snapshot',wraps=self.data.snapshot) as snapshot:
            actual=prepare(self.data,self.cfg,self.asof,lambda:None)
            self.assertEqual(actual,expected)
            dates=[call.args[0] for call in snapshot.call_args_list]
            self.assertEqual(len(dates),len(set(dates)))

    def test_parallel_features_labels_and_model_are_identical(self):
        features,labels=prepare(self.data,self.cfg,self.asof,lambda:None,workers=2)
        self.assertEqual((features,labels),(self.features,self.labels))
        date=self.data.calendar[160]
        serial=fit_and_predict(features,labels,date,count=4,workers=1)
        parallel=fit_and_predict(features,labels,date,count=4,workers=2)
        self.assertEqual(serial,parallel)

    def test_validation_waits_for_two_sessions(self):
        date=self.data.calendar[160]
        saved=fit_and_predict(self.features,self.labels,date,count=4)
        saved['provenance']='historical_replay'
        self.assertIsNone(validate_prediction(self.data,saved,self.data.calendar[161]))
        scored=validate_prediction(self.data,saved,self.data.calendar[162])
        self.assertEqual(scored['model_id'],saved['model_id'])
        self.assertIsNotNone(scored['rank_ic'])
        self.assertEqual(set(scored['binary_metrics']), {'gt_1pct','gt_3pct','gt_5pct'})

    def test_threshold_auc_uses_ties_and_strictly_greater_label(self):
        rows=[{'symbol':str(i),'rank':i,'status':'observed','score':score,
               'gross_return':gross} for i,(score,gross) in enumerate(
                   [(0.9,0.06),(0.8,0.04),(0.8,0.03),(0.1,0.00)],1)]
        result=binary_evaluation(rows,0.03)
        self.assertEqual(result['positive_count'],2)
        self.assertEqual(result['negative_count'],2)  # Exactly 3% is negative.
        self.assertAlmostEqual(result['auc'],0.875)  # Tied score receives half credit.
        self.assertEqual(result['base_rate'],0.5)
        self.assertEqual(result['top10_hit_rate'],0.5)
        self.assertIsNone(binary_evaluation(rows,0.06)['auc'])

    def test_missing_top10_label_does_not_promote_rank_11(self):
        rows=[{'symbol':str(i),'rank':i,'status':'observed','score':12-i,
               'gross_return':0.02 if i in (2,11) else 0.0} for i in range(1,12)]
        rows[0].update(status='missing_execution_quote',gross_return=None)
        result=binary_evaluation(rows,0.01)
        self.assertEqual((result['top10_requested'],result['top10_observed'],result['top10_hits']),
                         (10,9,1))
        self.assertAlmostEqual(result['top10_hit_rate'],1/9)
        self.assertEqual(result['observed_count'],10)

    def test_recent_summary_keeps_older_evaluations_unchanged(self):
        metric=binary_evaluation([
            {'symbol':'A','rank':1,'status':'observed','score':1,'gross_return':0.02},
            {'symbol':'B','rank':2,'status':'observed','score':0,'gross_return':0.00},
        ],0.01)
        summary=recent_binary_summary([{'rank_ic':0.1},
                                       {'binary_metrics':{'gt_1pct':metric}}])
        self.assertEqual(summary['gt_1pct']['evaluated_days'],1)
        self.assertEqual(summary['gt_1pct']['mean_daily_auc'],1.0)
        self.assertEqual(summary['gt_1pct']['pooled_base_rate'],0.5)
        self.assertEqual(summary['gt_3pct']['evaluated_days'],0)

    def test_saved_predictions_are_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            date=self.data.calendar[160]
            update_rolling(self.data,self.cfg,date,tmp,count=4)
            path=Path(tmp)/'predictions'/(date+'.json')
            before=path.read_bytes()
            same=update_rolling(self.data,self.cfg,date,tmp,count=4)
            self.assertEqual(same['new_prediction_dates'],[])
            later=update_rolling(self.data,self.cfg,self.data.calendar[162],tmp,count=4)
            self.assertIn(date,later['new_evaluation_dates'])
            self.assertEqual(path.read_bytes(),before)

    def test_extract_only_1000_no_full_minute_storage(self):
        rows=[dict(date='2026-09-24',time='20260924'+stamp+'000',close='10') for stamp in ['093000','100000','103000','150000']]
        self.assertEqual(len(extract_1000(rows)),1)
        self.assertEqual(extract_1000(rows)[0]['time'],'20260924100000000')
        self.assertTrue(main_board('sh.600000'))
        self.assertFalse(main_board('sh.688001'))

    def test_vendor_corrections_keep_previous_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/'revisions.jsonl'
            result=merge_revision([{'date':'2026-09-24','price':10}],[{'date':'2026-09-24','price':11}], 'date',journal,'1000','now')
            self.assertEqual(result[0]['price'],11)
            self.assertEqual(json.loads(journal.read_text())['previous']['price'],10)
            merge_revision(result,result,'date',journal,'1000','later')
            self.assertEqual(len(journal.read_text().splitlines()),1)

    def test_storage_budget_stops_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'core'; p.write_text('important')
            with self.assertRaises(ValidationError): admission(tmp,max_bytes=1)
            self.assertEqual(p.read_text(),'important')

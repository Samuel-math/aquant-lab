import copy
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from aquant.data import CSVData, generate_demo
from aquant.core import read_config, ValidationError
from aquant.paper import update, prospective, TZ
from aquant.strategy import rank


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        generate_demo(self.root/'data',200)
        self.data=CSVData(self.root/'data'); self.cfg=read_config('configs/demo.json')
        self.date=self.data.calendar[160]; self.next=self.data.next_day(self.date)
        self.now=dt.datetime.fromisoformat(self.date+'T20:00:00+08:00')
        self.pred={'signal_date':self.date,'candidate_count':24,'seed':17,'created_at':self.now.isoformat(),
                   'provenance':'prospective','model_id':'test','predictions':rank(self.data,self.date,self.cfg)}
        self.out=self.root/'paper'

    def tearDown(self): self.tmp.cleanup()

    def test_deadline_and_midnight(self):
        self.assertTrue(prospective(self.date,self.next,dt.datetime.fromisoformat(self.next+'T00:01:00+08:00')))
        self.assertFalse(prospective(self.date,self.next,dt.datetime.fromisoformat(self.next+'T09:30:00+08:00')))
        with self.assertRaises(ValidationError): update(self.data,self.cfg,self.pred,self.out,dt.datetime.fromisoformat(self.next+'T10:01:00+08:00'))
        self.assertFalse((self.out/'protocol.json').exists())

    def test_plan_immutable_and_atomic_settlement_idempotent(self):
        first=update(self.data,self.cfg,self.pred,self.out,self.now)
        self.assertEqual(first['trade_count'],0); self.assertEqual(first['equity'],10000)
        changed=copy.deepcopy(self.pred); changed['predictions']=[]
        again=update(self.data,self.cfg,changed,self.out,self.now)
        self.assertEqual(first['next_plan'],again['next_plan'])
        later=copy.deepcopy(self.pred); later.update(signal_date=self.next,created_at=self.next+'T20:00:00+08:00',predictions=rank(self.data,self.next,self.cfg))
        now=dt.datetime.fromisoformat(later['created_at'])
        a=update(self.data,self.cfg,later,self.out,now)
        b=update(self.data,self.cfg,later,self.out,now)
        self.assertEqual(a,b)
        self.assertGreater(a['trade_count'],0); self.assertGreater(a['fees_paid'],0)
        db=sqlite3.connect(str(self.out/'paper.sqlite'))
        self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],1); db.close()

    def test_config_change_rejected(self):
        update(self.data,self.cfg,self.pred,self.out,self.now)
        cfg=copy.deepcopy(self.cfg); cfg['initial_cash']=20000
        with self.assertRaises(ValidationError): update(self.data,cfg,self.pred,self.out,self.now)

    def test_selected_pool_requires_matching_data_and_selection_date(self):
        self.cfg['pool_hash']='frozen-pool'
        with self.assertRaises(ValidationError): update(self.data,self.cfg,self.pred,self.out,self.now)
        self.data.metadata['universe']={'pool_hash':'frozen-pool','selection_date':self.next}
        with self.assertRaises(ValidationError): update(self.data,self.cfg,self.pred,self.out,self.now)
        self.data.metadata['universe']['selection_date']=self.date
        self.cfg['trial_id']='new-pool'
        r=update(self.data,self.cfg,self.pred,self.out,self.now)
        self.assertEqual(r['trial_id'],'new-pool')

    def test_replay_cannot_trade(self):
        self.pred['provenance']='historical_replay'
        with self.assertRaises(ValidationError): update(self.data,self.cfg,self.pred,self.out,self.now)

    def test_missing_plans_do_not_invent_trades_and_month_ends(self):
        update(self.data,self.cfg,self.pred,self.out,self.now)
        later=copy.deepcopy(self.pred)
        later['signal_date']=self.data.calendar[185]
        later['created_at']=later['signal_date']+'T20:00:00+08:00'
        later['predictions']=[]
        r=update(self.data,self.cfg,later,self.out,dt.datetime.fromisoformat(later['created_at']))
        self.assertEqual(r['status'],'complete')
        self.assertIsNone(r['next_plan'])
        self.assertTrue(r['missing_plan_dates'])
        db=sqlite3.connect(str(self.out/'paper.sqlite'))
        self.assertLess(db.execute('SELECT max(date) FROM sessions').fetchone()[0],r['end_exclusive']); db.close()

    def test_missing_quote_rolls_back_whole_day(self):
        r=update(self.data,self.cfg,self.pred,self.out,self.now)
        orders=r['next_plan']['orders']; self.assertTrue(orders)
        data=copy.deepcopy(self.data)
        del data.intraday[(self.next,orders[-1]['symbol'])]
        later=copy.deepcopy(self.pred); later.update(signal_date=self.next,created_at=self.next+'T20:00:00+08:00')
        with self.assertRaises(ValidationError): update(data,self.cfg,later,self.out,dt.datetime.fromisoformat(later['created_at']))
        db=sqlite3.connect(str(self.out/'paper.sqlite'))
        self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0); db.close()

import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from aquant.core import read_config,ValidationError
from aquant.data import CSVData,generate_demo
from aquant.targets import MORNING_TARGET,forward_label,execution_quote
from aquant.paper import prospective
from aquant.strategy import propose,rank
from aquant.account import Account
from aquant.backtest import execute_orders
from aquant.intraday import read_quotes
from aquant.baostock_source import extract_1000


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        generate_demo(self.root,200)
        self.data=CSVData(self.root)
        self.cfg=read_config('configs/demo.json')
        self.cfg.update(prediction_target=copy.deepcopy(MORNING_TARGET),plan_deadline='09:00:00')
        self.cfg['strategy']['liquidate_daily']=True
        self.data.metadata['entry_time']='09:40:00'
        for q in self.data.intraday.values():q['timestamp']=q['timestamp'].replace('10:00:00','09:40:00')
        self.date,self.entry,self.exit=self.data.calendar[160:163]
        self.symbol='600001.SH'

    def tearDown(self): self.tmp.cleanup()

    def test_label_uses_0940_then_next_open_not_next_0940(self):
        self.data.intraday[(self.entry,self.symbol)]['price']=10
        self.data.intraday[(self.exit,self.symbol)]['price']=99
        self.data.rows[(self.exit,self.symbol)]['open']=12
        r=forward_label(self.data,self.symbol,self.date,self.exit,self.cfg)
        self.assertAlmostEqual(r['gross_return'],.2)
        self.assertIn('09:40:00',r['entry_time']);self.assertIn('09:30:00',r['exit_time'])
        self.assertEqual(forward_label(self.data,self.symbol,self.date,self.entry,self.cfg)['status'],'pending')

    def test_open_capacity_does_not_use_future_volume(self):
        a=execution_quote(self.data,self.cfg,self.exit,self.symbol,'SELL')
        self.data.rows[(self.exit,self.symbol)]['volume']=1e15
        self.data.intraday[(self.exit,self.symbol)]['volume']=1e15
        b=execution_quote(self.data,self.cfg,self.exit,self.symbol,'SELL')
        self.assertEqual(a,b)
        self.assertEqual(a['volume'],self.data.rows[(self.entry,self.symbol)]['volume']/240)

    def test_wrong_dataset_time_rejected(self):
        self.data.validate_mode(self.cfg)
        self.data.metadata['entry_time']='10:00:00'
        with self.assertRaises(ValidationError):self.data.validate_mode(self.cfg)
        with self.assertRaises(ValidationError):read_quotes(self.root/'intraday.csv',self.data.calendar,'09:40:00')

    def test_plan_exits_even_if_stock_is_selected_again(self):
        a=Account(10000)
        a.apply(dict(kind='BUY',date=self.date,symbol=self.symbol,qty=100,price=10,fee=0))
        ranking=rank(self.data,self.date,self.cfg)
        first=next(x for x in ranking if x['symbol']==self.symbol)
        ranking=[first]+[x for x in ranking if x['symbol']!=self.symbol]
        p=propose(self.data,self.date,self.cfg,a,ranking)
        same=[o for o in p['orders'] if o['symbol']==self.symbol]
        self.assertEqual([o['side'] for o in same],['SELL','BUY'])
        self.assertEqual([o['execution_time'] for o in same],['09:30:00','09:40:00'])
        self.assertEqual(a.positions[self.symbol]['qty'],100)
        now=dt.datetime.fromisoformat(self.entry+'T09:00:00+08:00')
        self.assertFalse(prospective(self.date,self.entry,now,self.cfg))

    def test_execution_sells_first_and_failed_sale_blocks_rebuy(self):
        a=Account(10000);a.apply(dict(kind='BUY',date=self.date,symbol=self.symbol,qty=100,price=10,fee=0))
        row=self.data.rows[(self.entry,self.symbol)]
        row.update(open=10,limit_up=1000,limit_down=1)
        self.data.rows[(self.date,self.symbol)]['volume']=1e9
        self.data.intraday[(self.entry,self.symbol)].update(price=10,volume=1e9)
        buy=dict(side='BUY',symbol=self.symbol,qty=100,reference_price=10)
        sell=dict(side='SELL',symbol=self.symbol,qty=100,reference_price=10)
        fills,_=execute_orders(self.data,self.cfg,a,self.entry,[buy,sell])
        self.assertEqual([t['kind'] for t in fills],['SELL','BUY'])
        self.assertEqual([t['execution_time'] for t in fills],['09:30:00+08:00','09:40:00+08:00'])
        b=Account(10000);b.apply(dict(kind='BUY',date=self.date,symbol=self.symbol,qty=100,price=10,fee=0))
        row['open']=5
        fills,rejected=execute_orders(self.data,self.cfg,b,self.entry,[sell,buy])
        self.assertEqual(fills,[]);self.assertEqual(len(rejected),2)

    def test_minute_extraction_accepts_only_exact_0940(self):
        rows=[dict(date='2026-09-30',time='20260930'+t+'000') for t in ['093500','094000','094500','100000']]
        self.assertEqual(extract_1000(rows,'094000'),[rows[1]])

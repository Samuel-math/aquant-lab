import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from scripts.local_monitor import assess, read_local_snapshot, TZ


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,9,29,8,40,tzinfo=TZ)
        self.paper={'asof':'2026-09-28','status':'awaiting_first_execution','equity':10000,'cash':10000,'total_return':0,'max_drawdown':0,'fees_paid':0,'trade_count':0,'start':'2026-09-29','end_exclusive':'2026-10-29','limitations':['研究样本'],
                    'next_plan':{'execution_date':'2026-09-29','frozen_at':'2026-09-28T20:10:00+08:00','max_price_deviation':.03,'orders':[{'side':'BUY','symbol':'600000.SH','name':'<script>','qty':100,'reference_price':10}]}}
        self.snapshot={'files':{'paper':self.paper,'cycle':{'paper':self.paper},'scheduler':{'checked_at':self.now.isoformat()},'rolling':{'status':'ok','date':'2026-09-28'},'sync':{'status':'ok','date':'2026-09-28'}},'calendar':['2026-09-28','2026-09-29','2026-09-30'],'writing':False}

    def test_ready_and_html_escaped(self):
        r=assess(self.snapshot,self.now)
        self.assertEqual(r['kind'],'report'); self.assertIn('&lt;script&gt;',r['html'])
        self.assertNotIn('<script>',r['html'])

    def test_distinct_trials_have_distinct_delivery_keys(self):
        old=assess(self.snapshot,self.now)
        self.paper['trial_id']='pool188-v21-20260929'
        new=assess(self.snapshot,self.now)
        self.assertNotEqual(old['key'],new['key'])
        self.assertIn('pool188-v21-20260929',new['html'])

    def test_late_notification_is_review_only(self):
        now=self.now.replace(hour=10)
        self.snapshot['files']['scheduler']['checked_at']=now.isoformat()
        self.assertIn('仅供复盘，不可追单',assess(self.snapshot,now)['html'])

    def test_stale_and_failed_runs_do_not_send_orders(self):
        for change in ['heartbeat','failed','writing','calendar','mismatch']:
            s=copy.deepcopy(self.snapshot)
            if change=='heartbeat': s['files']['scheduler']['checked_at']='2026-09-28T00:00:00+08:00'
            if change=='failed': s['files']['scheduler']['result']='failed'
            if change=='writing': s['writing']=True
            if change=='calendar': s['calendar']=[]
            if change=='mismatch': s['files']['cycle']={}
            r=assess(s,self.now)
            self.assertEqual(r['kind'],'alert'); self.assertNotIn('600000.SH',r['html'])

    def test_local_reports_need_no_server_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for relative,value in {
                'paper/latest.json':self.paper,
                'rolling-real/cycle.json':{'paper':self.paper},
                'rolling-real/status.json':{'status':'ok','date':'2026-09-28'},
                'real/sync.status.json':{'status':'ok','date':'2026-09-28'},
            }.items():
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(value),encoding='utf-8')
            calendar=root/'real/dataset/calendar.csv'
            calendar.parent.mkdir(parents=True,exist_ok=True)
            calendar.write_text('date\n2026-09-28\n2026-09-29\n2026-09-30\n',encoding='utf-8')
            snapshot=read_local_snapshot(root)
            self.assertEqual(snapshot['source'],'local')
            self.assertEqual(assess(snapshot,self.now)['kind'],'report')

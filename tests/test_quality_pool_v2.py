import copy
import unittest
from scripts.select_quality_pool_v2 import fundamental,relative,choose,tech_tag

class QualityPoolV2Tests(unittest.TestCase):
    def raw(self):
        raw={}
        periods={'profit_2024':'2024-12-31','profit_2025':'2025-12-31','profit_h1':'2026-06-30','profit_prev_h1':'2025-06-30','balance_h1':'2026-06-30','cash_2024':'2024-12-31','cash_2025':'2025-12-31'}
        for key,period in periods.items():
            raw[key]=[{'statDate':period,'pubDate':'2026-08-30','netProfit':'10','roeAvg':'.03','MBRevenue':'100','gpMargin':'.3','CFOToNP':'1','liabilityToAsset':'.3','currentRatio':'2.5','cashRatio':'1.6'}]
        raw['profit_2025'][0]['MBRevenue']='120'
        raw['profit_h1'][0]['MBRevenue']='150'
        return raw

    def test_growth_loss_can_pass_without_roe_floor(self):
        raw=self.raw();raw['profit_h1'][0]['netProfit']='-5';raw['profit_h1'][0]['roeAvg']='-.01';raw['profit_prev_h1'][0]['netProfit']='-10'
        value,why=fundamental(raw,'C39电子制造')
        self.assertIsNone(why);self.assertEqual(value['lane'],'成长亏损观察');self.assertIsNone(value['rd_intensity']);self.assertIsNone(value['cash_runway_months'])
        raw['balance_h1'][0]['cashRatio']='.9'
        self.assertIsNotNone(fundamental(raw,'C39电子制造')[1])

    def test_low_roe_profitable_growth_is_not_excluded(self):
        value,why=fundamental(self.raw(),'I65软件')
        self.assertIsNone(why);self.assertEqual(value['lane'],'成长盈利')

    def test_missing_growth_evidence_cannot_be_fabricated(self):
        raw=self.raw();raw['profit_h1'][0]['netProfit']='-5';raw['profit_h1'][0]['MBRevenue']=''
        self.assertIsNotNone(fundamental(raw,'C39电子')[1])

    def test_future_and_wrong_period_fail(self):
        for field,value in [('pubDate','2026-10-01'),('statDate','2026-03-31')]:
            raw=self.raw();raw['profit_h1'][0][field]=value
            self.assertIsNotNone(fundamental(raw,'I65软件')[1])

    def test_cross_industry_values_never_change_peer_score(self):
        rows=[{'industry':'I65','lane':'成长盈利','roe':n} for n in [.01,.02,.03]]
        a=relative(rows,rows[0],'roe')
        extra=[{'industry':'J66','lane':'成长盈利','roe':999} for _ in range(50)]
        self.assertEqual(a,relative(rows+extra,rows[0],'roe'))
        self.assertEqual(relative([rows[0]],rows[0],'roe'),.5)

    def test_caps_and_no_tech_quota(self):
        rows=[{'code':str(i),'industry':'J66银行','financial_sector':True,'loss_making':False,'lane':'金融成熟','peer_count':30,'revenue_growth_h1':None} for i in range(30)]
        picked,rejected=choose(rows,target=188)
        self.assertEqual(len(picked),10)
        self.assertEqual(tech_tag('C36汽车制造业'),'科技相关待核实')
        self.assertEqual(tech_tag('I65软件和信息技术服务业'),'科技行业代理')

    def test_growth_lane_does_not_require_unused_roe(self):
        raw=self.raw();raw['profit_h1'][0]['roeAvg']=''
        value,why=fundamental(raw,'I65软件')
        self.assertIsNone(why);self.assertIsNone(value['roe_h1'])

    def test_industry_slots_reflect_coverage_not_cross_industry_profit(self):
        from scripts.select_quality_pool_v2 import balanced_choose
        valid=[{'industry':'C39电子'} for _ in range(20)]+[{'industry':'J66银行'} for _ in range(10)]
        rows=[]
        for group in ['C39电子','J66银行']:
            for i in range(12):
                rows.append({'industry':group,'code':group+str(i),'score':1-i/20,'financial_sector':group.startswith('J'),'loss_making':False,'lane':'成长盈利' if i%2 else '非金融成熟','peer_count':6,'revenue_growth_h1':.3})
        rows.sort(key=lambda r:(-r['score'],r['code']))
        picked,_,allocation=balanced_choose(rows,valid,target=9)
        self.assertEqual(len(picked),9)
        self.assertEqual(allocation['industry_quotas'],{'C39电子':6,'J66银行':3})
        self.assertEqual(sum(r['industry']=='C39电子' and r['lane'].startswith('成长') for r in picked),3)

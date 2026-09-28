import copy
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from scripts.select_quality_pool import evaluate_financial, metrics, mainboard, percentile, financial_job


class QualityPoolTests(unittest.TestCase):
    def financial(self):
        result={}
        for name in ('profit_2024','profit_2025','profit_h1','profit_prev_h1'):
            result[name]=[{'netProfit':'100','roeAvg':'.10','pubDate':'2026-08-20'}]
        result['balance_h1']=[{'liabilityToAsset':'.5','pubDate':'2026-08-20'}]
        for name in ('cash_2024','cash_2025'): result[name]=[{'CFOToNP':'.8'}]
        return result

    def test_quality_requires_both_years_cash(self):
        raw=self.financial(); self.assertIsNone(evaluate_financial(raw,'C制造业')[1])
        raw['cash_2024'][0]['CFOToNP']='-.2'
        self.assertIsNotNone(evaluate_financial(raw,'C制造业')[1])

    def test_financials_use_distinct_leverage_and_cash_rules(self):
        raw=self.financial(); raw['balance_h1'][0]['liabilityToAsset']='.92'; raw['cash_2024']=[]
        self.assertIsNone(evaluate_financial(raw,'J66货币金融服务')[1])
        self.assertIsNotNone(evaluate_financial(raw,'C制造业')[1])

    def test_profit_decline_and_missing_fundamentals_fail_closed(self):
        raw=self.financial(); raw['profit_h1'][0]['netProfit']='60'
        self.assertIsNotNone(evaluate_financial(raw,'C制造业')[1])
        raw=self.financial();raw['profit_h1']=[]
        self.assertIsNotNone(evaluate_financial(raw,'C制造业')[1])

    def test_st_and_wrong_board_excluded(self):
        self.assertFalse(mainboard('sh.688001')); self.assertFalse(mainboard('sz.300001'))
        self.assertTrue(mainboard('sz.002001'))
        self.assertEqual(metrics([{'date':'2026-09-28','isST':'1'}],'2026-09-28',[])[1],'ST风险警示')

    def test_percentile_ties(self):
        self.assertEqual(percentile([1,1,1],1),.5)

    def test_future_publications_never_enter_financial_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[{'pubDate':'2026-08-20','statDate':'2026-06-30','netProfit':'100'},
                  {'pubDate':'2026-10-20','statDate':'2026-06-30','netProfit':'999'}]
            with patch('scripts.select_quality_pool.fetch',return_value=rows):
                self.assertEqual(financial_job((tmp,'sh.600000','2026-09-28')),('sh.600000',True))
            saved=json.loads((Path(tmp)/'financial/sh.600000.json').read_text())
            self.assertEqual(len(saved['profit_h1']),1)
            self.assertEqual(saved['profit_h1'][0]['netProfit'],'100')

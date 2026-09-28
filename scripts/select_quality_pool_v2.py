#!/usr/bin/env python3
"""Industry-relative quality/growth research pool; preserves V1 and live paper rules."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import shutil
import statistics as st
import sys
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import select_quality_pool as v1

TECH={'C39','C40','I63','I64','I65'}
RELATED={'C34','C35','C36','C37','C38','M73'}
ASOF='2026-09-28'


def tech_tag(industry):
    prefix=industry[:3]
    return '科技行业代理' if prefix in TECH else ('科技相关待核实' if prefix in RELATED else '其他')


def metrics(rows,asof,sessions):
    if not rows or rows[-1]['date']!=asof: return None,'最新行情缺失'
    last=rows[-1]
    if last['isST']=='1': return None,'ST风险警示'
    if last['tradestatus']!='1': return None,'最新交易日停牌'
    rows=[r for r in rows if r['date'] in sessions]
    if len(rows)!=60 or sum(r['tradestatus']=='1' for r in rows)<57: return None,'60日行情或交易连续性不足'
    if any(v1.number(r['pctChg']) is None for r in rows if r['tradestatus']=='1'):return None,'收益缺失'
    amounts=[v1.number(r['amount']) or 0 for r in rows]
    avg20,avg60=st.mean(amounts[-20:]),st.mean(amounts)
    if avg20<50e6: return None,'20日均成交额不足5000万元'
    price,pb=v1.number(last['close']),v1.number(last['pbMRQ'])
    if price is None or price<3: return None,'价格低于3元或缺失'
    if pb is None or pb<=0: return None,'净资产非正或PB缺失'
    returns=[(v1.number(r['pctChg']) or 0)/100 for r in rows]
    return {'close':price,'pe_ttm':v1.number(last['peTTM']),'pb':pb,'avg_amount20':avg20,'amount_ratio':avg20/avg60,
            'return20':math.prod(1+x for x in returns[-20:])-1,'return60':math.prod(1+x for x in returns)-1,
            'vol60':st.pstdev(returns)*math.sqrt(252),'affordable_100_shares':price*100+5.1<=2375},None


def fundamental(raw,industry):
    names=['profit_2024','profit_2025','profit_h1','profit_prev_h1','balance_h1']
    expected={'profit_2024':'2024-12-31','profit_2025':'2025-12-31','profit_h1':'2026-06-30','profit_prev_h1':'2025-06-30','balance_h1':'2026-06-30','cash_2024':'2024-12-31','cash_2025':'2025-12-31'}
    for k,period in expected.items():
        for x in raw.get(k,[]):
            if not x.get('pubDate') or x['pubDate']>ASOF or x.get('statDate')!=period: return None,'财报报告期或披露时间不符'
    if any(not raw.get(k) for k in names): return None,'关键财报缺失'
    rows={k:raw[k][-1] for k in names}
    get=lambda k,f:v1.number(rows[k].get(f))
    profit=[get(k,'netProfit') for k in names[:4]];roe=[get(k,'roeAvg') for k in names[:3]]
    if any(x is None for x in profit):return None,'盈利数据缺失'
    debt=get('balance_h1','liabilityToAsset')
    if debt is None or not 0<=debt<1: return None,'资产负债数据异常'
    revenue=[get(k,'MBRevenue') for k in names[:4]]
    rev_available=all(x is not None and x>0 for x in revenue)
    annual_growth=revenue[1]/revenue[0]-1 if rev_available else None
    half_growth=revenue[2]/revenue[3]-1 if rev_available else None
    gm,gm_prev=get('profit_h1','gpMargin'),get('profit_prev_h1','gpMargin')
    margin_change=gm-gm_prev if gm is not None and gm_prev is not None else None
    # Profit/revenue ratios avoid meaningless percentage growth from a negative profit base.
    net_margin_change=profit[2]/revenue[2]-profit[3]/revenue[3] if rev_available else None
    current=get('balance_h1','currentRatio');cash_ratio=get('balance_h1','cashRatio')
    cash=[v1.number(raw[k][-1].get('CFOToNP')) if raw.get(k) else None for k in ('cash_2024','cash_2025')]
    financial=industry.startswith('J')
    mature=all(x>0 for x in profit) and all(x is not None and x>0 for x in roe) and profit[2]/profit[3]>=.5
    growth=(not financial and rev_available and annual_growth>=.10 and half_growth>=.20 and gm is not None and gm>=.10
            and margin_change is not None and margin_change>=-.02 and debt<=.65 and current is not None and current>=1.2
            and cash_ratio is not None and cash_ratio>=.5)
    loss=profit[2]<=0
    if loss: growth=growth and cash_ratio>=1.5 and current>=2 and debt<=.5 and net_margin_change is not None and net_margin_change>0
    if financial:
        if not mature or debt>.96:return None,'金融基础盈利/负债门槛未通过'
        lane='金融成熟'
    elif growth:
        lane='成长亏损观察' if loss else '成长盈利'
    elif mature and debt<=.85 and all(x is not None and x>0 for x in cash):lane='非金融成熟'
    else:return None,'成熟或成长路径均未通过'
    warnings=['研发投入及产出缺失','现金消耗周期及融资依赖未完成专项核查']
    if financial:warnings+=['资本充足率/不良率/偿付能力专门指标缺失']
    if loss:warnings+=['最新中报亏损；仅为研究观察，不推断亏损由研发造成']
    return {'lane':lane,'loss_making':loss,'financial_sector':financial,'roe2024':roe[0],'roe2025':roe[1],'roe_h1':roe[2],
            'revenue_growth2025':annual_growth,'revenue_growth_h1':half_growth,'gross_margin_h1':gm,'gross_margin_change':margin_change,
            'net_margin_change':net_margin_change,'profit_growth_h1':profit[2]/profit[3]-1 if profit[3]>0 else None,
            'cash_to_profit2024':cash[0],'cash_to_profit2025':cash[1],'debt_ratio':debt,'cash_ratio':cash_ratio,'current_ratio':current,
            'rd_intensity':None,'cash_runway_months':None,'evidence_gaps':warnings,
            'publication_dates':{k:raw[k][-1]['pubDate'] for k in expected if raw.get(k)}},None


def relative(rows,row,field,invert=False):
    value=row.get(field)
    if value is None:return None
    # Never fall back to another industry; shrink small peer groups toward neutral.
    peers=[x[field] for x in rows if x['industry']==row['industry'] and x['lane']==row['lane'] and x.get(field) is not None]
    signed=[-x if invert else x for x in peers]
    p=v1.percentile(signed,-value if invert else value)
    reliability=min(1,len(peers)/8)
    return .5+reliability*(p-.5)


def score(rows,sectors):
    for r in rows:
        r['roe_floor']=min(r['roe2024'],r['roe2025']) if r['roe2024'] is not None and r['roe2025'] is not None else None
        r['cash_floor']=min(r['cash_to_profit2024'],r['cash_to_profit2025'],2) if all(r[k] is not None for k in ('cash_to_profit2024','cash_to_profit2025')) else None
    for r in rows:
        if r['lane'].startswith('成长'):
            weights={'revenue_growth2025':.20,'revenue_growth_h1':.25,'gross_margin_change':.15,'net_margin_change':.15,'cash_ratio':.15,'debt_ratio':.10}
        elif r['financial_sector']:
            weights={'roe2025':.35,'roe_floor':.25,'profit_growth_h1':.25,'debt_ratio':.15}
        else:
            weights={'roe2025':.25,'roe_floor':.15,'cash_floor':.20,'revenue_growth_h1':.20,'profit_growth_h1':.10,'debt_ratio':.10}
        vals=[(w,relative(rows,r,k,k=='debt_ratio')) for k,w in weights.items()]
        coverage=sum(w for w,v in vals if v is not None)
        quality=sum(w*v for w,v in vals if v is not None) # Missing values receive no positive credit; never fabricate data.
        r['fundamental_score']=quality;r['scored_field_coverage']=coverage
        r['peer_count']=sum(x['industry']==r['industry'] and x['lane']==r['lane'] for x in rows)
        r['sector_score']=sectors[r['industry']]['score']
        liq=relative(rows,r,'avg_amount20');strength=relative(rows,r,'return60');vol=relative(rows,r,'vol60',True)
        # Sector weakness only costs at most five percentage points, never removes an entire industry.
        r['score']=.65*quality+.15*liq+.10*strength+.05*vol+.05*r['sector_score']
        r['score_components']={'fundamental':.65*quality,'liquidity':.15*liq,'relative_strength':.10*strength,'low_volatility':.05*vol,'sector_activity':.05*r['sector_score']}
    return sorted(rows,key=lambda r:(-r['score'],r['code']))


def choose(rows,target=188):
    selected=[];rejected=[];counts=Counter();fin=loss=0
    for r in rows:
        reason=None
        if len(selected)>=target:reason='最终综合排序之外'
        elif counts[r['industry']]>=18 or (r['industry'].startswith('J66') and counts[r['industry']]>=10):reason='行业集中度上限'
        elif r['financial_sector'] and fin>=18:reason='金融合计上限'
        elif r['loss_making'] and loss>=12:reason='亏损观察上限'
        if reason:rejected.append({'code':r['code'],'reason':reason});continue
        counts[r['industry']]+=1;fin+=int(r['financial_sector']);loss+=int(r['loss_making']);r['rank']=len(selected)+1
        r['reason']='%s；在%s的同路径企业内评分（%d个样本）；营收中期同比%s；%s'%(r['lane'],r['industry'],r['peer_count'],'缺失' if r['revenue_growth_h1'] is None else '%.1f%%'%(100*r['revenue_growth_h1']), '专项财务风险尚需核查' if r['financial_sector'] or r['loss_making'] else '基础财务与流动性条件通过')
        selected.append(r)
    return selected,rejected



def balanced_choose(ranked, valid, target=188):
    """Apportion count-based coverage first; scores choose stocks within allocated groups."""
    weights=Counter(r['industry'] for r in valid)
    groups=defaultdict(list)
    for r in ranked:groups[r['industry']].append(r)
    quotas=Counter();financial=0
    for _ in range(target):
        candidates=[industry for industry,rows in groups.items()
                    if quotas[industry]<min(len(rows),10 if industry.startswith('J66') else 18)
                    and (not industry.startswith('J') or financial<18)]
        if not candidates:break
        industry=min(candidates,key=lambda k:(-weights[k]/(quotas[k]+1),k))
        quotas[industry]+=1;financial+=int(industry.startswith('J'))
    picked=[];lane_quotas={};loss_count=0
    for industry in sorted(quotas):
        rows=groups[industry];lanes=defaultdict(list)
        for r in rows:lanes['growth' if r['lane'].startswith('成长') else 'mature'].append(r)
        allocated=Counter()
        for _ in range(quotas[industry]):
            choices=[k for k,v in lanes.items() if allocated[k]<len(v)]
            kind=min(choices,key=lambda k:(-len(lanes[k])/(allocated[k]+1),k))
            allocated[kind]+=1
        lane_quotas[industry]=dict(allocated)
        for kind,amount in allocated.items():
            chosen=0
            for r in lanes[kind]:
                if chosen>=amount:break
                if r['loss_making'] and loss_count>=12:continue
                picked.append(r);chosen+=1;loss_count+=int(r['loss_making'])
        # A global loss cap may leave a slot: use another qualified stock in the same industry.
        used={r['code'] for r in picked}
        missing=quotas[industry]-sum(r['industry']==industry for r in picked)
        for r in rows:
            if not missing:break
            if r['code'] in used or (r['loss_making'] and loss_count>=12):continue
            picked.append(r);used.add(r['code']);loss_count+=int(r['loss_making']);missing-=1
    picked.sort(key=lambda r:(-r['score'],r['code']))
    for i,r in enumerate(picked,1):
        r['rank']=i
        r['reason']='%s；行业名额%d只；同行业同路径%d个评分样本；中期营收同比%s；证据缺口见记录' % (r['lane'],quotas[r['industry']],r['peer_count'],'缺失' if r['revenue_growth_h1'] is None else '%.1f%%'%(100*r['revenue_growth_h1']))
    codes={r['code'] for r in picked}
    rejected=[{'code':r['code'],'reason':'行业及成长/成熟名额内排序未入选'} for r in ranked if r['code'] not in codes]
    return picked,rejected,{'industry_quotas':dict(quotas),'lane_quotas':lane_quotas,'basis':'D’Hondt on basic eligible industry counts; within-industry lane counts on financially qualified candidates; no tech floor'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--root',required=True);p.add_argument('--financial-cache');args=p.parse_args()
    source=Path(args.source);root=Path(args.root);root.mkdir(parents=True,exist_ok=True)
    rules={'version':'2.1','asof':ASOF,'target':188,'minimum_listing_days':730,'minimum_price':3,'minimum_amount20':50e6,
           'pe_positive_required':False,'sector_hard_exclusion':False,'preselection':'per industry union of top25 liquidity and top20 60-day relative return; no low-volatility preselection; no global truncation',
           'comparison':'same CSRC industry AND same screening lane; peer count below8 shrunk toward0.5; never cross-industry fallback',
           'industry_cap':18,'bank_cap':10,'financial_cap':18,'loss_watch_cap':12,'technology_minimum_quota':None,
           'score_weights':{'fundamental':.65,'liquidity':.15,'relative_strength':.10,'low_volatility':.05,'sector_activity':.05},
           'growth_gates':{'annual_revenue_growth':.10,'h1_revenue_growth':.20,'gross_margin_min':.10,'gross_margin_change_min':-.02,'debt_max':.65,'current_ratio_min':1.2,'cash_ratio_min':.5},
           'loss_extra_gates':{'cash_ratio_min':1.5,'current_ratio_min':2,'debt_max':.5,'net_margin_change_positive':True},
           'mature_gates':'annual2024/2025 and H1 2025/2026 profits and ROE positive; H1 profit decline <=50%; nonfinancial debt<=85% and positive 2024/2025 CFO/NP; financial debt<=96%',
           'technology_industry_proxy':sorted(TECH),'technology_related_unverified':sorted(RELATED),'rd_or_runway_missing_policy':'null and explicit evidence gaps; never claim measured R&D or cash runway',
           'allocation':'industry seats proportional to basic eligible stock counts under caps; within-industry growth/mature seats proportional to qualified candidate counts; D Hondt deterministic allocation',
           'scope':'screening comparison only; neither V1 nor frozen paper trial changes'}
    if (root/'rules.json').exists() and json.loads((root/'rules.json').read_text())!=rules:raise SystemExit('已冻结的V2规则不可改写')
    v1.write(root/'rules.json',rules)
    meta=json.loads((source/'metadata.json').read_text());assert meta['asof']==ASOF
    basics={r['code']:r for r in meta['basic']};industries={r['code']:r['industry'] for r in meta['industry'] if r.get('updateDate','')<=ASOF};sessions=set(meta['sessions'])
    valid=[];excluded=[]
    for s in meta['stocks']:
        c=s['code']
        if not v1.mainboard(c):continue
        b=basics.get(c,{})
        if b.get('type')!='1' or b.get('status')!='1' or b.get('outDate') or not b.get('ipoDate') or (dt.date.fromisoformat(ASOF)-dt.date.fromisoformat(b['ipoDate'])).days<730 or not industries.get(c):excluded.append({'code':c,'reason':'上市状态、年限或行业条件'});continue
        path=source/'daily'/(c+'.json')
        if not path.exists():excluded.append({'code':c,'reason':'原始行情缺失'});continue
        m,why=metrics(json.loads(path.read_text()),ASOF,sessions)
        if why:excluded.append({'code':c,'reason':why});continue
        valid.append(dict(m,code=c,name=b['code_name'],industry=industries[c],tech_tag=tech_tag(industries[c])))
    groups=defaultdict(list)
    for r in valid:groups[r['industry']].append(r)
    sectors=[]
    for industry,rows in groups.items():sectors.append({'industry':industry,'count':len(rows),'return60':st.median(x['return60'] for x in rows),'amount_ratio':st.median(x['amount_ratio'] for x in rows),'breadth20':sum(x['return20']>0 for x in rows)/len(rows)})
    for s in sectors:s['score']=.4*v1.percentile([x['return60'] for x in sectors],s['return60'])+.35*v1.percentile([x['amount_ratio'] for x in sectors],s['amount_ratio'])+.25*v1.percentile([x['breadth20'] for x in sectors],s['breadth20'])
    sector_map={s['industry']:s for s in sectors};pre=[]
    for rows in groups.values():
        codes={r['code'] for r in sorted(rows,key=lambda r:(-r['avg_amount20'],r['code']))[:25]}
        codes.update(r['code'] for r in sorted(rows,key=lambda r:(-r['return60'],r['code']))[:20])
        pre.extend(r for r in rows if r['code'] in codes)
        excluded.extend({'code':r['code'],'reason':'行业内流动性/强度预筛之外，未深查财报'} for r in rows if r['code'] not in codes)
    pre.sort(key=lambda r:r['code']);v1.write(root/'prescreen.json',pre);v1.write(root/'sectors.json',sectors)
    (root/'financial').mkdir(exist_ok=True)
    copied=0
    for r in pre:
        path=(Path(args.financial_cache) if args.financial_cache else source)/'financial'/(r['code']+'.json');dest=root/'financial'/path.name
        if path.exists() and not dest.exists():shutil.copyfile(str(path),str(dest));copied+=1
    print('V2 DAILY',len(valid),'FINANCIAL',len(pre),'REUSED',copied,flush=True)
    errors=v1.parallel([(str(root),r['code'],ASOF) for r in pre],v1.financial_job,'v2_financial',root)
    qualified=[]
    for r in pre:
        path=root/'financial'/(r['code']+'.json')
        if not path.exists():excluded.append({'code':r['code'],'reason':'财务下载失败'});continue
        f,why=fundamental(json.loads(path.read_text()),r['industry'])
        if why:excluded.append({'code':r['code'],'reason':why});continue
        qualified.append(dict(r,**f))
    ranked=score(qualified,sector_map);selected,rejected,allocation=balanced_choose(ranked,valid);excluded.extend(rejected)
    previous=json.loads((source/'selected.json').read_text());old={x['code']:x for x in previous};new={x['code']:x for x in selected}
    summary={'version':'2.1','allocation':allocation,'asof':ASOF,'selected_count':len(selected),'daily_pass':len(valid),'financial_checked':len(pre),'qualified_count':len(qualified),'industry_counts':dict(Counter(x['industry'] for x in selected)),
             'lane_counts':dict(Counter(x['lane'] for x in selected)),'tech_counts':dict(Counter(x['tech_tag'] for x in selected)),
             'v1_tech_counts':dict(Counter(tech_tag(x['industry']) for x in previous)),
             'financial_count':sum(x['financial_sector'] for x in selected),'v1_financial_count':sum(x['financial_sector'] for x in previous),
             'loss_making_count':sum(x['loss_making'] for x in selected),'kept_count':len(set(old)&set(new)),'added':[new[c] for c in sorted(set(new)-set(old))],'removed':[old[c] for c in sorted(set(old)-set(new))],
             'rejection_counts':dict(Counter(x['reason'] for x in excluded)),'errors':errors,
             'rules_hash':hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest(),'script_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'dependency_hash':hashlib.sha256(Path(v1.__file__).read_bytes()).hexdigest(),'source_metadata_hash':hashlib.sha256((source/'metadata.json').read_bytes()).hexdigest(),'source_v1_selected_hash':hashlib.sha256((source/'selected.json').read_bytes()).hexdigest(),
             'limitations':['同业同路径比较，不等于严格行业中性组合','成长指标是营收和利润率代理，不证明研发产出或未来盈利','未取得完整研发费用、现金消耗周期和融资依赖数据','科技行业标签只是行业代理，设备/汽车不自动当作科技股','金融专项监管指标缺失；亏损观察不是立即买入信号','当前名单不可回填历史冒充无偏回测','V1与V2没有完成收益优劣验证；不更改现有模拟试验']}
    v1.write(root/'selected.json',selected);v1.write(root/'qualified.json',ranked);v1.write(root/'rejected.json',excluded);v1.write(root/'summary.json',summary)
    fields=['rank','code','name','industry','tech_tag','lane','score','peer_count','revenue_growth2025','revenue_growth_h1','gross_margin_change','net_margin_change','roe2025','cash_ratio','current_ratio','debt_ratio','loss_making','affordable_100_shares','reason']
    with (root/'pool188-v2.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(selected)
    v1.write(root/'progress.json',{'stage':'complete' if len(selected)==188 else 'insufficient','selected_count':len(selected)})
    print(json.dumps({k:v for k,v in summary.items() if k not in ('added','removed')},ensure_ascii=False),flush=True)
    if len(selected)!=188:raise SystemExit('合格候选不足188，不放宽规则凑数')

if __name__=='__main__':main()

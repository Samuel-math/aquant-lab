#!/usr/bin/env python3
"""Reproducible current-date pool screen; independent from the frozen paper trial."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import multiprocessing as mp
import socket
import statistics as st
import time
from collections import Counter, defaultdict
from pathlib import Path

BS=None

def write(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False));temp.replace(path)

def fetch(name,kwargs):
    for attempt in range(3):
        try:
            r=getattr(BS,name)(**kwargs);rows=[]
            if r.error_code!='0': raise RuntimeError(r.error_code+':'+r.error_msg)
            while r.next(): rows.append(dict(zip(r.fields,r.get_row_data())))
            if r.error_code!='0': raise RuntimeError(r.error_code+':'+r.error_msg)
            time.sleep(.05)
            return rows
        except Exception:
            if attempt==2: raise
            time.sleep(1+attempt);BS.logout();BS.login()

def init():
    global BS
    import baostock
    BS=baostock;socket.setdefaulttimeout(30)
    r=BS.login()
    if r.error_code!='0': raise RuntimeError('login failed')

def mainboard(c): return c.startswith(('sh.600','sh.601','sh.603','sh.605','sz.000','sz.001','sz.002','sz.003'))

def number(x):
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except (ValueError,TypeError): return None

def daily_job(job):
    root,code,start,asof=job;p=Path(root)/'daily'/(code+'.json')
    if p.exists(): return code,True
    try:
        rows=fetch('query_history_k_data_plus',dict(code=code,fields='date,code,close,preclose,amount,volume,tradestatus,isST,pctChg,turn,peTTM,pbMRQ',start_date=start,end_date=asof,frequency='d',adjustflag='3'))
        write(p,rows);return code,True
    except Exception as e: return code,str(e)

def financial_job(job):
    root,code,asof=job;p=Path(root)/'financial'/(code+'.json')
    if p.exists(): return code,True
    periods=[('profit_2024','query_profit_data',2024,4),('profit_2025','query_profit_data',2025,4),('profit_h1','query_profit_data',2026,2),('profit_prev_h1','query_profit_data',2025,2),('cash_2025','query_cash_flow_data',2025,4),('cash_2024','query_cash_flow_data',2024,4),('balance_h1','query_balance_data',2026,2)]
    try:
        result={}
        for label,method,year,quarter in periods:
            rows=fetch(method,dict(code=code,year=year,quarter=quarter))
            result[label]=[r for r in rows if r.get('pubDate') and r['pubDate']<=asof and r.get('statDate','')<=asof]
        result['retrieved_at']=dt.datetime.now(dt.timezone.utc).isoformat();write(p,result);return code,True
    except Exception as e: return code,str(e)

def percentile(values,x):
    return (sum(v<x for v in values)+.5*sum(v==x for v in values))/len(values) if values else .5

def metrics(rows,asof,sessions):
    if not rows or rows[-1]['date']!=asof: return None,'最新行情缺失'
    last=rows[-1]
    if last['isST']=='1': return None,'ST风险警示'
    if last['tradestatus']!='1': return None,'最新交易日停牌'
    rows=[r for r in rows if r['date'] in sessions]
    if len(rows)<60: return None,'不足60个交易日行情'
    if sum(r['tradestatus']=='1' for r in rows)<57: return None,'近60日停牌较多'
    if any(number(r['pctChg']) is None for r in rows if r['tradestatus']=='1'): return None,'收益行情缺失'
    amounts=[number(r['amount']) or 0 for r in rows]
    avg20=st.mean(amounts[-20:]);avg60=st.mean(amounts)
    if avg20<50_000_000: return None,'20日平均成交额不足5000万元'
    close=number(last['close'])
    if close is None or close<3: return None,'股价低于3元或缺失'
    pe,pb=number(last['peTTM']),number(last['pbMRQ'])
    if pe is None or pe<=0 or pb is None or pb<=0: return None,'TTM盈利或净资产非正/估值缺失'
    returns=[(number(r['pctChg']) or 0)/100 for r in rows]
    growth=lambda vals: math.prod(1+x for x in vals)-1
    peak=wealth=1.;dd=0
    for r in returns:
        wealth*=1+r;peak=max(peak,wealth);dd=min(dd,wealth/peak-1)
    return {'close':close,'avg_amount20':avg20,'avg_amount60':avg60,'amount_ratio':avg20/avg60,'return20':growth(returns[-20:]),'return60':growth(returns),'vol60':st.pstdev(returns)*math.sqrt(252),'drawdown60':dd,'pe_ttm':pe,'pb':pb,'affordable_100_shares':close*100+5.1<=2375},None

def evaluate_financial(raw,industry):
    names=['profit_2024','profit_2025','profit_h1','profit_prev_h1','balance_h1']
    if any(not raw.get(k) for k in names): return None,'盈利或负债财报缺失'
    row={k:raw[k][-1] for k in names};financial=industry.startswith('J')
    profits=[number(row[k].get('netProfit')) for k in names[:4]]
    roes=[number(row[k].get('roeAvg')) for k in names[:3]]
    if any(p is None or p<=0 for p in profits): return None,'两年年报或两期中报净利润非正/缺失'
    if any(r is None for r in roes): return None,'ROE缺失'
    if min(roes[:2])<.06 or roes[2]<.025: return None,'年度ROE不足6%或中期ROE不足2.5%'
    growth=profits[2]/profits[3]-1
    if growth<-.30: return None,'中期净利润同比下降超过30%'
    debt=number(row['balance_h1'].get('liabilityToAsset'))
    if debt is None or not 0<=debt<1: return None,'资产负债率缺失/异常'
    if not financial and debt>.80: return None,'非金融资产负债率超过80%'
    if financial and debt>.96: return None,'金融资产负债率超过96%'
    cash=[]
    for k in ('cash_2024','cash_2025'):
        cash.append(number(raw[k][-1].get('CFOToNP')) if raw.get(k) else None)
    if not financial and (any(c is None or c<=0 for c in cash) or st.mean(cash)<.6):
        return None,'非金融两年经营现金流质量不足/缺失'
    return {'roe2024':roes[0],'roe2025':roes[1],'roe_h1':roes[2],'profit_growth_h1':growth,'debt_ratio':debt,'cash_to_profit2024':cash[0],'cash_to_profit2025':cash[1],'financial_sector':financial,'publication_dates':{k:row[k]['pubDate'] for k in names}},None

def parallel(jobs,fn,label,root):
    errors=[]
    with mp.Pool(3,initializer=init) as pool:
        for i,(code,result) in enumerate(pool.imap_unordered(fn,jobs),1):
            if result is not True: errors.append({'code':code,'error':result})
            if i%25==0 or i==len(jobs):
                print(label,i,'/',len(jobs),'errors',len(errors),flush=True)
                write(Path(root)/'progress.json',{'stage':label,'done':i,'total':len(jobs),'errors':errors,'updated_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    return errors

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--asof',default='2026-09-28');args=p.parse_args()
    root=Path(args.root);root.mkdir(parents=True,exist_ok=True);asof=args.asof
    if asof!='2026-09-28': raise SystemExit('财报窗口冻结于2026-09-28；其他日期须更新规则并建立新版本')
    rule={'asof':asof,'target':188,'universe':'all current Shanghai/Shenzhen main-board A shares','daily_sessions':60,'minimum_listing_days':730,'minimum_amount20':50_000_000,'minimum_price':3,'sector_exclusion':'bottom 25% activity score AND at least two weak signals: negative 60-day excess return; amount20/amount60<0.8; positive-return20 breadth<0.4','sector_score_weights':{'relative_return60':.4,'amount_ratio':.35,'breadth20':.25},'financial_prescreen':'up to top 35 liquidity/low-volatility stocks per industry, maximum 1000 total, ranked by 60% liquidity and 40% low volatility','stock_score_weights':{'quality':.60,'sector_activity':.15,'liquidity':.15,'low_volatility':.10},'industry_cap':24,'financial_sector_cap':38,'annual_roe_min':.06,'halfyear_roe_min':.025,'profit_growth_min':-.30,'nonfinancial_debt_max':.80,'financial_debt_max':.96,'nonfinancial_two_year_cfo_positive':True,'nonfinancial_average_cfo_np_min':.6,'no_future_data':True,'cash_budget_not_a_pool_filter':True}
    path=root/'rules.json'
    if path.exists() and json.loads(path.read_text())!=rule: raise SystemExit('规则已冻结，禁止覆盖')
    write(path,rule)
    init()
    metadata=root/'metadata.json'
    if metadata.exists(): meta=json.loads(metadata.read_text())
    else:
        calendar=fetch('query_trade_dates',dict(start_date='2026-06-01',end_date=asof))
        sessions=[r['calendar_date'] for r in calendar if r['is_trading_day']=='1'][-60:]
        meta={'asof':asof,'sessions':sessions,'stocks':fetch('query_all_stock',dict(day=asof)),
              'basic':fetch('query_stock_basic',{}),'industry':fetch('query_stock_industry',dict(date=asof)),
              'retrieved_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        write(metadata,meta)
    BS.logout()
    sessions=meta['sessions'];basic={r['code']:r for r in meta['basic']};industries={r['code']:r['industry'] for r in meta['industry'] if r.get('updateDate','')<=asof}
    rejected=[];codes=[]
    for row in meta['stocks']:
        c=row['code']
        if not mainboard(c): continue
        b=basic.get(c,{})
        reason=None
        if b.get('type')!='1' or b.get('status')!='1' or b.get('outDate'):reason='非正常上市股票'
        elif not b.get('ipoDate') or (dt.date.fromisoformat(asof)-dt.date.fromisoformat(b['ipoDate'])).days<730:reason='上市未满两年'
        elif not industries.get(c):reason='行业分类缺失'
        if reason:rejected.append({'code':c,'reason':reason})
        else:codes.append(c)
    errors=parallel([(str(root),c,sessions[0],asof) for c in codes],daily_job,'daily',root)
    valid=[]
    for c in codes:
        path=root/'daily'/(c+'.json')
        if not path.exists(): rejected.append({'code':c,'reason':'行情下载失败'});continue
        m,why=metrics(json.loads(path.read_text()),asof,set(sessions))
        if why:rejected.append({'code':c,'reason':why});continue
        valid.append(dict(m,code=c,name=basic[c]['code_name'],industry=industries[c]))
    groups=defaultdict(list)
    for r in valid:groups[r['industry']].append(r)
    benchmark=st.median(r['return60'] for r in valid)
    sectors=[]
    for industry,rows in groups.items():
        sectors.append({'industry':industry,'count':len(rows),'return60':st.median(r['return60'] for r in rows),
                        'excess60':st.median(r['return60'] for r in rows)-benchmark,
                        'amount_ratio':sum(r['avg_amount20'] for r in rows)/sum(r['avg_amount60'] for r in rows),
                        'breadth20':sum(r['return20']>0 for r in rows)/len(rows)})
    for s in sectors:
        s['score']=.4*percentile([x['excess60'] for x in sectors],s['excess60'])+.35*percentile([x['amount_ratio'] for x in sectors],s['amount_ratio'])+.25*percentile([x['breadth20'] for x in sectors],s['breadth20'])
        s['weak_signals']=int(s['excess60']<0)+int(s['amount_ratio']<.8)+int(s['breadth20']<.4)
    for s in sectors:s['excluded']=percentile([x['score'] for x in sectors],s['score'])<=.25 and s['weak_signals']>=2
    sector_map={s['industry']:s for s in sectors};pre=[]
    for industry,rows in groups.items():
        if sector_map[industry]['excluded']:
            rejected.extend({'code':r['code'],'reason':'板块活跃度初筛未通过'} for r in rows);continue
        for r in rows:r['prescore']=.6*percentile([x['avg_amount20'] for x in valid],r['avg_amount20'])+.4*percentile([-x['vol60'] for x in valid],-r['vol60'])
        ordered=sorted(rows,key=lambda r:(-r['prescore'],r['code']))
        pre.extend(ordered[:35]);rejected.extend({'code':r['code'],'reason':'行业内流动性/低波动预筛35名之外，未深查财报'} for r in ordered[35:])
    pre.sort(key=lambda r:(-r['prescore'],r['code']))
    rejected.extend({'code':r['code'],'reason':'财务深查预算1000只之外'} for r in pre[1000:]);pre=pre[:1000]
    write(root/'sectors.json',sectors);write(root/'prescreen.json',pre)
    print('FINANCIAL SHORTLIST',len(pre),'SECTORS',len(sectors),'EXCLUDED',sum(s['excluded'] for s in sectors),flush=True)
    errors+=parallel([(str(root),r['code'],asof) for r in pre],financial_job,'financial',root)
    qualified=[]
    for r in pre:
        path=root/'financial'/(r['code']+'.json')
        if not path.exists(): rejected.append({'code':r['code'],'reason':'财务下载失败'});continue
        f,why=evaluate_financial(json.loads(path.read_text()),r['industry'])
        if why:rejected.append({'code':r['code'],'reason':why});continue
        qualified.append(dict(r,**f))
    for r in qualified:
        peers=[x for x in qualified if x['financial_sector']==r['financial_sector']]
        roe=.5*(r['roe2024']+r['roe2025'])
        rq=percentile([.5*(x['roe2024']+x['roe2025']) for x in peers],roe)
        stability=percentile([min(x['roe2024'],x['roe2025']) for x in peers],min(r['roe2024'],r['roe2025']))
        growth=percentile([x['profit_growth_h1'] for x in peers],r['profit_growth_h1'])
        debt=percentile([-x['debt_ratio'] for x in peers],-r['debt_ratio'])
        if r['financial_sector']:quality=.45*rq+.25*stability+.20*growth+.10*debt
        else:
            cash=percentile([min(x['cash_to_profit2024'],x['cash_to_profit2025'],2.) for x in peers],min(r['cash_to_profit2024'],r['cash_to_profit2025'],2.))
            quality=.35*rq+.20*stability+.20*cash+.15*growth+.10*debt
        r['quality_score']=quality;r['sector_score']=sector_map[r['industry']]['score']
        r['score']=.60*quality+.15*r['sector_score']+.15*percentile([x['avg_amount20'] for x in qualified],r['avg_amount20'])+.10*percentile([-x['vol60'] for x in qualified],-r['vol60'])
    qualified.sort(key=lambda r:(-r['score'],r['code']))
    selected=[];counts=Counter();financial_count=0
    for r in qualified:
        if len(selected)>=188:rejected.append({'code':r['code'],'reason':'综合排序在最终188只之外'});continue
        if counts[r['industry']]>=24 or (r['financial_sector'] and financial_count>=38):rejected.append({'code':r['code'],'reason':'行业/金融板块集中度上限'});continue
        counts[r['industry']]+=1;financial_count+=int(r['financial_sector']);r['rank']=len(selected)+1
        r['reason']='两年年报及两期中报盈利；2025年ROE %.1f%%；中期利润同比 %.1f%%；20日均成交额 %.2f亿元；行业活跃度通过；%s' % (100*r['roe2025'],100*r['profit_growth_h1'],r['avg_amount20']/1e8,'金融股独立财务规则' if r['financial_sector'] else '两年经营现金流质量通过')
        selected.append(r)
    summary={'asof':asof,'selected_count':len(selected),'qualified_count':len(qualified),'mainboard_count':sum(mainboard(r['code']) for r in meta['stocks']),
             'daily_screen_pass':len(valid),'financial_checked':len(pre),'industry_counts':dict(counts),'excluded_sectors':[s for s in sectors if s['excluded']],
             'errors':errors,'rejection_counts':dict(Counter(r['reason'] for r in rejected)),
             'rule_hash':hashlib.sha256(json.dumps(rule,sort_keys=True).encode()).hexdigest(),
             'script_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'limitations':['当前可得数据筛选，不用于回填历史收益','财报字段可能被供应方事后修订，不保证严格历史时点数据库','行业为证监会行业，不代表全部主题概念板块','流动性预筛限制财务深查范围，不声称全市场最优','金融企业未审计不良贷款/偿付能力等专门指标','高价股可入研究池，1万元组合另行检查整手预算','优质为本规则的操作性定义，不保证盈利，未全面核查审计意见/诉讼/财务造假']}
    write(root/'selected.json',selected);write(root/'qualified.json',qualified);write(root/'rejected.json',rejected);write(root/'summary.json',summary)
    fields=['rank','code','name','industry','score','quality_score','sector_score','close','avg_amount20','return60','roe2024','roe2025','roe_h1','profit_growth_h1','debt_ratio','cash_to_profit2024','cash_to_profit2025','pe_ttm','pb','affordable_100_shares','reason']
    with (root/'pool188.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(selected)
    lines=['# 沪深主板188只研究底池','', '数据截止：'+asof+'；最终入选：'+str(len(selected))+'。','', '该名单独立于现有24只模拟试验，不改变已冻结的交易计划。','', '|排名|代码|名称|行业|综合分|2025 ROE|中期利润同比|20日均成交额（亿）|','|---:|---|---|---|---:|---:|---:|---:|']
    lines += ['|%d|%s|%s|%s|%.2f|%.1f%%|%.1f%%|%.2f|'%(r['rank'],r['code'],r['name'],r['industry'],100*r['score'],100*r['roe2025'],100*r['profit_growth_h1'],r['avg_amount20']/1e8) for r in selected]
    lines += ['', '## 规则与限制','']+['- '+s for s in summary['limitations']]
    (root/'pool188.md').write_text('\n'.join(lines)+'\n')
    write(root/'progress.json',{'stage':'complete' if len(selected)==188 else 'insufficient','selected_count':len(selected),'updated_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if len(selected)!=188: raise SystemExit('合格数量不足188，未放宽规则凑数')

if __name__=='__main__':main()

"""Incremental public BaoStock adapter. Retains daily bars and 10:00 observations only."""
import csv
import datetime as dt
import hashlib
import json
import random
import socket
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from .core import ValidationError, digest, write_json
from .data import FIELDS, CSVData
from .intraday import FIELDS as QUOTE_FIELDS
from .operations import lock, status
from .storage import admission

DAILY_FIELDS = 'date,code,open,close,preclose,volume,amount,tradestatus,isST'
MINUTE_FIELDS = 'date,time,code,close,volume'


def collect(result):
    if result.error_code != '0':
        raise ValidationError('BaoStock: ' + result.error_code + ' ' + result.error_msg)
    rows=[]
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    if result.error_code != '0':
        raise ValidationError('BaoStock分页失败: ' + result.error_msg)
    return rows


def main_board(code):
    return code.startswith(('sh.600','sh.601','sh.603','sh.605','sz.000','sz.001','sz.002','sz.003'))


def internal_symbol(code):
    exchange, number = code.split('.')
    return number + '.' + exchange.upper()


def extract_1000(rows):
    # BaoStock minute timestamps denote bar end; accept exactly HHMMSS=100000.
    output=[]
    for r in rows:
        if r['time'][8:14] != '100000': continue
        if len(r['time']) != 17 or r['time'][:8] != r['date'].replace('-',''):
            raise ValidationError('分钟时间戳格式异常')
        output.append(r)
    return output


def merge_revision(old, incoming, key, journal, category, fetched_at):
    result={r[key]:r for r in old}
    changes=[]
    for row in incoming:
        previous=result.get(row[key])
        if previous is not None and previous != row:
            changes.append({'category':category,'fetched_at':fetched_at,'key':row[key], 'previous':previous,'replacement':row})
        result[row[key]]=row
    if changes:
        with Path(journal).open('a',encoding='utf-8') as f:
            for change in changes: f.write(json.dumps(change,ensure_ascii=False)+'\n')
    return [result[k] for k in sorted(result)]


def write_csv(path, fields, rows):
    path=Path(path); temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    temp.replace(path)


def normalized(cache, dates):
    basic=cache['basic']; code=basic['code']; symbol=internal_symbol(code)
    factors=sorted(cache['factors'],key=lambda r:r['dividOperateDate'])
    rows=[]; last_price=None
    factor=1.; index=0
    for raw in sorted(cache['daily'],key=lambda r:r['date']):
        date=raw['date']
        if date not in dates: continue
        while index<len(factors) and factors[index]['dividOperateDate']<=date:
            factor=float(factors[index]['backAdjustFactor']); index+=1
        suspended=raw['tradestatus']!='1'
        close=float(raw['close'] or last_price or raw['preclose'] or 0)
        op=float(raw['open'] or close)
        if min(close,op)<=0: raise ValidationError('缺失价格且无前值: '+code+' '+date)
        pre=Decimal(raw['preclose'] or str(last_price or close))
        is_st=raw['isST']=='1'
        rate=Decimal('.05') if is_st and date<'2026-07-06' else Decimal('.10')
        # Pilot universe predates start by >120 days, so no IPO no-limit windows.
        upper=float((pre*(1+rate)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        lower=float((pre*(1-rate)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        rows.append(dict(date=date,symbol=symbol,name=basic['code_name'],
                         board='SSE_MAIN' if code.startswith('sh.') else 'SZSE_MAIN',
                         listed_date=basic['ipoDate'],open=op,close=close,volume=float(raw['volume'] or 0),
                         amount=float(raw['amount'] or 0),adj_factor=factor,suspended=int(suspended),
                         risk_warning=int(is_st),delisting=int(bool(basic['outDate']) and date>=basic['outDate']),
                         limit_up=upper,limit_down=lower,buy_lot=100))
        last_price=close
    quotes=[]
    for raw in cache['quotes']:
        if raw['date'] not in dates: continue
        price=float(raw['close'] or 0); volume=float(raw['volume'] or 0)
        if price<=0: continue
        quotes.append(dict(date=raw['date'],symbol=symbol,timestamp=raw['date']+'T10:00:00+08:00',
                           price=price,volume=volume,tradable=int(volume>0),volume_window_minutes=30))
    return rows,quotes


def sync_baostock(root, start='2025-01-02', end=None, sample_size=24, seed=17):
    # Optional dependency is imported only when explicitly pulling real data.
    import baostock as bs
    end=end or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    if start>end or not 3<=sample_size<=200:
        raise ValidationError('数据范围或股票样本数量非法（3..200）')
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    with lock(root/'sync.lock'):
        status(root/'sync.status.json',end,'running')
        logged_in=False
        try:
            storage=admission(root)
            socket.setdefaulttimeout(60)
            response=bs.login()
            if response.error_code!='0': raise ValidationError('BaoStock登录失败: '+response.error_msg)
            logged_in=True
            calendar_end=(dt.date.fromisoformat(end)+dt.timedelta(days=35)).isoformat()
            calendar=collect(bs.query_trade_dates(start_date=start,end_date=calendar_end))
            trading=[r['calendar_date'] for r in calendar if r['is_trading_day']=='1']
            expected=max(d for d in trading if d<=end)
            manifest_path=root/'universe.json'
            if manifest_path.exists():
                universe=json.loads(manifest_path.read_text(encoding='utf-8'))
                if universe['start']!=start or universe['seed']!=seed or universe['sample_size']!=sample_size:
                    raise ValidationError('固定股票池参数变化，请使用新数据目录，禁止悄悄替换历史样本')
            else:
                historical=collect(bs.query_all_stock(day=trading[0]))
                codes=sorted(r['code'] for r in historical if main_board(r['code']))
                random.Random(seed).shuffle(codes)
                selected=[]
                for code in codes:
                    basic_rows=collect(bs.query_stock_basic(code=code))
                    if not basic_rows: continue
                    basic=basic_rows[0]
                    if basic['type']!='1' or not basic['ipoDate']: continue
                    if (dt.date.fromisoformat(start)-dt.date.fromisoformat(basic['ipoDate'])).days<120: continue
                    selected.append(basic)
                    if len(selected)==sample_size: break
                if len(selected)!=sample_size: raise ValidationError('历史股票池数量不足')
                universe={'start':start,'seed':seed,'sample_size':sample_size,'selection_date':trading[0],
                          'selection':'seeded sample of historical main-board list, not return-selected; not full market',
                          'stocks':selected}
                write_json(manifest_path,universe)
            raw_dir=root/'core'; raw_dir.mkdir(exist_ok=True)
            fetched_at=dt.datetime.now(dt.timezone.utc).isoformat()
            received=0
            for n,basic in enumerate(universe['stocks'],1):
                code=basic['code']; file=raw_dir/(code+'.json')
                cache=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'basic':basic,'daily':[],'quotes':[],'factors':[]}
                # Re-fetch a short overlap to catch corrections; preserve every changed value.
                fetched_through=cache.get('fetched_through')
                if fetched_through == expected:
                    continue
                fetch_start=max(start,(dt.date.fromisoformat(fetched_through)-dt.timedelta(days=7)).isoformat()) if fetched_through else start
                daily=collect(bs.query_history_k_data_plus(code,DAILY_FIELDS,start_date=fetch_start,end_date=expected,frequency='d',adjustflag='3'))
                minute=collect(bs.query_history_k_data_plus(code,MINUTE_FIELDS,start_date=fetch_start,end_date=expected,frequency='30',adjustflag='3'))
                quotes=extract_1000(minute)
                active = not basic['outDate'] or expected <= basic['outDate']
                latest = next((r for r in daily if r['date'] == expected), None)
                if active and latest is None:
                    raise ValidationError('数据源尚未更新到预期交易日: ' + code + ' ' + expected)
                if latest and latest['tradestatus'] == '1' and not any(r['date'] == expected for r in quotes):
                    raise ValidationError('数据源10:00分钟线尚未更新: ' + code + ' ' + expected)
                factors=collect(bs.query_adjust_factor(code=code,start_date='1990-01-01',end_date=expected))
                journal=raw_dir/(code+'.revisions.jsonl')
                cache['daily']=merge_revision(cache['daily'],daily,'date',journal,'daily',fetched_at)
                cache['quotes']=merge_revision(cache['quotes'],quotes,'date',journal,'1000',fetched_at)
                cache['factors']=merge_revision(cache['factors'],factors,'dividOperateDate',journal,'adjustment',fetched_at)
                cache['fetched_through']=expected
                cache['latest_receipt']={'fetched_at':fetched_at,'start':fetch_start,'end':expected,
                                         'daily_rows':len(daily),'minute_rows_received':len(minute),
                                         'minute_payload_hash':digest(minute),'retained_1000_rows':len(quotes),
                                         'full_minute_retained':False,'frequency':'30','adjustflag':'3'}
                write_json(file,cache)
                received+=len(minute)
                print('SYNC %d/%d %s through %s; received %d bars, retained %d 10:00 observations' % (n,sample_size,code,expected,len(minute),len(quotes)),flush=True)
                del minute
                time.sleep(.1)
            dataset=root/'dataset'; dataset.mkdir(exist_ok=True)
            marker=dataset/'.writing'; marker.touch()
            all_rows=[]; all_quotes=[]
            days=[d for d in trading if d<=expected]
            expected_symbols={d:[] for d in days}
            for basic in universe['stocks']:
                cache=json.loads((raw_dir/(basic['code']+'.json')).read_text(encoding='utf-8'))
                rows,quotes=normalized(cache,set(days)); all_rows.extend(rows); all_quotes.extend(quotes)
                for d in days:
                    if basic['ipoDate']<=d and (not basic['outDate'] or d<=basic['outDate']):
                        expected_symbols[d].append(internal_symbol(basic['code']))
            all_rows.sort(key=lambda r:(r['date'],r['symbol'])); all_quotes.sort(key=lambda r:(r['date'],r['symbol']))
            write_csv(dataset/'bars.csv',FIELDS,all_rows)
            write_csv(dataset/'intraday.csv',QUOTE_FIELDS+['volume_window_minutes'],all_quotes)
            write_csv(dataset/'calendar.csv',['date'],[{'date':d} for d in trading])
            metadata={'kind':'real','source':'BaoStock 0.9.4 public API','asof':expected,'requested_end':end,
                      'fetched_at':fetched_at,'universe':universe,'expected_symbols':expected_symbols,
                      'execution_observation':'unadjusted close of 30-minute bar ending at 10:00+08:00',
                      'volume_window_minutes':30,'storage_policy':'retain daily and 10:00 only; revision journal; no full intraday archive',
                      'limitations':['固定历史股票池随机样本，非全市场','名称来自证券基本信息，不作为历史预测特征',
                                     '前30分钟成交量不是一分钟成交量；撮合仅使用每分钟均量近似',
                                     '分红送转持仓核算仍未实现，收益标签跨除权日期会保留异常状态',
                                     '涨跌停价为普通主板规则推导，异常特殊交易状态需另行审计']}
            write_json(dataset/'metadata.json',metadata); marker.unlink()
            parsed=CSVData(dataset)
            for day in days: parsed.snapshot(day)
            last=parsed.snapshot(expected)
            missing=[s for s,r in last.items() if not r['suspended'] and (expected,s) not in parsed.intraday]
            if missing: raise ValidationError('最新交易日10:00行情不完整: '+','.join(missing))
            result={'dataset':str(dataset),'asof':expected,'symbols':sample_size,'daily_rows':len(all_rows),
                    'retained_1000_rows':len(all_quotes),'minute_rows_received_this_run':received,
                    'storage':admission(root),'data_hash':parsed.version}
            write_json(root/'latest-sync.json',result)
            status(root/'sync.status.json',expected,'ok',**result)
            return result
        except Exception as e:
            status(root/'sync.status.json',end,'failed',error=str(e)); raise
        finally:
            if logged_in: bs.logout()

import json
import socket
socket.setdefaulttimeout(20)
import baostock as bs

def rows(rs, maximum=100000):
    print('RESPONSE', rs.error_code, rs.error_msg, flush=True)
    result=[]
    while rs.error_code=='0' and rs.next():
        result.append(dict(zip(rs.fields, rs.get_row_data())))
        if len(result)>=maximum: break
    return result
lg=bs.login()
print('LOGIN',lg.error_code,lg.error_msg,flush=True)
assert lg.error_code=='0'
print('CALENDAR',json.dumps(rows(bs.query_trade_dates(start_date='2026-09-20',end_date='2026-10-10')),ensure_ascii=False),flush=True)
print('DAILY',json.dumps(rows(bs.query_history_k_data_plus('sh.600000','date,code,open,close,preclose,volume,amount,tradestatus,isST',start_date='2026-09-20',end_date='2026-09-27',frequency='d',adjustflag='3')),ensure_ascii=False),flush=True)
r=rows(bs.query_history_k_data_plus('sh.600000','date,time,code,open,high,low,close,volume,amount',start_date='2026-09-24',end_date='2026-09-27',frequency='5',adjustflag='3'))
print('MINUTE_ROWS',len(r),flush=True)
print('MINUTE_1000',json.dumps([x for x in r if x['time'][8:12]=='1000'],ensure_ascii=False),flush=True)
print('BASIC',rows(bs.query_stock_basic(code='sh.600000')),flush=True)
print('ADJUST',rows(bs.query_adjust_factor(code='sh.600000',start_date='2025-01-01',end_date='2026-09-27')),flush=True)
bs.logout()

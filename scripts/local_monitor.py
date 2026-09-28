#!/usr/bin/env python3
"""Read server reports with a restricted SSH key; prepare deduplicated Gmail notices."""
import argparse
import datetime as dt
import hashlib
import html
import json
import sqlite3
import subprocess
from pathlib import Path

TZ=dt.timezone(dt.timedelta(hours=8))
ROOT=Path(__file__).resolve().parents[1]/'artifacts/local-monitor'


def assess(snapshot,now):
    files=snapshot['files']; p=files.get('paper'); issues=[]
    if snapshot.get('writing'): issues.append('行情正在写入，本次不发送交易计划')
    scheduler=files.get('scheduler') or {}
    stamp=scheduler.get('checked_at')
    if not stamp or (now-dt.datetime.fromisoformat(stamp)).total_seconds()>180:
        issues.append('服务器调度心跳失效')
    if scheduler.get('result')=='failed': issues.append('最近一次每日任务失败')
    if not p: issues.append('模拟日报缺失')
    calendar=snapshot.get('calendar',[])
    cutoff=now.date() if now.hour>=20 else now.date()-dt.timedelta(days=1)
    expected=max((d for d in calendar if d<=str(cutoff)),default=None)
    if not calendar or max(calendar)<str(now.date()): issues.append('交易日历缺失或过期')
    if p and expected and p['asof']<expected: issues.append('模拟日报落后于最新应完成交易日')
    if p and not issues:
        for key in ('rolling','sync'):
            status=files.get(key) or {}
            if status.get('status')!='ok' or status.get('date')!=p['asof']:
                issues.append(key+'状态异常或日期不一致')
        cycle=files.get('cycle') or {}
        if cycle.get('paper') != p: issues.append('日报尚未完成完整发布')
    if issues:
        kind='alert'; body='AQuant Lab 巡检异常：\n'+'\n'.join(issues)+'\n请检查服务器。此次不提供可执行的新买卖计划。'
        key='alert-'+hashlib.sha256(json.dumps(issues,ensure_ascii=False).encode()).hexdigest()[:20]
        subject='[巡检异常] AQuant Lab'
    else:
        kind='report'; plan=p.get('next_plan')
        key='paper-'+p.get('trial_id','legacy24')+'-'+p['asof']+'-'+p['status']
        subject='[模拟日报] AQuant Lab '+p['asof']
        body='仅为1万元模拟观察，不是实盘成交或收益承诺。\n行情日期：'+p['asof']+'\n'
        body+='净值 %.2f 元；现金 %.2f 元；累计收益 %.2f%%；最大回撤 %.2f%%；费用 %.2f 元；成交 %d 笔。\n' % (p['equity'],p['cash'],100*p['total_return'],100*p['max_drawdown'],p['fees_paid'],p['trade_count'])
        body+='观察期：'+p['start']+' 至 '+p['end_exclusive']+'（不含结束日）。\n'
        body+='试验：'+p.get('trial_id','legacy24')+'；以本试验新计划为准，旧试验计划不再执行。\n'
        if plan:
            deadline=dt.datetime.fromisoformat(plan['execution_date']+'T09:30:00+08:00')
            body+='\n'+('下次模拟计划' if now<deadline else '以下计划已过通知截止时间，仅供复盘，不可追单')+'：'+plan['execution_date']+' 10:00（北京时间）\n'
            body+='计划冻结于：'+plan['frozen_at']+'\n'
            for o in plan['orders']:
                body+='%s %s %s，%d股，参考收盘价%.2f元\n'%(o['side'],o['symbol'],o['name'],o['qty'],o['reference_price'])
            if not plan['orders']: body+='无调仓。\n'
            body+='10:00价相对参考价偏离超过%.1f%%则取消该单；另有停牌、涨跌停、现金和成交容量限制。\n'%(100*plan['max_price_deviation'])
        else: body+='\n无新的模拟交易计划。\n'
        body+='\n'+'\n'.join(p['limitations'])
    return {'key':key,'kind':kind,'to':'2711543085@qq.com','from':'samuelzhaomath@gmail.com','subject':subject,
            'html':'<pre style="white-space:pre-wrap;font-family:sans-serif">'+html.escape(body)+'</pre>'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reserve'); parser.add_argument('--sent'); parser.add_argument('--message-id')
    args=parser.parse_args(); ROOT.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(ROOT/'deliveries.sqlite'))
    db.execute('CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY,status TEXT,message_id TEXT)')
    if args.reserve:
        try:
            with db: db.execute('INSERT INTO deliveries VALUES (?, ?, ?)',(args.reserve,'pending',None))
            print(json.dumps({'reserved':True,'key':args.reserve}))
        except sqlite3.IntegrityError:
            print(json.dumps({'reserved':False,'key':args.reserve})); raise SystemExit(2)
        return
    if args.sent:
        if not args.message_id: raise SystemExit('--message-id required')
        with db:
            n=db.execute("UPDATE deliveries SET status='sent',message_id=? WHERE key=? AND status='pending'",(args.message_id,args.sent)).rowcount
        if n!=1: raise SystemExit('No pending delivery found')
        print(json.dumps({'recorded':True})); return
    now=dt.datetime.now(TZ)
    cmd=['ssh','-i',str(Path.home()/'.ssh/aquant_monitor_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
         '-o','ControlPath=none','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-p','45626','root@connect.bjb1.seetacloud.com']
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=25,check=True)
        snapshot=json.loads(p.stdout)
        (ROOT/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
        envelope=assess(snapshot,now)
    except (subprocess.SubprocessError,ValueError,KeyError,TypeError,OSError) as error:
        envelope={'key':'monitor-error-'+type(error).__name__,'kind':'alert','to':'2711543085@qq.com','from':'samuelzhaomath@gmail.com',
                  'subject':'[巡检异常] AQuant Lab 读取失败','html':'<p>无法验证服务器最新日报，请检查连接和报告格式；不发送旧的交易计划。错误类型：'+type(error).__name__+'</p>'}
    health_path=ROOT/'health.json'
    prior=json.loads(health_path.read_text()) if health_path.exists() else {}
    category=envelope['key'] if envelope['kind']=='alert' else 'healthy'
    episode=prior.get('episode') if prior.get('category')==category else now.isoformat()
    health_path.write_text(json.dumps({'category':category,'episode':episode}))
    if envelope['kind']=='alert':
        envelope['key']+='-'+hashlib.sha256(episode.encode()).hexdigest()[:12]
    previous=db.execute('SELECT status FROM deliveries WHERE key=?',(envelope['key'],)).fetchone()
    envelope['delivery_status']=previous[0] if previous else 'ready'
    (ROOT/'outbox.json').write_text(json.dumps(envelope,ensure_ascii=False,indent=2))
    print(json.dumps({'delivery_status':envelope['delivery_status'],'key':envelope['key'],'kind':envelope['kind'],'outbox':str(ROOT/'outbox.json')},ensure_ascii=False))


if __name__=='__main__': main()

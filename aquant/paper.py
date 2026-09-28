"""Immutable prospective plans and atomic daily paper-account snapshots."""
import calendar
import datetime as dt
import json
import sqlite3
from pathlib import Path
from .account import Account
from .backtest import execute_orders
from .core import ValidationError, digest, write_json, code_hash
from .operations import lock
from .strategy import propose, rank

TZ = dt.timezone(dt.timedelta(hours=8))


def deadline(date):
    return dt.datetime.fromisoformat(date + 'T09:30:00+08:00')


def prospective(signal_date, execution_date, now):
    return dt.datetime.fromisoformat(signal_date+'T15:00:00+08:00') <= now < deadline(execution_date)


def update(data, cfg, prediction, directory, now=None):
    now = now or dt.datetime.now(TZ)
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    with lock(root/'paper.lock'):
        return _update(data, cfg, prediction, root, now)


def _update(data, cfg, prediction, root, now):
    asof = prediction['signal_date']; execution = data.next_day(asof)
    data.validate_mode(cfg)
    if cfg.get('pool_hash'):
        universe=data.metadata.get('universe',{})
        if universe.get('pool_hash')!=cfg['pool_hash'] or asof<universe.get('selection_date','9999'):
            raise ValidationError('股票池与模拟配置不符或信号早于选池日')
    manifest_path = root/'protocol.json'
    if not manifest_path.exists():
        if not prospective(asof, execution, now):
            raise ValidationError('首次模拟必须在下一交易日09:30前冻结，禁止补做历史成交')
        start = dt.date.fromisoformat(execution)
        year, month = (start.year+1, 1) if start.month == 12 else (start.year, start.month+1)
        end = dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
        write_json(manifest_path, {'start':str(start), 'end_exclusive':str(end), 'created_at':now.isoformat(),
            'config':cfg, 'config_hash':digest(cfg), 'code_hash':code_hash(),
            'candidate_count':24, 'seed':17, 'train_days':126,
            'mode':'paper', 'selection':'daily rolling training; fixed 24 candidates; no automatic strategy promotion',
            'valuation':'daily close; fills at 10:00 plus slippage and fees; no terminal liquidation',
            'cashflows':'fixed initial cash; deposits require a separate trial'})
    protocol = json.loads(manifest_path.read_text())
    if protocol['config_hash'] != digest(cfg) or protocol['code_hash'] != code_hash():
        raise ValidationError('模拟协议或代码改变，请审核后启动独立试验，禁止改写本次观察')
    if prediction['candidate_count'] != 24 or prediction['seed'] != 17:
        raise ValidationError('预测参数与冻结协议不一致')
    db = sqlite3.connect(str(root/'paper.sqlite'))
    try:
        db.execute('CREATE TABLE IF NOT EXISTS plans (date TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (date TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.commit()
        history = [json.loads(r[0]) for r in db.execute('SELECT payload FROM sessions ORDER BY date')]
        account = Account(cfg['initial_cash'])
        if history: account.__dict__.update(history[-1]['account'])
        last = history[-1]['date'] if history else ''
        if last > asof: raise ValidationError('行情日期倒退')
        days = [d for d in data.calendar if protocol['start'] <= d < protocol['end_exclusive'] and last < d <= asof]
        for date in days:
            rows = data.snapshot(date)
            for symbol, position in account.positions.items():
                if symbol not in rows: raise ValidationError('持仓行情缺失: '+symbol)
                held = [r['adj_factor'] for (d,s),r in data.rows.items() if s==symbol and min(l['date'] for l in position['lots']) <= d <= date]
                if len(set(held)) > 1: raise ValidationError('持仓公司行动，暂停模拟并等待对账: '+symbol)
            saved = db.execute('SELECT payload FROM plans WHERE date=?',(date,)).fetchone()
            plan = json.loads(saved[0]) if saved else None
            if plan and dt.datetime.fromisoformat(plan['frozen_at']) >= deadline(date):
                raise ValidationError('计划冻结晚于截止时间')
            fills, rejected = execute_orders(data,cfg,account,date,plan['orders'] if plan else [])
            equity = account.cash + sum(p['qty']*rows[s]['close'] for s,p in account.positions.items())
            record = {'date':date,'account':json.loads(json.dumps(account.__dict__)), 'equity':equity,
                      'fills':fills,'unfilled':rejected,'plan_missing':plan is None,'data_hash':data.version}
            with db: db.execute('INSERT INTO sessions VALUES (?,?)',(date,json.dumps(record)))
            history.append(record)
        plan_row = db.execute('SELECT payload FROM plans WHERE date=?',(execution,)).fetchone()
        plan = json.loads(plan_row[0]) if plan_row else None
        if not plan and protocol['start'] <= execution < protocol['end_exclusive'] and prospective(asof,execution,now):
            created = dt.datetime.fromisoformat(prediction['created_at'])
            if not prospective(asof,execution,created) or created > now or prediction['provenance'] != 'prospective':
                raise ValidationError('不得将历史回放预测用于前向模拟')
            eligible = {r['symbol'] for r in rank(data,asof,cfg)}
            rankings = [dict(r) for r in prediction['predictions'] if r['symbol'] in eligible]
            for i,r in enumerate(rankings,1): r['rank']=i
            plan = propose(data,asof,cfg,account,rankings)
            plan.update(frozen_at=now.isoformat(),model_id=prediction['model_id'],data_hash=data.version)
            with db: db.execute('INSERT INTO plans VALUES (?,?)',(execution,json.dumps(plan)))
        equity = history[-1]['equity'] if history else cfg['initial_cash']
        peak = cfg['initial_cash']; drawdown = 0
        for row in history:
            peak=max(peak,row['equity']); drawdown=min(drawdown,row['equity']/peak-1)
        result = {'mode':'paper','trial_id':cfg.get('trial_id','legacy24'),'asof':asof,'start':protocol['start'],'end_exclusive':protocol['end_exclusive'],
                  'status':'complete' if asof >= protocol['end_exclusive'] else ('observing' if history else 'awaiting_first_execution'),
                  'equity':round(equity,2),'cash':round(account.cash,2),'total_return':equity/cfg['initial_cash']-1,
                  'max_drawdown':drawdown,'fees_paid':round(sum(t['fee'] for r in history for t in r['fills']),2),
                  'trade_count':sum(len(r['fills']) for r in history),'positions':account.positions,'next_plan':plan,
                  'missing_plan_dates':[r['date'] for r in history if r['plan_missing']],
                  'limitations':[cfg.get('universe_description','24只历史主板样本，不是全市场'),'10:00近似撮合，盘后核算，不代表实盘成交','费用为假设配置；公司行动暂停核算','短期结果不证明策略有效；现金基准收益为0']}
        write_json(root/'latest.json',result)
        text = render(result)
        (root/'latest.md').write_text(text,encoding='utf-8')
        (root/(asof+'.md')).write_text(text,encoding='utf-8')
        return result
    finally:
        db.close()


def render(r):
    lines=['# AQuant Lab 模拟组合日报','', '仅模拟，不是实际成交。', '',
           '行情日期：'+r['asof'], '观察区间：'+r['start']+' 至 '+r['end_exclusive']+'（不含结束日）',
           '净值金额：%.2f 元；现金：%.2f 元；收益率：%.2f%%；最大回撤：%.2f%%；累计费用：%.2f 元。' % (r['equity'],r['cash'],100*r['total_return'],100*r['max_drawdown'],r['fees_paid']),
           '模拟成交笔数：'+str(r['trade_count']), '', '## 下次模拟计划','']
    plan=r['next_plan']
    if plan:
        lines += ['执行日：'+plan['execution_date']+' 10:00（北京时间）；冻结于：'+plan['frozen_at'], '', '|方向|股票|股数|参考价|','|---|---|---:|---:|']
        lines += ['|%s|%s %s|%d|%.2f|'%(o['side'],o['symbol'],o['name'],o['qty'],o['reference_price']) for o in plan['orders']]
        if not plan['orders']: lines += ['无调仓。']
    else: lines += ['无可执行的新计划；不会追补历史交易。']
    lines += ['', '## 口径与限制','']+['- '+s for s in r['limitations']]
    return '\n'.join(lines)+'\n'

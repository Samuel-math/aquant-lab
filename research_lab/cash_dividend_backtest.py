"""Research-only pretax cash-dividend replay; never used by the paper ledger."""
from __future__ import annotations

import math
import statistics

from aquant.account import Account
from aquant.backtest import execute_orders
from aquant.core import ValidationError
from aquant.strategy import propose


def cash_entitlement(account, symbol, date, previous_date, factor_changed, events):
    """Credit only a sourced, same-day paid, cash-only event to prior-close holders."""
    if not factor_changed:
        return None
    event = events.get((date, symbol))
    if event is None:
        raise ValidationError(f'持仓公司行动缺少明细: {symbol} {date}')
    if event.get('record_date') != previous_date or event.get('ex_date') != date or \
       event.get('pay_date') != date or not event.get('source') or \
       not event.get('announcement_date') or event['announcement_date'] > previous_date or \
       event.get('shares_per_share') != 0:
        raise ValidationError(f'公司行动类型或到账时点未支持: {symbol} {date}')
    per_share = event.get('cash_before_tax_per_share')
    if not isinstance(per_share, (int, float)) or not math.isfinite(per_share) or per_share <= 0:
        raise ValidationError(f'现金红利明细非法: {symbol} {date}')
    qty = account.positions[symbol]['qty']
    return {'kind': 'DIVIDEND', 'date': date, 'symbol': symbol, 'qty_at_record_close': qty,
            'cash_before_tax_per_share': per_share, 'amount': round(qty * per_share, 8),
            'source': event['source'], 'tax_status': 'pretax_research_scenario'}


def run_cash_dividend_replay(data, cfg, events, start, end, signal_ranker):
    """Mirror frozen execution, adding only verified cash-only entitlements.

    The gross dividend is credited after same-day orders, so it cannot finance
    purchases before its arrival time is known. Other action types still block.
    """
    data.validate_mode(cfg)
    if not data.intraday:
        raise ValidationError('缺少指定时点执行观测')
    observed = sorted({d for d, _ in data.rows})
    if start < observed[0] or end > observed[-1] or start > end:
        raise ValidationError('回测日期超出行情区间')
    days = [d for d in data.calendar if start <= d <= end]
    if len(days) < 2:
        raise ValidationError('回测区间至少需要两个交易日')
    account = Account(cfg['initial_cash'])
    pending, curve, trades, rejected, dividends = [], [], [], [], []
    previous = {}
    previous_date = None
    for date in days:
        rows = data.snapshot(date)
        missing = set(account.positions) - set(rows)
        if missing:
            raise ValidationError('持仓缺失行情: ' + str(missing))
        entitlements = []
        for symbol in account.positions:
            if symbol in previous:
                event = cash_entitlement(account, symbol, date, previous_date,
                                         rows[symbol]['adj_factor'] != previous[symbol]['adj_factor'], events)
                if event:
                    entitlements.append(event)
        fills, unfilled = execute_orders(data, cfg, account, date, pending)
        trades.extend(fills)
        rejected.extend(unfilled)
        for event in entitlements:
            account.apply(event)
            dividends.append(event)
        equity = account.cash + sum(p['qty'] * rows[s]['close'] for s, p in account.positions.items())
        curve.append({'date': date, 'equity': round(equity, 4), 'cash': round(account.cash, 4)})
        pending = (propose(data, date, cfg, account, rankings=signal_ranker(date))['orders']
                   if date != days[-1] else [])
        previous, previous_date = rows, date
    values = [cfg['initial_cash']] + [r['equity'] for r in curve]
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    peak, drawdown = values[0], 0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    vol = statistics.pstdev(returns)
    return {'metrics': {'total_return': values[-1] / values[0] - 1,
                        'max_drawdown': drawdown,
                        'sharpe_zero_rate': statistics.mean(returns) / vol * math.sqrt(252) if vol else 0,
                        'trade_count': len(trades),
                        'fees_paid': round(sum(t['fee'] for t in trades), 2),
                        'pretax_cash_dividends': round(sum(e['amount'] for e in dividends), 8)},
            'curve': curve, 'trades': trades, 'unfilled': rejected, 'dividends': dividends,
            'limitations': ['税前股息研究情景，未计算个人持有期限税和到账可用时间',
                            '仅支持登记日为前一交易日且除息日同日到账的纯现金分红',
                            '回放预测和188只回填历史名单均非独立前向绩效',
                            '开盘与09:40观测价不是可保证的实际成交价']}

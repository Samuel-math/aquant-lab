"""Bounded formula discovery. No eval, no automatic strategy promotion."""
import copy
import datetime as dt
import math
import random
import statistics
import time
from pathlib import Path
from .backtest import run
from .core import ValidationError, code_hash, digest, write_json
from .operations import lock, status
from .strategy import rank
from .targets import forward_label, training_rows, validate_target


def centered_ranks(values):
    ordered = sorted(values)
    positions = {}
    for i, value in enumerate(ordered):
        positions.setdefault(value, []).append(i)
    n = len(ordered)
    return [2 * statistics.mean(positions[v]) / max(1, n - 1) - 1 for v in values]


def correlation(x, y):
    if len(x) < 3:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    dx, dy = [v - mx for v in x], [v - my for v in y]
    denom = math.sqrt(sum(v*v for v in dx) * sum(v*v for v in dy))
    return sum(a*b for a, b in zip(dx, dy)) / denom if denom > 1e-12 else None


def evaluate_expression(expr, features):
    op = expr['op']
    if op == 'feature':
        return features[expr['name']]
    a = evaluate_expression(expr['left'], features)
    b = evaluate_expression(expr['right'], features)
    if op == 'add': return (a + b) / 2
    if op == 'sub': return (a - b) / 2
    if op == 'mul': return a * b
    if op == 'ratio': return math.tanh(a / (abs(b) + .1))
    raise ValidationError('未知公式算子: ' + op)


def candidates(names, count, seed):
    if not names or not 1 <= count <= 256:
        raise ValidationError('候选数范围为1..256，特征不能为空')
    rng = random.Random(seed)
    results, seen = [], set()
    base = [{'op': 'feature', 'name': name} for name in sorted(names)]
    attempts = 0
    while len(results) < count:
        if len(results) < len(base):
            expr = base[len(results)]
        else:
            left = rng.choice(base)
            if rng.random() < .35:
                left = {'op': rng.choice(['add', 'sub', 'mul']), 'left': rng.choice(base), 'right': rng.choice(base)}
            expr = {'op': rng.choice(['add', 'sub', 'mul', 'ratio']), 'left': left, 'right': rng.choice(base)}
        key = digest(expr)[:16]
        attempts += 1
        if attempts > count * 100:
            raise ValidationError('候选去重后不足，请减少搜索规模')
        if key not in seen:
            seen.add(key); results.append({'id': key, 'expression': expr})
    return results


class _SnapshotView:
    """Memoize validated snapshots for one read-only feature preparation pass."""
    def __init__(self, data):
        self.data = data
        self.snapshots = {}

    def __getattr__(self, name):
        return getattr(self.data, name)

    def snapshot(self, date):
        if date not in self.snapshots:
            self.snapshots[date] = self.data.snapshot(date)
        return self.snapshots[date]


def _init_prepare(data, cfg, asof):
    global _prepare_data, _prepare_cfg, _prepare_asof
    _prepare_data = _SnapshotView(data)
    _prepare_cfg, _prepare_asof = cfg, asof


def _prepare_day(date):
    data, cfg, asof = _prepare_data, _prepare_cfg, _prepare_asof
    records = []
    for item in rank(data, date, cfg):
        hist = data.history(item['symbol'], date, 61)
        if not hist or any(r['suspended'] for r in hist):
            continue
        prices = [r['close'] * r['adj_factor'] for r in hist]
        returns = [b / a - 1 for a, b in zip(prices, prices[1:])]
        values = {}
        for window in (5, 10, 20, 60):
            values['momentum_%d' % window] = prices[-1] / prices[-window-1] - 1
            values['volatility_%d' % window] = statistics.pstdev(returns[-window:])
            values['amount_ratio_%d' % window] = hist[-1]['amount'] / max(1, statistics.mean(r['amount'] for r in hist[-window:]))
        values['reversal_1'] = -returns[-1]
        values['overnight_gap'] = hist[-1]['open'] * hist[-1]['adj_factor'] / prices[-2] - 1
        records.append({'symbol': item['symbol'], 'name': item['name'], 'price': item['price'], 'features': values})
    if len(records) < 3:
        return date, [], []
    for name in records[0]['features']:
        normalized = centered_ranks([r['features'][name] for r in records])
        for r, value in zip(records, normalized):
            r['features'][name] = value
    return date, records, [forward_label(data, row['symbol'], date, asof) for row in records]


def prepare(data, cfg, asof, check_budget, workers=1):
    from .parallel import ordered_map
    first = min(d for d, _ in data.rows)
    days = [d for d in data.calendar if first <= d <= asof]
    features, labels = {}, []
    global _prepare_data, _prepare_cfg, _prepare_asof
    try:
        for date, records, outcomes in ordered_map(_prepare_day, days, workers,
                                                   _init_prepare, (data, cfg, asof), check_budget):
            if records:
                features[date] = records
                labels.extend(outcomes)
    finally:
        _prepare_data = _prepare_cfg = _prepare_asof = None
    return features, labels


def mean_ic(values, labels):
    grouped = {}
    for row in labels:
        key = (row['signal_date'], row['symbol'])
        if key in values and row['status'] == 'observed':
            grouped.setdefault(row['signal_date'], []).append((values[key], row['gross_return']))
    scores = []
    for rows in grouped.values():
        value = correlation(centered_ranks([x[0] for x in rows]), centered_ranks([x[1] for x in rows]))
        if value is not None: scores.append(value)
    return statistics.mean(scores) if scores else 0.0


def formula_ranker(features, expression, direction):
    cache = {}
    for date, records in features.items():
        items = [dict(symbol=r['symbol'], name=r['name'], price=r['price'],
                      score=direction * evaluate_expression(expression, r['features'])) for r in records]
        items.sort(key=lambda r: (-r['score'], r['symbol']))
        for number, row in enumerate(items, 1): row['rank'] = number
        cache[date] = items
    return lambda date: cache.get(date, [])


def mine(data, cfg, asof, output, count=48, shortlist=8, seed=17, max_seconds=300):
    """Train IC screens formulas; validation net return selects; holdout never selects."""
    if not 1 <= count <= 256 or not 1 <= shortlist <= min(count, 32) or not 1 <= max_seconds <= 3600:
        raise ValidationError('搜索预算非法：候选1..256、入围1..32且不超过候选数、时间1..3600秒')
    validate_target(cfg); data.validate_mode(cfg); data.snapshot(asof)
    if not data.intraday:
        raise ValidationError('因子搜索需要10:00分钟观测，不能使用日线开盘价替代')
    started = time.monotonic()
    def check_budget():
        if time.monotonic() - started > max_seconds:
            raise ValidationError('搜索超过时间预算，未提升任何策略')
    output = Path(output)
    status_path = output.with_suffix('.status.json')
    with lock(str(output) + '.lock'):
        try:
            status(status_path, asof, 'running', stage='features', seed=seed)
            features, labels = prepare(data, cfg, asof, check_budget)
            dates = sorted(d for d in features if data.calendar.index(d) + 2 < len(data.calendar)
                           and data.calendar[data.calendar.index(d)+2] <= asof)
            if len(dates) < 150:
                raise ValidationError('至少需要150个有61日历史且标签成熟的信号日期')
            validation_start = dates[int(len(dates) * .55)]
            holdout_start = dates[int(len(dates) * .80)]
            validation_end = data.calendar[data.calendar.index(holdout_start) - 1]
            train = training_rows(labels, validation_start, validation_start)
            if not train:
                raise ValidationError('训练标签为空')
            generated = candidates(list(next(iter(features.values()))[0]['features']), count, seed)
            registry = output.with_suffix('.trials.jsonl')
            # Every formula is recorded before its statistics are used for selection.
            with registry.open('w', encoding='utf-8') as f:
                import json
                for candidate in generated:
                    check_budget()
                    values = {(date, r['symbol']): evaluate_expression(candidate['expression'], r['features'])
                              for date, rows in features.items() if date < validation_start for r in rows}
                    ic = mean_ic(values, train)
                    candidate.update(train_rank_ic=ic, direction=1 if ic >= 0 else -1)
                    f.write(json.dumps(candidate, ensure_ascii=False) + '\n'); f.flush()
            generated.sort(key=lambda c: (-abs(c['train_rank_ic']), c['id']))
            selected = generated[:shortlist]
            status(status_path, asof, 'running', stage='validation', candidate_count=count)
            for candidate in selected:
                check_budget()
                scorer = formula_ranker(features, candidate['expression'], candidate['direction'])
                candidate['validation_metrics'] = run(data, cfg, validation_start, validation_end, signal_ranker=scorer)['metrics']
            selected.sort(key=lambda c: (-c['validation_metrics']['total_return'], c['id']))
            winner = selected[0]
            # Freeze formula, direction and parameters before reading holdout performance.
            frozen = {'candidate': copy.deepcopy(winner), 'holdout_start': holdout_start,
                      'config_hash': digest(cfg), 'data_hash': data.version, 'code_hash': code_hash()}
            write_json(output.with_suffix('.frozen.json'), frozen)
            check_budget()
            scorer = formula_ranker(features, winner['expression'], winner['direction'])
            holdout = run(data, cfg, holdout_start, asof, signal_ranker=scorer)
            stressed = copy.deepcopy(cfg)
            for k in ('commission_rate', 'minimum_commission', 'slippage_bps'):
                stressed['fees'][k] *= 2
            check_budget()
            stress = run(data, stressed, holdout_start, asof, signal_ranker=scorer)
            baseline = run(data, cfg, holdout_start, asof)
            check_budget()
            counts = {}
            for row in labels: counts[row['status']] = counts.get(row['status'], 0) + 1
            result = {'status': 'pipeline_verified_only' if cfg['mode'] == 'demo' else 'research_candidate_only',
                      'mode': cfg['mode'], 'asof': asof, 'target': cfg['prediction_target'],
                      'seed': seed, 'budget': {'candidates': count, 'shortlist': shortlist, 'max_seconds': max_seconds},
                      'split': {'train_signal_start': dates[0], 'train_max_label_end': max(r['exit_date'] for r in train),
                                'validation': [validation_start, validation_end], 'holdout': [holdout_start, asof]},
                      'label_status_counts': counts, 'all_candidates': generated, 'winner': winner,
                      'holdout_metrics': holdout['metrics'], 'double_cost_metrics': stress['metrics'],
                      'baseline_holdout_metrics': baseline['metrics'],
                      'beats_cash_on_holdout': holdout['metrics']['total_return'] > 0,
                      'config': cfg, 'data_hash': data.version, 'code_hash': code_hash(),
                      'runtime_seconds': round(time.monotonic() - started, 3),
                      'limitations': ['合成数据仅验证流程，不代表投资价值' if cfg['mode'] == 'demo' else '真实数据亦需独立审计与前向模拟',
                                      '一次训练/验证/留出切分，尚非完整嵌套滚动验证',
                                      '尝试过的候选全部记录，但尚未实现多重检验显著性修正',
                                      '同一留出区间反复搜索会失去独立性，不可视为新的样本外证据',
                                      '前一分钟成交量仅作容量近似；人工10:00成交可能偏离观测价',
                                      '公式方向只在训练期确定；没有自动启用候选策略']}
            result['experiment_id'] = digest({k: v for k, v in result.items() if k != 'runtime_seconds'})[:20]
            write_json(output, result)
            write_json(output.parent / 'experiments' / (result['experiment_id'] + '.json'), result)
            write_json(output.with_suffix('.holdout.json'), holdout)
            text = ['# 因子搜索结果', '', '状态：' + result['status'], '',
                    '数据：' + cfg['mode'] + '；目标：下一交易日10:00到再下一交易日10:00', '',
                    '尝试公式 %d 个，训练期筛选后验证 %d 个；未自动启用策略。' % (count, shortlist), '',
                    '训练/验证/留出区间：' + str(result['split']), '',
                    '胜出公式：`' + str(winner['expression']) + '`', '',
                    '|指标|独立留出期|双倍佣金/滑点|固定因子基线|', '|---|---:|---:|---:|']
            for key in ('total_return', 'max_drawdown', 'fees_paid', 'trade_count'):
                text.append('|%s|%.4f|%.4f|%.4f|' % (key, holdout['metrics'][key], stress['metrics'][key], baseline['metrics'][key]))
            text.extend(['', '## 局限', ''] + ['- ' + x for x in result['limitations']])
            output.with_suffix('.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
            status(status_path, asof, 'ok', experiment_id=result['experiment_id'], report=str(output.with_suffix('.md')))
            return result
        except Exception as e:
            status(status_path, asof, 'failed', error=str(e)); raise

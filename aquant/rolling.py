"""Daily prequential validation: score saved predictions, then learn new labels."""
import datetime as dt
import json
import statistics
import time
from pathlib import Path
from .core import ValidationError, digest, code_hash, write_json
from .mining import candidates, prepare, mean_ic, evaluate_expression, centered_ranks, correlation
from .operations import lock, status
from .targets import forward_label


def fit_and_predict(features, labels, date, count=24, seed=17, train_days=126):
    # The target horizon is two sessions: only labels already observed by this date can train.
    available = [r for r in labels if r['status'] == 'observed' and r['label_available_date'] <= date
                 and r['signal_date'] < date]
    dates = sorted({r['signal_date'] for r in available})[-train_days:]
    if len(dates) < 60:
        raise ValidationError('滚动训练至少需要60个标签成熟的信号日')
    allowed = set(dates)
    train = [r for r in available if r['signal_date'] in allowed]
    current = features.get(date, [])
    if not current:
        raise ValidationError('当日无可用因子特征')
    formulas = candidates(list(current[0]['features']), count, seed)
    for candidate in formulas:
        values = {(d, row['symbol']): evaluate_expression(candidate['expression'], row['features'])
                  for d, rows in features.items() if d in allowed for row in rows}
        candidate['train_rank_ic'] = mean_ic(values, train)
        candidate['direction'] = 1 if candidate['train_rank_ic'] >= 0 else -1
    formulas.sort(key=lambda r: (-abs(r['train_rank_ic']), r['id']))
    winner = formulas[0]
    predicted = [{'symbol': r['symbol'], 'name': r['name'],
                  'score': winner['direction'] * evaluate_expression(winner['expression'], r['features'])}
                 for r in current]
    predicted.sort(key=lambda r: (-r['score'], r['symbol']))
    for i, r in enumerate(predicted, 1): r['rank'] = i
    training_fingerprint = digest({'features': {d: features[d] for d in dates}, 'labels': train})
    record = {'signal_date': date, 'train_signal_range': [dates[0], dates[-1]],
              'train_max_label_end': max(r['exit_date'] for r in train),
              'training_days': len(dates), 'training_hash': training_fingerprint,
              'candidate_count': count, 'seed': seed, 'winner': winner, 'all_candidates': formulas,
              'predictions': predicted, 'code_hash': code_hash()}
    record['model_id'] = digest(record)[:20]
    return record


def validate_prediction(data, saved, asof):
    outcomes = []
    for prediction in saved['predictions']:
        row = forward_label(data, prediction['symbol'], saved['signal_date'], asof)
        outcomes.append(dict(row, score=prediction['score'], rank=prediction['rank']))
    if not outcomes or any(r['status'] == 'pending' for r in outcomes):
        return None
    observed = [r for r in outcomes if r['status'] == 'observed']
    ic = correlation(centered_ranks([r['score'] for r in observed]), centered_ranks([r['gross_return'] for r in observed]))
    top = [r['gross_return'] for r in observed if r['rank'] <= 4]
    return {'signal_date': saved['signal_date'], 'model_id': saved['model_id'], 'evaluated_asof': asof,
            'provenance': saved['provenance'], 'rank_ic': ic,
            'top4_gross_return': statistics.mean(top) if top else None,
            'coverage': len(observed) / len(outcomes), 'outcomes': outcomes,
            'metric_type': 'factor_diagnostic_not_net_portfolio_return'}


def update_rolling(data, cfg, asof, directory, replay_days=0, count=24, seed=17, max_seconds=600):
    data.validate_mode(cfg); data.snapshot(asof)
    if not 1 <= count <= 128 or not 0 <= replay_days <= 60:
        raise ValidationError('候选1..128；历史顺序回放0..60日')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    clock_start = time.monotonic()
    def check():
        if time.monotonic() - clock_start > max_seconds:
            raise ValidationError('滚动更新超过时间预算')
    with lock(directory / 'run.lock'):
        try:
            status(directory / 'status.json', asof, 'running')
            features, labels = prepare(data, cfg, asof, check)
            predictions_dir = directory / 'predictions'; predictions_dir.mkdir(exist_ok=True)
            evaluations_dir = directory / 'evaluations'; evaluations_dir.mkdir(exist_ok=True)
            new_evaluations = []
            def score_existing():
                for path in sorted(predictions_dir.glob('*.json')):
                    dest = evaluations_dir / path.name
                    if dest.exists(): continue
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    if saved['signal_date'] > asof: continue
                    scored = validate_prediction(data, saved, asof)
                    if scored is not None:
                        write_json(dest, scored); new_evaluations.append(saved['signal_date'])
            # First evaluate previous immutable predictions, before fitting today's model.
            score_existing()
            feature_dates = sorted(d for d in features if d <= asof)
            dates = feature_dates[-(replay_days + 1):] if replay_days else [asof]
            created = []
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
            for date in dates:
                check()
                dest = predictions_dir / (date + '.json')
                if dest.exists(): continue  # Never rewrite a historical prediction after seeing outcomes.
                model = fit_and_predict(features, labels, date, count, seed)
                model.update(target=cfg['prediction_target'], mode=cfg['mode'],
                             created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                             provenance='prospective' if date == today and date == asof else 'historical_replay')
                write_json(dest, model); created.append(date)
            score_existing()  # Mature replay labels may be evaluated, still explicitly historical replay.
            evaluated = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(evaluations_dir.glob('*.json'))]
            recent = [r for r in evaluated if r['evaluated_asof'] <= asof][-20:]
            values = [r['rank_ic'] for r in recent if r['rank_ic'] is not None]
            result = {'asof': asof, 'mode': cfg['mode'], 'new_prediction_dates': created,
                      'new_evaluation_dates': new_evaluations, 'prediction_count': len(list(predictions_dir.glob('*.json'))),
                      'evaluation_count': len(evaluated), 'recent_mean_rank_ic': statistics.mean(values) if values else None,
                      'prospective_evaluation_count': sum(r['provenance']=='prospective' for r in evaluated),
                      'historical_replay_evaluation_count': sum(r['provenance']=='historical_replay' for r in evaluated),
                      'latest_predictions': str(predictions_dir / (asof + '.json')),
                      'limitations': ['历史顺序回放不是部署后的真实前向验证，分开计数',
                                      'Rank IC和前四名毛收益是因子诊断，不是扣成本的账户收益',
                                      '每天先评价已冻结预测，再吸收新成熟标签训练下一版',
                                      '尚未进行多重检验修正；不自动下单或提升正式策略']}
            write_json(directory / 'latest.json', result)
            status(directory / 'status.json', asof, 'ok', **{k:v for k,v in result.items() if k!='asof'})
            return result
        except Exception as e:
            status(directory / 'status.json', asof, 'failed', error=str(e)); raise

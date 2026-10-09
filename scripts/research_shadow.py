#!/usr/bin/env python3
"""Freeze independent daily research scores and later evaluate mature labels."""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib

from aquant.core import read_config, write_json
from aquant.data import CSVData
from aquant.operations import lock
from aquant.paper import prospective
from aquant.targets import forward_label
from research_lab.features import FEATURE_COLUMNS, MINUTE_FEATURE_COLUMNS, build_features
from research_lab.formula_baseline import formula_predictions
from research_lab.models import (MODEL_SETTINGS, SnapshotCache, attach_labels, evaluate,
                                 fit_models, mature_training_rows, positive_scores)


def evaluate_frozen(data, cfg, root, asof):
    cached = SnapshotCache(data)
    count = 0
    evaluations = root / 'evaluations'
    evaluations.mkdir(parents=True, exist_ok=True)
    for path in sorted((root / 'predictions').glob('*.json')):
        output = evaluations / path.name
        if output.exists():
            continue
        frozen = json.loads(path.read_text(encoding='utf-8'))
        rows = []
        pending = False
        for prediction in frozen['scores']:
            label = forward_label(cached, prediction['symbol'], frozen['signal_date'], asof, cfg)
            pending |= label['status'] == 'pending'
            rows.append({'date': frozen['signal_date'], 'symbol': prediction['symbol'],
                         'model': prediction['model'], 'score': prediction['score'],
                         'status': label['status'], 'gross_return': label['gross_return']})
        if pending:
            continue
        write_json(output, {'signal_date': frozen['signal_date'],
                            'evaluated_asof': asof, 'provenance': frozen['provenance'],
                            'metrics': evaluate(rows, frozen['signal_date'], frozen['signal_date']),
                            'outcomes': rows, 'data_hash': data.version})
        count += 1
    return count


def summarize_prospective(root, asof):
    rows = []
    dates = []
    for path in sorted((root / 'evaluations').glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['provenance'] == 'prospective':
            rows.extend(record['outcomes'])
            dates.append(record['signal_date'])
    write_json(root / 'latest-summary.json', {
        'evaluated_asof': asof, 'prospective_dates': len(dates),
        'period': [min(dates), max(dates)] if dates else None,
        'metrics': evaluate(rows, min(dates), max(dates)) if dates else {},
        'research_only': True,
    })


def freeze(data, cfg, root, track, summary_path=None):
    asof = data.metadata['asof']
    output = root / 'predictions' / (asof + '.json')
    if output.exists():
        return False
    columns = FEATURE_COLUMNS + (MINUTE_FEATURE_COLUMNS if summary_path else ())
    frame = build_features(data, cfg, asof, summary_path)
    labeled = attach_labels(data, cfg, frame, asof)
    train, mature_dates = mature_training_rows(labeled, asof)
    if len(mature_dates) < MODEL_SETTINGS['minimum_train_days']:
        raise ValueError('Insufficient mature training days')
    current = labeled.loc[labeled['date'] == asof]
    if current.empty:
        raise ValueError('No current research features')
    models, train_positive_rate = fit_models(train, columns)
    scores = []
    for name, model in models.items():
        rows = sorted(zip(current['symbol'], positive_scores(model, current[list(columns)])),
                      key=lambda x: (-x[1], x[0]))
        scores.extend({'model': name, 'symbol': symbol, 'score': float(score), 'rank': rank}
                      for rank, (symbol, score) in enumerate(rows, 1))
        model_path = root / 'models' / (asof + '-' + name + '.joblib')
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, model_path)
    baseline, baseline_fit = formula_predictions(data, cfg, asof, [asof])
    baseline.sort(key=lambda r: (-r['score'], r['symbol']))
    scores.extend({'model': 'frozen_24_formula', 'symbol': row['symbol'],
                   'score': row['score'], 'rank': rank}
                  for rank, row in enumerate(baseline, 1))
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    code_hash = hashlib.sha256(b''.join(p.name.encode() + p.read_bytes() for p in
                       [*sorted((ROOT / 'research_lab').glob('*.py')), Path(__file__)])).hexdigest()
    write_json(output, {'version': 'research-shadow-v2-classification', 'track': track,
                        'trial_id': cfg['trial_id'], 'signal_date': asof,
                        'created_at': now.isoformat(),
                        'provenance': 'prospective' if prospective(asof, data.next_day(asof), now, cfg)
                        else 'late_replay', 'prediction_target': cfg['prediction_target'],
                        'train_signal_range': [mature_dates[0], mature_dates[-1]],
                        'train_days': len(mature_dates),
                        'train_positive_rate': train_positive_rate,
                        'train_max_label_available_date': max(train['label_available_date']),
                        'score_meaning': {'logistic': 'P(gross_return_strictly_above_1pct)',
                                          'lightgbm': 'P(gross_return_strictly_above_1pct)',
                                          'frozen_24_formula': 'formula_rank_score_not_probability'},
                        'feature_columns': list(columns), 'model_settings': MODEL_SETTINGS,
                        'formula_fit': baseline_fit[0], 'data_hash': data.version,
                        'research_code_hash': code_hash, 'scores': scores,
                        'research_only': True})
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track', choices=('core', 'minute'), required=True)
    parser.add_argument('--data', default=str(ROOT / 'data/local-pool188-morning-v1-20261009/dataset'))
    parser.add_argument('--config', default=str(ROOT / 'configs/paper-pool188-morning-local-v1.json'))
    parser.add_argument('--minute-summary', default=str(ROOT / 'data/research-intraday-summary-v1/daily-summary.csv'))
    parser.add_argument('--output', default=str(ROOT / 'artifacts/research-shadow-v2'))
    args = parser.parse_args()
    data = CSVData(args.data)
    cfg = read_config(args.config)
    if cfg.get('pool_hash') != data.metadata['universe'].get('pool_hash'):
        parser.error('Pool mismatch')
    minute_path = None
    if args.track == 'minute':
        minute_path = Path(args.minute_summary)
        receipt = json.loads((minute_path.parent / 'receipt.json').read_text(encoding='utf-8'))
        if receipt['asof'] < data.metadata['asof'] or receipt['pool_hash'] != cfg['pool_hash'] or \
           receipt.get('file_sha256') != hashlib.sha256(minute_path.read_bytes()).hexdigest():
            parser.error('Minute summary is stale or from another pool')
    root = Path(args.output) / args.track
    (root / 'predictions').mkdir(parents=True, exist_ok=True)
    with lock(root / 'freeze.lock'):
        evaluated = evaluate_frozen(data, cfg, root, data.metadata['asof'])
        created = freeze(data, cfg, root, args.track, minute_path)
        summarize_prospective(root, data.metadata['asof'])
    print(json.dumps({'track': args.track, 'asof': data.metadata['asof'],
                      'created': created, 'new_evaluations': evaluated,
                      'root': str(root)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Run isolated walk-forward model diagnostics; never publish trading signals."""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import pandas as pd

from aquant.backtest import run
from aquant.core import read_config, write_json
from aquant.data import CSVData
from research_lab.features import FEATURE_COLUMNS, MINUTE_FEATURE_COLUMNS, build_features
from research_lab.formula_baseline import formula_predictions
from research_lab.models import MODEL_SETTINGS, attach_labels, evaluate, rankers, walk_forward


def net_backtests(data, cfg, predictions, start, end):
    if not start or not end or start >= end:
        return {}
    rankings = rankers(predictions)
    result = {}
    for model, by_date in rankings.items():
        try:
            score = run(data, cfg, start=start, end=end,
                        signal_ranker=lambda date, ranks=by_date: ranks.get(date, []))
            result[model] = {'status': 'complete', 'metrics': score['metrics'],
                             'unfilled_count': len(score['unfilled'])}
        except Exception as error:
            result[model] = {'status': 'blocked', 'error_type': type(error).__name__,
                             'reason': str(error)[:250]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default=str(ROOT / 'data/local-pool188-morning-v1-20261009/dataset'))
    parser.add_argument('--config', default=str(ROOT / 'configs/paper-pool188-morning-local-v1.json'))
    parser.add_argument('--pool', default=str(ROOT / 'configs/pool188-v2-20260928.json'))
    parser.add_argument('--evaluation-start', default='2026-07-01')
    parser.add_argument('--minute-summary', help='Optional complete 5-minute daily feature summaries')
    parser.add_argument('--skip-formula-baseline', action='store_true')
    parser.add_argument('--output', default=str(ROOT / 'artifacts/research-morning-models-v1'))
    args = parser.parse_args()
    out = Path(args.output)
    if out.resolve() == (ROOT / 'artifacts/local-pool188-morning-v1-20261009').resolve() or \
       (ROOT / 'artifacts/local-pool188-morning-v1-20261009').resolve() in out.resolve().parents:
        parser.error('Research output must not be inside the running paper trial')
    out.mkdir(parents=True, exist_ok=True)
    data = CSVData(args.data)
    cfg = read_config(args.config)
    pool = json.loads(Path(args.pool).read_text(encoding='utf-8'))
    asof = data.metadata['asof']
    selection_date = pool['asof']
    if cfg.get('pool_hash') != data.metadata['universe'].get('pool_hash'):
        parser.error('Pool/config/data hash mismatch')
    if args.minute_summary:
        summary_path = Path(args.minute_summary)
        receipt = json.loads((summary_path.parent / 'receipt.json').read_text(encoding='utf-8'))
        if receipt['pool_hash'] != cfg['pool_hash'] or receipt['asof'] < asof or \
           receipt.get('file_sha256') != hashlib.sha256(summary_path.read_bytes()).hexdigest():
            parser.error('Minute summary pool hash or asof mismatch')
    else:
        summary_path = None
    feature_columns = FEATURE_COLUMNS + (MINUTE_FEATURE_COLUMNS if summary_path else ())
    features = build_features(data, cfg, asof, summary_path)
    labeled = attach_labels(data, cfg, features, asof)
    predictions, fits, models = walk_forward(labeled, args.evaluation_start, asof, feature_columns)
    if not fits or not predictions:
        parser.error('Insufficient mature dates for research')
    formula_fits = []
    if not args.skip_formula_baseline:
        formula_rows, formula_fits = formula_predictions(
            data, cfg, asof, sorted({row['date'] for row in predictions}))
        predictions.extend(formula_rows)
    prediction_frame = pd.DataFrame(predictions)
    prediction_frame.to_csv(out / 'walk-forward-predictions.csv', index=False)
    write_json(out / 'fit-audit.json', fits)
    if formula_fits:
        write_json(out / 'formula-fit-audit.json', formula_fits)
    for name, model in models.items():
        joblib.dump(model, out / (name + '-last-fit.joblib'))
    dates = sorted({r['date'] for r in predictions})
    pre_dates = [d for d in dates if d < selection_date]
    post_dates = [d for d in dates if d >= selection_date]
    scopes = {}
    for name, segment in (('selected_pool_historical_diagnostic', pre_dates),
                          ('selection_date_onward_replay_too_short', post_dates)):
        if not segment:
            scopes[name] = {'status': 'no_dates'}
            continue
        scopes[name] = {'period': [segment[0], segment[-1]],
                        'metrics': evaluate(predictions, segment[0], segment[-1]),
                        'net_backtest': net_backtests(data, cfg, predictions, segment[0], segment[-1])}
    code_hash = hashlib.sha256(b''.join(p.name.encode() + p.read_bytes() for p in
                       [*sorted((ROOT / 'research_lab').glob('*.py')), Path(__file__)])).hexdigest()
    report = {'version': 'morning-model-research-v2' if summary_path else 'morning-model-research-v1',
              'status': 'research_only_not_deployed',
              'asof': asof, 'selection_date': selection_date,
              'target': cfg['prediction_target'], 'data_hash': data.version,
              'research_code_hash': code_hash, 'features': list(feature_columns),
              'minute_summary': str(summary_path) if summary_path else None,
              'models': MODEL_SETTINGS, 'fit_count': len(fits), 'last_fit': fits[-1],
              'formula_fit_count': len(formula_fits),
              'prediction_rows': len(predictions), 'scopes': scopes,
              'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'limitations': [
                  '188只名单在2026-09-28选出；此前所有历史结果带有事后选池偏差，不能称为无偏样本外收益',
                  '本研究预测在运行时回放生成，不等于部署当天冻结的真实前向预测',
                  '选池后样本太短；不得根据这些指标替换正在运行的24公式模拟试验',
                  'AUC、Top10命中率和Top4毛收益不含交易成本；净回测另外报告并保留公司行动阻断',
                  ('额外5分钟特征为完整交易日的汇总，信号只在收盘后形成' if summary_path
                   else '5分钟成交量来自当天09:40结束的窗口；其余日内细节尚未接入'),
                  '模型参数事先固定，无自动调参、策略提升或邮件发送',
                  '24公式基线在相同日期重新回放训练；其历史预测也不是真实前向冻结记录',
              ]}
    write_json(out / 'report.json', report)
    print(json.dumps({'report': str(out / 'report.json'), 'asof': asof,
                      'fit_count': len(fits), 'prediction_rows': len(predictions),
                      'scopes': {name: value.get('period') for name, value in scopes.items()}},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()

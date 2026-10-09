#!/usr/bin/env python3
"""Replay frozen research rankings under a sourced pretax cash-dividend scenario."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from aquant.core import read_config, write_json
from aquant.data import CSVData
from research_lab.cash_dividend_backtest import run_cash_dividend_replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default=str(ROOT / 'data/local-pool188-morning-v1-20261009/dataset'))
    parser.add_argument('--config', default=str(ROOT / 'configs/paper-pool188-morning-local-v1.json'))
    parser.add_argument('--events', default=str(ROOT / 'research/cash-dividend-events-20261010.json'))
    parser.add_argument('--predictions', required=True)
    parser.add_argument('--start', default='2026-09-28')
    parser.add_argument('--end', default='2026-10-09')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    data = CSVData(args.data)
    cfg = read_config(args.config)
    event_payload = json.loads(Path(args.events).read_text(encoding='utf-8'))
    events = {(r['ex_date'], r['symbol']): r for r in event_payload['events']}
    if len(events) != len(event_payload['events']):
        parser.error('Duplicate corporate-action events')
    rows = pd.read_csv(args.predictions, dtype={'date': str, 'symbol': str})
    result = {'status': 'research_only_pretax_scenario', 'data_asof': data.metadata['asof'],
              'start': args.start, 'end': args.end,
              'events_source': str(args.events), 'predictions_source': args.predictions,
              'models': {}}
    for model, group in rows.groupby('model'):
        by_date = {}
        for date, daily in group.groupby('date'):
            ordered = daily.sort_values(['score', 'symbol'], ascending=[False, True])
            by_date[date] = [{'symbol': r.symbol, 'score': float(r.score), 'rank': index}
                             for index, r in enumerate(ordered.itertuples(index=False), 1)]
        try:
            replay = run_cash_dividend_replay(data, cfg, events, args.start, args.end,
                                              lambda date: by_date.get(date, []))
            result['models'][model] = {'status': 'complete', **replay}
        except Exception as error:
            result['models'][model] = {'status': 'blocked', 'error_type': type(error).__name__,
                                       'reason': str(error)[:250]}
    write_json(args.output, result)
    print(json.dumps({'output': args.output, 'models': {name: row['status']
                        for name, row in result['models'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()

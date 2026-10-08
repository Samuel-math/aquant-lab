#!/usr/bin/env python3
"""Read-only activation audit for the isolated 188-stock local paper trial."""
import datetime as dt
import json
from pathlib import Path

from aquant.core import code_hash, digest, read_config
from aquant.data import CSVData
from aquant.paper import deadline

REPO = Path(__file__).resolve().parents[1]
TRIAL = REPO / 'artifacts/local-pool188-morning-v1-20261009'
CONFIG = REPO / 'configs/paper-pool188-morning-local-v1.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    cfg = read_config(CONFIG)
    data = CSVData(TRIAL / 'real/dataset')
    asof = data.metadata['asof']
    snapshot = data.snapshot(asof)
    active = [symbol for symbol, row in snapshot.items() if not row['suspended']]
    quoted = [symbol for symbol in active if (asof, symbol) in data.intraday]
    prediction = read(TRIAL / 'rolling-real/predictions' / (asof + '.json'))
    protocol = read(TRIAL / 'paper/protocol.json')
    latest = read(TRIAL / 'paper/latest.json')
    cycle = read(TRIAL / 'rolling-real/cycle.json')
    sync = read(TRIAL / 'real/sync.status.json')
    rolling = read(TRIAL / 'rolling-real/status.json')
    plan = latest.get('next_plan')
    checks = {
        'trial_id': cfg['trial_id'] == latest['trial_id'],
        'pool_hash': data.metadata['universe']['pool_hash'] == cfg['pool_hash'],
        'stock_count': len(snapshot) == 188,
        'active_quote_coverage': len(active) == len(quoted),
        'quote_time': data.metadata['entry_time'] == '09:40:00',
        'prediction_target': prediction['target'] == cfg['prediction_target'],
        'candidate_count': prediction['candidate_count'] == 24,
        'prediction_prospective': prediction['provenance'] == 'prospective',
        'protocol_config_hash': protocol['config_hash'] == digest(cfg),
        'protocol_code_hash': protocol['code_hash'] == code_hash(),
        'empty_start': latest['trade_count'] == 0 and latest['equity'] == cfg['initial_cash']
                       and not latest['positions'],
        'next_plan': bool(plan) and plan['execution_date'] == data.next_day(asof),
        'plan_frozen_early': bool(plan) and dt.datetime.fromisoformat(plan['frozen_at'])
                              < deadline(plan['execution_date'], cfg),
        'complete_cycle': cycle['paper'] == latest and sync['status'] == rolling['status'] == 'ok'
                          and sync['date'] == rolling['date'] == asof,
    }
    report = {'trial_id': cfg['trial_id'], 'asof': asof, 'active_stocks': len(active),
              'quoted_stocks': len(quoted), 'checks': checks,
              'passed': all(checks.values()), 'data_hash': data.version,
              'code_hash': code_hash(), 'plan_date': plan['execution_date'] if plan else None,
              'audited_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()

#!/usr/bin/env python3
"""Research-only BaoStock 5-minute aggregation; retain daily summaries, not bars."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aquant.baostock_source import collect, internal_symbol
from aquant.core import digest, write_json
from aquant.operations import lock, status

FIELDS = ('date', 'symbol', 'bar_count', 'first10_volume_share', 'open30_volume_share',
          'close30_volume_share', 'morning_volume_share', 'intraday_volume_concentration',
          'realized_volatility_5m', 'first10_return', 'close_vs_bar_vwap_proxy')
SOURCE_FIELDS = 'date,time,code,open,close,volume,amount'


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        stamp = row['time']
        if len(stamp) != 17 or stamp[:8] != row['date'].replace('-', ''):
            raise ValueError('Invalid BaoStock 5-minute timestamp')
        grouped[row['date']].append(row)
    output = []
    for date, bars in grouped.items():
        bars.sort(key=lambda r: r['time'])
        times = [r['time'][8:14] for r in bars]
        if len(bars) != 48 or times[0] != '093500' or times[-1] != '150000' or len(set(times)) != 48:
            continue
        volume = [float(r['volume'] or 0) for r in bars]
        close = [float(r['close'] or 0) for r in bars]
        first_open = float(bars[0]['open'] or 0)
        total = sum(volume)
        if total <= 0 or first_open <= 0 or min(close) <= 0:
            continue
        log_returns = [math.log(b / a) for a, b in zip(close, close[1:])]
        vwap_proxy = sum(v * p for v, p in zip(volume, close)) / total
        output.append({
            'date': date, 'bar_count': 48,
            'first10_volume_share': sum(v for t, v in zip(times, volume) if t <= '094000') / total,
            'open30_volume_share': sum(v for t, v in zip(times, volume) if t <= '100000') / total,
            'close30_volume_share': sum(v for t, v in zip(times, volume) if t >= '143500') / total,
            'morning_volume_share': sum(v for t, v in zip(times, volume) if t <= '113000') / total,
            'intraday_volume_concentration': 48 * sum((v / total) ** 2 for v in volume),
            'realized_volatility_5m': math.sqrt(sum(r * r for r in log_returns)),
            'first10_return': close[1] / first_open - 1,
            'close_vs_bar_vwap_proxy': close[-1] / vwap_proxy - 1,
        })
    return output


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def merge_summaries(previous_rows, incoming_rows):
    prior = {row['date']: row for row in previous_rows}
    revisions = [{'date': row['date'], 'old': prior[row['date']], 'new': row}
                 for row in incoming_rows if row['date'] in prior and prior[row['date']] != row]
    prior.update({row['date']: row for row in incoming_rows})
    return [prior[date] for date in sorted(prior)], revisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT / 'data/research-intraday-summary-v1'))
    parser.add_argument('--pool', default=str(ROOT / 'configs/pool188-v2-20260928.json'))
    parser.add_argument('--start', default='2026-05-01')
    parser.add_argument('--end', default='2026-10-09')
    args = parser.parse_args()
    if args.start > args.end:
        parser.error('start after end')
    root = Path(args.root)
    pool = json.loads(Path(args.pool).read_text(encoding='utf-8'))
    codes = [stock['code'] for stock in pool['stocks']]
    if len(codes) != 188 or len(set(codes)) != 188:
        parser.error('Expected frozen 188-stock pool')
    manifest = {'pool_hash': digest(pool), 'start': args.start, 'end': args.end,
                'fields': SOURCE_FIELDS, 'frequency': '5', 'adjustflag': '3',
                'retention': 'daily aggregates only; no complete minute-bar archive',
                'usage': 'research only, not the running paper trial'}
    root.mkdir(parents=True, exist_ok=True)
    with lock(root / 'sync.lock'):
        manifest_path = root / 'manifest.json'
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding='utf-8'))
            if {k: v for k, v in existing.items() if k != 'end'} != \
               {k: v for k, v in manifest.items() if k != 'end'} or args.end < existing['end']:
                parser.error('Summary manifest changed or requested end moved backward')
        else:
            existing = manifest
            write_json(manifest_path, manifest)
        core = root / 'core'
        core.mkdir(exist_ok=True)
        previous_status_path = root / 'status.json'
        previous_status = (json.loads(previous_status_path.read_text(encoding='utf-8'))
                           if previous_status_path.exists() else {})
        receipt_path = root / 'receipt.json'
        summary_path = root / 'daily-summary.csv'
        if previous_status.get('status') == 'ok' and previous_status.get('date') == args.end and \
           receipt_path.exists() and summary_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            if receipt.get('file_sha256') == hashlib.sha256(summary_path.read_bytes()).hexdigest() and \
               receipt.get('latest_active_coverage') == 1.0:
                print(json.dumps(receipt, ensure_ascii=False), flush=True)
                return
        retry_incomplete = previous_status.get('status') != 'ok'
        status(root / 'status.json', args.end, 'running')
        import baostock as bs
        attempts = 0
        while True:
            login = bs.login()
            if login.error_code != '0':
                attempts += 1
                if attempts >= 3:
                    status(root / 'status.json', args.end, 'failed', error='BaoStock login failed')
                    raise RuntimeError('BaoStock login failed')
                time.sleep(20)
                continue
            try:
                for number, code in enumerate(codes, 1):
                    dest = core / (code + '.json')
                    previous = json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else None
                    last = previous.get('fetched_through', existing['end']) if previous else None
                    if last and last >= args.end and not retry_incomplete:
                        continue
                    fetch_start = (max(args.start, (dt.date.fromisoformat(last) -
                                   dt.timedelta(days=7)).isoformat()) if last else args.start)
                    rows = collect(bs.query_history_k_data_plus(code, SOURCE_FIELDS,
                                   start_date=fetch_start, end_date=args.end,
                                   frequency='5', adjustflag='3'))
                    summaries = summarize(rows)
                    symbol = internal_symbol(code)
                    for summary in summaries:
                        summary['symbol'] = symbol
                    daily, revisions = merge_summaries(previous['daily'] if previous else [], summaries)
                    if revisions:
                        with (core / (code + '.revisions.jsonl')).open('a', encoding='utf-8') as journal:
                            for revision in revisions:
                                journal.write(json.dumps(revision, ensure_ascii=False) + '\n')
                    write_json(dest, {'code': code, 'symbol': symbol,
                                      'fetched_through': args.end,
                                      'received_bars_last_fetch': len(rows),
                                      'complete_days': len(daily),
                                      'payload_hash_last_fetch': digest(rows),
                                      'daily': daily})
                    print('SUMMARY %d/188 %s: %d bars -> %d days' %
                          (number, code, len(rows), len(daily)), flush=True)
                break
            except Exception:
                attempts += 1
                if attempts >= 3:
                    status(root / 'status.json', args.end, 'failed', error='BaoStock data request failed')
                    raise
                time.sleep(20)
            finally:
                bs.logout()
        all_rows = []
        for code in codes:
            payload = json.loads((core / (code + '.json')).read_text(encoding='utf-8'))
            all_rows.extend(payload['daily'])
        all_rows.sort(key=lambda row: (row['date'], row['symbol']))
        from aquant.data import CSVData
        production = ROOT / 'data/local-pool188-morning-v1-20261009/dataset'
        latest_active_coverage = None
        if production.exists():
            source = CSVData(production)
            if source.metadata['asof'] == args.end:
                active = {symbol for symbol, row in source.snapshot(args.end).items()
                          if not row['suspended']}
                observed = {row['symbol'] for row in all_rows if row['date'] == args.end}
                latest_active_coverage = len(active & observed) / len(active) if active else 1.0
                if active - observed:
                    status(root / 'status.json', args.end, 'failed',
                           error='Latest active 5-minute summary incomplete',
                           missing_count=len(active - observed))
                    raise ValueError('Latest active 5-minute summary incomplete')
        write_csv(root / 'daily-summary.csv', all_rows)
        receipt = {'asof': args.end, 'symbols': len(codes), 'summary_rows': len(all_rows),
                   'pool_hash': manifest['pool_hash'], 'summary_hash': digest(all_rows),
                   'file_sha256': hashlib.sha256((root / 'daily-summary.csv').read_bytes()).hexdigest(),
                   'latest_active_coverage': latest_active_coverage,
                   'generated_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        write_json(root / 'receipt.json', receipt)
        status(root / 'status.json', args.end, 'ok', **receipt)
        print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

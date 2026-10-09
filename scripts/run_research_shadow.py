#!/usr/bin/env python3
"""Run both independent shadow tracks after the production data cycle."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aquant.core import write_json
from aquant.operations import lock

PYTHON = ROOT / '.venv-research/bin/python'
DATASET = ROOT / 'data/local-pool188-morning-v1-20261009/dataset'
OUTPUT = ROOT / 'artifacts/research-shadow-v1'


def call(*parts, timeout=1800):
    command = [str(PYTHON), str(ROOT / 'scripts' / parts[0]), *parts[1:]]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                               text=True, timeout=timeout)
    return {'ok': completed.returncode == 0, 'exit_code': completed.returncode,
            'stdout_tail': completed.stdout[-1000:], 'stderr_tail': completed.stderr[-1000:]}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with lock(OUTPUT / 'run.lock'):
        metadata = json.loads((DATASET / 'metadata.json').read_text(encoding='utf-8'))
        asof = metadata['asof']
        result = {'asof': asof, 'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        result['core'] = call('research_shadow.py', '--track', 'core')
        if result['core']['ok']:
            result['minute_sync'] = call('backfill_intraday_summary.py', '--end', asof, timeout=3600)
            if result['minute_sync']['ok']:
                result['minute'] = call('research_shadow.py', '--track', 'minute')
        result['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(OUTPUT / 'status.json', result)
        print(json.dumps({'asof': asof, 'core_ok': result['core']['ok'],
                          'minute_sync_ok': result.get('minute_sync', {}).get('ok'),
                          'minute_ok': result.get('minute', {}).get('ok')}, ensure_ascii=False))
        if not result['core']['ok'] or not result.get('minute', {}).get('ok'):
            raise SystemExit(1)


if __name__ == '__main__':
    main()

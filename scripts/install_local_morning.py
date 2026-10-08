#!/usr/bin/env python3
"""Install a launchd schedule for the isolated local morning paper trial."""
import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = 'com.aquant.pool188-morning-local'
REPO = Path(__file__).resolve().parents[1]
TRIAL = REPO / 'artifacts/local-pool188-morning-v1-20261009'
DATA = REPO / 'data/local-pool188-morning-v1-20261009'
CONFIG = REPO / 'configs/paper-pool188-morning-local-v1.json'
POOL = REPO / 'configs/pool188-v2-20260928.json'
PYTHON = REPO / '.venv/bin/python'
DEST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def schedule(workers):
    return {
        'Label': LABEL,
        'ProgramArguments': [str(PYTHON), '-m', 'aquant.research_worker', '--once',
                             '--data-root', str(DATA), '--out-root', str(TRIAL / 'rolling-real'),
                             '--sample-size', '188', '--config', str(CONFIG),
                             '--pool', str(POOL), '--workers', str(workers)],
        'WorkingDirectory': str(REPO),
        'StartCalendarInterval': [{'Hour': hour, 'Minute': 10} for hour in (8, 20, 21, 22)],
        'RunAtLoad': False,
        'StandardOutPath': str(TRIAL / 'launchd.out.log'),
        'StandardErrorPath': str(TRIAL / 'launchd.err.log'),
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'OMP_NUM_THREADS': '1',
                                 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
                                 'AQUANT_MAIL_CONFIG': str(TRIAL / 'smtp-disabled.json')},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--install', action='store_true', help='register the generated plist with launchd')
    args = parser.parse_args()
    if not 1 <= args.workers <= 10: parser.error('workers must be 1..10 on this local setup')
    for required in (PYTHON, CONFIG, POOL):
        if not required.exists(): parser.error('missing ' + str(required))
    TRIAL.mkdir(parents=True, exist_ok=True)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    payload = plistlib.dumps(schedule(args.workers))
    DEST.write_bytes(payload)
    print(DEST)
    if args.install:
        subprocess.run(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(DEST)], check=True)
        print('launchd schedule registered:', LABEL)
    else:
        print('plist written but not registered; pass --install after local trial audit')


if __name__ == '__main__': main()

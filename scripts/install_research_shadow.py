#!/usr/bin/env python3
"""Register read-only model research after the production data schedule."""
import argparse
import os
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'com.aquant.research-shadow-v1'
DEST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    payload = {
        'Label': LABEL,
        'ProgramArguments': [str(ROOT / '.venv-research/bin/python'),
                             str(ROOT / 'scripts/run_research_shadow.py')],
        'WorkingDirectory': str(ROOT),
        'StartCalendarInterval': [{'Hour': 23, 'Minute': 30},
                                  {'Hour': 6, 'Minute': 30}],
        'RunAtLoad': False,
        'StandardOutPath': str(ROOT / 'artifacts/research-shadow-v2/launchd.out.log'),
        'StandardErrorPath': str(ROOT / 'artifacts/research-shadow-v2/launchd.err.log'),
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'OMP_NUM_THREADS': '1',
                                 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'},
    }
    (ROOT / 'artifacts/research-shadow-v2').mkdir(parents=True, exist_ok=True)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(plistlib.dumps(payload))
    print(DEST)
    if args.install:
        subprocess.run(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(DEST)], check=True)
        print('Registered', LABEL)
    else:
        print('Not registered; pass --install after shadow audit')


if __name__ == '__main__':
    main()

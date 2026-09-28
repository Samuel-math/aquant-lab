#!/usr/bin/env python3
"""Forced SSH command: exports only allowlisted reports, never executes client input."""
import csv
import datetime as dt
import json
from pathlib import Path

root=Path('/root/autodl-tmp/aquant')
files={'paper':'paper/latest.json','protocol':'paper/protocol.json','cycle':'rolling-real/cycle.json',
       'scheduler':'rolling-real/scheduler.json','rolling':'rolling-real/status.json','sync':'real/sync.status.json'}
result={'exported_at':dt.datetime.now(dt.timezone.utc).isoformat(),'files':{}}
for name,relative in files.items():
    path=root/relative
    try:
        if path.stat().st_size>2_000_000: raise ValueError('oversized report')
        result['files'][name]=json.loads(path.read_text())
    except (OSError,ValueError): result['files'][name]=None
try:
    with (root/'real/dataset/calendar.csv').open() as f:
        result['calendar']=[r['date'] for r in csv.DictReader(f)]
except OSError: result['calendar']=[]
result['writing']=(root/'real/dataset/.writing').exists()
print(json.dumps(result,ensure_ascii=False))

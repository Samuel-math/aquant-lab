"""Single-session 09:40 backfill with bounded reconnects and resumable caches."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aquant.baostock_source import sync_baostock


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True);p.add_argument('--end',required=True)
    p.add_argument('--minute-start',default='2026-01-01')
    p.add_argument('--pool',default='configs/pool188-v2-20260928.json')
    args=p.parse_args();root=Path(args.root)
    universe=json.loads((root/'universe.json').read_text())
    if universe.get('quote_time')!='09:40:00' or universe.get('minute_start')!=args.minute_start:
        raise ValueError('Backfill manifest mismatch')
    for attempt in range(1,4):
        try:
            result=sync_baostock(root,start=universe['start'],end=args.end,
                                sample_size=universe['sample_size'],seed=universe['seed'],
                                pool_path=args.pool,quote_time='09:40:00',minute_start=args.minute_start)
            print(json.dumps(result,ensure_ascii=False),flush=True)
            return
        except Exception as error:
            print('Attempt %d failed: %s; completed caches retained' % (attempt,error),flush=True)
            if attempt==3: raise
            time.sleep(20)


if __name__=='__main__': main()

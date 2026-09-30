"""Bounded two-connection initial 09:40 backfill; no simulation writes."""
import argparse
import json
import multiprocessing as mp
import socket
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aquant.baostock_source import sync_security, collect
from aquant.operations import lock
from aquant.storage import admission


def fetch(item):
    import baostock as bs
    basic,root,start,end,minute_start,n,count=item
    socket.setdefaulttimeout(60)
    response=bs.login()
    if response.error_code!='0': raise RuntimeError(response.error_msg)
    try:
        return sync_security(bs,basic,Path(root)/'core',start,end,minute_start,'5','09:40:00',n,count)
    finally:
        bs.logout()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True);p.add_argument('--end',required=True)
    p.add_argument('--minute-start',default='2026-01-01')
    args=p.parse_args();root=Path(args.root)
    with lock(root/'sync.lock'):
        admission(root)
        universe=json.loads((root/'universe.json').read_text())
        if universe.get('quote_time')!='09:40:00' or universe.get('minute_start')!=args.minute_start:
            raise ValueError('Backfill manifest mismatch')
        items=[(basic,str(root),universe['start'],args.end,args.minute_start,i,len(universe['stocks']))
               for i,basic in enumerate(universe['stocks'],1)]
        with mp.get_context('spawn').Pool(2) as pool:
            for _ in pool.imap_unordered(fetch,items): pass
        print('Backfill complete; publication and validation still required',flush=True)


if __name__=='__main__': main()

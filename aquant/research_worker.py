"""Server daily research scheduler, 20:00 Asia/Shanghai, bounded retries."""
import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from .core import write_json
from .operations import lock

TZ=dt.timezone(dt.timedelta(hours=8))


def cycle(data_root, out_root, sample_size, replay_days=0):
    from .baostock_source import sync_baostock
    from .core import read_config
    from .data import CSVData
    from .rolling import update_rolling
    now=dt.datetime.now(TZ)
    completed=now.date() if now.hour>=20 else now.date()-dt.timedelta(days=1)
    synced=sync_baostock(data_root,end=completed.isoformat(),sample_size=sample_size)
    data=CSVData(synced['dataset'])
    result=update_rolling(data,read_config('configs/research-real.json'),synced['asof'],out_root,
                          replay_days=replay_days,count=24,max_seconds=1200)
    from .paper import update, render
    from .mailer import deliver
    prediction=json.loads(Path(result['latest_predictions']).read_text())
    paper=update(data,read_config('configs/research-real.json'),prediction,Path(out_root).parent/'paper')
    mail=deliver('paper-'+paper['asof'], '[模拟] AQuant Lab '+paper['asof'], render(paper), Path(out_root).parent/'mail.sqlite')
    summary={'sync':synced,'rolling':result,'paper':paper,'mail':mail}
    write_json(Path(out_root)/'cycle.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data-root',required=True); p.add_argument('--out-root',required=True)
    p.add_argument('--sample-size',type=int,default=24); p.add_argument('--once',action='store_true')
    p.add_argument('--replay-days',type=int,default=0)
    args=p.parse_args()
    if args.once:
        print(json.dumps(cycle(args.data_root,args.out_root,args.sample_size,args.replay_days),ensure_ascii=False,indent=2)); return
    out=Path(args.out_root); out.mkdir(parents=True,exist_ok=True)
    state_path=out/'scheduler.json'
    with lock(out/'scheduler.lock'):
        while True:
            now=dt.datetime.now(TZ); today=now.date().isoformat()
            state=json.loads(state_path.read_text()) if state_path.exists() else {}
            if state.get('attempt_date')!=today: state={'attempt_date':today,'attempts':0}
            due=now.weekday()<5 and now.hour>=20 and state.get('result')!='ok' and state['attempts']<3
            elapsed=time.time()-state.get('last_attempt_epoch',0)
            if due and elapsed>=3600:
                state.update(attempts=state['attempts']+1,last_attempt_epoch=time.time(),result='running')
                write_json(state_path,state)
                log=out/('worker-'+today+'.log')
                cmd=[sys.executable,'-m','aquant.research_worker','--once','--data-root',args.data_root,
                     '--out-root',args.out_root,'--sample-size',str(args.sample_size)]
                try:
                    with log.open('a',encoding='utf-8') as f:
                        process=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=1800)
                    state.update(result='ok' if process.returncode==0 else 'failed',exit_code=process.returncode,log=str(log))
                except subprocess.TimeoutExpired:
                    state.update(result='failed',error='1800-second budget exceeded',log=str(log))
                write_json(state_path,state)
            state.update(checked_at=now.isoformat(), schedule='Mon-Fri 20:00 Asia/Shanghai', market_calendar_checked_by_source=True)
            write_json(state_path,state)
            time.sleep(30)


if __name__=='__main__': main()

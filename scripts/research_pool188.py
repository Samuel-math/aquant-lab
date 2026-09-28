#!/usr/bin/env python3
"""One preregistered, parallel, conditional-on-current-pool research run."""
import argparse
import copy
import datetime as dt
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aquant.core import ValidationError, read_config, digest, code_hash, write_json
from aquant.data import CSVData
from aquant.mining import prepare, candidates, formula_ranker, _SnapshotView
from aquant.rolling import _init_fit, _fit_candidate
from aquant.parallel import ordered_map, worker_count
from aquant.targets import training_rows
from aquant.backtest import run
from aquant.operations import lock


def init_validation(data, cfg, features, start, end):
    global _data, _cfg, _features, _start, _end
    _data, _cfg, _features, _start, _end = _SnapshotView(data), cfg, features, start, end


def validate_candidate(candidate):
    result = copy.deepcopy(candidate)
    try:
        scorer = formula_ranker(_features, candidate['expression'], candidate['direction'])
        result.update(validation_status='ok', validation_metrics=run(_data, _cfg, _start, _end, scorer)['metrics'])
    except ValidationError as error:
        result.update(validation_status='blocked', validation_error=str(error))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--workers', type=int, default=24)
    args = parser.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    with lock(out/'run.lock'):
        if (out/'protocol.json').exists():
            raise ValidationError('研究已登记；不自动重复搜索同一留出区间，请检查已有输出')
        started = time.monotonic()
        def check():
            if time.monotonic()-started > 1800:
                raise ValidationError('研究超过1800秒预算')
        data, cfg = CSVData(args.data), read_config(args.config)
        asof = data.metadata['asof']
        if data.metadata.get('universe', {}).get('pool_hash') != cfg.get('pool_hash'):
            raise ValidationError('研究名单哈希不一致')
        protocol = {'created_at':dt.datetime.now(dt.timezone.utc).isoformat(), 'asof':asof,
                    'candidates':48, 'shortlist':8, 'seed':17, 'split_fractions':[.55,.80],
                    'workers':worker_count(args.workers), 'data_hash':data.version,
                    'config_hash':digest(cfg), 'code_hash':code_hash(),
                    'script_hash':__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest(),
                    'pool_selection_date':data.metadata['universe']['selection_date'],
                    'conditional_on_current_pool':True, 'automatic_promotion':False,
                    'limitations':['当前底池由期末数据筛选，历史分段不构成无偏样本外业绩',
                                   '单次时间切分，未完成多重检验修正；与每日24公式模拟独立',
                                   '公司行动缺少适配时保留失败记录，不跳过日期或伪造成交']}
        write_json(out/'protocol.json', protocol)
        try:
            write_json(out/'status.json', {'status':'running', 'stage':'features'})
            features, labels = prepare(data, cfg, asof, check, workers=args.workers)
            dates = sorted(d for d in features if data.calendar.index(d)+2 < len(data.calendar)
                           and data.calendar[data.calendar.index(d)+2] <= asof)
            if len(dates)<150: raise ValidationError('至少需要150个成熟信号日期')
            validation_start, holdout_start = dates[int(len(dates)*.55)], dates[int(len(dates)*.80)]
            validation_end = data.calendar[data.calendar.index(holdout_start)-1]
            train = training_rows(labels, validation_start, validation_start)
            generated = candidates(list(next(iter(features.values()))[0]['features']), 48, 17)
            write_json(out/'registered-candidates.json', generated)
            write_json(out/'status.json', {'status':'running', 'stage':'training'})
            scored = list(ordered_map(_fit_candidate, generated, args.workers, _init_fit,
                        ({d:rows for d,rows in features.items() if d<validation_start}, train), check))
            scored.sort(key=lambda c:(-abs(c['train_rank_ic']),c['id']))
            write_json(out/'training.json', scored)
            write_json(out/'status.json', {'status':'running', 'stage':'validation'})
            validated = list(ordered_map(validate_candidate, scored[:8], args.workers, init_validation,
                        (data,cfg,features,validation_start,validation_end), check))
            write_json(out/'validation.json', validated)
            eligible = [c for c in validated if c['validation_status']=='ok']
            eligible.sort(key=lambda c:(-c['validation_metrics']['total_return'],c['id']))
            result = {'protocol':protocol, 'split':{'train_signal_start':dates[0],
                      'train_max_label_end':max(r['exit_date'] for r in train),
                      'validation':[validation_start,validation_end], 'holdout':[holdout_start,asof]},
                      'validation':validated,'status':'research_only', 'automatic_promotion':False}
            if not eligible:
                result.update(status='blocked', reason='所有预选策略均未完成有效组合验证；不降低约束或继续挑选候选')
            else:
                winner = eligible[0]
                write_json(out/'frozen.json', {'winner':winner,'holdout_start':holdout_start,'protocol':protocol})
                result['winner'] = winner
                scorer = formula_ranker(features,winner['expression'],winner['direction'])
                view = _SnapshotView(data)
                for name in ['holdout','double_cost','baseline']:
                    check()
                    settings=copy.deepcopy(cfg)
                    if name=='double_cost':
                        for k in ['commission_rate','minimum_commission','slippage_bps']: settings['fees'][k]*=2
                    try:
                        report=run(view,settings,holdout_start,asof,None if name=='baseline' else scorer)
                        write_json(out/(name+'.json'),report)
                        result[name]={'status':'ok','metrics':report['metrics']}
                    except ValidationError as error:
                        result[name]={'status':'blocked','error':str(error)}
                if result['holdout']['status']!='ok': result['status']='blocked'
            result['runtime_seconds']=round(time.monotonic()-started,3)
            write_json(out/'result.json',result)
            write_json(out/'status.json',{'status':result['status'],'stage':'complete'})
            print(json.dumps(result,ensure_ascii=False,indent=2))
        except Exception as error:
            write_json(out/'status.json',{'status':'failed','error':str(error)})
            raise


if __name__=='__main__': main()

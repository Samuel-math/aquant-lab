"""Bounded chronological walk-forward experiment; never changes approved config."""
import copy
from .backtest import run
from .core import ValidationError, digest, write_json, code_hash
from pathlib import Path


CANDIDATES = [
    {"momentum": .4, "low_volatility": .3, "liquidity": .3},
    {"momentum": .6, "low_volatility": .3, "liquidity": .1},
    {"momentum": .2, "low_volatility": .6, "liquidity": .2},
]


def research(data, cfg, output):
    days = sorted(set(d for d, _ in data.rows))[cfg["strategy"]["lookback"]:]
    if len(days) < 150:
        raise ValidationError("滚动研究至少需要150个可用交易日，另加因子预热期")
    holdout_start = int(len(days) * .8)
    development, holdout = days[:holdout_start], days[holdout_start:]
    fold_size = max(20, len(development) // 5)
    folds = []
    for boundary in range(fold_size * 2, len(development) - fold_size + 1, fold_size):
        train, validation = development[:boundary], development[boundary:boundary + fold_size]
        scored = []
        for weights in CANDIDATES:
            candidate = copy.deepcopy(cfg); candidate["strategy"]["weights"] = weights
            metrics = run(data, candidate, train[0], train[-1])["metrics"]
            scored.append((metrics["sharpe_zero_rate"], weights, metrics))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = copy.deepcopy(cfg); chosen["strategy"]["weights"] = scored[0][1]
        result = run(data, chosen, validation[0], validation[-1])
        folds.append({"train": [train[0], train[-1]], "validation": [validation[0], validation[-1]], "weights": scored[0][1], "validation_metrics": result["metrics"]})
    scored = []
    for weights in CANDIDATES:
        candidate = copy.deepcopy(cfg); candidate["strategy"]["weights"] = weights
        scored.append((run(data, candidate, development[0], development[-1])["metrics"]["sharpe_zero_rate"], weights))
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = copy.deepcopy(cfg); selected["strategy"]["weights"] = scored[0][1]
    stress = copy.deepcopy(selected)
    for key in ("commission_rate", "minimum_commission", "slippage_bps"):
        stress["fees"][key] *= 2
    result = {"status": "candidate_only_not_approved", "mode": cfg["mode"], "data_hash": data.version, "config_hash": digest(cfg), "config": cfg, "code_hash": code_hash(), "trials": CANDIDATES, "walk_forward": folds, "selected_weights": scored[0][1], "holdout": [holdout[0], holdout[-1]], "holdout_metrics": run(data, selected, holdout[0], holdout[-1])["metrics"], "double_cost_metrics": run(data, stress, holdout[0], holdout[-1])["metrics"], "limitations": ["仅三个预定义基线组合，非自动发现优质策略", "各折从空仓开始，结果不可拼接为连续实盘收益", "留出集反复使用将失去独立性，需保留新的未见数据", "演示数据表现没有投资含义；未提供显著性检验或基准超额归因"]}
    result["experiment_id"] = digest(result)[:20]
    write_json(output, result)
    archive = Path(output).parent / "experiments" / (result["experiment_id"] + ".json")
    write_json(archive, result)
    return result

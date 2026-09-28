import hashlib
import json
import math
from pathlib import Path


class ValidationError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if cfg.get("mode") not in ("demo", "live"):
        raise ValidationError("mode 必须为 demo 或 live")
    if not isinstance(cfg.get("initial_cash"), (int, float)) or not math.isfinite(cfg["initial_cash"]) or cfg["initial_cash"] <= 0:
        raise ValidationError("初始资金必须为正数")
    for key in ("commission_rate", "minimum_commission", "sell_stamp_tax_rate", "transfer_fee_rate", "slippage_bps"):
        x = cfg.get("fees", {}).get(key)
        if not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0:
            raise ValidationError("费用参数未设置或非法: " + key)
    s = cfg["strategy"]
    if set(s["weights"]) != {"momentum", "low_volatility", "liquidity"}:
        raise ValidationError("因子必须为 momentum/low_volatility/liquidity")
    if any(not math.isfinite(v) or v < 0 for v in s["weights"].values()) or sum(s["weights"].values()) <= 0:
        raise ValidationError("因子权重非法")
    for key in ("lookback", "holdings", "exit_rank"):
        if type(s[key]) is not int or s[key] < 1:
            raise ValidationError(key + " 必须是正整数")
    if s["lookback"] < 2 or s["exit_rank"] < s["holdings"]:
        raise ValidationError("回看窗口/退出排名非法")
    for key in ("max_weight", "max_participation", "max_price_deviation"):
        if not 0 < s[key] <= 1:
            raise ValidationError(key + " 超出范围")
    if not 0 <= s["cash_reserve"] < 1:
        raise ValidationError("现金保留比例非法")
    for key in ("min_listing_days", "min_amount", "min_order_value"):
        if not math.isfinite(s[key]) or s[key] < 0:
            raise ValidationError(key + " 非法")
    from .targets import validate_target
    validate_target(cfg)
    return cfg


def fee(cfg, side, value):
    f = cfg["fees"]
    if value <= 0:
        return 0.0
    return round(max(f["minimum_commission"], value * f["commission_rate"]) + value * f["transfer_fee_rate"] + (value * f["sell_stamp_tax_rate"] if side == "SELL" else 0), 2)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def code_hash():
    files = sorted(Path(__file__).parent.glob("*.py"))
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in files)).hexdigest()

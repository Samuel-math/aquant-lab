"""Storage admission checks. Core labels are never age-downsampled or deleted."""
import shutil
import json
from pathlib import Path
from .core import ValidationError


def admission(root, max_bytes=None, min_free_bytes=None):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    config = Path('configs/storage.json')
    policy = json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
    if policy.get('full_minute_archive', False):
        raise ValidationError('当前适配器仅支持提取式存储，不支持开启全量分钟归档')
    max_bytes = max_bytes if max_bytes is not None else policy.get('max_bytes', 2 * 1024**3)
    min_free_bytes = min_free_bytes if min_free_bytes is not None else policy.get('min_free_bytes', 512 * 1024**2)
    total = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    free = shutil.disk_usage(root).free
    if total > max_bytes:
        raise ValidationError('数据目录超过存储预算；保留核心数据，停止新增抓取并等待扩容/归档')
    if free < min_free_bytes:
        raise ValidationError('磁盘可用空间低于保留阈值，停止抓取，禁止自动删除核心训练数据')
    return {'used_bytes': total, 'free_bytes': free, 'max_bytes': max_bytes,
            'raw_full_minute_retained': False, 'core_retention': 'permanent_daily_1000_actions_revisions'}

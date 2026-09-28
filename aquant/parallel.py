"""Ordered, bounded process maps; worker count does not affect model semantics."""
import multiprocessing as mp
import os
import sys
from pathlib import Path
from .core import ValidationError


def worker_count(requested):
    if type(requested) is not int or not 1 <= requested <= 64:
        raise ValidationError('workers must be an integer in 1..64')
    available = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    quota = Path('/sys/fs/cgroup/cpu.max')
    if quota.exists():
        amount, period = quota.read_text().split()
        if amount != 'max':
            available = min(available, max(1, int(amount) // int(period)))
    return min(requested, available)


def ordered_map(function, items, workers, initializer, initargs, check_budget):
    workers = worker_count(workers)
    items = list(items)
    if not items:
        return
    if workers == 1:
        initializer(*initargs)
        for item in items:
            check_budget()
            yield function(item)
        return
    # The Linux research worker is single-threaded before creating this pool.
    # fork shares the read-only dataset pages; other platforms use spawn.
    context = mp.get_context('fork' if sys.platform == 'linux' else 'spawn')
    with context.Pool(min(workers, len(items)), initializer, initargs) as pool:
        iterator = pool.imap(function, items, chunksize=1)
        for _ in items:
            while True:
                check_budget()
                try:
                    yield iterator.next(timeout=1)
                    break
                except mp.TimeoutError:
                    continue

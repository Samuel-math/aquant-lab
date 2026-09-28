import multiprocessing as mp
import time
import unittest
from aquant.parallel import ordered_map, worker_count
from aquant.core import ValidationError


def initialize():
    pass


def work(value):
    if value < 0:
        raise ValueError('worker failed')
    time.sleep(value)
    return value


class ParallelTests(unittest.TestCase):
    def test_invalid_worker_count(self):
        for count in [0, -1, 65, True]:
            with self.assertRaises(ValidationError): worker_count(count)

    def test_failure_propagates_and_workers_exit(self):
        before={p.pid for p in mp.active_children()}
        with self.assertRaises(ValueError):
            list(ordered_map(work,[-1,0],2,initialize,(),lambda:None))
        self.assertEqual({p.pid for p in mp.active_children()},before)

    def test_budget_terminates_running_workers(self):
        if worker_count(2)<2: self.skipTest('requires two CPU slots')
        before={p.pid for p in mp.active_children()}
        start=time.monotonic()
        def check():
            if time.monotonic()-start>.2: raise ValidationError('budget')
        with self.assertRaises(ValidationError):
            list(ordered_map(work,[10,10],2,initialize,(),check))
        self.assertLess(time.monotonic()-start,5)
        self.assertEqual({p.pid for p in mp.active_children()},before)

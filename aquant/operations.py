import contextlib
import datetime as dt
import fcntl
import json
from pathlib import Path
from .core import ValidationError, write_json


@contextlib.contextmanager
def lock(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValidationError("任务正在运行，拒绝重复启动")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def health(state_path, expected_date):
    p = Path(state_path)
    if not p.exists():
        raise ValidationError("尚无运行状态")
    state = json.loads(p.read_text(encoding="utf-8"))
    if state.get("status") != "ok" or state.get("date") != expected_date:
        raise ValidationError("巡检失败: " + json.dumps(state, ensure_ascii=False))
    return state


def status(path, date, state, **kwargs):
    write_json(path, dict(date=date, status=state, updated_at=dt.datetime.now(dt.timezone.utc).isoformat(), **kwargs))

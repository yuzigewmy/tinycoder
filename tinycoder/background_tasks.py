from __future__ import annotations

import os
import random
import signal
import string
import time
from typing import Any

BackgroundTaskResult = dict[str, Any]

_tasks: dict[str, BackgroundTaskResult] = {}


def _make_task_id() -> str:
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"shell_{int(time.time() * 1000):x}_{suffix}"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _refresh_record(record: BackgroundTaskResult) -> BackgroundTaskResult:
    if record.get("status") != "running":
        return record
    if _process_alive(int(record.get("pid") or 0)):
        return record
    next_record = dict(record)
    next_record["status"] = "completed"
    _tasks[next_record["taskId"]] = next_record
    return next_record


def register_background_shell_task(args: dict[str, Any]) -> BackgroundTaskResult:
    task = {
        "taskId": _make_task_id(),
        "type": "local_bash",
        "command": args["command"],
        "pid": int(args["pid"]),
        "cwd": args["cwd"],
        "status": "running",
        "startedAt": int(time.time() * 1000),
    }
    _tasks[task["taskId"]] = task
    return dict(task)


def list_background_tasks() -> list[BackgroundTaskResult]:
    return [dict(_refresh_record(task)) for task in _tasks.values()]


def get_background_task(task_id: str) -> BackgroundTaskResult | None:
    task = _tasks.get(task_id)
    if task is None:
        return None
    return dict(_refresh_record(task))


registerBackgroundShellTask = register_background_shell_task
listBackgroundTasks = list_background_tasks
getBackgroundTask = get_background_task

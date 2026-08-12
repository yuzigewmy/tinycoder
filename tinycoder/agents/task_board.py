"""In-memory task board — mirrors qwen-code agents/team/tasks.ts.

Since TinyCoder is single-process, we use an in-memory board instead
of file-based JSON with file locks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

TaskStatus = Literal["pending", "in_progress", "completed"]

@dataclass
class TaskItem:
    id: str
    subject: str
    description: str
    status: TaskStatus = "pending"
    owner: Optional[str] = None
    active_form: Optional[str] = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: Optional[dict] = None


_tasks: dict[str, TaskItem] = {}
_next_id = 1


def reset() -> None:
    global _tasks, _next_id
    _tasks.clear()
    _next_id = 1


def create_task(subject: str, description: str, active_form: Optional[str] = None, metadata: Optional[dict] = None) -> TaskItem:
    global _next_id
    tid = str(_next_id)
    _next_id += 1
    task = TaskItem(id=tid, subject=subject, description=description,
                    active_form=active_form, metadata=metadata)
    _tasks[tid] = task
    return task


def get_task(task_id: str) -> Optional[TaskItem]:
    return _tasks.get(task_id)


def list_tasks(status: Optional[TaskStatus] = None, owner: Optional[str] = None) -> list[TaskItem]:
    result = list(_tasks.values())
    if status:
        result = [t for t in result if t.status == status]
    if owner:
        result = [t for t in result if t.owner == owner]
    result.sort(key=lambda t: int(t.id))
    return result


def update_task(task_id: str, **kwargs) -> Optional[TaskItem]:
    task = _tasks.get(task_id)
    if not task:
        return None
    for key, value in kwargs.items():
        if hasattr(task, key) and value is not None:
            setattr(task, key, value)
    # Unblock dependents on completion
    if kwargs.get("status") == "completed" and task.blocks:
        _unblock_dependents(task.id, task.blocks)
    return task


def delete_task(task_id: str) -> bool:
    task = _tasks.pop(task_id, None)
    if task:
        for t in _tasks.values():
            if task_id in t.blocks:
                t.blocks.remove(task_id)
            if task_id in t.blocked_by:
                t.blocked_by.remove(task_id)
    return task is not None


def _unblock_dependents(completed_id: str, dependent_ids: list[str]) -> None:
    for dep_id in dependent_ids:
        dep = _tasks.get(dep_id)
        if dep and completed_id in dep.blocked_by:
            dep.blocked_by.remove(completed_id)

"""Sub-agent task tools — mirrors qwen-code task-create/list/update.ts."""
from __future__ import annotations

from typing import Any

from ...tool import ToolDefinition
from .. import task_board


# ---- task_create ----

def _validate_create(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict):
        raise ValueError("input must be an object")
    subject = input_value.get("subject")
    description = input_value.get("description")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject is required")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    return {
        "subject": subject.strip(),
        "description": description.strip(),
        "active_form": input_value.get("activeForm"),
        "metadata": input_value.get("metadata"),
    }


async def _run_create(input_value: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    task = task_board.create_task(
        subject=input_value["subject"],
        description=input_value["description"],
        active_form=input_value.get("active_form"),
        metadata=input_value.get("metadata"),
    )
    return {"ok": True, "output": f"Created task #{task.id}: {task.subject}"}


task_create_tool = ToolDefinition(
    name="task_create",
    description="Create a new task in the task list. Tasks can be claimed and completed by sub-agents.",
    input_schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Short title for the task."},
            "description": {"type": "string", "description": "Detailed instructions for the task."},
            "activeForm": {"type": "string", "description": "Present-tense label (e.g. 'Running tests')."},
            "metadata": {"type": "object", "description": "Arbitrary metadata."},
        },
        "required": ["subject", "description"],
    },
    validator=_validate_create,
    run=_run_create,
)


# ---- task_list ----

def _validate_list(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict):
        return {}
    return {
        "status": input_value.get("status"),
        "owner": input_value.get("owner"),
    }


async def _run_list(input_value: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    tasks = task_board.list_tasks(
        status=input_value.get("status"),
        owner=input_value.get("owner"),
    )
    if not tasks:
        return {"ok": True, "output": "No tasks found."}
    lines = []
    for t in tasks:
        block_info = ""
        if t.blocks:
            block_info = f"  blocks: {t.blocks}"
        if t.blocked_by:
            block_info += f"  blocked_by: {t.blocked_by}"
        lines.append(f"#{t.id} [{t.status}] {t.subject}{block_info}")
        if t.owner:
            lines.append(f"  owner: {t.owner}")
    return {"ok": True, "output": "\n".join(lines)}


task_list_tool = ToolDefinition(
    name="task_list",
    description="List tasks with optional filters (status, owner).",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            "owner": {"type": "string", "description": "Filter by owner name."},
        },
    },
    validator=_validate_list,
    run=_run_list,
)


# ---- task_update ----

def _validate_update(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict):
        raise ValueError("input must be an object")
    task_id = input_value.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("taskId is required")
    return {
        "task_id": task_id.strip(),
        "status": input_value.get("status"),
        "owner": input_value.get("owner"),
        "subject": input_value.get("subject"),
        "description": input_value.get("description"),
    }


async def _run_update(input_value: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    tid = input_value["task_id"]
    updates = {k: v for k, v in input_value.items() if k != "task_id" and v is not None}
    task = task_board.update_task(tid, **updates)
    if not task:
        return {"ok": False, "output": f"Task #{tid} not found."}
    return {"ok": True, "output": f"Updated task #{tid}. New status: {task.status}."}


task_update_tool = ToolDefinition(
    name="task_update",
    description="Update a task: change status, assign owner, edit subject/description. " +
                "Set status='completed' to mark done and unblock dependents.",
    input_schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "The task ID to update."},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            "owner": {"type": "string", "description": "Assign or change owner."},
            "subject": {"type": "string", "description": "New subject."},
            "description": {"type": "string", "description": "New description."},
        },
        "required": ["taskId"],
    },
    validator=_validate_update,
    run=_run_update,
)

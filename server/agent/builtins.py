"""builtins.py — meta-tools every agent has: check_task and cancel_task.

These are how the agent answers "is that task done?" and "cancel that". They
act on the TaskManager itself, so they are marked inline_only — the Agent runs
them directly instead of submitting them as tasks (a task that tracks tasks
would be circular).

Task references are resolved loosely: an empty reference means the most recent
task; otherwise we match the reference words against task types. Good enough
for a conversation where the user says "the research" or "that task".
"""
from __future__ import annotations

import logging
from typing import Optional

from .registry import Tool
from .task import Task, TaskStatus
from .task_manager import TaskManager

log = logging.getLogger(__name__)

_REFERENCE_PARAM = {
    "type": "object",
    "properties": {
        "task_reference": {
            "type": "string",
            "description": (
                "Which task the user means, e.g. 'the research' or 'weather'. "
                "Leave empty to mean the most recent task."
            ),
        },
    },
    "required": [],
}


def _resolve(tm: TaskManager, reference: str) -> Optional[Task]:
    tasks = tm.recent()  # newest first
    if not tasks:
        return None
    ref = (reference or "").strip().lower()
    if not ref:
        return tasks[0]
    # Exact task id.
    for t in tasks:
        if t.task_id.lower() == ref:
            return t
    # Word overlap between the reference and the tool/type name.
    ref_words = set(ref.replace("_", " ").split())
    for t in tasks:
        type_words = set(t.type.lower().replace("_", " ").split())
        if ref_words & type_words:
            return t
    # Nothing matched — fall back to the most recent task.
    return tasks[0]


def make_builtin_tools(tm: TaskManager) -> list[Tool]:
    """Build the check_task / cancel_task tools bound to this task manager."""

    async def check_task(task_reference: str = "") -> dict:
        t = _resolve(tm, task_reference)
        if t is None:
            return {"ok": True, "message": "You haven't started any tasks yet."}
        if t.status == TaskStatus.RUNNING:
            msg = f"The {t.type} task (id {t.task_id}) is still running."
        elif t.status == TaskStatus.QUEUED:
            msg = f"The {t.type} task (id {t.task_id}) is queued and about to start."
        elif t.status == TaskStatus.DONE:
            detail = t.result.get("message") if isinstance(t.result, dict) else None
            msg = f"The {t.type} task is done. {detail}" if detail else f"The {t.type} task is done."
        elif t.status == TaskStatus.FAILED:
            msg = f"The {t.type} task failed: {t.error}"
        elif t.status == TaskStatus.CANCELLED:
            msg = f"The {t.type} task was cancelled."
        else:
            msg = f"The {t.type} task is {t.status.value}."
        log.info("check_task -> %s", msg)
        return {"ok": True, "message": msg, "task_id": t.task_id, "status": t.status.value}

    async def cancel_task(task_reference: str = "") -> dict:
        t = _resolve(tm, task_reference)
        if t is None:
            return {"ok": True, "message": "There's no task to cancel."}
        if t.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return {
                "ok": True,
                "message": (f"The {t.type} task already finished "
                            f"({t.status.value}); nothing to cancel."),
            }
        sent = await tm.cancel(t.task_id)
        msg = (f"Okay, I've cancelled the {t.type} task."
               if sent else f"I couldn't cancel the {t.type} task.")
        log.info("cancel_task -> %s", msg)
        return {"ok": True, "message": msg, "task_id": t.task_id}

    return [
        Tool(
            name="check_task",
            description=(
                "Check the status or result of a task the user started earlier. "
                "Use when the user asks if something is done, finished, ready, or "
                "how a task is going."
            ),
            parameters=_REFERENCE_PARAM,
            handler=check_task,
            inline_only=True,
        ),
        Tool(
            name="cancel_task",
            description=(
                "Cancel a task the user started earlier. Use when the user wants "
                "to stop, abort, or call off something already running."
            ),
            parameters=_REFERENCE_PARAM,
            handler=cancel_task,
            inline_only=True,
        ),
    ]

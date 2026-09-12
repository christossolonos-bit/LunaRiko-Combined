"""task_manager.py — runs tools as tasks; fast ones inline, slow ones in the background.

The core idea: tools are never labelled "fast" or "slow". Every task starts as
a background coroutine, and submit() then waits INLINE_TIMEOUT seconds for it:

  * finishes in time  -> result is ready in the same turn (felt as "fast");
  * still running     -> submit() returns and the task keeps going ("slow").

asyncio.wait(timeout=...) does NOT cancel the pending task when the timeout
fires, so a slow task simply continues. Speed is observed, never declared.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .registry import Tool
from .task import Task, TaskStatus

log = logging.getLogger(__name__)

# How long submit() waits for a task before treating it as a background job.
INLINE_TIMEOUT_SECONDS = 3.0


class TaskManager:
    def __init__(self, inline_timeout: float = INLINE_TIMEOUT_SECONDS) -> None:
        self.inline_timeout = inline_timeout
        # Dicts keep insertion order, so iterating in reverse is newest-first.
        self._tasks: dict[str, Task] = {}
        self._aio: dict[str, asyncio.Task] = {}

    async def submit(self, tool: Tool, args: dict) -> Task:
        """Start `tool` as a task. Returns once the task has either finished
        or been running longer than inline_timeout. Read .status on the
        returned Task to tell which happened."""
        task = Task(type=tool.name, args=dict(args))
        self._tasks[task.task_id] = task
        log.info("task %s submitted (tool=%s, args=%s)", task.task_id, tool.name, args)

        aio = asyncio.create_task(self._run(tool, task))
        self._aio[task.task_id] = aio

        done, _pending = await asyncio.wait({aio}, timeout=self.inline_timeout)
        if aio in done:
            log.info("task %s finished inline (status=%s)", task.task_id, task.status.value)
        else:
            log.info("task %s exceeded %.1fs — continuing in background",
                     task.task_id, self.inline_timeout)
        return task

    async def _run(self, tool: Tool, task: Task) -> None:
        task.status = TaskStatus.RUNNING
        log.info("task %s running", task.task_id)
        try:
            result = await tool.handler(**task.args)
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            log.warning("task %s cancelled", task.task_id)
            raise
        except Exception as e:  # handlers shouldn't raise, but never trust that
            task.status = TaskStatus.FAILED
            task.error = f"{type(e).__name__}: {e}"
            log.exception("task %s crashed", task.task_id)
            return

        task.result = result
        if isinstance(result, dict) and result.get("ok") is False:
            task.status = TaskStatus.FAILED
            task.error = result.get("error")
            log.info("task %s failed: %s", task.task_id, task.error)
        else:
            task.status = TaskStatus.DONE
            log.info("task %s done", task.task_id)

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def recent(self, tool_name: Optional[str] = None) -> list[Task]:
        """All tracked tasks, newest first, optionally filtered by tool name."""
        tasks = list(reversed(self._tasks.values()))
        if tool_name is not None:
            tasks = [t for t in tasks if t.type == tool_name]
        return tasks

    async def cancel(self, task_id: str) -> bool:
        """Best-effort cancellation. Works for tasks parked at an await point;
        it cannot interrupt CPU-bound work. Returns True if a cancel was sent."""
        aio = self._aio.get(task_id)
        if aio is None or aio.done():
            return False
        aio.cancel()
        log.info("task %s cancellation requested", task_id)
        return True

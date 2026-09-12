"""task.py — the uniform Task abstraction.

Every tool call becomes a Task with the same shape, whether it finishes in
milliseconds or runs for minutes. One shape means one task manager can handle
every tool the same way.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    type: str                                  # the tool name this task runs
    args: dict                                 # resolved arguments for the tool
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: TaskStatus = TaskStatus.QUEUED
    progress: Optional[str] = None             # optional freeform note ("step 2/5")
    result: Optional[dict] = None              # the tool's {"ok": ...} dict
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        """A plain-dict view — safe to log or hand to a caller."""
        return {
            "task_id": self.task_id,
            "type": self.type,
            "status": self.status.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }

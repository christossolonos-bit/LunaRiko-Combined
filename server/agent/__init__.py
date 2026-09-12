"""agent — a reusable natural-language action agent.

Turns one natural-language request into one tool call and runs it. Natural
language in, natural language out.

This folder is generic — it knows nothing about any particular tool. The two
things you would change are the tool registry (see server/agent_registries/)
and which LLM does the picking (llm_groq.py, or llm.py for the offline mock).
"""
from .core import Agent
from .registry import Tool, ToolRegistry
from .task import Task, TaskStatus
from .task_manager import TaskManager

__all__ = ["Agent", "Tool", "ToolRegistry", "Task", "TaskStatus", "TaskManager"]

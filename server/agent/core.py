"""core.py — the Agent: natural language in, natural language out.

handle_prompt(task, context) is the single entry point. It:
  1. asks the LLM to pick a tool for the task (function calling);
  2. runs the tool — builtins inline, everything else through the task
     manager (which decides fast/inline vs slow/background);
  3. returns a plain natural-language string for the caller (the character
     model) to speak.

The Agent owns one TaskManager, so tasks persist across handle_prompt calls
within a session — that is what makes "is that task done?" work.
"""
from __future__ import annotations

import logging
from typing import Optional

from .builtins import make_builtin_tools
from .llm import LLMClient
from .registry import ToolRegistry
from .task import Task, TaskStatus
from .task_manager import TaskManager

log = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        registry: ToolRegistry,
        llm_client: LLMClient,
        inline_timeout: Optional[float] = None,
        name: str = "agent",
    ) -> None:
        self.name = name
        self.registry = registry
        self.llm = llm_client
        self.task_manager = (
            TaskManager(inline_timeout) if inline_timeout is not None
            else TaskManager()
        )
        # Builtin meta-tools (check_task / cancel_task) are always available,
        # on top of whatever domain tools the deployment registered.
        for tool in make_builtin_tools(self.task_manager):
            self.registry.register(tool)
        log.info(
            "agent '%s' ready with %d tools: %s",
            name, len(self.registry.all()),
            ", ".join(t.name for t in self.registry.all()),
        )

    async def handle_prompt(self, task: str, context: Optional[str] = None) -> str:
        """Turn one natural-language request into an action and return a
        natural-language reply. Never raises."""
        log.info("handle_prompt: %r (context=%r)", task, context)
        try:
            return await self._handle(task, context)
        except Exception as e:  # the agent must never crash its caller
            log.exception("handle_prompt failed unexpectedly")
            return f"Something went wrong while handling that: {e}"

    async def _handle(self, task: str, context: Optional[str]) -> str:
        response = await self.llm.choose_tool(task, context, self.registry.schemas())

        if response.tool_choice is None:
            msg = response.message or "I'm not sure how to do that."
            log.info("no tool selected — replying: %s", msg)
            return msg

        choice = response.tool_choice
        tool = self.registry.get(choice.tool_name)
        if tool is None:
            log.warning("LLM picked unknown tool %r", choice.tool_name)
            return "I tried to do that, but I couldn't find the right action for it."

        log.info("selected tool=%s args=%s", tool.name, choice.arguments)
        # The LLM's arguments are untrusted: keys it invented would blow up
        # handler(**args). Both dispatch paths below get the filtered set.
        args = tool.filter_args(choice.arguments)

        # Builtin meta-tools act on the task manager itself — run them
        # directly, never as a background task.
        if tool.inline_only:
            result = await tool.handler(**args)
            return self._message_of(result)

        # Domain tool — hand to the task manager (inline vs background race).
        task_obj = await self.task_manager.submit(tool, args)
        return self._describe(task_obj)

    def _describe(self, t: Task) -> str:
        """Turn a submitted task's state into a natural-language reply."""
        if t.status == TaskStatus.RUNNING:
            return (
                f"I've started that ({t.type}) — it's running in the background. "
                f"Ask me about it anytime and I'll check on it. (task id: {t.task_id})"
            )
        if t.status == TaskStatus.DONE:
            return self._message_of(t.result)
        if t.status == TaskStatus.FAILED:
            # Prefer the tool's own natural-language message. The agent's tool
            # standard puts the user-facing string in result["message"] for
            # BOTH ok and not-ok results — but the original _describe only used
            # t.error, which is set when a handler RAISES. Tools that return
            # {"ok": False, "message": "<reason>"} left
            # t.error as None, so Riko said "That didn't work: None". Falling
            # back to _message_of(result) surfaces the real reason.
            # — if you want it fixed there too, mirror this change.
            if t.result is not None:
                return self._message_of(t.result)
            return f"That didn't work: {t.error}"
        if t.status == TaskStatus.CANCELLED:
            return f"The {t.type} task was cancelled."
        return f"The {t.type} task is {t.status.value}."

    @staticmethod
    def _message_of(result) -> str:
        """Extract the natural-language message from a tool result dict."""
        if isinstance(result, dict):
            if result.get("message"):
                return str(result["message"])
            if result.get("ok") is False:
                return f"That didn't work: {result.get('error', 'unknown error')}"
        return str(result)

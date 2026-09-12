"""agent_backend.py — the swappable "actions agent" behind do_task.

`do_task` hands a plain-language instruction to an actions agent. WHICH agent
runs it is a deployment choice — and the one part of this feature meant to be
swapped out:

  - step 1 (now): StubActionsAgent — echoes only, does no real work. Proves
                  the wiring without needing a real agent.
  - step 2:       a dummy agent returning a canned result.
  - AGENT_BACKEND="real":  the real Agent — Groq function calling over the
                           tools in the chosen registry.
  - future:       a local llama.cpp server on a Mac mini, etc.

Everything above the agent (do_task, the tool loop, the orchestrator) only
ever sees the `ActionsAgent` protocol — `run(instruction) -> str`. Swapping
the agent never touches them. That is the whole point of this seam.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class ActionsAgent(Protocol):
    """The contract every actions agent must satisfy.

    `run` takes one natural-language instruction and returns one
    natural-language result. It should not raise — see `run_instruction`.
    """

    def run(self, instruction: str) -> str:
        ...


class StubActionsAgent:
    """Default no-op agent. Does no real work — it just confirms the
    instruction reached the agent layer. Replace via `set_actions_agent`."""

    def run(self, instruction: str) -> str:
        print(f"[agent_backend] STUB agent received: {instruction!r}")
        return (
            f"(stub agent) Got the instruction '{instruction}', but no real "
            f"agent is connected yet."
        )


class CallableActionsAgent:
    """Adapts a plain function `(instruction) -> str` into an ActionsAgent.

    Convenient for tests and for the step-2 dummy agent — no class needed.
    """

    def __init__(self, fn: Callable[[str], str]) -> None:
        self._fn = fn

    def run(self, instruction: str) -> str:
        return self._fn(instruction)


# ---------------------------------------------------------------------------
# Step 3 — wrapping the real Agent (async) behind the sync ActionsAgent API.
#
# The pipeline (orchestrator -> backend -> do_task -> agent_backend) is sync /
# threaded. The actions Agent (server.agent.core.Agent) is async, and — more
# importantly — its TaskManager creates `asyncio.create_task` background tasks
# that must keep running between `handle_prompt` calls (that is how
# `check_task` / `cancel_task` can answer "is the research done yet?" on a
# later turn).
#
# So we cannot just `asyncio.run(agent.handle_prompt(...))` per call: that
# spins up a fresh loop, runs the coro, and tears the loop down — cancelling
# every background task. Instead, we keep ONE persistent event loop alive in a
# daemon thread, shared by every AgentRunnerBackend, and submit coroutines to
# it via `run_coroutine_threadsafe`. Background tasks survive across calls
# because the loop never stops.
# ---------------------------------------------------------------------------

import asyncio
import threading


class _AsyncRunner:
    """A single persistent event loop in a daemon background thread.

    Process-wide singleton, created lazily on first use. Designed for the
    actions agent — the loop runs forever, so any task the agent's
    TaskManager creates can outlive the call that submitted it.
    """

    _loop: "asyncio.AbstractEventLoop | None" = None
    _thread: "threading.Thread | None" = None
    _lock = threading.Lock()

    @classmethod
    def loop(cls) -> "asyncio.AbstractEventLoop":
        with cls._lock:
            if cls._loop is None:
                cls._loop = asyncio.new_event_loop()
                cls._thread = threading.Thread(
                    target=cls._loop.run_forever,
                    name="agent-asyncio",
                    daemon=True,
                )
                cls._thread.start()
                print("[agent_backend] started persistent asyncio loop "
                      f"on thread '{cls._thread.name}'")
        return cls._loop

    @classmethod
    def run_coro(cls, coro):
        """Submit `coro` to the persistent loop and block until it finishes."""
        future = asyncio.run_coroutine_threadsafe(coro, cls.loop())
        return future.result()


class AgentRunnerBackend:
    """ActionsAgent that routes instructions through the real `Agent`.

    Wraps `Agent.handle_prompt` (async) so the sync pipeline never has to
    know about asyncio. Every instance shares the persistent loop above, so
    slow background tasks the agent kicks off survive across `do_task`
    calls — that is what makes `check_task` work between Riko's turns.

    Example:
        from server.agent import Agent
        from server.agent.llm_groq import GroqLLMClient
        from server.agent_registries.sample_registry import build_sample_registry

        real = Agent(registry=build_sample_registry(),
                     llm_client=GroqLLMClient(), name="riko-actions")
        set_actions_agent(AgentRunnerBackend(real))
    """

    def __init__(self, agent) -> None:
        # Duck-typed: any object with an async `handle_prompt(text) -> str`
        # works. We deliberately do NOT import server.agent.core here to keep
        # this module importable without the agent stack.
        self._agent = agent
        name = getattr(agent, "name", type(agent).__name__)
        print(f"[agent_backend] AgentRunnerBackend wrapping agent '{name}'")

    def run(self, instruction: str) -> str:
        print(f"[agent_backend] -> agent.handle_prompt({instruction!r})")
        reply = _AsyncRunner.run_coro(self._agent.handle_prompt(instruction))
        print(f"[agent_backend] <- agent reply: {reply!r}")
        return str(reply)


# Module-level singleton: the agent currently wired in. Starts as the stub.
_active_agent: ActionsAgent = StubActionsAgent()


def set_actions_agent(agent: ActionsAgent) -> None:
    """Swap the active actions agent. The single seam for changing providers."""
    global _active_agent
    _active_agent = agent
    print(f"[agent_backend] active agent set to {type(agent).__name__}")


def get_actions_agent() -> ActionsAgent:
    """Return the agent currently wired in."""
    return _active_agent


def run_instruction(instruction: str) -> str:
    """Run `instruction` on the active agent.

    Never raises: a failure comes back as a plain-language string so the
    character model can react to it in voice instead of the turn crashing.
    """
    agent = get_actions_agent()
    try:
        return agent.run(instruction)
    except Exception as e:  # an agent failure must not kill Riko's turn
        print(f"[agent_backend] ERROR running instruction: {type(e).__name__}: {e}")
        return f"The task couldn't be completed ({type(e).__name__}: {e})."


if __name__ == "__main__":
    # Offline self-check — no network, no API key needed.
    # Run with: python -m server.function_calling.agent_backend
    print("[agent_backend.__main__] default (stub) agent:")
    print("  ->", run_instruction("check the weather in Vancouver"))

    print("[agent_backend.__main__] swapping in a CallableActionsAgent:")
    set_actions_agent(CallableActionsAgent(
        lambda instr: f"Pretended to do: {instr}"
    ))
    out = run_instruction("start a poll")
    print("  ->", out)
    assert out == "Pretended to do: start a poll", out

    print("[agent_backend.__main__] error path (agent raises):")
    def _boom(_instr: str) -> str:
        raise ValueError("simulated failure")
    set_actions_agent(CallableActionsAgent(_boom))
    out = run_instruction("do something")
    print("  ->", out)
    assert out.startswith("The task couldn't be completed"), out

    print("[agent_backend.__main__] OK — stub, swap, and error path all work")

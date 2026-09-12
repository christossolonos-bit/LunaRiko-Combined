"""dummy_agent.py — a hand-written stand-in for the real actions agent.

Step 2 of the function-calling rollout. `DummyActionsAgent` satisfies the
`ActionsAgent` protocol — `run(instruction) -> str` — but does NO real work
and uses NO LLM and NO network: it keyword-matches the instruction to one of
a few canned "functions" and returns a fixed result.

Its only job is to prove the whole pipeline end to end:

  Riko -> do_task -> agent_backend -> DummyActionsAgent -> result -> Riko

Swap it for the real `Agent`, which routes with an LLM over real tools, by
setting AGENT_BACKEND = "real" in server/config.py. Because both satisfy the
same `ActionsAgent`
protocol, that swap is a single line in chat_terminal.py.
"""

from __future__ import annotations


class DummyActionsAgent:
    """Keyword-routed, no-LLM, no-network stand-in for the real agent.

    Recognises two "functions" — weather and Twitch poll — and falls back to a
    generic confirmation for anything else.
    """

    def run(self, instruction: str) -> str:
        low = instruction.lower()
        print(f"[dummy_agent] routing instruction: {instruction!r}")

        if any(w in low for w in ("weather", "temperature", "forecast")):
            return self._weather(instruction)
        if "poll" in low:
            return self._poll(instruction)
        # Fallback — no specific function matched.
        result = f"Done — I completed the task: '{instruction}'."
        print(f"[dummy_agent] -> fallback: {result!r}")
        return result

    # --- canned "functions" ----------------------------------------------
    def _weather(self, instruction: str) -> str:
        location = self._after(instruction, ("in ", "for ", "at ")) or "your area"
        result = f"The weather in {location} is 23 degrees C and sunny today."
        print(f"[dummy_agent] -> weather: {result!r}")
        return result

    def _poll(self, instruction: str) -> str:
        result = (
            f"A Twitch poll has been started for: '{instruction}'. "
            f"Viewers are voting now."
        )
        print(f"[dummy_agent] -> poll: {result!r}")
        return result

    # --- helper ----------------------------------------------------------
    @staticmethod
    def _after(text: str, markers: tuple[str, ...]) -> str:
        """Text following the first marker found (case-insensitive)."""
        low = text.lower()
        for m in markers:
            i = low.find(m)
            if i != -1:
                return text[i + len(m):].strip(" ?.!,'\"")
        return ""


if __name__ == "__main__":
    # Offline self-check — no network, no API key. Run with:
    #   python -m server.function_calling.dummy_agent
    agent = DummyActionsAgent()

    cases = [
        ("check the weather in Vancouver", "weather"),
        ("what's the temperature for Tokyo", "weather"),
        ("start a poll asking which game to play", "poll"),
        ("write a haiku about foxes", "fallback"),
    ]
    for instruction, kind in cases:
        out = agent.run(instruction)
        print(f"  [{kind:8}] {instruction!r}\n            -> {out!r}\n")
        assert isinstance(out, str) and out

    # Spot-check the weather location extraction.
    assert "Vancouver" in agent.run("check the weather in Vancouver")
    print("[dummy_agent.__main__] OK — routing + location extraction work")

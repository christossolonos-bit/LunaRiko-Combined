"""Tool registries — the lists of things she can actually DO.

She only ever sees ONE tool: `do_task`. She calls it with a plain-English
instruction ("start a poll about what to play next"), and a second, smaller
agent reads that instruction and picks the real tool to run. Two consequences
worth knowing:

  - adding a tool never means rewriting her personality prompt;
  - her own model never sees ten tool schemas, so her replies stay in
    character instead of turning into a function-call machine.

WHAT IS IN HERE
  base_registry.py    Always on. Merged into whichever registry below is
                      chosen, so she can reach both in one turn.
  sample_registry.py  The example: `get_weather` and `start_poll`, both
                      canned. No credentials, no network. This is the file to
                      copy when you write your own.

Which one is live is `REGISTRY` in server/config.py.

---------------------------------------------------------------------------
WRITING YOUR OWN
---------------------------------------------------------------------------
1. Copy sample_registry.py to `my_registry.py` and edit the two handlers.
   A handler is an `async def` that returns {"ok": bool, "message": str} and
   NEVER raises — the message is what she is told, so write it as a sentence
   she can react to.
2. Add a branch for it in `_build_actions_agent()` in server/chat_terminal.py
   (three lines, next to the "sample" one).
3. Set `REGISTRY = "my_registry"` in server/config.py.

Two rules the agent depends on:
  - `inline_only=True` means "she waits for the answer". Leave it off and
    anything slower than a few seconds is run in the background and reported
    later instead of stalling her mid-sentence.
  - The tool `description` is the ONLY thing the routing agent sees. Write it
    for a reader who cannot see the code: say when to use it, not how it
    works.

Check a registry without any network:
    python -m server.agent_registries.sample_registry
"""

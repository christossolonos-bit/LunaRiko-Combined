"""function_calling — the tool-calling layer. Letting her DO things.

She is exposed to exactly ONE tool, `do_task`, which forwards a plain-English
instruction to a separate, swappable agent. That agent picks the real tool and
runs it.

Why one tool and not ten: over-instructing a character model makes it drift
into generic-assistant behaviour — long, helpful, and nothing like the persona
you wrote. Her prompt stays about who she is, and the tools behind `do_task`
can grow without ever touching it.

Layout:
  do_task.py       the tool itself: schema, prompt hint, handler
  agent_backend.py the swap point — which agent actually answers
  dummy_agent.py   a canned agent that needs no API key. Good for testing
  tool_loop.py     the streaming Responses-API tool-call loop

Switch the whole feature with ENABLE_FUNCTION_CALLING in server/config.py.
The tools themselves live in server/agent_registries/ — start there.
"""

"""Streaming agent — decides WHEN she speaks and WHAT she is told.

Three layers, each replaceable on its own:

    inputs/            producers, each on its own thread (voice today)
    state.py           what all of them add up to
    decision_engine    the loop: does anything here mean she should reply?
    payload_formatter  turns the state into plain English for her prompt

Today only one thing makes her speak: you finished a sentence. The structure
exists so that adding a second one — a camera, a chat feed, a timer — is an
input plus a rule, not a rewrite of the conversation loop.

runtime.py wraps the whole thing in four calls. That is what
server/chat_terminal.py talks to.
"""

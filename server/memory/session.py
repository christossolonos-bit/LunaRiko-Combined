"""MemorySession — orchestrates the three memory tiers for one session.

Owns the verbatim window (short), the rolling gist (medium), the FAISS
store (long) and the raw SessionLog. This is the glue the live loop needs;
the chat demo (and, later, chat_terminal) drive it through three calls:

    ctx, history = sess.retrieve_context(user_text)   # build prompt injection
    ...                                               # run the LLM yourself
    sess.record(user_text, assistant_text)            # store turn + maybe compact
    sess.extract()                                    # at session end

NON-BLOCKING by design: when the window overflows, the gist is recomputed on
a BACKGROUND thread and the window is trimmed only once that finishes. Until
then the full (untrimmed) window stays in the prompt — so the turn never
waits on the network and there is never a context gap. A single compaction
runs at a time; if another overflow happens first, it's picked up next turn.
"""

from __future__ import annotations

import threading

from server.memory.session_log import SessionLog, ROLE_LABELS
from server.memory.window import RollingWindow
from server.memory.gist import update_gist


def _fmt(msgs: list[dict]) -> str:
    """Render window messages as plain text for the gist LLM. Handles the two
    Responses-API typed items (function_call / function_call_output) that ride
    in the window alongside role messages — they have no role/content."""
    lines = []
    for m in msgs:
        if "role" in m:
            lines.append(f"{ROLE_LABELS.get(m['role'], m['role'])}: {m['content']}")
        elif m.get("type") == "function_call":
            lines.append(f"tool_call: {m.get('name', '?')}({m.get('arguments', '')})")
        elif m.get("type") == "function_call_output":
            lines.append(f"tool_result: {m.get('output', '')}")
    return "\n".join(lines)


class MemorySession:
    def __init__(self, store=None,
                 log: SessionLog | None = None,
                 window: RollingWindow | None = None,
                 enable_memory: bool = True,
                 enable_gist: bool = True):
        # enable_memory → FAISS long-term retrieval + session-end extraction.
        # enable_gist   → medium-term rolling gist + window compaction.
        # Independent so the two config flags map straight through.
        self.enable_memory = enable_memory
        self.enable_gist = enable_gist
        # Only open FAISS when long-term RAG is actually on (optional backup).
        if store is not None:
            self.store = store
        elif enable_memory:
            from server.memory.store import MemoryStore
            self.store = MemoryStore()
        else:
            self.store = None
        self.log = log or (SessionLog() if enable_memory else None)
        self.window = window or RollingWindow()
        self.history: list[dict] = []
        self.gist: str = ""
        self._lock = threading.Lock()
        self._compacting = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    def retrieve_context(self, user_text: str) -> tuple[str, list[dict]]:
        """Return (injected_context, history_snapshot) for prompt building.

        injected_context = memory block + gist block, in the §6 format. The
        history snapshot is a copy so the caller can't race the bg trimmer.
        Discord speakers only get THEIR memories / scoped history.
        """
        from server.memory.discord_speaker import (
            parse_discord_speaker,
            recent_facts_about_speaker,
            scope_history_for_speaker,
            speaker_context_block,
            strip_discord_prefix,
        )

        speaker = parse_discord_speaker(user_text)
        with self._lock:
            gist = self.gist
            history = list(self.history)

        query = strip_discord_prefix(user_text) if speaker else user_text
        mem_block = ""
        if self.enable_memory:
            if speaker and speaker.subject_id != "unknown":
                mem_block = self.store.retrieve_formatted(
                    query,
                    subject_id=speaker.subject_id,
                    subject_name=speaker.display,
                    subject_role=speaker.role,
                )
            else:
                mem_block = self.store.retrieve_formatted(
                    query,
                    subject_id="local",
                    subject_name=None,
                    subject_role="local",
                )

        parts = []
        if speaker:
            parts.append(speaker_context_block(speaker))
            recent = recent_facts_about_speaker(history, speaker)
            if recent:
                parts.append(recent)
            history = scope_history_for_speaker(history, speaker)
        if mem_block:
            parts.append(mem_block)
        if self.enable_gist and gist.strip():
            # Gist is still shared — remind the model not to mix identities.
            parts.append(
                "Earlier this session (shared call summary — keep speakers "
                "distinct; do not move one person's facts onto another):\n"
                + gist
            )
        return "\n\n".join(parts), history

    def record(self, user_text: str, assistant_text: str,
               tool_items: list[dict] | None = None) -> None:
        """Append the turn to window + raw log, then maybe kick off compaction.

        `tool_items`, if given, are the Responses-API function_call /
        function_call_output items from this turn (built by
        orchestrator._build_tool_items). They sit BETWEEN the user and assistant
        messages in the verbatim window so the next turn's prompt shows the model
        its own recent tool use. They are NOT written to the long-term raw log —
        that stays a clean user/assistant transcript for memory extraction.
        """
        with self._lock:
            self.history.append({"role": "user", "content": user_text})
            if tool_items:
                self.history.extend(tool_items)
            self.history.append({"role": "assistant", "content": assistant_text})
        if self.enable_memory:  # raw log only feeds long-term extraction
            self.log.append("user", user_text)
            self.log.append("assistant", assistant_text)
        if self.enable_gist:
            self._maybe_compact()

    def history_snapshot(self) -> list[dict]:
        """Thread-safe copy of the window (for persisting while the bg trimmer runs)."""
        with self._lock:
            return list(self.history)

    # ------------------------------------------------------------------ #
    def _maybe_compact(self) -> None:
        with self._lock:
            if self._compacting or not self.window.needs_compaction(self.history):
                return
            self._compacting = True
            evict, _ = self.window.split(self.history)
        self._thread = threading.Thread(target=self._compact, args=(evict,), daemon=True)
        self._thread.start()

    def _compact(self, evict: list[dict]) -> None:
        try:
            new_gist = update_gist(self.gist, _fmt(evict))
        except Exception as e:  # never lose context on a failed gist call
            print(f"[mem] gist update failed; keeping window intact: {e}")
            with self._lock:
                self._compacting = False
            return
        with self._lock:
            self.gist = new_gist
            del self.history[: len(evict)]  # trim only the pairs we summarized
            self._compacting = False
        print(f"[mem] gist updated in background; window trimmed to {len(self.history)}")

    def wait_idle(self) -> None:
        """Block until any in-flight background compaction finishes."""
        t = self._thread
        if t is not None and t.is_alive():
            print("[mem] waiting for background gist to finish...")
            t.join()

    # ------------------------------------------------------------------ #
    def extract(self) -> dict:
        """Distill the full session transcript into the long-term store."""
        if not self.enable_memory:
            return {"added": 0, "skipped": 0}
        from server.memory.extractor import extract_session
        self.wait_idle()
        return extract_session(self.log.transcript(), store=self.store)


if __name__ == "__main__":
    # Offline smoke test: drive fake turns through compaction with a stubbed
    # gist (no network), assert the window trims and never starts with assistant.
    # Rebind the module global so _compact (same module) picks up the stub.
    update_gist = lambda old, lines: ((old + " | " if old else "") + lines.replace("\n", " "))[-400:]

    sess = MemorySession(window=RollingWindow(max_msgs=8, evict_chunk=4))
    sess.log = type("L", (), {"append": lambda *a: None, "transcript": lambda s="": ""})()
    for i in range(6):
        sess.record(f"user line {i}", f"riko line {i}")
        sess.wait_idle()
    with sess._lock:
        assert sess.history[0]["role"] == "user"
        assert len(sess.history) <= 8
    assert sess.gist, "gist never built"
    print(f"[session.__main__] OK — window={len(sess.history)} gist_len={len(sess.gist)} (no network)")

    # Tool items ride in the window between user and assistant, survive
    # compaction (folded into the gist via _fmt), and never leave the window
    # starting on a non-user message.
    tool_items = [
        {"type": "function_call", "call_id": "c9", "name": "do_task", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c9", "output": "done"},
    ]
    for i in range(6, 10):
        sess.record(f"user line {i}", f"riko line {i}", tool_items=tool_items)
        sess.wait_idle()
    with sess._lock:
        assert sess.history[0]["role"] == "user", "window must start with user"
        assert any(m.get("type") == "function_call" for m in sess.history), \
            "tool items should be present in the recent window"
    print("[session.__main__] OK — tool items recorded, compacted, window valid")

"""Run the agent on its own, with no LLM and no avatar.

Useful for checking the microphone and the trigger rules in isolation: it
prints the payload it WOULD have sent instead of running a turn. If this
prints your words back at you, the input half of the pipeline is fine and any
problem is downstream.

    python -m server.streaming_agent.main
"""
import logging
from queue import Queue

from server.config import VAD_SILENCE_DURATION
from server.streaming_agent.decision_engine import DecisionEngine
from server.streaming_agent.inputs.voice_input import VoiceInput
from server.streaming_agent.state import AgentState


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    configure_logging()
    log = logging.getLogger("streaming_agent")
    log.info("=" * 60)
    log.info("Streaming agent — voice only, no LLM. Say something. Ctrl-C quits.")
    log.info("=" * 60)

    event_queue: Queue = Queue()
    state = AgentState()

    # Add an input here and it joins the fan-in. One line each.
    inputs = [
        VoiceInput(event_queue=event_queue, state=state,
                   silence_duration=VAD_SILENCE_DURATION),
    ]
    for stream in inputs:
        stream.start()

    # No payload_sink and no responding_event: the engine prints each payload
    # and treats the response as instant.
    engine = DecisionEngine(state=state, event_queue=event_queue)
    try:
        engine.run()
    except KeyboardInterrupt:
        log.info("Ctrl-C — shutting down")
    finally:
        engine.stop()
        for stream in inputs:
            stream.stop()


if __name__ == "__main__":
    main()

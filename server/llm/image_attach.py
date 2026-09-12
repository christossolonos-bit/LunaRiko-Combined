"""Structured image input for the OpenAI Responses API.

Turns a user message + an image file on disk into the Responses-API
multimodal content array:

    [
      {"type": "input_text",  "text":  "<what the user said>"},
      {"type": "input_image", "image_url": "data:image/jpeg;base64,..."},
    ]

Only the SINGLE turn that carries the image pays image tokens — history
persists `history_text()` (plain text + a short marker) instead, so the
image is never re-sent on later turns.

Swap point for the vision phase: when the Mac mini VLM comes online, a
local caption can replace `build_image_content` (inject caption text
instead of the image) without touching the orchestrators or the UI.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import List

# Spoken/stored stand-in when the user attaches an image with no message.
DEFAULT_IMAGE_TEXT = "[shared an image]"

# Suffix → MIME. Anything unknown is sent as jpeg (browsers only hand us
# real image files — the picker is `accept="image/*"`).
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def image_to_data_url(image_path: str) -> str:
    """Read the file and return a base64 `data:` URL for `input_image`."""
    path = Path(image_path)
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    print(f"[image_attach] encoded {path.name} ({path.stat().st_size} bytes, {mime})")
    return f"data:{mime};base64,{b64}"


def build_image_content(user_text: str, image_path: str) -> List[dict]:
    """Content array for a user message that carries an image."""
    return [
        {"type": "input_text", "text": user_text or DEFAULT_IMAGE_TEXT},
        {"type": "input_image", "image_url": image_to_data_url(image_path)},
    ]


def history_text(user_text: str) -> str:
    """What gets PERSISTED to history for an image turn (no image tokens)."""
    base = user_text or DEFAULT_IMAGE_TEXT
    return base if "[shared an image]" in base else f"{base} [shared an image]"


if __name__ == "__main__":
    # Self-test with a generated 1x1 PNG — no network, no real image needed.
    import tempfile

    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
        "YGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
    )
    tmp = Path(tempfile.gettempdir()) / "image_attach_test.png"
    tmp.write_bytes(png_1x1)

    content = build_image_content("what is this?", str(tmp))
    assert content[0] == {"type": "input_text", "text": "what is this?"}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    print("[image_attach.__main__] content array OK")

    content_no_text = build_image_content("", str(tmp))
    assert content_no_text[0]["text"] == DEFAULT_IMAGE_TEXT
    print("[image_attach.__main__] empty-text default OK")

    assert history_text("look at this") == "look at this [shared an image]"
    assert history_text("") == DEFAULT_IMAGE_TEXT
    print("[image_attach.__main__] history_text OK")
    tmp.unlink()

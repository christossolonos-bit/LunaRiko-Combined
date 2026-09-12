"""Shared embedder for the memory subsystem.

Reuses the *same* MiniLM already loaded for the expression / emote
semantic pickers (`semantic_picker._get_model`), so only ONE copy of the
model sits in RAM process-wide. No download, no second model — the model
lives at `config.MEMORY_EMBED_MODEL` (== `MINILM_PATH`).

The old multi-user embedder also handed out an OpenAI client and used
sklearn to normalize; both are gone. Cosine similarity is handled by
`SentenceTransformer(..., normalize_embeddings=True)` at encode time, so
FAISS `IndexFlatIP` gives true cosine scores.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

from server.config import MEMORY_EMBED_MODEL
from server.llm.semantic_picker import _get_model


def get_embedder() -> SentenceTransformer:
    """Return the shared MiniLM SentenceTransformer at MEMORY_EMBED_MODEL.

    Goes through `semantic_picker._get_model` so the picker and the memory
    store share one cached model instance.
    """
    return _get_model(MEMORY_EMBED_MODEL)


if __name__ == "__main__":
    import numpy as np

    embedder = get_embedder()
    vecs = embedder.encode(
        ["hello world", "hello world", "completely different sentence"],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    print(f"[embedder.__main__] model loaded, dim={vecs.shape[1]}")
    # Normalized → identical sentences have cosine ~1.0, different ones lower.
    assert np.isclose(float(vecs[0] @ vecs[1]), 1.0, atol=1e-4), "identical text should match"
    assert float(vecs[0] @ vecs[2]) < 0.9, "different text should not match closely"

    # Confirm it is the SAME cached object the semantic picker would get.
    assert get_embedder() is embedder, "embedder should be cached/shared"
    print("[embedder.__main__] OK — shared model, normalized embeddings")

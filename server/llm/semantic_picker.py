"""Reusable semantic picker.

Maps a free-text query (e.g. "moneyeyes", "furious", "backflip") to the
closest name in a fixed list (e.g. the 6 VRM expressions, or all `.fbx`
emote names) by cosine similarity on MiniLM embeddings.

Below `threshold`, returns a caller-supplied fallback so unknown queries
land on a safe default (e.g. "neutral" expression, "idle" emote).

The MiniLM model is loaded lazily once and cached at module scope so that
multiple pickers — expression, emote, and any future ones — share a single
loaded model in memory.

Public surface:
    SemanticPicker(names, model_path, threshold=0.35, fallback="neutral")
        .pick(query) -> (name: str, score: float)
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer


# Module-level cache: model_path -> loaded SentenceTransformer.
# Two pickers using the same model_path share one in-memory model.
_MODEL_CACHE: Dict[str, SentenceTransformer] = {}


def _get_model(model_path: str) -> SentenceTransformer:
    """Return the cached SentenceTransformer for `model_path`, loading on miss."""
    if model_path not in _MODEL_CACHE:
        print(f"[match] loading model: {model_path}")
        _MODEL_CACHE[model_path] = SentenceTransformer(model_path)
    return _MODEL_CACHE[model_path]


class SemanticPicker:
    """Pick the closest name in a fixed list for a free-text query.

    Names are embedded once at construction. Queries are embedded on each
    `.pick(...)` call. Below `threshold`, returns `(fallback, top_score)`.
    """

    def __init__(
        self,
        names: Iterable[str],
        model_path: str,
        threshold: float = 0.35,
        fallback: str = "neutral",
        aliases: Optional[Mapping[str, str]] = None,
    ) -> None:
        """If `aliases` is provided, each name's embedding is computed from
        `aliases[name]` (a richer synonym string) instead of the bare name.
        The public `.pick()` return value is still the bare name — only the
        embedding side changes. Names not in `aliases` fall back to their
        bare form."""
        self.names: List[str] = list(names)
        if not self.names:
            raise ValueError("SemanticPicker needs at least one candidate name")
        self.threshold = threshold
        self.fallback = fallback

        self._model = _get_model(model_path)

        # Choose what to embed per name: the alias string if one exists, else
        # the bare name. `texts_to_embed` and `self.names` stay index-aligned.
        if aliases:
            texts_to_embed = [aliases.get(n, n) for n in self.names]
            n_aliased = sum(1 for n in self.names if n in aliases)
            print(f"[match] using aliases for {n_aliased}/{len(self.names)} names")
        else:
            texts_to_embed = list(self.names)

        # Pre-embed candidates once. normalize_embeddings=True so cosine
        # similarity reduces to a plain dot product later.
        self._name_embs = self._model.encode(
            texts_to_embed,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        print(
            f"[match] picker ready: {len(self.names)} names, "
            f"threshold={self.threshold}, fallback={self.fallback!r}"
        )

    def pick(self, query: str) -> Tuple[str, float]:
        """Return `(best_name, score)`. Falls back to `self.fallback` when the
        top similarity is below `self.threshold`."""
        q_emb = self._model.encode(
            [query],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )[0]
        sims = self._name_embs @ q_emb  # both already L2-normalized → cosine
        best_idx = int(np.argmax(sims))
        best_score = float(sims[best_idx])
        best_name = self.names[best_idx]

        if best_score < self.threshold:
            print(
                f"[match] '{query}' -> '{self.fallback}' "
                f"(score={best_score:.2f}, top='{best_name}', below threshold)"
            )
            return self.fallback, best_score

        print(f"[match] '{query}' -> '{best_name}' (score={best_score:.2f})")
        return best_name, best_score


if __name__ == "__main__":
    # Hardcoded test against the 6 VRM 1.0 expressions.
    # Run with: python -m server.llm.semantic_picker
    from server.config import MINILM_PATH, MATCH_THRESHOLD

    names = ["angry", "happy", "neutral", "relaxed", "sad", "surprised"]
    queries = ["moneyeyes", "furious", "crying", "zzz", "glee", "backflip"]

    picker = SemanticPicker(
        names=names,
        model_path=MINILM_PATH,
        threshold=MATCH_THRESHOLD,
        fallback="neutral",
    )

    print(f"\n[semantic_picker.__main__] queries: {queries}\n")
    results = {q: picker.pick(q) for q in queries}

    print("\n[semantic_picker.__main__] summary:")
    for q, (name, score) in results.items():
        print(f"  {q!r:>14}  ->  {name!r:<12}  (score={score:.2f})")

    # Soft check: obviously unrelated queries should fall back to neutral.
    for q in ("zzz", "backflip"):
        name, score = results[q]
        if name != "neutral":
            print(
                f"[semantic_picker.__main__] WARN: expected fallback for {q!r} "
                f"but got {name!r} (score={score:.2f}) — threshold may need tuning"
            )

    print("[semantic_picker.__main__] done")

"""MemoryStore — FAISS vector store for long-term memories (single user).

One index, fixed paths (no `user_id` — this repo is single-user). Stores
rich `MemorySchema` rows but injects ONLY the summaries into the prompt.

  - `add(memories)`     embed + cosine-dedup + persist; returns {added, skipped}
  - `retrieve_formatted(query, top_k)`  → lean bullet block for the system prompt

Vectors are L2-normalized at encode time, so FAISS `IndexFlatIP` returns
true cosine similarity. Metadata lives index-aligned in `memories.json`.

Ranking (v1): similarity first, with a *light* recency tiebreak so that,
among comparably-relevant memories, the more recent one surfaces. Richer
scoring (arousal/valence/MMR/retrieval_count) is future work — the stored
fields already support it without a rewrite.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import faiss
import numpy as np

from server.config import (
    MEMORY_DIR,
    MEMORY_TOP_K,
    MEMORY_DEDUP_THRESHOLD,
    MEMORY_MIN_SIMILARITY,
)
from server.memory.embedder import get_embedder
from server.memory.schema import MemorySchema

logger = logging.getLogger(__name__)

# Whom the retrieved memories are about when no Discord subject is given.
USER_NAME = "the user"

# How much a memory's recency nudges its rank. Small on purpose ("light"):
# it only re-orders memories of near-equal similarity, never overrides a
# clearly-more-relevant hit. Newer date → up to this much added to score.
RECENCY_WEIGHT = 0.05


class MemoryStore:
    def __init__(self, mem_dir: Path | str | None = None):
        self.dir = Path(mem_dir) if mem_dir is not None else MEMORY_DIR
        self.faiss_path = self.dir / "memories.faiss"
        self.meta_path = self.dir / "memories.json"

        self.embedder = get_embedder()
        # Probe-encode for the vector dim — avoids the deprecated
        # get_sentence_embedding_dimension() and is version-proof.
        self.dim = int(self._embed(["dimension probe"]).shape[1])

        if self.faiss_path.exists():
            self.index = faiss.read_index(str(self.faiss_path))
        else:
            self.index = faiss.IndexFlatIP(self.dim)

        if self.meta_path.exists():
            with open(self.meta_path, "r", encoding="utf-8") as f:
                self.metadata: list[dict] = json.load(f)
        else:
            self.metadata = []

        print(f"[mem] store ready: {self.index.ntotal} memories at {self.dir}")

    # ------------------------------------------------------------------ #
    # WRITE
    # ------------------------------------------------------------------ #
    def _embed(self, texts: list[str]) -> np.ndarray:
        vecs = self.embedder.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True
        )
        return vecs.astype("float32")

    def add(self, memories: list[MemorySchema]) -> dict:
        """Embed + dedup + persist. Returns {'added': n, 'skipped': n}.

        Dedup is one-by-one (against the live index) so near-duplicates
        *within* the same batch are also caught.
        """
        if not memories:
            return {"added": 0, "skipped": 0}

        vectors = self._embed([m.summary for m in memories])
        added, skipped = 0, 0

        for i, mem in enumerate(memories):
            vec = vectors[i].reshape(1, -1)
            if self.index.ntotal > 0:
                sims, _ = self.index.search(vec, 1)
                similarity = float(sims[0][0])
                if similarity >= MEMORY_DEDUP_THRESHOLD:
                    print(f"[mem] SKIP dup (sim={similarity:.2f}): {mem.summary!r}")
                    skipped += 1
                    continue

            self.index.add(vec)
            self.metadata.append(mem.model_dump())
            added += 1
            print(f"[mem] ADD ({mem.memory_type}/{mem.emotional_tone}): {mem.summary!r}")

        if added > 0:
            self._persist()
        print(f"[mem] add done: added={added} skipped={skipped} total={self.index.ntotal}")
        return {"added": added, "skipped": skipped}

    def _persist(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.faiss_path))
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # READ
    # ------------------------------------------------------------------ #
    def _recency_bonus(self, timestamp: str) -> float:
        """0..RECENCY_WEIGHT — newer dates score higher, older fade to 0.

        Linear over a ~2-year horizon; anything older contributes nothing.
        Unparseable timestamps contribute nothing (treated as oldest).
        """
        try:
            days_old = (date.today() - date.fromisoformat(timestamp)).days
        except (ValueError, TypeError):
            return 0.0
        horizon = 730.0  # days
        freshness = max(0.0, 1.0 - days_old / horizon)
        return RECENCY_WEIGHT * freshness

    def retrieve_formatted(
        self,
        query: str,
        top_k: int = MEMORY_TOP_K,
        *,
        subject_id: str | None = None,
        subject_name: str | None = None,
        subject_role: str | None = None,
    ) -> str:
        """Return a lean bullet block of the top_k summaries, or "" if empty.

        When `subject_id` is set, only memories about that person are returned.
        Legacy rows (no subject_id) are only shown to owner/local speakers —
        never to guests — so old "the user" facts don't leak onto strangers.
        """
        if self.index.ntotal == 0 or top_k <= 0:
            return ""

        fetch_k = min(max(top_k * 8, top_k), self.index.ntotal)
        qvec = self._embed([query])
        sims, idxs = self.index.search(qvec, fetch_k)

        role = (subject_role or "").strip().lower()
        scored = []
        dropped = 0
        skipped_subject = 0
        for sim, idx in zip(sims[0], idxs[0]):
            if idx == -1:
                continue
            if float(sim) < MEMORY_MIN_SIMILARITY:
                dropped += 1
                continue
            mem = self.metadata[idx]
            if subject_id:
                mem_sid = (mem.get("subject_id") or "").strip()
                if mem_sid:
                    if mem_sid != subject_id:
                        skipped_subject += 1
                        continue
                else:
                    # Unscoped legacy memory.
                    if role == "guest" or (subject_id.isdigit() and role != "owner"):
                        skipped_subject += 1
                        continue
            score = float(sim) + self._recency_bonus(mem.get("timestamp", ""))
            scored.append((score, mem))

        if not scored:
            print(
                f"[mem] no memories for query={query!r} subject={subject_id!r} "
                f"(dropped_floor={dropped} skipped_subject={skipped_subject})"
            )
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        lines = [f"- {mem['summary']}" for _, mem in scored[:top_k]]
        label = subject_name or USER_NAME
        if subject_id and subject_id not in ("local", "unknown"):
            who = f"{label} (<@{subject_id}>)"
        else:
            who = label
        block = f"Things you remember about {who} (this person only):\n" + "\n".join(lines)
        print(
            f"[mem] retrieved {len(lines)} for subject={subject_id!r} "
            f"(dropped_floor={dropped} skipped_subject={skipped_subject}) query={query!r}"
        )
        return block


if __name__ == "__main__":
    # Offline smoke test against a throwaway temp dir (does NOT touch the
    # real store). Full coverage lives in server/memory/test_store.py.
    import tempfile
    from server.memory.schema import VAD

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(mem_dir=tmp)
        res = store.add([
            MemorySchema(summary="The user loves spicy ramen.", memory_type="semantic",
                         emotional_tone="fond", vad=VAD(valence=0.8, arousal=0.4, dominance=0.5),
                         timestamp="2026-06-27"),
        ])
        assert res == {"added": 1, "skipped": 0}, res
        out = store.retrieve_formatted("what food does the user like?", top_k=1)
        assert "ramen" in out and out.startswith("Things you remember about"), out
        assert MemoryStore(mem_dir=tmp).index.ntotal == 1, "did not persist"
    print("[store.__main__] OK — add / persist / retrieve")

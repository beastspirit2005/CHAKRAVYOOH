"""
Project Pukar - Semantic Incident Correlation & Deduplication Engine
====================================================================

Overview:
---------
Provides high-fidelity multilingual semantic correlation between incoming distress reports
and existing incident clusters. Powers the "1 report -> 3 -> 7 corroboration" demo beat
with true semantic understanding rather than simplistic geospatial/temporal proximity alone.

Key Architecture:
-----------------
1. Seam Ownership:
   - Harshit owns persistence, PostgreSQL pgvector storage, and spatial/temporal candidate filtering.
   - Rishabh (ML) owns vector generation (`embed`) and semantic correlation scoring (`correlate`).
2. Model:
   - Multilingual sentence transformer (`paraphrase-multilingual-MiniLM-L12-v2`).
   - Lazy-loaded once at startup (thread-safe singleton).
3. Fast Vector Math:
   - Normalized cosine similarity comparisons against candidate vector sets.
4. Thresholding:
   - Configurable via `ml/configs/correlate.yaml` (default similarity threshold: 0.72).
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Any

import yaml

from .contracts import CorrelationVerdict

try:
    import numpy as np
    HAS_ML = True
except ImportError:
    HAS_ML = False
    np = None

logger = logging.getLogger("pukar.ml.correlate")


class IncidentCorrelator:
    """
    Multilingual semantic correlation engine for incident deduplication and clustering.
    """

    _instance: IncidentCorrelator | None = None
    _lock = threading.Lock()

    def __init__(
        self,
        model_name: str | None = None,
        config_path: str | Path | None = None,
    ):
        self.config_path = self._resolve_config_path(config_path)
        self.config = self._load_config()

        self.model_name = (
            model_name
            or self.config.get("model", {}).get("name", "paraphrase-multilingual-MiniLM-L12-v2")
        )
        self.similarity_threshold = float(
            self.config.get("clustering", {}).get("similarity_threshold", 0.72)
        )
        self.high_confidence_threshold = float(
            self.config.get("clustering", {}).get("high_confidence_threshold", 0.85)
        )

        self._model = None
        self._model_lock = threading.Lock()
        self._fallback_mode = False

    def _resolve_config_path(self, custom_path: str | Path | None) -> Path:
        if custom_path:
            return Path(custom_path)

        candidates = [
            Path(__file__).parent / "configs" / "correlate.yaml",
            Path(__file__).resolve().parents[4] / "ml" / "configs" / "correlate.yaml",
            Path("ml/configs/correlate.yaml"),
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def _load_config(self) -> dict[str, Any]:
        if self.config_path and self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning(f"Failed to load correlate.yaml from {self.config_path}: {e}")
        return {}

    def _get_model(self):
        """Lazy-loads the SentenceTransformer model in a thread-safe manner."""
        if self._model is not None or self._fallback_mode:
            return self._model

        with self._model_lock:
            if self._model is not None or self._fallback_mode:
                return self._model

            try:
                from sentence_transformers import SentenceTransformer
                logger.info("Loading SentenceTransformer model: %s", self.model_name)
                self._model = SentenceTransformer(self.model_name)
                logger.info("SentenceTransformer model loaded successfully.")
            except Exception as e:
                logger.warning(
                    "SentenceTransformer could not be loaded (%s). Operating in lightweight semantic hashing fallback mode.",
                    e,
                )
                self._fallback_mode = True
                self._model = None

        return self._model

    def embed(
        self,
        text: str | list[str],
        auto_canonicalize: bool = True,
    ) -> list[float] | list[list[float]]:
        """
        Computes 384-dimensional dense semantic embedding vector(s) for given text(s).
        Returns python list of floats directly serializable into PostgreSQL `pgvector`.
        """
        is_single = isinstance(text, str)
        texts = [text] if is_single else list(text)

        if auto_canonicalize:
            try:
                from .normalize import normalize
                clean_texts = [
                    normalize(str(t)).canonical_en if (t and str(t).strip()) else "empty distress signal"
                    for t in texts
                ]
            except Exception as e:
                logger.debug(f"Auto-canonicalize skipped: {e}")
                clean_texts = [str(t).strip() if t else "empty distress signal" for t in texts]
        else:
            clean_texts = [str(t).strip() if t else "empty distress signal" for t in texts]

        model = self._get_model()
        if model is not None:
            try:
                raw_embeddings = model.encode(
                    clean_texts,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                # Convert ndarray to standard float list
                vectors = [
                    v.tolist() if hasattr(v, "tolist") else list(map(float, v))
                    for v in raw_embeddings
                ]
                return vectors[0] if is_single else vectors
            except Exception as e:
                logger.warning(f"Model encode failed ({e}), falling back to deterministic dense vector.")

        # Resilient Fallback Dense Vector (384-dim normalized lexical/hash projection)
        vectors = [self._fallback_embed(t) for t in clean_texts]
        return vectors[0] if is_single else vectors

    def _fallback_embed(self, text: str, dim: int = 384) -> list[float]:
        """
        Deterministic, normalized pseudo-semantic embedding projection used when
        PyTorch/SentenceTransformers is not available.
        """
        import hashlib
        import re

        vec = np.zeros(dim, dtype=np.float32)
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            vec[0] = 1.0
            return vec.tolist()

        for i, token in enumerate(tokens):
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx1 = h % dim
            idx2 = (h >> 16) % dim
            sign = 1.0 if (h & 1) else -1.0
            vec[idx1] += sign * (1.0 / math.sqrt(i + 1))
            vec[idx2] += -sign * 0.5

        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 1e-6:
            vec = vec / norm
        else:
            vec[0] = 1.0

        return vec.tolist()

    def correlate(
        self,
        new_embedding: list[float] | np.ndarray,
        candidates: list[dict[str, Any]],
        threshold: float | None = None,
    ) -> CorrelationVerdict:
        """
        Compares incoming report embedding against candidate existing incident embeddings.

        Args:
            new_embedding: Vector for the incoming report.
            candidates: List of candidate dicts: [{"id": "inc-101", "embedding": [...], ...}, ...].
            threshold: Optional similarity threshold override (defaults to correlate.yaml).

        Returns:
            CorrelationVerdict with match_id, similarity, is_duplicate, and cluster_hint.
        """
        if not candidates or new_embedding is None:
            return CorrelationVerdict(
                match_id=None,
                similarity=0.0,
                is_duplicate=False,
                cluster_hint=None,
            )

        sim_threshold = threshold if threshold is not None else self.similarity_threshold
        target_vec = np.array(new_embedding, dtype=np.float32)
        target_norm = np.linalg.norm(target_vec)
        if target_norm > 1e-6:
            target_vec = target_vec / target_norm

        best_sim = 0.0
        best_match_id: str | None = None
        best_candidate: dict[str, Any] | None = None

        for cand in candidates:
            cand_id = str(cand.get("id") or cand.get("incident_id") or cand.get("report_id") or "")
            cand_emb = cand.get("embedding") or cand.get("vector")
            if cand_emb is None:
                continue

            c_vec = np.array(cand_emb, dtype=np.float32)
            c_norm = np.linalg.norm(c_vec)
            if c_norm > 1e-6:
                c_vec = c_vec / c_norm

            sim = float(np.dot(target_vec, c_vec))
            sim = max(0.0, min(1.0, sim))

            if sim > best_sim:
                best_sim = sim
                best_match_id = cand_id
                best_candidate = cand

        is_duplicate = best_sim >= sim_threshold
        cluster_hint = None
        if best_candidate and is_duplicate:
            cluster_hint = best_candidate.get("cluster_hint") or best_candidate.get("category")

        return CorrelationVerdict(
            match_id=best_match_id if is_duplicate else None,
            similarity=round(best_sim, 4),
            is_duplicate=is_duplicate,
            cluster_hint=str(cluster_hint) if cluster_hint else None,
        )


# Global singleton instance
_DEFAULT_CORRELATOR = IncidentCorrelator()


def embed(
    text: str | list[str],
    auto_canonicalize: bool = True,
) -> list[float] | list[list[float]]:
    """
    Public embedding entry point exposed to Harshit for PostgreSQL pgvector storage.

    Example:
        >>> vector = embed("Building collapsed 3 trapped inside")
        >>> len(vector)
        384
    """
    return _DEFAULT_CORRELATOR.embed(text, auto_canonicalize=auto_canonicalize)


def correlate(
    new_embedding: list[float] | np.ndarray,
    candidates: list[dict[str, Any]],
    threshold: float | None = None,
) -> CorrelationVerdict:
    """
    Public semantic correlation entry point called by Harshit's correlation pipeline.

    Example:
        >>> candidates = [{"id": "inc-001", "embedding": emb1}]
        >>> verdict = correlate(new_emb, candidates)
        >>> if verdict.is_duplicate:
        ...     attach_to_incident(verdict.match_id)
    """
    return _DEFAULT_CORRELATOR.correlate(new_embedding, candidates, threshold=threshold)

"""Embedding generation — multi-backend with graceful degradation.

Supports:
- fastembed (default): local ONNX model, free, Chinese-optimized
- openai: text-embedding-3-small via OpenAI API
- none: no-op, always returns None (for testing / no-embedding mode)
"""

import asyncio
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class EmbeddingService:
    """Generate text embeddings. Lazy-loads the model on first use."""

    def __init__(self, provider: str = ""):
        self._provider = (provider or os.getenv("EMBEDDING_PROVIDER", "fastembed")).lower()
        self._model = None
        self._warned = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> Optional[list[float]]:
        """Generate embedding for a single text. Returns None on failure."""
        if self._provider == "none":
            return None

        try:
            return await self._embed_internal(text)
        except Exception as e:
            if not self._warned:
                print(f"[Embedding] {self._provider} embed failed: {e}")
                self._warned = True
            return None

    async def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Generate embeddings for multiple texts."""
        results = []
        for text in texts:
            results.append(await self.embed(text))
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _embed_internal(self, text: str) -> Optional[list[float]]:
        if self._provider == "fastembed":
            return await self._embed_fastembed(text)
        elif self._provider == "openai":
            return await self._embed_openai(text)
        else:
            print(f"[Embedding] Unknown provider '{self._provider}', falling back to none")
            self._provider = "none"
            return None

    async def _embed_fastembed(self, text: str) -> Optional[list[float]]:
        """Use fastembed with BAAI/bge-small-zh-v1.5 (512 dims, Chinese-optimized)."""
        if self._model is None:
            from fastembed import TextEmbedding

            model_name = os.getenv("FASTEMBED_MODEL", "BAAI/bge-small-zh-v1.5")
            print(f"[Embedding] Loading fastembed model: {model_name} ...")
            self._model = TextEmbedding(model_name=model_name)
            print("[Embedding] fastembed model loaded.")

        # fastembed.embed() is synchronous — run in thread
        result = await asyncio.to_thread(lambda: list(self._model.embed([text])))
        if result and len(result) > 0:
            return result[0].tolist()
        return None

    async def _embed_openai(self, text: str) -> Optional[list[float]]:
        """Use OpenAI text-embedding-3-small (1536 dims)."""
        from openai import AsyncOpenAI

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            print("[Embedding] OPENAI_API_KEY not set")
            self._provider = "none"
            return None

        if self._model is None:
            self._model = AsyncOpenAI(api_key=api_key)

        response = await self._model.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding


# Module-level singleton for the bot to use
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Return the global EmbeddingService singleton, creating it if needed."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service

"""Memory storage and retrieval backed by pgvector.

- store(): embed content → write to memory_fragment table
- search(): embed query → cosine similarity search → return top-K content
- forget_old(): prune old memories per chatid
"""

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.models.memory import MemoryFragment
from app.services.embedding_service import EmbeddingService, get_embedding_service


class MemoryService:
    """Agent memory — semantic storage + retrieval."""

    def __init__(self, embedding_service: Optional[EmbeddingService] = None):
        self._emb = embedding_service or get_embedding_service()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store(
        self,
        chatid: str,
        content: str,
        memory_type: str = "task",
        source_id: Optional[int] = None,
    ) -> Optional[int]:
        """Embed content and persist to DB. Returns memory id or None."""
        if not content.strip():
            return None

        embedding = await self._emb.embed(content)
        if embedding is None:
            print(f"[Memory] Embedding failed for chatid={chatid}, skipping store")
            return None

        db = SessionLocal()
        try:
            fragment = MemoryFragment(
                chatid=chatid,
                content=content,
                memory_type=memory_type,
                source_id=source_id,
            )
            # Set embedding via setattr for Vector column compatibility
            fragment.embedding = embedding
            db.add(fragment)
            db.commit()
            db.refresh(fragment)
            print(f"[Memory] Stored memory {fragment.id} (type={memory_type}) for chatid={chatid}")
            return fragment.id
        except Exception as e:
            db.rollback()
            print(f"[Memory] Store failed: {e}")
            return None
        finally:
            db.close()

    async def search(
        self, chatid: str, query: str, top_k: int = 3
    ) -> list[str]:
        """Retrieve the most relevant memories for a query.

        Uses pgvector cosine distance operator (<=>).
        Returns list of content strings, most relevant first.
        """
        if not query.strip():
            return []

        embedding = await self._emb.embed(query)
        if embedding is None:
            return []

        db = SessionLocal()
        try:
            # Use pgvector cosine distance: embedding <=> query_vector
            # We format the vector as a pgvector literal string
            vector_str = f"[{','.join(str(v) for v in embedding)}]"

            rows = db.execute(
                text(
                    "SELECT content FROM memory_fragment "
                    "WHERE chatid = :chatid AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> :vec "
                    "LIMIT :limit"
                ),
                {"chatid": chatid, "vec": vector_str, "limit": top_k},
            ).fetchall()

            results = [row[0] for row in rows]
            if results:
                print(f"[Memory] Found {len(results)} relevant memories for chatid={chatid}")
            return results
        except Exception as e:
            print(f"[Memory] Search failed: {e}")
            return []
        finally:
            db.close()

    async def forget_old(self, chatid: str, keep: int = 50) -> int:
        """Delete old memories, keeping the most recent `keep` per chatid.
        Returns number of deleted rows.
        """
        db = SessionLocal()
        try:
            # Find IDs to keep (most recent)
            keep_ids = [
                row[0]
                for row in db.execute(
                    text(
                        "SELECT id FROM memory_fragment "
                        "WHERE chatid = :chatid "
                        "ORDER BY created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"chatid": chatid, "limit": keep},
                ).fetchall()
            ]

            if not keep_ids:
                return 0

            result = (
                db.query(MemoryFragment)
                .filter(
                    MemoryFragment.chatid == chatid,
                    MemoryFragment.id.notin_(keep_ids),
                )
                .delete(synchronize_session="fetch")
            )
            db.commit()
            if result:
                print(f"[Memory] Pruned {result} old memories for chatid={chatid}")
            return result
        except Exception as e:
            db.rollback()
            print(f"[Memory] Forget failed: {e}")
            return 0
        finally:
            db.close()


# Module-level singleton
_memory_service: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    """Return the global MemoryService singleton."""
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service

"""Per-user conversation state — in-memory cache + DB persistence.

API is identical to the old in-memory-only version, so bot_ws_client.py
and llm_service.py need no changes.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.models.conversation import ConversationRecord


# Per-intent TTL (seconds). Longer for create_task to allow multi-turn thinking.
INTENT_TTL = {
    "create_task": 600,      # 10 minutes
    "associate_file": 300,   # 5 minutes
    "cleanup_confirm": 300,  # 5 minutes
}
DEFAULT_TTL = 300


@dataclass
class ConversationState:
    """A single user's ongoing conversation with the bot."""

    chatid: str
    intent: str = ""
    collected: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    ttl_seconds: int = 300

    @property
    def expired(self) -> bool:
        return datetime.now() - self.created_at > timedelta(seconds=self.ttl_seconds)


class ConversationManager:
    """In-memory cache + DB-backed conversation state, keyed by chatid."""

    TTL_SECONDS = 300

    def __init__(self):
        self._cache: dict[str, ConversationState] = {}

    # ------------------------------------------------------------------
    # Public API (unchanged from old version)
    # ------------------------------------------------------------------

    def get(self, chatid: str) -> Optional[ConversationState]:
        """Return conversation state, or None if expired/missing."""
        if not chatid:
            return None

        # 1. Check cache
        session = self._cache.get(chatid)
        if session is not None:
            if session.expired:
                self.delete(chatid)
                return None
            return session

        # 2. Cache miss — load from DB
        db = SessionLocal()
        try:
            record = db.query(ConversationRecord).filter(
                ConversationRecord.chatid == chatid
            ).first()

            if record is None:
                return None

            # Per-intent TTL
            intent = record.intent or ""
            ttl = INTENT_TTL.get(intent, DEFAULT_TTL)

            # Check TTL
            if record.updated_at:
                age = datetime.now() - record.updated_at
                if age.total_seconds() > ttl:
                    db.delete(record)
                    db.commit()
                    return None

            # Deserialize
            try:
                ctx = json.loads(record.context_json)
            except (json.JSONDecodeError, TypeError):
                ctx = {}

            session = ConversationState(
                chatid=chatid,
                intent=intent,
                collected=ctx.get("collected", {}),
                messages=ctx.get("messages", []),
                created_at=record.created_at or datetime.now(),
                ttl_seconds=ttl,
            )
            self._cache[chatid] = session
            return session
        finally:
            db.close()

    def create(self, chatid: str, **kwargs) -> ConversationState:
        """Create (or replace) a conversation state."""
        intent = kwargs.get("intent", "")
        kwargs.setdefault("ttl_seconds", INTENT_TTL.get(intent, DEFAULT_TTL))
        session = ConversationState(chatid=chatid, **kwargs)
        self._cache[chatid] = session
        self._persist(session)
        return session

    def update(self, chatid: str, **kwargs):
        """Update fields and refresh TTL."""
        session = self.get(chatid)
        if session is None:
            return
        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.created_at = datetime.now()
        self._persist(session)

    def delete(self, chatid: str):
        """Remove from cache and DB."""
        self._cache.pop(chatid, None)

        db = SessionLocal()
        try:
            record = db.query(ConversationRecord).filter(
                ConversationRecord.chatid == chatid
            ).first()
            if record:
                db.delete(record)
                db.commit()
        finally:
            db.close()

    def cleanup_expired(self):
        """Remove expired sessions from cache and DB."""
        # Cache
        expired = [cid for cid, s in self._cache.items() if s.expired]
        for cid in expired:
            del self._cache[cid]

        # DB — use max TTL as cutoff; precise check happens in get()
        max_ttl = max(INTENT_TTL.values()) if INTENT_TTL else DEFAULT_TTL
        db = SessionLocal()
        try:
            cutoff = datetime.now() - timedelta(seconds=max_ttl)
            db.query(ConversationRecord).filter(
                ConversationRecord.updated_at < cutoff
            ).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self, session: ConversationState):
        """Upsert session to DB."""
        db = SessionLocal()
        try:
            record = db.query(ConversationRecord).filter(
                ConversationRecord.chatid == session.chatid
            ).first()

            if record is None:
                record = ConversationRecord(chatid=session.chatid)
                db.add(record)

            record.intent = session.intent
            record.context_json = json.dumps({
                "collected": session.collected,
                "messages": session.messages,
            }, ensure_ascii=False)
            record.updated_at = datetime.now()

            db.commit()
        finally:
            db.close()

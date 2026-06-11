import asyncio
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.api.task import router as task_router

load_dotenv()

BOT_ID = os.getenv("WECOM_BOT_ID", "")
BOT_SECRET = os.getenv("WECOM_BOT_SECRET", "")



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background tasks on app startup, clean up on shutdown."""
    bot_task = None
    scheduler_task = None

    if BOT_ID and BOT_SECRET:
        # Preload embedding model before bot connects (avoids 2-5s delay on first message)
        from app.services.embedding_service import get_embedding_service
        emb = get_embedding_service()
        await emb.preload()

        from app.services.bot_ws_client import run_bot
        bot_task = asyncio.create_task(run_bot(BOT_ID, BOT_SECRET))
        print("[Main] Bot WebSocket client started")

        # Start reminder scheduler
        from app.services.scheduler import reminder_loop
        scheduler_task = asyncio.create_task(reminder_loop())
        print("[Main] Scheduler started")

    yield

    # Shutdown in reverse order: scheduler first, then bot
    if scheduler_task:
        scheduler_task.cancel()
        print("[Main] Scheduler stopped")
    if bot_task:
        bot_task.cancel()
        print("[Main] Bot WebSocket client stopped")


app = FastAPI(lifespan=lifespan)

app.include_router(task_router)


@app.get("/")
def root():
    return {"message": "DDL-Killer Running"}

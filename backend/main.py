import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import news, chat
from services import db

REFRESH_INTERVAL = 600  # 10 minutes


async def _auto_refresh_loop():
    """Background task: refresh news every 10 minutes."""
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        try:
            await news.do_refresh()
            print(f"[Hayden 2] Auto-refresh complete — {len(news._news_store)} items")
        except Exception as e:
            print(f"[Hayden 2] Auto-refresh failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init DB
    db.init_db()

    # Load persisted news instantly, then refresh in background
    news.load_from_db()
    if len(news._news_store) > 0:
        print(f"[Hayden 2] Serving {len(news._news_store)} cached items while refreshing...")
        asyncio.create_task(news.do_refresh())
    else:
        print("[Hayden 2] No cached news — fetching fresh...")
        try:
            await news.do_refresh()
            print(f"[Hayden 2] Loaded {len(news._news_store)} news items")
        except Exception as e:
            print(f"[Hayden 2] Initial fetch failed (will retry on /refresh): {e}")

    # Start background auto-refresh
    refresh_task = asyncio.create_task(_auto_refresh_loop())
    print(f"[Hayden 2] Auto-refresh every {REFRESH_INTERVAL // 60} minutes")

    yield

    # Shutdown
    refresh_task.cancel()
    print("[Hayden 2] Shutting down")


app = FastAPI(
    title="Hayden 2",
    description="AI-powered stock research platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3357", "http://127.0.0.1:3357"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(news.router)
app.include_router(chat.router)


@app.get("/")
async def root():
    return {"status": "ok", "app": "hayden2"}

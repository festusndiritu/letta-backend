from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.core.cleanup import start_scheduler, stop_scheduler
from app.core.rate_limit import limiter
from app.auth.router import router as auth_router
from app.messaging.router import router as messaging_router
from app.messaging.anxiety import router as anxiety_router
from app.messaging.sessions import router as sessions_router
from app.messaging.reactions import router as reactions_router
from app.messaging.search import router as search_router
from app.messaging.preview import router as preview_router
from app.messaging.delete import router as delete_router
from app.messaging.pins import router as pins_router
from app.messaging.polls import router as polls_router
from app.contacts.router import router as contacts_router
from app.groups.router import router as groups_router
from app.media.router import router as media_router
from app.dashboard.router import router as dashboard_router
from app.statuses.router import router as statuses_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Letta",
    description="An anxiety-free messaging backend.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(messaging_router, tags=["messaging"])
app.include_router(anxiety_router, tags=["anxiety controls"])
app.include_router(sessions_router, tags=["sessions"])
app.include_router(reactions_router, tags=["reactions"])
app.include_router(search_router, tags=["search & discovery"])
app.include_router(preview_router, tags=["link preview"])
app.include_router(delete_router, tags=["messaging"])
app.include_router(pins_router, tags=["messaging"])
app.include_router(polls_router, tags=["messaging"])
app.include_router(contacts_router, tags=["contacts"])
app.include_router(groups_router, tags=["conversations"])
app.include_router(media_router, tags=["media"])
app.include_router(dashboard_router, tags=["dashboard"])
app.include_router(statuses_router, tags=["statuses"])

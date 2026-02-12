from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.security import SecurityHeadersMiddleware
from app.routers import agents, entries, feed, questions, bounties, streams

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Engram",
    description="AI Agent Journal Platform",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)

# Request size limit
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    MAX_SIZE = 1_000_000  # 1MB

    async def dispatch(self, request: Request, call_next):
        if request.headers.get("content-length"):
            if int(request.headers["content-length"]) > self.MAX_SIZE:
                return Response("Request too large", status_code=413)
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)

# API routers
app.include_router(agents.router)
app.include_router(entries.router)
app.include_router(questions.router)
app.include_router(bounties.router)
app.include_router(streams.router)

# Web routers
app.include_router(feed.router)
app.include_router(agents.web)
app.include_router(entries.web)
app.include_router(questions.web)
app.include_router(bounties.web)
app.include_router(streams.web)

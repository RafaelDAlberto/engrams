from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
import traceback
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.config import get_settings
from app.database import init_db
from app.security import SecurityHeadersMiddleware
from app.routers import agents, entries, feed, questions, bounties, streams

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from app.database import async_session
    from app.seed import seed
    async with async_session() as db:
        await seed(db)
    yield


app = FastAPI(
    title="Engram",
    description="AI Agent Journal Platform",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url.path}: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})

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


# SEO routes
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from app.database import get_db
from app.models import Agent, Entry


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /
Disallow: /api/
Disallow: /register

Sitemap: https://engrams.net/sitemap.xml
"""


@app.get("/sitemap.xml", response_class=Response)
async def sitemap(db=Depends(get_db)):
    from app.database import async_session
    async with async_session() as db:
        agents_r = await db.execute(select(Agent))
        all_agents = agents_r.scalars().all()
        entries_r = await db.execute(select(Entry).order_by(Entry.created_at.desc()).limit(500))
        all_entries = entries_r.scalars().all()

    urls = ['<url><loc>https://engrams.net/</loc><priority>1.0</priority></url>']
    urls.append('<url><loc>https://engrams.net/feed</loc><priority>0.9</priority></url>')
    urls.append('<url><loc>https://engrams.net/streams</loc><priority>0.8</priority></url>')
    urls.append('<url><loc>https://engrams.net/bounties</loc><priority>0.8</priority></url>')
    urls.append('<url><loc>https://engrams.net/questions</loc><priority>0.8</priority></url>')

    for a in all_agents:
        urls.append(f'<url><loc>https://engrams.net/agent/{a.name}</loc><priority>0.7</priority></url>')
    for e in all_entries:
        urls.append(f'<url><loc>https://engrams.net/entry/{e.id}</loc><priority>0.6</priority></url>')

    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{"".join(urls)}</urlset>'
    return Response(content=xml, media_type="application/xml")



import secrets
import bcrypt
from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from app.database import get_db
from app.models import Agent
from app.config import get_settings

# --- API Key ---

def generate_api_key() -> str:
    return f"eng_{secrets.token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    return bcrypt.hashpw(key.encode(), bcrypt.gensalt()).decode()


def verify_api_key(key: str, hashed: str) -> bool:
    return bcrypt.checkpw(key.encode(), hashed.encode())


async def get_current_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Agent:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    key = auth[7:]
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    for agent in agents:
        if verify_api_key(key, agent.api_key_hash):
            return agent
    raise HTTPException(status_code=401, detail="Invalid API key")


# --- Rate Limiting ---

_rate_store: dict[str, list[float]] = {}

RATE_LIMIT = 10
RATE_WINDOW = 60  # seconds


def check_rate_limit(agent_id: str):
    import time
    now = time.time()
    timestamps = _rate_store.get(agent_id, [])
    timestamps = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    timestamps.append(now)
    _rate_store[agent_id] = timestamps


# --- CSP Middleware ---

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' https://fonts.gstatic.com"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# --- Markdown ---

import markdown
import bleach

ALLOWED_TAGS = [
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr",
    "strong", "em", "code", "pre", "blockquote", "ul", "ol", "li",
    "a", "img", "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRS = {"a": ["href", "title"], "img": ["src", "alt", "title"]}


def render_markdown(text: str) -> str:
    html = markdown.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)

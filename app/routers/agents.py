from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.database import get_db
from app.models import Agent, Entry, Follow
from app.schemas import AgentCreate, AgentResponse, AgentCreateResponse
from app.security import generate_api_key, hash_api_key

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("", response_model=AgentCreateResponse, status_code=201)
async def register_agent(data: AgentCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Agent).where(Agent.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Agent name already taken")

    api_key = generate_api_key()
    agent = Agent(
        name=data.name,
        description=data.description,
        api_key_hash=hash_api_key(api_key),
    )
    db.add(agent)
    await db.flush()
    return AgentCreateResponse(id=agent.id, name=agent.name, api_key=api_key)


@router.get("", response_model=list[AgentResponse])
async def list_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
    return result.scalars().all()


# --- Web views ---
from fastapi import APIRouter as _
from fastapi.templating import Jinja2Templates
import pathlib

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent.parent / "templates"))

web = APIRouter(tags=["web-agents"])


@web.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@web.post("/register")
async def register_submit(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    name = str(form.get("name", "")).strip().lower()
    description = str(form.get("description", "")).strip()

    if not name or len(name) > 100:
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "Name is required (max 100 chars)."
        })

    # Only allow lowercase, numbers, hyphens
    import re
    if not re.match(r'^[a-z0-9][a-z0-9\-]{0,98}[a-z0-9]$', name) and len(name) > 1:
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "Name must be lowercase letters, numbers, and hyphens only."
        })

    existing = await db.execute(select(Agent).where(Agent.name == name))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse("register.html", {
            "request": request, "error": f"Agent name '{name}' is already taken."
        })

    api_key = generate_api_key()
    agent = Agent(
        name=name,
        description=description,
        api_key_hash=hash_api_key(api_key),
    )
    db.add(agent)
    await db.flush()

    return templates.TemplateResponse("register.html", {
        "request": request, "api_key": api_key, "agent_name": name
    })


@web.get("/agent/{agent_name}")
async def agent_profile(request: Request, agent_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")

    entries_q = await db.execute(
        select(Entry).where(Entry.agent_id == agent.id)
        .options(selectinload(Entry.agent), selectinload(Entry.tags))
        .order_by(Entry.created_at.desc()).limit(50)
    )
    entries = entries_q.scalars().all()

    # Check follow status
    visitor_id = request.cookies.get("engram_visitor")
    is_following = False
    if visitor_id:
        fq = await db.execute(
            select(Follow).where(Follow.visitor_id == visitor_id, Follow.agent_id == agent.id)
        )
        is_following = fq.scalar_one_or_none() is not None

    follower_count = await db.scalar(
        select(func.count()).select_from(Follow).where(Follow.agent_id == agent.id)
    )

    return templates.TemplateResponse("agent.html", {
        "request": request,
        "agent": agent,
        "entries": entries,
        "is_following": is_following,
        "follower_count": follower_count or 0,
    })


@web.post("/agent/{agent_name}/follow")
async def toggle_follow(request: Request, response: Response, agent_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")

    visitor_id = request.cookies.get("engram_visitor")
    if not visitor_id:
        visitor_id = str(uuid.uuid4())

    fq = await db.execute(
        select(Follow).where(Follow.visitor_id == visitor_id, Follow.agent_id == agent.id)
    )
    existing = fq.scalar_one_or_none()

    if existing:
        await db.delete(existing)
    else:
        db.add(Follow(visitor_id=visitor_id, agent_id=agent.id))

    await db.flush()

    from starlette.responses import RedirectResponse
    resp = RedirectResponse(f"/agent/{agent_name}", status_code=303)
    resp.set_cookie(
        "engram_visitor", visitor_id,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return resp

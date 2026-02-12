from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import Response as RawResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from feedgen.feed import FeedGenerator
import pathlib

from app.database import get_db
from app.models import Entry, Agent, Tag, entry_tags, Question, question_tags
from app.security import render_markdown
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent.parent / "templates"))

router = APIRouter(tags=["feed"])


async def _popular_tags(db: AsyncSession, limit: int = 30):
    """Return list of (tag_name, count) ordered by usage."""
    result = await db.execute(
        select(Tag.name, func.count(entry_tags.c.entry_id).label("cnt"))
        .join(entry_tags, Tag.id == entry_tags.c.tag_id)
        .group_by(Tag.name)
        .order_by(func.count(entry_tags.c.entry_id).desc())
        .limit(limit)
    )
    return result.all()


@router.get("/")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Entry).order_by(Entry.created_at.desc()).limit(20))
    entries = result.scalars().all()
    agents_q = await db.execute(select(Agent).order_by(Agent.created_at.desc()).limit(20))
    agents = agents_q.scalars().all()
    popular = await _popular_tags(db)
    return templates.TemplateResponse("home.html", {
        "request": request,
        "entries": entries,
        "agents": agents,
        "popular_tags": popular,
    })


@router.get("/feed")
async def public_feed(
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: str = Query(None),
    tag: str = Query(None),
    q: str = Query(None),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    query = select(Entry)

    if agent:
        agent_q = await db.execute(select(Agent).where(Agent.name == agent))
        agent_obj = agent_q.scalar_one_or_none()
        if agent_obj:
            query = query.where(Entry.agent_id == agent_obj.id)

    if tag:
        query = query.where(
            Entry.id.in_(
                select(entry_tags.c.entry_id)
                .join(Tag, Tag.id == entry_tags.c.tag_id)
                .where(Tag.name == tag.lower())
            )
        )

    if q:
        query = query.where(Entry.title.ilike(f"%{q}%"))

    query = query.order_by(Entry.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    return templates.TemplateResponse("feed.html", {
        "request": request,
        "entries": entries,
        "agent_filter": agent or "",
        "tag_filter": tag or "",
        "search_query": q or "",
        "page": page,
    })


@router.get("/tag/{tag_name}")
async def tag_page(
    request: Request,
    tag_name: str,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    tag_name_lower = tag_name.lower()

    tag_q = await db.execute(select(Tag).where(Tag.name == tag_name_lower))
    tag_obj = tag_q.scalar_one_or_none()
    if not tag_obj:
        return templates.TemplateResponse("tag.html", {
            "request": request,
            "tag_name": tag_name_lower,
            "entries": [],
            "questions": [],
            "entry_count": 0,
            "question_count": 0,
            "page": page,
        })

    entry_count = await db.scalar(
        select(func.count()).select_from(entry_tags).where(entry_tags.c.tag_id == tag_obj.id)
    )

    result = await db.execute(
        select(Entry)
        .where(Entry.id.in_(
            select(entry_tags.c.entry_id).where(entry_tags.c.tag_id == tag_obj.id)
        ))
        .order_by(Entry.created_at.desc())
        .offset(offset).limit(limit)
    )
    entries = result.scalars().all()

    question_count = await db.scalar(
        select(func.count()).select_from(question_tags).where(question_tags.c.tag_id == tag_obj.id)
    )

    q_result = await db.execute(
        select(Question)
        .where(Question.id.in_(
            select(question_tags.c.question_id).where(question_tags.c.tag_id == tag_obj.id)
        ))
        .order_by(Question.created_at.desc())
        .limit(20)
    )
    questions = q_result.scalars().all()

    return templates.TemplateResponse("tag.html", {
        "request": request,
        "tag_name": tag_name_lower,
        "entries": entries,
        "questions": questions,
        "entry_count": entry_count or 0,
        "question_count": question_count or 0,
        "page": page,
    })


@router.get("/vulns")
async def vulns_feed(
    request: Request,
    db: AsyncSession = Depends(get_db),
    severity: str = Query(None),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit

    # Security entries = entries tagged with security-related tags OR having a severity
    security_tags = {"security", "vulnerability", "cve", "exploit", "audit", "pentest"}
    tag_q = await db.execute(select(Tag.id).where(Tag.name.in_(security_tags)))
    sec_tag_ids = [r[0] for r in tag_q.all()]

    q = select(Entry).where(
        (Entry.severity.isnot(None)) |
        (Entry.id.in_(
            select(entry_tags.c.entry_id).where(entry_tags.c.tag_id.in_(sec_tag_ids))
        )) if sec_tag_ids else (Entry.severity.isnot(None))
    )

    if severity:
        q = q.where(Entry.severity == severity.lower())

    q = q.order_by(Entry.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(q)
    entries = result.scalars().all()

    return templates.TemplateResponse("vulns.html", {
        "request": request,
        "entries": entries,
        "severity_filter": severity or "",
        "page": page,
    })


@router.get("/agent/{agent_name}/rss")
async def agent_rss(agent_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.name == agent_name))
    agent = result.scalar_one_or_none()
    if not agent:
        return RawResponse(status_code=404, content="Agent not found")

    entries_q = await db.execute(
        select(Entry).where(Entry.agent_id == agent.id).order_by(Entry.created_at.desc()).limit(50)
    )
    entries = entries_q.scalars().all()

    fg = FeedGenerator()
    fg.id(f"engram-agent-{agent.id}")
    fg.title(f"Engram — {agent.name}")
    fg.description(agent.description or f"Journal entries by {agent.name}")
    fg.link(href=f"/agent/{agent.name}", rel="alternate")
    fg.language("en")

    for entry in entries:
        fe = fg.add_entry()
        fe.id(entry.id)
        fe.title(entry.title)
        fe.content(render_markdown(entry.content), type="html")
        fe.published(entry.created_at)
        fe.link(href=f"/entry/{entry.id}")

    rss_xml = fg.rss_str(pretty=True)
    return RawResponse(content=rss_xml, media_type="application/rss+xml")

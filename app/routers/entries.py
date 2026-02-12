from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import pathlib

from app.database import get_db
from app.models import Entry, Tag, entry_tags, Agent
from app.schemas import EntryCreate, EntryResponse
from app.security import get_current_agent, check_rate_limit, render_markdown
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/api/entries", tags=["entries"])
templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent.parent / "templates"))


def entry_to_response(entry: Entry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "agent_id": entry.agent_id,
        "agent_name": entry.agent.name if entry.agent else "",
        "tags": [t.name for t in entry.tags],
        "severity": entry.severity,
        "repo_url": entry.repo_url,
        "evidence": entry.evidence,
        "evidence_type": entry.evidence_type,
        "parent_id": entry.parent_id,
        "created_at": entry.created_at,
    }


@router.post("", response_model=EntryResponse, status_code=201)
async def create_entry(
    data: EntryCreate,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    check_rate_limit(agent.id)

    # Validate parent_id if provided
    if data.parent_id:
        result = await db.execute(select(Entry).where(Entry.id == data.parent_id))
        if not result.scalar_one_or_none():
            raise HTTPException(404, "Parent entry not found")

    tag_objects = []
    for tag_name in data.tags[:20]:
        tag_name = tag_name.strip().lower()[:50]
        if not tag_name:
            continue
        result = await db.execute(select(Tag).where(Tag.name == tag_name))
        tag = result.scalar_one_or_none()
        if not tag:
            tag = Tag(name=tag_name)
            db.add(tag)
            await db.flush()
        tag_objects.append(tag)

    entry = Entry(
        title=data.title,
        content=data.content,
        severity=data.severity.lower() if data.severity else None,
        repo_url=data.repo_url,
        evidence=data.evidence,
        evidence_type=data.evidence_type,
        parent_id=data.parent_id,
        agent_id=agent.id,
    )
    entry.tags = tag_objects
    db.add(entry)
    await db.flush()
    await db.refresh(entry)

    return entry_to_response(entry)


@router.get("/{entry_id}", response_model=EntryResponse)
async def get_entry(entry_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Entry not found")
    return entry_to_response(entry)


# Web view
web = APIRouter(tags=["web-entries"])


def _build_chain(entry: Entry) -> list:
    """Walk up the parent chain to build breadcrumbs."""
    chain = []
    current = entry
    visited = set()
    while current and current.parent and current.parent_id not in visited:
        visited.add(current.parent_id)
        current = current.parent
        chain.insert(0, current)
    chain.append(entry)
    return chain


@web.get("/entry/{entry_id}")
async def view_entry(request: Request, entry_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Entry not found")

    rendered_content = render_markdown(entry.content)
    rendered_evidence = render_markdown(entry.evidence) if entry.evidence else None
    chain = _build_chain(entry)

    return templates.TemplateResponse("entry.html", {
        "request": request,
        "entry": entry,
        "rendered_content": rendered_content,
        "rendered_evidence": rendered_evidence,
        "chain": chain,
    })

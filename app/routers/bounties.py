from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse
import pathlib
import time

from app.database import get_db
from app.models import Bounty, BountySubmission, Tag, bounty_tags, Agent
from app.schemas import BountyCreate, BountyResponse, BountySubmissionCreate, BountySubmissionResponse
from app.security import get_current_agent, render_markdown
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent.parent / "templates"))

router = APIRouter(tags=["bounties"])

# Rate limiting for bounty creation: 2 per hour per IP
_bounty_rate_store: dict[str, list[float]] = {}
BOUNTY_RATE_LIMIT = 2
BOUNTY_RATE_WINDOW = 3600  # 1 hour


def check_bounty_rate_limit(ip: str):
    now = time.time()
    timestamps = _bounty_rate_store.get(ip, [])
    timestamps = [t for t in timestamps if now - t < BOUNTY_RATE_WINDOW]
    if len(timestamps) >= BOUNTY_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded: max 2 bounties per hour")
    timestamps.append(now)
    _bounty_rate_store[ip] = timestamps


async def _get_or_create_tags(db: AsyncSession, tag_names: list[str]) -> list[Tag]:
    tag_objects = []
    for tag_name in tag_names[:20]:
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
    return tag_objects


# --- API Routes ---

@router.post("/api/bounties", response_model=BountyResponse, status_code=201)
async def create_bounty_api(
    data: BountyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    check_bounty_rate_limit(client_ip)

    tag_objects = await _get_or_create_tags(db, data.tags)
    bounty = Bounty(
        title=data.title,
        description=data.description,
        reward_amount=data.reward_amount,
        author_name=data.author_name,
        author_email=data.author_email,
        deadline=data.deadline,
    )
    bounty.tags = tag_objects
    db.add(bounty)
    await db.flush()
    await db.refresh(bounty)

    return BountyResponse(
        id=bounty.id,
        title=bounty.title,
        description=bounty.description,
        reward_amount=bounty.reward_amount,
        author_name=bounty.author_name,
        status=bounty.status,
        tags=[t.name for t in bounty.tags],
        created_at=bounty.created_at,
        deadline=bounty.deadline,
        submission_count=0,
    )


@router.get("/api/bounties", response_model=list[BountyResponse])
async def list_bounties_api(
    db: AsyncSession = Depends(get_db),
    status: str = Query(None),
    tag: str = Query(None),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    query = select(Bounty)

    if status:
        query = query.where(Bounty.status == status)
    if tag:
        query = query.where(
            Bounty.id.in_(
                select(bounty_tags.c.bounty_id)
                .join(Tag, Tag.id == bounty_tags.c.tag_id)
                .where(Tag.name == tag.lower())
            )
        )

    query = query.order_by(Bounty.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    bounties = result.scalars().all()

    responses = []
    for b in bounties:
        sub_count = await db.scalar(
            select(func.count()).select_from(BountySubmission).where(BountySubmission.bounty_id == b.id)
        )
        responses.append(BountyResponse(
            id=b.id, title=b.title, description=b.description,
            reward_amount=b.reward_amount, author_name=b.author_name,
            status=b.status, tags=[t.name for t in b.tags],
            created_at=b.created_at, deadline=b.deadline,
            submission_count=sub_count or 0,
        ))
    return responses


@router.get("/api/bounties/{bounty_id}", response_model=BountyResponse)
async def get_bounty_api(bounty_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Bounty).where(Bounty.id == bounty_id))
    bounty = result.scalar_one_or_none()
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    sub_count = await db.scalar(
        select(func.count()).select_from(BountySubmission).where(BountySubmission.bounty_id == bounty.id)
    )
    return BountyResponse(
        id=bounty.id, title=bounty.title, description=bounty.description,
        reward_amount=bounty.reward_amount, author_name=bounty.author_name,
        status=bounty.status, tags=[t.name for t in bounty.tags],
        created_at=bounty.created_at, deadline=bounty.deadline,
        submission_count=sub_count or 0,
    )


@router.post("/api/bounties/{bounty_id}/submit", response_model=BountySubmissionResponse, status_code=201)
async def submit_to_bounty(
    bounty_id: str,
    data: BountySubmissionCreate,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Bounty).where(Bounty.id == bounty_id))
    bounty = result.scalar_one_or_none()
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    if bounty.status not in ("open", "in_progress"):
        raise HTTPException(400, "Bounty is not accepting submissions")

    submission = BountySubmission(
        bounty_id=bounty_id,
        agent_id=agent.id,
        content=data.content,
        evidence=data.evidence,
    )
    db.add(submission)

    if bounty.status == "open":
        bounty.status = "in_progress"

    await db.flush()
    await db.refresh(submission)

    return BountySubmissionResponse(
        id=submission.id, bounty_id=submission.bounty_id,
        agent_id=submission.agent_id, agent_name=agent.name,
        content=submission.content, evidence=submission.evidence,
        is_winner=submission.is_winner, created_at=submission.created_at,
    )


# --- Web Routes ---

web = APIRouter(tags=["web-bounties"])


@web.get("/bounties")
async def bounties_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = Query(None),
    tag: str = Query(None),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    query = select(Bounty)

    if status:
        query = query.where(Bounty.status == status)
    if tag:
        query = query.where(
            Bounty.id.in_(
                select(bounty_tags.c.bounty_id)
                .join(Tag, Tag.id == bounty_tags.c.tag_id)
                .where(Tag.name == tag.lower())
            )
        )

    query = query.order_by(Bounty.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    bounties = result.scalars().all()

    bounty_data = []
    for b in bounties:
        sc = await db.scalar(
            select(func.count()).select_from(BountySubmission).where(BountySubmission.bounty_id == b.id)
        )
        bounty_data.append({"bounty": b, "submission_count": sc or 0})

    return templates.TemplateResponse("bounties.html", {
        "request": request,
        "bounty_data": bounty_data,
        "status_filter": status or "",
        "tag_filter": tag or "",
        "page": page,
    })


@web.get("/bounties/create")
async def bounty_create_page(request: Request):
    return templates.TemplateResponse("bounty_create.html", {"request": request})


@web.post("/bounties/create")
async def bounty_create_submit(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    title = str(form.get("title", "")).strip()
    description = str(form.get("description", "")).strip()
    reward_str = str(form.get("reward_amount", "")).strip()
    author_name = str(form.get("author_name", "")).strip()
    author_email = str(form.get("author_email", "")).strip() or None
    tags_raw = str(form.get("tags", "")).strip()
    deadline_str = str(form.get("deadline", "")).strip()

    if not title or not description or not author_name or not reward_str:
        return templates.TemplateResponse("bounty_create.html", {
            "request": request,
            "error": "Title, description, reward amount, and your name are required.",
        })

    try:
        reward_amount = float(reward_str)
        if reward_amount <= 0:
            raise ValueError()
    except ValueError:
        return templates.TemplateResponse("bounty_create.html", {
            "request": request,
            "error": "Reward amount must be a positive number.",
        })

    client_ip = request.client.host if request.client else "unknown"
    try:
        check_bounty_rate_limit(client_ip)
    except HTTPException:
        return templates.TemplateResponse("bounty_create.html", {
            "request": request,
            "error": "Rate limit exceeded. Max 2 bounties per hour.",
        })

    deadline = None
    if deadline_str:
        from datetime import datetime, timezone
        try:
            deadline = datetime.fromisoformat(deadline_str).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    tag_names = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
    tag_objects = await _get_or_create_tags(db, tag_names)

    bounty = Bounty(
        title=title[:300],
        description=description[:50000],
        reward_amount=reward_amount,
        author_name=author_name[:100],
        author_email=author_email[:200] if author_email else None,
        deadline=deadline,
    )
    bounty.tags = tag_objects
    db.add(bounty)
    await db.flush()

    return RedirectResponse(f"/bounty/{bounty.id}", status_code=303)


@web.get("/bounty/{bounty_id}")
async def view_bounty(request: Request, bounty_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Bounty).where(Bounty.id == bounty_id))
    bounty = result.scalar_one_or_none()
    if not bounty:
        raise HTTPException(404, "Bounty not found")

    rendered_description = render_markdown(bounty.description)

    submissions_q = await db.execute(
        select(BountySubmission).where(BountySubmission.bounty_id == bounty_id)
        .order_by(BountySubmission.created_at.asc())
    )
    submissions = submissions_q.scalars().all()

    rendered_submissions = []
    for s in submissions:
        rendered_submissions.append({
            "submission": s,
            "rendered_content": render_markdown(s.content),
            "rendered_evidence": render_markdown(s.evidence) if s.evidence else None,
        })

    return templates.TemplateResponse("bounty.html", {
        "request": request,
        "bounty": bounty,
        "rendered_description": rendered_description,
        "rendered_submissions": rendered_submissions,
    })

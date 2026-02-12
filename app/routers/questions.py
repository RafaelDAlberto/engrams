from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse
import pathlib
import uuid

from app.database import get_db
from app.models import Question, Answer, AnswerUpvote, Tag, question_tags, Agent
from app.schemas import QuestionCreate, QuestionResponse, AnswerCreate, AnswerResponse
from app.security import get_current_agent, render_markdown
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent.parent / "templates"))

router = APIRouter(prefix="/api/questions", tags=["questions"])


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


@router.post("", response_model=QuestionResponse, status_code=201)
async def create_question(data: QuestionCreate, db: AsyncSession = Depends(get_db)):
    tag_objects = await _get_or_create_tags(db, data.tags)
    question = Question(
        title=data.title,
        content=data.content,
        author_name=data.author_name,
    )
    question.tags = tag_objects
    db.add(question)
    await db.flush()
    await db.refresh(question)
    return QuestionResponse(
        id=question.id,
        title=question.title,
        content=question.content,
        author_name=question.author_name,
        status=question.status,
        tags=[t.name for t in question.tags],
        created_at=question.created_at,
        answer_count=0,
    )


@router.get("", response_model=list[QuestionResponse])
async def list_questions(
    db: AsyncSession = Depends(get_db),
    tag: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    query = select(Question)

    if status:
        query = query.where(Question.status == status)

    if tag:
        query = query.where(
            Question.id.in_(
                select(question_tags.c.question_id)
                .join(Tag, Tag.id == question_tags.c.tag_id)
                .where(Tag.name == tag.lower())
            )
        )

    query = query.order_by(Question.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    questions = result.scalars().all()

    responses = []
    for q in questions:
        answer_count = await db.scalar(
            select(func.count()).select_from(Answer).where(Answer.question_id == q.id)
        )
        responses.append(QuestionResponse(
            id=q.id,
            title=q.title,
            content=q.content,
            author_name=q.author_name,
            status=q.status,
            tags=[t.name for t in q.tags],
            created_at=q.created_at,
            answer_count=answer_count or 0,
        ))
    return responses


@router.post("/{question_id}/answers", response_model=AnswerResponse, status_code=201)
async def create_answer(
    question_id: str,
    data: AnswerCreate,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    q_result = await db.execute(select(Question).where(Question.id == question_id))
    question = q_result.scalar_one_or_none()
    if not question:
        raise HTTPException(404, "Question not found")

    answer = Answer(
        question_id=question_id,
        agent_id=agent.id,
        content=data.content,
    )
    db.add(answer)

    # Auto-update status to answered
    if question.status == "open":
        question.status = "answered"

    await db.flush()
    await db.refresh(answer)

    return AnswerResponse(
        id=answer.id,
        question_id=answer.question_id,
        agent_id=answer.agent_id,
        agent_name=agent.name,
        content=answer.content,
        upvotes=answer.upvotes,
        created_at=answer.created_at,
    )


@router.post("/{question_id}/upvote/{answer_id}")
async def upvote_answer(
    request: Request,
    question_id: str,
    answer_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Answer).where(Answer.id == answer_id, Answer.question_id == question_id)
    )
    answer = result.scalar_one_or_none()
    if not answer:
        raise HTTPException(404, "Answer not found")

    visitor_id = request.cookies.get("engram_visitor")
    if not visitor_id:
        visitor_id = str(uuid.uuid4())

    # Check if already upvoted
    existing = await db.execute(
        select(AnswerUpvote).where(
            AnswerUpvote.visitor_id == visitor_id,
            AnswerUpvote.answer_id == answer_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "already_upvoted", "upvotes": answer.upvotes}

    db.add(AnswerUpvote(visitor_id=visitor_id, answer_id=answer_id))
    answer.upvotes += 1
    await db.flush()

    return {"status": "upvoted", "upvotes": answer.upvotes}


# --- Web views ---

web = APIRouter(tags=["web-questions"])


@web.get("/questions")
async def questions_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tag: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
):
    limit = 20
    offset = (page - 1) * limit
    query = select(Question)

    if status:
        query = query.where(Question.status == status)
    if tag:
        query = query.where(
            Question.id.in_(
                select(question_tags.c.question_id)
                .join(Tag, Tag.id == question_tags.c.tag_id)
                .where(Tag.name == tag.lower())
            )
        )

    query = query.order_by(Question.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    questions = result.scalars().all()

    # Get answer counts
    question_data = []
    for q in questions:
        ac = await db.scalar(
            select(func.count()).select_from(Answer).where(Answer.question_id == q.id)
        )
        question_data.append({"question": q, "answer_count": ac or 0})

    return templates.TemplateResponse("questions.html", {
        "request": request,
        "question_data": question_data,
        "tag_filter": tag or "",
        "status_filter": status or "",
        "page": page,
    })


@web.get("/questions/ask")
async def ask_page(request: Request):
    return templates.TemplateResponse("ask.html", {"request": request})


@web.post("/questions/ask")
async def ask_submit(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", "")).strip()
    author_name = str(form.get("author_name", "")).strip()
    tags_raw = str(form.get("tags", "")).strip()

    if not title or not content or not author_name:
        return templates.TemplateResponse("ask.html", {
            "request": request,
            "error": "Title, content, and your name are required.",
        })

    tag_names = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
    tag_objects = await _get_or_create_tags(db, tag_names)

    question = Question(
        title=title[:300],
        content=content[:50000],
        author_name=author_name[:100],
    )
    question.tags = tag_objects
    db.add(question)
    await db.flush()

    return RedirectResponse(f"/question/{question.id}", status_code=303)


@web.get("/question/{question_id}")
async def view_question(request: Request, question_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(404, "Question not found")

    answers_q = await db.execute(
        select(Answer).where(Answer.question_id == question_id).order_by(Answer.upvotes.desc(), Answer.created_at.asc())
    )
    answers = answers_q.scalars().all()

    rendered_content = render_markdown(question.content)
    rendered_answers = []
    for a in answers:
        rendered_answers.append({
            "answer": a,
            "rendered_content": render_markdown(a.content),
        })

    # Check which answers this visitor already upvoted
    visitor_id = request.cookies.get("engram_visitor")
    upvoted_ids = set()
    if visitor_id:
        uv_result = await db.execute(
            select(AnswerUpvote.answer_id).where(AnswerUpvote.visitor_id == visitor_id)
        )
        upvoted_ids = {r[0] for r in uv_result.all()}

    return templates.TemplateResponse("question.html", {
        "request": request,
        "question": question,
        "rendered_content": rendered_content,
        "rendered_answers": rendered_answers,
        "upvoted_ids": upvoted_ids,
    })


@web.post("/question/{question_id}/upvote/{answer_id}")
async def web_upvote_answer(
    request: Request,
    question_id: str,
    answer_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Answer).where(Answer.id == answer_id, Answer.question_id == question_id)
    )
    answer = result.scalar_one_or_none()
    if not answer:
        raise HTTPException(404, "Answer not found")

    visitor_id = request.cookies.get("engram_visitor")
    if not visitor_id:
        visitor_id = str(uuid.uuid4())

    existing = await db.execute(
        select(AnswerUpvote).where(
            AnswerUpvote.visitor_id == visitor_id,
            AnswerUpvote.answer_id == answer_id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(AnswerUpvote(visitor_id=visitor_id, answer_id=answer_id))
        answer.upvotes += 1
        await db.flush()

    resp = RedirectResponse(f"/question/{question_id}", status_code=303)
    resp.set_cookie(
        "engram_visitor", visitor_id,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return resp

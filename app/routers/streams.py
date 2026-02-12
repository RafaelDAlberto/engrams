from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
import pathlib
import asyncio
import json

from app.database import get_db, async_session
from app.models import Stream, StreamChunk, Agent
from app.schemas import StreamCreate, StreamResponse, StreamChunkCreate, StreamChunkResponse
from app.security import get_current_agent, check_rate_limit
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent.parent / "templates"))

router = APIRouter(tags=["streams"])


# --- API Routes ---

@router.post("/api/streams", response_model=StreamResponse, status_code=201)
async def create_stream(
    data: StreamCreate,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    check_rate_limit(agent.id)
    stream = Stream(
        agent_id=agent.id,
        title=data.title,
    )
    db.add(stream)
    await db.flush()
    await db.refresh(stream)

    return StreamResponse(
        id=stream.id, agent_id=stream.agent_id, agent_name=agent.name,
        title=stream.title, status=stream.status,
        started_at=stream.started_at, ended_at=stream.ended_at,
        chunk_count=0,
    )


@router.post("/api/streams/{stream_id}/chunk", response_model=StreamChunkResponse, status_code=201)
async def post_chunk(
    stream_id: str,
    data: StreamChunkCreate,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    if stream.agent_id != agent.id:
        raise HTTPException(403, "Not your stream")
    if stream.status != "live":
        raise HTTPException(400, "Stream has ended")

    chunk = StreamChunk(
        stream_id=stream_id,
        content=data.content,
        chunk_type=data.chunk_type,
    )
    db.add(chunk)
    await db.flush()
    await db.refresh(chunk)

    return StreamChunkResponse(
        id=chunk.id, stream_id=chunk.stream_id,
        content=chunk.content, chunk_type=chunk.chunk_type,
        created_at=chunk.created_at,
    )


@router.post("/api/streams/{stream_id}/end", response_model=StreamResponse)
async def end_stream(
    stream_id: str,
    agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    if stream.agent_id != agent.id:
        raise HTTPException(403, "Not your stream")
    if stream.status != "live":
        raise HTTPException(400, "Stream already ended")

    from app.models import utcnow
    stream.status = "ended"
    stream.ended_at = utcnow()
    await db.flush()

    chunk_count = len(stream.chunks) if stream.chunks else 0

    return StreamResponse(
        id=stream.id, agent_id=stream.agent_id, agent_name=agent.name,
        title=stream.title, status=stream.status,
        started_at=stream.started_at, ended_at=stream.ended_at,
        chunk_count=chunk_count,
    )


@router.get("/api/streams/{stream_id}/live")
async def stream_sse(stream_id: str):
    """SSE endpoint — polls DB for new chunks and streams them to the client."""

    async def event_generator():
        last_chunk_count = 0
        while True:
            async with async_session() as db:
                result = await db.execute(select(Stream).where(Stream.id == stream_id))
                stream = result.scalar_one_or_none()
                if not stream:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Stream not found'})}\n\n"
                    return

                chunks_q = await db.execute(
                    select(StreamChunk).where(StreamChunk.stream_id == stream_id)
                    .order_by(StreamChunk.created_at.asc())
                )
                chunks = chunks_q.scalars().all()

                # Send any new chunks
                if len(chunks) > last_chunk_count:
                    for chunk in chunks[last_chunk_count:]:
                        data = {
                            "type": "chunk",
                            "id": chunk.id,
                            "content": chunk.content,
                            "chunk_type": chunk.chunk_type,
                            "created_at": chunk.created_at.isoformat(),
                        }
                        yield f"data: {json.dumps(data)}\n\n"
                    last_chunk_count = len(chunks)

                if stream.status == "ended":
                    yield f"data: {json.dumps({'type': 'ended'})}\n\n"
                    return

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Web Routes ---

web = APIRouter(tags=["web-streams"])


@web.get("/streams")
async def streams_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
):
    # Live streams first
    live_q = await db.execute(
        select(Stream).where(Stream.status == "live").order_by(Stream.started_at.desc())
    )
    live_streams = live_q.scalars().all()

    # Recent ended streams
    limit = 20
    offset = (page - 1) * limit
    ended_q = await db.execute(
        select(Stream).where(Stream.status == "ended")
        .order_by(Stream.ended_at.desc()).offset(offset).limit(limit)
    )
    ended_streams = ended_q.scalars().all()

    return templates.TemplateResponse("streams.html", {
        "request": request,
        "live_streams": live_streams,
        "ended_streams": ended_streams,
        "page": page,
    })


@web.get("/stream/{stream_id}")
async def view_stream(request: Request, stream_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    chunks_q = await db.execute(
        select(StreamChunk).where(StreamChunk.stream_id == stream_id)
        .order_by(StreamChunk.created_at.asc())
    )
    chunks = chunks_q.scalars().all()

    return templates.TemplateResponse("stream.html", {
        "request": request,
        "stream": stream,
        "chunks": chunks,
    })

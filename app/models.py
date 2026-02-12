import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey, Table, Column, Integer, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return str(uuid.uuid4())


entry_tags = Table(
    "entry_tags",
    Base.metadata,
    Column("entry_id", String(36), ForeignKey("entries.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)

question_tags = Table(
    "question_tags",
    Base.metadata,
    Column("question_id", String(36), ForeignKey("questions.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)

bounty_tags = Table(
    "bounty_tags",
    Base.metadata,
    Column("bounty_id", String(36), ForeignKey("bounties.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    api_key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entries: Mapped[list["Entry"]] = relationship(back_populates="agent", lazy="selectin")
    answers: Mapped[list["Answer"]] = relationship(back_populates="agent", lazy="selectin")
    bounty_submissions: Mapped[list["BountySubmission"]] = relationship(back_populates="agent", lazy="selectin")
    streams: Mapped[list["Stream"]] = relationship(back_populates="agent", lazy="selectin")


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("entries.id"), nullable=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent: Mapped["Agent"] = relationship(back_populates="entries", lazy="selectin")
    tags: Mapped[list["Tag"]] = relationship(secondary=entry_tags, lazy="selectin")
    parent: Mapped["Entry | None"] = relationship(
        back_populates="children", remote_side="Entry.id", lazy="selectin"
    )
    children: Mapped[list["Entry"]] = relationship(back_populates="parent", lazy="selectin")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)


class Follow(Base):
    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tags: Mapped[list["Tag"]] = relationship(secondary=question_tags, lazy="selectin")
    answers: Mapped[list["Answer"]] = relationship(back_populates="question", lazy="selectin")


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    upvotes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped["Question"] = relationship(back_populates="answers", lazy="selectin")
    agent: Mapped["Agent"] = relationship(back_populates="answers", lazy="selectin")


class AnswerUpvote(Base):
    __tablename__ = "answer_upvotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    answer_id: Mapped[str] = mapped_column(String(36), ForeignKey("answers.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Bounties ---

class Bounty(Base):
    __tablename__ = "bounties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reward_amount: Mapped[float] = mapped_column(Float, nullable=False)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    author_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    winner_submission_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bounty_submissions.id", use_alter=True), nullable=True)

    tags: Mapped[list["Tag"]] = relationship(secondary=bounty_tags, lazy="selectin")
    submissions: Mapped[list["BountySubmission"]] = relationship(
        back_populates="bounty", lazy="selectin", foreign_keys="BountySubmission.bounty_id"
    )


class BountySubmission(Base):
    __tablename__ = "bounty_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    bounty_id: Mapped[str] = mapped_column(String(36), ForeignKey("bounties.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)

    bounty: Mapped["Bounty"] = relationship(back_populates="submissions", lazy="selectin", foreign_keys=[bounty_id])
    agent: Mapped["Agent"] = relationship(back_populates="bounty_submissions", lazy="selectin")


# --- Streams ---

class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="live", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="streams", lazy="selectin")
    chunks: Mapped[list["StreamChunk"]] = relationship(back_populates="stream", lazy="selectin", order_by="StreamChunk.created_at")


class StreamChunk(Base):
    __tablename__ = "stream_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    stream_id: Mapped[str] = mapped_column(String(36), ForeignKey("streams.id"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(20), default="thought")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stream: Mapped["Stream"] = relationship(back_populates="chunks", lazy="selectin")

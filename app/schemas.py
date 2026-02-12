from pydantic import BaseModel, Field
from datetime import datetime


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=1000)


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    model_config = {"from_attributes": True}


class AgentCreateResponse(BaseModel):
    id: str
    name: str
    api_key: str  # Only returned once at creation


VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}
VALID_EVIDENCE_TYPES = {"code_output", "scan_results", "data", "logs", "chart"}


class EntryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1, max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    severity: str | None = Field(None, max_length=20)
    repo_url: str | None = Field(None, max_length=500)
    evidence: str | None = Field(None, max_length=50000)
    evidence_type: str | None = Field(None, max_length=50)
    parent_id: str | None = Field(None, max_length=36)

    def model_post_init(self, __context):
        if self.severity is not None and self.severity not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of: {', '.join(VALID_SEVERITIES)}")
        if self.evidence_type is not None and self.evidence_type not in VALID_EVIDENCE_TYPES:
            raise ValueError(f"evidence_type must be one of: {', '.join(VALID_EVIDENCE_TYPES)}")


class EntryResponse(BaseModel):
    id: str
    title: str
    content: str
    agent_id: str
    agent_name: str = ""
    tags: list[str] = []
    severity: str | None = None
    repo_url: str | None = None
    evidence: str | None = None
    evidence_type: str | None = None
    parent_id: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Questions ---

class QuestionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1, max_length=50000)
    author_name: str = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)


class QuestionResponse(BaseModel):
    id: str
    title: str
    content: str
    author_name: str
    status: str
    tags: list[str] = []
    created_at: datetime
    answer_count: int = 0
    model_config = {"from_attributes": True}


class AnswerCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)


class AnswerResponse(BaseModel):
    id: str
    question_id: str
    agent_id: str
    agent_name: str = ""
    content: str
    upvotes: int = 0
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Bounties ---

class BountyCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=50000)
    reward_amount: float = Field(..., gt=0)
    author_name: str = Field(..., min_length=1, max_length=100)
    author_email: str | None = Field(None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)
    deadline: datetime | None = None


class BountyResponse(BaseModel):
    id: str
    title: str
    description: str
    reward_amount: float
    author_name: str
    status: str
    tags: list[str] = []
    created_at: datetime
    deadline: datetime | None = None
    submission_count: int = 0
    model_config = {"from_attributes": True}


class BountySubmissionCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)
    evidence: str | None = Field(None, max_length=50000)


class BountySubmissionResponse(BaseModel):
    id: str
    bounty_id: str
    agent_id: str
    agent_name: str = ""
    content: str
    evidence: str | None = None
    is_winner: bool = False
    created_at: datetime
    model_config = {"from_attributes": True}


# --- Streams ---

class StreamCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)


class StreamResponse(BaseModel):
    id: str
    agent_id: str
    agent_name: str = ""
    title: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    chunk_count: int = 0
    model_config = {"from_attributes": True}


class StreamChunkCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    chunk_type: str = Field("thought", max_length=20)

    def model_post_init(self, __context):
        valid = {"thought", "action", "result", "error"}
        if self.chunk_type not in valid:
            raise ValueError(f"chunk_type must be one of: {', '.join(valid)}")


class StreamChunkResponse(BaseModel):
    id: str
    stream_id: str
    content: str
    chunk_type: str
    created_at: datetime
    model_config = {"from_attributes": True}

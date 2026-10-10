from __future__ import annotations

from pydantic import BaseModel, Field

from app.knowledge.documents import CATEGORIES


class KnowledgeQuery(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    categories: list[str] | None = Field(None, description=f"Any of {CATEGORIES}")
    week: int | None = Field(None, ge=1, le=42)
    language: str | None = None
    generate_answer: bool = True
    top_k: int = Field(4, ge=1, le=10)


class KnowledgeHit(BaseModel):
    id: str
    category: str
    title: str
    title_hi: str | None = None
    text: str
    text_hi: str | None = None
    source: str | None = None
    relevance: str  # high | medium | low — no raw scores to clients


class KnowledgeAnswer(BaseModel):
    question: str
    answer: str | None
    generated_by: str
    results: list[KnowledgeHit]
    retrieval_backend: str

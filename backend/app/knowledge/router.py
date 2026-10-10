from __future__ import annotations

from fastapi import APIRouter, Query

from app.ai.language import detect_language
from app.ai.llm import ChatMessage, get_llm
from app.ai.prompts import KNOWLEDGE_SYSTEM, format_context
from app.ai.safety import apply, check_message
from app.api.deps import DB, OptionalPrincipal
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.core.pregnancy import gestational_week
from app.database.models import Mother
from app.knowledge.documents import CATEGORIES
from app.knowledge.schemas import KnowledgeAnswer, KnowledgeHit, KnowledgeQuery
from app.knowledge.service import get_kb, week_guide

guide_router = APIRouter(prefix="/guide", tags=["Pregnancy Guide"])
router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])


def _relevance(score: float) -> str:
    return "high" if score >= 0.3 else "medium" if score >= 0.15 else "low"


def _mother_week(db, p) -> tuple[int | None, int]:
    if p and p.role == Role.MOTHER and p.user:
        m = db.query(Mother).filter(Mother.user_id == p.user.id).first()
        if m and m.pregnancy:
            return gestational_week(m.pregnancy.lmp), m.pregnancy.anc_visits_completed
    return None, 0


@guide_router.get("/week")
def guide_week(db: DB, p: OptionalPrincipal, week: int | None = Query(None, ge=1, le=42)):
    """Week-wise guide (works for guests). Defaults to the mother's current week."""
    own_week, visits = _mother_week(db, p)
    w = week or own_week
    if w is None:
        raise ValidationFailed("Pass ?week= or add your LMP to your passport", code="week_required")
    return week_guide(w, visits if w == own_week else 0)


@guide_router.get("/topics")
def guide_topics(category: str = Query(..., description=f"One of {CATEGORIES}")):
    if category not in CATEGORIES:
        raise ValidationFailed("Unknown category", details={"categories": CATEGORIES})
    return {"category": category, "items": [
        {k: d[k] for k in ("id", "title", "title_hi", "text", "text_hi", "source")} for d in get_kb().by_category(category)
    ]}


@router.post("/query", response_model=KnowledgeAnswer)
def query(body: KnowledgeQuery, db: DB, p: OptionalPrincipal):
    if body.categories and any(c not in CATEGORIES for c in body.categories):
        raise ValidationFailed("Unknown category", details={"categories": CATEGORIES})
    own_week, _ = _mother_week(db, p)
    kb = get_kb()
    hits = kb.search(body.question, k=body.top_k, categories=body.categories, week=body.week or own_week)
    lang = body.language or detect_language(body.question)
    answer, generated_by = None, "retrieval"
    if body.generate_answer and hits:
        llm = get_llm("knowledge")
        raw = llm.generate([
            ChatMessage("system", KNOWLEDGE_SYSTEM.format(language="Hindi" if lang == "hi" else "English")),
            ChatMessage("user", f"Context:\n{format_context([h.doc for h in hits])}\n\nQuestion: {body.question}"),
        ]) if llm.available else None
        if raw:
            generated_by = "gemma+retrieval"
        else:
            top = hits[0].doc
            raw = top["text_hi"] if lang == "hi" and top.get("text_hi") else top["text"]
        answer = apply(raw, check_message(body.question), lang)
    return KnowledgeAnswer(
        question=body.question, answer=answer, generated_by=generated_by,
        retrieval_backend=kb.store.backend,
        results=[KnowledgeHit(**{k: h.doc.get(k) for k in ("id", "category", "title", "title_hi", "text", "text_hi", "source")},
                              relevance=_relevance(h.score)) for h in hits],
    )

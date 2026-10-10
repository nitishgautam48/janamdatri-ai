"""Knowledge base: FAISS retrieval over curated maternal-health documents."""
from __future__ import annotations

import threading

from app.ai.embeddings import get_embedder
from app.ai.vector_store import Hit, VectorStore
from app.core.config import settings
from app.core.pregnancy import TRIMESTER_LABELS, next_anc, trimester
from app.knowledge.documents import DOCUMENTS
from app.knowledge.weeks import focus_for_week, size_for_week


class KnowledgeBase:
    def __init__(self) -> None:
        self._store: VectorStore | None = None
        self._lock = threading.Lock()

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            with self._lock:
                if self._store is None:
                    store = VectorStore(get_embedder())
                    docs = [{**d, "search_text": f"{d['title']}. {d['title']}. {d['text']} {d['title_hi']} {d['title_hi']} {d['text_hi']}"}
                            for d in DOCUMENTS]
                    store.build(docs, text_key="search_text")
                    self._store = store
        return self._store

    def add(self, docs: list[dict]) -> None:
        """Add documents (e.g. from a state programme) and rebuild the index."""
        DOCUMENTS.extend(docs)
        self._store = None

    def search(self, query: str, *, k: int | None = None, categories: list[str] | None = None,
               week: int | None = None, min_score: float = 0.05) -> list[Hit]:
        where = {"category": categories} if categories else None
        hits = self.store.search(query, k=(k or settings.knowledge_top_k) * 2, where=where)
        out = []
        for h in hits:
            d = h.doc
            # Week-specific docs only count when they match the mother's week (if known).
            if week is not None and d.get("week_from") is not None and d["category"] == "pregnancy_guide":
                if not (d["week_from"] <= week <= d["week_to"]):
                    continue
            if h.score >= min_score:
                out.append(h)
        return out[: k or settings.knowledge_top_k]

    def by_category(self, category: str) -> list[dict]:
        return [d for d in DOCUMENTS if d["category"] == category]

    def get(self, doc_id: str) -> dict | None:
        return next((d for d in DOCUMENTS if d["id"] == doc_id), None)


_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb


def source_ref(doc: dict) -> dict:
    return {"id": doc["id"], "title": doc["title"], "category": doc["category"], "source": doc.get("source")}


def week_guide(week: int, anc_visits_completed: int = 0) -> dict:
    week = max(4, min(40, week))
    size, size_hi, length, weight, milestone, milestone_hi = size_for_week(week)
    tri = trimester(week)
    kb = get_kb()
    pick = lambda doc_id: kb.get(doc_id)  # noqa: E731
    nutrition = pick("nu-ifa" if week >= 14 else "nu-diet")
    exercise = pick("ex-walk")
    anc = next_anc(week, anc_visits_completed)
    schemes = [pick("sc-pmmvy"), pick("sc-jsy"), pick("sc-jssk"), pick("sc-pmsma")]
    danger = kb.by_category("danger_signs")
    if week < 28:
        danger = [d for d in danger if d["id"] != "ds-movement"]
    return {
        "week": week,
        "weeks_to_go": max(40 - week, 0),
        "trimester": tri,
        "trimester_label": TRIMESTER_LABELS[tri][0],
        "trimester_label_hi": TRIMESTER_LABELS[tri][1],
        "baby": {"size": size, "size_hi": size_hi, "length_cm": length, "weight_g": weight},
        "milestone": milestone,
        "milestone_hi": milestone_hi,
        "focus": [{"kind": f["kind"], "text": f["en"], "text_hi": f["hi"]} for f in focus_for_week(week)]
                 + [{"kind": "MILESTONE", "text": milestone, "text_hi": milestone_hi}],
        "sections": {
            "nutrition": _section(nutrition),
            "exercise": _section(exercise),
            "anc_schedule": {
                "title": anc["label"] if anc else "Delivery and postnatal care",
                "text": f"Due between weeks {anc['from_week']}–{anc['to_week']}. Checks: {'; '.join(anc['checks'])}."
                if anc else "Plan your delivery at a facility. After birth, ASHA home visits on days 3, 7, 14, 21, 28 and 42.",
            },
            "danger_signs": [_section(d) for d in danger],
            "schemes": [_section(s) for s in schemes if s],
        },
    }


def _section(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {"id": doc["id"], "title": doc["title"], "title_hi": doc["title_hi"], "text": doc["text"],
            "text_hi": doc["text_hi"]}

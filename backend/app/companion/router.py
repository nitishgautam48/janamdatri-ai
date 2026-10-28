from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentPrincipal, resolve_mother
from app.companion import dayplan, service
from app.core.exceptions import NotFound, ValidationFailed
from app.core.ratelimit import limit

router = APIRouter(prefix="/companion", tags=["Pregnancy Companion"])


class PlanIn(BaseModel):
    date: dt.date | None = None
    regenerate: bool = True
    mother_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    done: bool


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    language: str | None = Field(None, description="hi | en; detected when omitted")
    input_mode: str = Field("text", pattern="^(text|voice)$")


class TaskIn(BaseModel):
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    title: str = Field(min_length=1, max_length=80)


class TaskEdit(BaseModel):
    time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    title: str | None = Field(None, min_length=1, max_length=80)
    done: bool | None = None


class HydrationIn(BaseModel):
    glasses: int = Field(1, ge=-3, le=5, description="Glasses to add (negative to undo)")


def _today() -> date:
    return date.today()


@router.post("/plan")
def plan(body: PlanIn, db: DB, p: CurrentPrincipal):
    """'Plan My Day': (re)build the plan from passport, reports, assessments and week."""
    mother = resolve_mother(db, p, body.mother_id)
    day = body.date or _today()
    if abs((day - _today()).days) > 7:
        raise ValidationFailed("Plans can be made up to 7 days ahead or back")
    state = dayplan._state(mother, day)["today"]
    if body.regenerate and state["stage"] == "planned" and state["collected"]:  # keep the day she described
        plan = dayplan.make_plan(db, mother, day, state["collected"])
        db.commit()
        return service.plan_out(db, mother, plan)
    return service.plan_out(db, mother, service.get_or_create(db, mother, day, regenerate=body.regenerate))


@router.get("/today")
def today(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    mother = resolve_mother(db, p, mother_id)
    return service.plan_out(db, mother, service.get_or_create(db, mother, _today()))


@router.patch("/today/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, db: DB, p: CurrentPrincipal):
    mother = resolve_mother(db, p, None)
    plan = service.get_or_create(db, mother, _today())
    tasks = [dict(t) for t in plan.tasks]
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        raise NotFound("Task not found")
    task["done"] = body.done
    if task_id == "goal-hydration" and body.done:
        plan.hydration_count = max(plan.hydration_count, plan.hydration_goal)
    plan.tasks = tasks
    db.commit()
    return service.plan_out(db, mother, plan)


@router.post("/hydration")
def hydration(body: HydrationIn, db: DB, p: CurrentPrincipal):
    mother = resolve_mother(db, p, None)
    plan = service.get_or_create(db, mother, _today())
    plan.hydration_count = max(0, min(plan.hydration_count + body.glasses, 20))
    tasks = [dict(t) for t in plan.tasks]
    for t in tasks:
        if t["id"] == "goal-hydration":
            t["done"] = plan.hydration_count >= plan.hydration_goal
    plan.tasks = tasks
    db.commit()
    return service.plan_out(db, mother, plan)


@router.get("/week")
def week(db: DB, p: CurrentPrincipal, mother_id: uuid.UUID | None = Query(None)):
    """This week: daily goal completion, streak, weekly goal (days fully completed) and insights."""
    mother = resolve_mother(db, p, mother_id)
    today_plan = service.get_or_create(db, mother, _today())
    run, days = service.streak(db, mother, _today())
    return {
        "week_start": _today() - timedelta(days=_today().weekday()),
        "days": days, "streak_days": run,
        "weekly_goal": {"completed_days": sum(1 for d in days if d["complete"]), "target_days": 7},
        "pregnancy_week": today_plan.pregnancy_week,
        "weekly_insights": today_plan.weekly_insights,
        "anc_preparation": today_plan.anc_preparation,
    }


@router.get("/chat")
def chat_state(db: DB, p: CurrentPrincipal, language: str | None = Query(None)):
    """The 'Plan My Day' conversation for today: greeting, messages so far, quick replies and today's plan."""
    mother = resolve_mother(db, p, None)
    out = dayplan.chat_state(db, mother, _today(), language)
    out["plan"] = service.plan_out(db, mother, service.get_or_create(db, mother, _today()))
    return out


@router.post("/chat", dependencies=[Depends(limit("companion-chat", 200, 3600))])
def chat(body: ChatIn, db: DB, p: CurrentPrincipal):
    """Tell the companion how today should look; it asks back, builds the plan around her day and edits it on request."""
    mother = resolve_mother(db, p, None)
    return dayplan.chat(db, mother, _today(), body.message, body.language, body.input_mode)


def _edit(db, mother, change) -> dict:
    plan = service.get_or_create(db, mother, _today())
    tasks = [dict(t) for t in plan.tasks]
    change(tasks)
    tasks.sort(key=lambda t: (t["time"] is None, t["time"] or ""))
    plan.tasks = tasks
    db.commit()
    return service.plan_out(db, mother, plan)


@router.post("/today/tasks", status_code=201)
def add_task(body: TaskIn, db: DB, p: CurrentPrincipal):
    """Add her own item to today's plan."""
    mother = resolve_mother(db, p, None)
    return _edit(db, mother, lambda tasks: tasks.append(
        {"id": f"my-{uuid.uuid4().hex[:6]}", "time": body.time, "title": body.title.strip(), "title_hi": body.title.strip(),
         "category": "personal", "goal": False, "detail": None, "detail_hi": None, "done": False}))


@router.put("/today/tasks/{task_id}")
def edit_task(task_id: str, body: TaskEdit, db: DB, p: CurrentPrincipal):
    """Change an item's time or name (or tick it)."""
    mother = resolve_mother(db, p, None)

    def change(tasks):
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            raise NotFound("Task not found")
        if body.time:
            task["time"] = body.time
        if body.title and task["category"] in ("personal", "routine", "wellbeing", "rest", "activity"):
            task["title"] = task["title_hi"] = body.title.strip()
        if body.done is not None:
            task["done"] = body.done
    return _edit(db, mother, change)


@router.delete("/today/tasks/{task_id}")
def delete_task(task_id: str, db: DB, p: CurrentPrincipal):
    """Remove an item from today's plan. Tablets stay (their time can be changed)."""
    mother = resolve_mother(db, p, None)

    def change(tasks):
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            raise NotFound("Task not found")
        if task["category"] == "medication":
            raise ValidationFailed("Tablets stay in the plan; you can change their time. Ask your doctor before stopping any.",
                                   code="medication_required")
        tasks.remove(task)
    return _edit(db, mother, change)

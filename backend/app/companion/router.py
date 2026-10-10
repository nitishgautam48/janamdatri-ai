from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentPrincipal, resolve_mother
from app.companion import service
from app.core.exceptions import NotFound, ValidationFailed

router = APIRouter(prefix="/companion", tags=["Pregnancy Companion"])


class PlanIn(BaseModel):
    date: dt.date | None = None
    regenerate: bool = True
    mother_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    done: bool


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

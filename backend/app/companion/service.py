"""Pregnancy Companion: personalised daily plan built from the passport, reports, assessments and week."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import ChatMessage, get_llm
from app.ai.prompts import COMPANION_SYSTEM
from app.ai.safety import scrub
from app.assistant.context import mother_profile
from app.core.enums import CareLevel
from app.core.pregnancy import anc_target_date, gestational_week, next_anc
from app.database.models import AssessmentResult, CompanionPlan, Mother
from app.knowledge.weeks import focus_for_week, size_for_week
from app.reports.service import medication_schedule

BREAKFASTS = [("Poha with peanuts", "मूंगफली वाला पोहा"), ("Besan chilla with curd", "दही के साथ बेसन चीला"),
              ("Vegetable daliya", "सब्ज़ी वाला दलिया"), ("Ragi porridge with gud", "गुड़ वाला रागी दलिया"),
              ("Stuffed methi paratha with curd", "दही के साथ मेथी पराठा"), ("Sprouted moong chaat", "अंकुरित मूंग चाट"),
              ("Idli with sambar", "इडली सांभर")]
DIABETIC_BREAKFASTS = [("Besan chilla with curd", "दही के साथ बेसन चीला"), ("Vegetable daliya (no sugar)", "बिना चीनी सब्ज़ी दलिया"),
                       ("Sprouted moong chaat", "अंकुरित मूंग चाट"), ("Ragi roti with sabzi", "रागी रोटी सब्ज़ी")]
LUNCHES = [("Roti, dal, palak sabzi and curd", "रोटी, दाल, पालक की सब्ज़ी और दही"),
           ("Rice, rajma, salad and curd", "चावल, राजमा, सलाद और दही"),
           ("Roti, chana, lauki sabzi and buttermilk", "रोटी, चना, लौकी की सब्ज़ी और छाछ"),
           ("Bajra roti, sarson saag and dal", "बाजरे की रोटी, सरसों का साग और दाल")]
DINNERS = [("Khichdi with vegetables and curd", "सब्ज़ियों वाली खिचड़ी और दही"), ("Roti, paneer or egg curry and salad", "रोटी, पनीर या अंडा करी और सलाद"),
           ("Roti, moong dal and methi sabzi", "रोटी, मूंग दाल और मेथी की सब्ज़ी")]
SNACKS = [("Guava or orange", "अमरूद या संतरा"), ("Roasted chana with gud", "भुने चने और गुड़"), ("Banana and a glass of milk", "केला और एक गिलास दूध"),
          ("Til-gud laddoo", "तिल-गुड़ का लड्डू"), ("Handful of peanuts and dates", "मूंगफली और खजूर")]
DIABETIC_SNACKS = [("Guava", "अमरूद"), ("Roasted chana", "भुने चने"), ("Buttermilk", "छाछ"), ("Cucumber and peanuts", "खीरा और मूंगफली")]

GOAL_ORDER = ["goal-iron", "goal-hydration", "goal-nutrition", "goal-walk", "goal-sleep"]
ANC_BRING = [("MCP card", "MCP कार्ड"), ("Previous reports", "पुरानी रिपोर्ट"), ("Aadhaar and bank details (for JSY/PMMVY)", "आधार और बैंक विवरण (JSY/PMMVY)"),
             ("List of medicines you take", "दवाइयों की सूची"), ("Your questions for the doctor", "डॉक्टर से पूछने के सवाल")]


def _pick(options: list, day: date, salt: int = 0):
    return options[(day.toordinal() + salt) % len(options)]


def _task(tid, time, title, title_hi, category, goal=False, detail=None, detail_hi=None):
    return {"id": tid, "time": time, "title": title, "title_hi": title_hi, "category": category, "goal": goal,
            "detail": detail, "detail_hi": detail_hi, "done": False}


def build_plan(db: Session, mother: Mother, day: date) -> dict:
    profile = mother_profile(db, mother)
    preg = mother.pregnancy
    week = gestational_week(preg.lmp, day) if preg else None
    conditions = set(profile.get("conditions") or [])
    latest = profile.get("latest") or {}
    hb_low = (latest.get("hemoglobin") or {}).get("flag") in ("low", "critical") or "anemia" in conditions
    sugar_high = any((latest.get(c) or {}).get("flag") in ("high", "critical")
                     for c in ("blood_sugar_fasting", "blood_sugar_ogtt", "blood_sugar_random")) or "diabetes" in conditions
    bp_high = (latest.get("bp") or {}).get("flag") in ("high", "critical") or "hypertension" in conditions
    res = db.scalar(select(AssessmentResult).where(AssessmentResult.mother_id == mother.id)
                    .order_by(AssessmentResult.created_at.desc()))
    level = CareLevel(res.level) if res else CareLevel.ROUTINE_MONITORING
    meds = medication_schedule(db, mother.id)
    med_codes = {m["code"] for m in meds}

    breakfast = _pick(DIABETIC_BREAKFASTS if sugar_high else BREAKFASTS, day)
    lunch, dinner = _pick(LUNCHES, day, 1), _pick(DINNERS, day, 2)
    snack = _pick(DIABETIC_SNACKS if sugar_high else SNACKS, day, 3)
    if hb_low and not sugar_high:
        snack = ("Roasted chana with gud", "भुने चने और गुड़")
    meals = [
        {"time": "08:00", "meal": "Breakfast", "meal_hi": "नाश्ता", "food": breakfast[0], "food_hi": breakfast[1]},
        {"time": "11:00", "meal": "Snack", "meal_hi": "नाश्ता", "food": snack[0], "food_hi": snack[1]},
        {"time": "13:00", "meal": "Lunch", "meal_hi": "दोपहर का खाना", "food": lunch[0], "food_hi": lunch[1]},
        {"time": "16:30", "meal": "Snack", "meal_hi": "शाम का नाश्ता", "food": _pick(SNACKS if not sugar_high else DIABETIC_SNACKS, day, 4)[0],
         "food_hi": _pick(SNACKS if not sugar_high else DIABETIC_SNACKS, day, 4)[1]},
        {"time": "19:30", "meal": "Dinner", "meal_hi": "रात का खाना", "food": dinner[0], "food_hi": dinner[1]},
    ]

    medications = []
    if week is not None and week >= 14 or "ifa" in med_codes:
        medications.append({"code": "ifa", "time": "13:30", "name": "Iron + folic acid tablet", "name_hi": "आयरन + फ़ोलिक एसिड की गोली",
                            "instructions": "1 tablet after lunch, with lemon water, not tea", "instructions_hi": "खाने के बाद 1 गोली, नींबू पानी से, चाय से नहीं"})
        medications.append({"code": "calcium", "time": "21:00", "name": "Calcium tablets", "name_hi": "कैल्शियम की गोली",
                            "instructions": "2 tablets at night, not with iron", "instructions_hi": "रात को 2 गोली, आयरन के साथ नहीं"})
    elif week is not None:
        medications.append({"code": "folic_acid", "time": "09:00", "name": "Folic acid tablet", "name_hi": "फ़ोलिक एसिड की गोली",
                            "instructions": "1 tablet daily", "instructions_hi": "रोज़ 1 गोली"})
    known = {m["code"] for m in medications}
    times = {"levothyroxine": "06:30", "metformin": "13:00", "insulin": "07:45", "labetalol": "09:00", "nifedipine": "09:00",
             "methyldopa": "09:00", "aspirin": "21:00", "vitamin_d": "09:00", "progesterone": "21:00"}
    for m in meds:
        if m["code"] in known or m["code"] in ("calcium", "folic_acid") and "ifa" in known:
            continue
        medications.append({"code": m["code"], "time": times.get(m["code"], "09:00"), "name": m["name"], "name_hi": m["name"],
                            "instructions": m["instructions"] or "As your doctor prescribed",
                            "instructions_hi": "डॉक्टर के बताए अनुसार"})

    hydration_goal = 10 if day.month in (4, 5, 6, 7) else 8
    rest_needed = level >= CareLevel.MEDICAL_REVIEW
    walk_title = ("Rest and a gentle 10 min stroll if comfortable" if rest_needed else
                  "15 min walk after lunch and dinner" if sugar_high else "20 min evening walk")
    walk_hi = ("आराम, ठीक लगे तो 10 मिनट धीरे टहलें" if rest_needed else
               "दोपहर और रात के खाने के बाद 15 मिनट टहलें" if sugar_high else "शाम को 20 मिनट टहलना")
    iron_med = next((m for m in medications if m["code"] in ("ifa", "folic_acid")), None)

    tasks = [
        _task("goal-iron", iron_med["time"] if iron_med else "13:30", "Iron Tablet" if iron_med and iron_med["code"] == "ifa" else "Folic acid tablet",
              "आयरन की गोली" if iron_med and iron_med["code"] == "ifa" else "फ़ोलिक एसिड की गोली", "medication", True,
              iron_med["instructions"] if iron_med else None, iron_med["instructions_hi"] if iron_med else None),
        _task("goal-hydration", None, "Hydration Goal", "पानी", "hydration", True,
              f"{hydration_goal} glasses of water", f"{hydration_goal} गिलास पानी"),
        _task("goal-nutrition", None, "Nutrition Goal", "पोषण", "nutrition", True,
              "dal, palak, gud" if hb_low else "dal, green sabzi, fruit, curd", "दाल, पालक, गुड़" if hb_low else "दाल, हरी सब्ज़ी, फल, दही"),
        _task("goal-walk", "17:00", "Walk Goal", "टहलना", "activity", True, walk_title, walk_hi),
        _task("goal-sleep", "21:30", "Sleep Goal", "नींद", "sleep", True, "8 hours, left side", "8 घंटे, बाईं करवट"),
    ]
    for i, meal in enumerate(meals):
        tasks.append(_task(f"meal-{i}", meal["time"], f"{meal['meal']}: {meal['food']}", f"{meal['meal_hi']}: {meal['food_hi']}", "meal"))
    for m in medications:
        if m["code"] not in ("ifa", "folic_acid"):
            tasks.append(_task(f"med-{m['code']}", m["time"], m["name"], m["name_hi"], "medication",
                               detail=m["instructions"], detail_hi=m["instructions_hi"]))
    if week is not None and week >= 28:
        tasks.append(_task("kick-count", "18:00", "Count baby's kicks", "बच्चे की हलचल गिनें", "baby",
                           detail="At least 10 movements in 12 hours", detail_hi="12 घंटे में कम से कम 10 बार"))
    if bp_high:
        tasks.append(_task("bp-check", "10:00", "BP check with your ASHA or at the PHC", "आशा दीदी या PHC पर BP जाँच", "health"))
    tasks.append(_task("kegel", "20:00", "Pelvic floor exercise (10 squeezes)", "पेल्विक व्यायाम (10 बार)", "activity"))
    tasks.sort(key=lambda t: (t["time"] is None, t["time"] or ""))

    item = next_anc(week, preg.anc_visits_completed if preg else 0) if week is not None else None
    anc_date = (preg.next_anc_date if preg and preg.next_anc_date and preg.next_anc_date >= day
                else anc_target_date(preg.lmp, item, day) if preg else None)
    anc = {
        "date": anc_date, "days_until": (anc_date - day).days if anc_date else None,
        "visit": item["label"] if item else None, "checks": item["checks"] if item else [],
        "bring": [{"text": a, "text_hi": b} for a, b in ANC_BRING],
        "facility": None,
    }
    insights = []
    if week is not None:
        size, size_hi, length, _, milestone, milestone_hi = size_for_week(week)
        insights.append({"kind": "MILESTONE", "text": milestone, "text_hi": milestone_hi})
        insights.append({"kind": "BABY_SIZE", "text": f"Baby is about the size of a {size}.", "text_hi": f"बच्चा {size_hi} जितना बड़ा है।"})
        insights += [{"kind": f["kind"], "text": f["en"], "text_hi": f["hi"]} for f in focus_for_week(week)]
    if hb_low:
        insights.append({"kind": "HEALTH", "text": "Your haemoglobin is low: iron tablets and iron-rich food matter most this week.",
                         "text_hi": "आपका हीमोग्लोबिन कम है: इस हफ़्ते आयरन की गोली और आयरन वाला खाना सबसे ज़रूरी है।"})
    tip, tip_hi = (("Take your iron tablet with lemon water, not tea.", "आयरन की गोली नींबू पानी से लें, चाय के साथ नहीं।") if hb_low
                   else ("Walk for 15 minutes after meals to keep sugar steady.", "शुगर ठीक रखने के लिए खाने के बाद 15 मिनट टहलें।") if sugar_high
                   else ("Eat less salt and rest with your feet up in the afternoon.", "नमक कम खाएँ और दोपहर में पैर ऊपर करके आराम करें।") if bp_high
                   else ("Drink a glass of water every time you finish a task.", "हर काम के बाद एक गिलास पानी पिएँ।"))
    generated_by = "rules"
    llm = get_llm("tip")
    if llm.available:
        lang = "Hindi" if mother.language == "hi" else "English"
        text = llm.generate([ChatMessage("system", COMPANION_SYSTEM.format(language=lang)),
                             ChatMessage("user", f"Week {week}. Notes: {', '.join(profile.get('latest_findings') or []) or 'none'}. "
                                                 f"Conditions: {', '.join(conditions) or 'none'}. Default tip: {tip}")],
                            max_tokens=80)
        if text:
            if mother.language == "hi":
                tip_hi = scrub(text)
            else:
                tip = scrub(text)
            generated_by = "rules+gemma"
    return {"pregnancy_week": week, "tasks": tasks, "meals": meals, "medications": medications,
            "hydration_goal": hydration_goal, "anc_preparation": anc, "weekly_insights": insights,
            "tip": tip, "tip_hi": tip_hi, "generated_by": generated_by}


def get_or_create(db: Session, mother: Mother, day: date, regenerate: bool = False) -> CompanionPlan:
    plan = db.scalar(select(CompanionPlan).where(CompanionPlan.mother_id == mother.id, CompanionPlan.plan_date == day))
    if plan and not regenerate:
        return plan
    data = build_plan(db, mother, day)
    if plan:
        done = {t["id"] for t in plan.tasks if t.get("done")}
        for t in data["tasks"]:
            t["done"] = t["id"] in done
        for k, v in data.items():
            setattr(plan, k, v)
    else:
        plan = CompanionPlan(mother_id=mother.id, plan_date=day, hydration_count=0, **data)
        db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def progress(plan: CompanionPlan) -> dict:
    goals = [t for t in plan.tasks if t.get("goal")]
    done = sum(1 for t in goals if t["done"])
    return {"goals_done": done, "goals_total": len(goals), "all_done": bool(goals) and done == len(goals),
            "hydration": {"count": plan.hydration_count, "goal": plan.hydration_goal}}


def streak(db: Session, mother: Mother, day: date) -> tuple[int, list[dict]]:
    start = day - timedelta(days=day.weekday())  # Monday
    plans = {p.plan_date: p for p in db.scalars(select(CompanionPlan).where(
        CompanionPlan.mother_id == mother.id, CompanionPlan.plan_date >= start - timedelta(days=60),
        CompanionPlan.plan_date <= day)).all()}
    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        p = plans.get(d)
        days.append({"date": d, "weekday": d.strftime("%a")[0], "complete": bool(p and progress(p)["all_done"]),
                     "goals_done": progress(p)["goals_done"] if p else 0, "future": d > day})
    run, d = 0, day
    while (p := plans.get(d)) and progress(p)["all_done"]:
        run += 1
        d -= timedelta(days=1)
    return run, days


def plan_out(db: Session, mother: Mother, plan: CompanionPlan) -> dict:
    prog = progress(plan)
    upcoming = [t for t in plan.tasks if not t["done"] and t["time"]]
    hi = mother.language == "hi"
    names = ", ".join((t["title_hi"] if hi else t["title"]) for t in plan.tasks if t["time"] and not t["goal"])
    out = {
        "date": plan.plan_date, "pregnancy_week": plan.pregnancy_week, "tasks": plan.tasks,
        "goals": sorted((t for t in plan.tasks if t["goal"]), key=lambda t: GOAL_ORDER.index(t["id"])), "meals": plan.meals, "medications": plan.medications,
        "hydration": prog["hydration"], "progress": prog, "next_task": upcoming[0] if upcoming else None,
        "anc_preparation": plan.anc_preparation, "weekly_insights": plan.weekly_insights,
        "tip": plan.tip, "tip_hi": plan.tip_hi, "generated_by": plan.generated_by,
        "spoken_summary": (f"आज की योजना: {names}।" if hi else f"Today's plan: {names}."),
    }
    if prog["all_done"]:
        run, days = streak(db, mother, plan.plan_date)
        complete_days = sum(1 for d in days if d["complete"])
        name = (mother.name or "").split(" ")[0]
        out["celebration"] = {"title": f"Wonderful, {name}!" if name else "Wonderful!",
                              "title_hi": "शाबाश! आज का पूरा प्लान हो गया",
                              "message": f"You completed every wellness goal today. That's {complete_days} day{'s' if complete_days != 1 else ''} this week.",
                              "streak_days": run, "week": days}
    return out

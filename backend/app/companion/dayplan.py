"""'Plan My Day' as a conversation: the companion asks how she wants her day, understands her answers
(typed or spoken, English / Hindi / Hinglish) and builds a plan around her routine, which she can then change
by chat ("move walk to 7 pm", "add temple at 6", "remove nap") or by tapping an item.

The plan's health content (meals, tablets, goals) still comes from her passport, reports and week
(``service.build_plan``); the conversation decides *when* things happen and adds her own activities.
"""
from __future__ import annotations

import copy
import difflib
import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.language import detect_language
from app.ai.llm import ChatMessage, get_llm
from app.ai.safety import apply, check_message, scrub
from app.companion import service
from app.companion.understand import parse_times, understand
from app.database.models import CompanionPlan, ConversationMessage, Mother

CHANNEL = "companion"
SLOTS = ["routine", "work", "food", "today", "feeling"]
ROUTINE_KEYS = ("wake", "sleep", "breakfast", "lunch", "dinner", "work", "work_start", "work_end", "diet", "walk", "nap")

QUESTIONS = {
    "routine": ("What time do you usually wake up, and when do you go to sleep?",
                "आप सुबह कितने बजे उठती हैं और रात को कितने बजे सोती हैं?"),
    "work": ("What does your day look like: housework, farm work, or a job outside? Any fixed hours?",
             "आपका दिन कैसा रहता है: घर का काम, खेत का काम, या बाहर नौकरी? कोई तय समय?"),
    "food": ("Do you eat eggs or non-veg, or are you vegetarian? And around what time do you have lunch?",
             "आप अंडा या नॉन-वेज खाती हैं या शाकाहारी हैं? और दोपहर का खाना लगभग कितने बजे खाती हैं?"),
    "today": ("Anything special today: temple, market, a check-up, visiting family? Tell me roughly when.",
              "आज कुछ ख़ास है: मंदिर, बाज़ार, जाँच, किसी से मिलना? लगभग कब?"),
    "feeling": ("And how are you feeling today: good, tired, nauseous, any back pain or swelling?",
                "और आज आप कैसा महसूस कर रही हैं: ठीक, थकान, जी मिचलाना, कमर दर्द या सूजन?"),
}
QUICK = {
    "routine": [("6 am to 10 pm", "सुबह 6 से रात 10"), ("5 am to 9 pm", "सुबह 5 से रात 9"), ("7 am to 11 pm", "सुबह 7 से रात 11")],
    "work": [("Housework at home", "घर का काम"), ("Farm work in the morning", "सुबह खेत का काम"), ("Office 9 to 5", "ऑफ़िस 9 से 5")],
    "food": [("Vegetarian, lunch at 1 pm", "शाकाहारी, खाना 1 बजे"), ("I eat eggs, lunch at 1 pm", "अंडा खाती हूँ, खाना 1 बजे"),
             ("Non-veg, lunch at 2 pm", "नॉन-वेज, खाना 2 बजे")],
    "today": [("Nothing special", "कुछ ख़ास नहीं"), ("Temple in the morning", "सुबह मंदिर"), ("Market in the evening", "शाम को बाज़ार"),
              ("Check-up today", "आज जाँच है")],
    "feeling": [("Feeling good", "ठीक हूँ"), ("Tired", "थकान है"), ("Nausea", "जी मिचला रहा है"), ("Back pain", "कमर दर्द")],
    "planned": [("Move walk to 7 pm", "walk 7 बजे कर दो"), ("Add rest at 3 pm", "3 बजे आराम जोड़ो"), ("Plan again", "फिर से प्लान बनाओ")],
    "usual": [("Same as usual", "रोज़ जैसा"), ("Start fresh", "नए सिरे से")],
}
LABELS = {"wake": ("Wake", "उठना"), "sleep": ("Sleep", "सोना"), "breakfast": ("Breakfast", "नाश्ता"), "lunch": ("Lunch", "दोपहर का खाना"),
          "dinner": ("Dinner", "रात का खाना"), "walk": ("Walk", "टहलना"), "work": ("Work", "काम"), "diet": ("Food", "खाना")}
VALUES_HI = {"home": "घर का काम", "farm": "खेत", "office": "नौकरी", "none": "नहीं", "veg": "शाकाहारी", "egg": "अंडा", "nonveg": "नॉन-वेज",
             "morning": "सुबह", "evening": "शाम", "good": "ठीक", "tired": "थकान", "nausea": "जी मिचलाना", "back_pain": "कमर दर्द",
             "swelling": "सूजन", "low": "उदासी"}
FEELING_EN = {"good": "good", "tired": "tired", "nausea": "nausea", "back_pain": "back pain", "swelling": "swelling", "low": "feeling low"}

# Words she may use for plan items → task ids
ALIASES = [(r"walk|tahal|टहल|ghoom", "goal-walk"), (r"iron|ifa|goli|गोली|tablet|folic", "goal-iron"),
           (r"sleep|neend|नींद|so(na|ne)", "goal-sleep"), (r"breakfast|nashta|नाश्ता", "meal-0"), (r"lunch|dopahar ka khana", "meal-2"),
           (r"dinner|raat ka khana|रात का खाना", "meal-4"), (r"kegel|pelvic|पेल्विक", "kegel"), (r"kick|hal ?chal|हलचल", "kick-count"),
           (r"\bnap\b|rest|aaram|आराम", "rest"), (r"calcium|कैल्शियम", "med-calcium"), (r"wake|uth", "wake"), (r"work|kaam|काम|office|khet", "work")]

REPLY_SYSTEM = """You are Janamdatri's Pregnancy Companion, chatting with a pregnant woman in India to plan her day together.
Write a short, warm reply (at most 2 sentences) in {language} (Hinglish if she writes Hinglish).
First briefly acknowledge what she told you, in your own words. Then ask exactly this next question, keeping its meaning: "{question}"
No medical advice, no lists, no numbers about risk."""
FREE_SYSTEM = """You are Janamdatri's Pregnancy Companion. Her plan for today is already made: {plan}.
Reply in {language} in at most 3 short sentences. If she asks about her day or plan, help with that. If she asks about
pregnancy, answer briefly and safely and suggest her ASHA didi or doctor for medical decisions. If it is unrelated to
her day or pregnancy, kindly say you can help with her day plan and pregnancy questions. You can change the plan only
when she says e.g. "move walk to 7 pm", "add temple at 6" or "remove nap"; tell her that if she seems to want a change."""


# --- small helpers ------------------------------------------------------------------------------------------

def _min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _hhmm(n: int) -> str:
    n = max(0, min(n, 23 * 60 + 59))
    return f"{n // 60:02d}:{n % 60:02d}"


def _add(t: str, minutes: int) -> str:
    return _hhmm(_min(t) + minutes)


def say_time(t: str, hi: bool) -> str:
    """'17:30' → '5:30 PM' / 'शाम 5:30', the way she would say it."""
    h, m = (int(x) for x in t.split(":"))
    hh = (h + 11) % 12 + 1
    if hi:
        part = "सुबह" if 4 <= h < 12 else "दोपहर" if 12 <= h < 16 else "शाम" if 16 <= h < 20 else "रात"
        return f"{part} {hh}:{m:02d}"
    return f"{hh}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _round(n: int) -> int:
    return int(round(n / 15.0) * 15)


def thread_for(mother: Mother, day: date) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"janamdatri:companion:{mother.id}:{day.isoformat()}")


def _state(mother: Mother, day: date) -> dict:
    prefs = (mother.user.preferences or {}) if mother.user else {}
    dp = copy.deepcopy(prefs.get("day_plan") or {})  # a copy: in-place changes would not be saved
    today = dp.get("today") or {}
    if today.get("date") != day.isoformat():
        today = {"date": day.isoformat(), "asked": [], "collected": {}, "stage": "asking"}
    return {"usual": dp.get("usual") or {}, "today": today}


def _save_state(mother: Mother, state: dict) -> None:
    if not mother.user:
        return
    prefs = dict(mother.user.preferences or {})
    prefs["day_plan"] = {"usual": state["usual"], "today": state["today"]}
    mother.user.preferences = prefs  # reassign so the JSON column is saved


def _hi(mother: Mother, lang: str | None) -> bool:
    return (lang or mother.language) == "hi"


def _quick(key: str, hi: bool) -> list[dict]:
    return [{"text": hi_ if hi else en, "text_en": en, "text_hi": hi_} for en, hi_ in QUICK[key]]


def _filled(slot: str, c: dict) -> bool:
    return {"routine": "wake" in c or "sleep" in c, "work": "work" in c, "food": "diet" in c or "lunch" in c,
            "today": "activities" in c, "feeling": "feeling" in c}[slot]


def _next_slot(today: dict) -> str | None:
    return next((s for s in SLOTS if s not in today["asked"] and not _filled(s, today["collected"])), None)


def _summary(found: dict, hi: bool) -> str:
    """What the understanding layer picked up, said back to her so she can correct it."""
    parts = []
    for k in ("wake", "breakfast", "lunch", "dinner", "sleep", "walk", "work", "diet"):
        if k in found:
            v = found[k]
            label = LABELS[k][1 if hi else 0]
            if re.match(r"^\d\d:\d\d$", str(v)):
                v = say_time(v, hi)
            if k == "work" and found.get("work_start"):
                hours = say_time(found["work_start"], hi) + (f"–{say_time(found['work_end'], hi)}" if found.get("work_end") else "")
                v = f"{VALUES_HI.get(v, v) if hi else v} {hours}"
            parts.append(f"{label} {VALUES_HI.get(v, v) if hi else v}")
    for a in found.get("activities", []):
        parts.append(f"{a['title_hi'] if hi else a['title']} {say_time(a['time'], hi)}")
    if found.get("feeling"):
        parts.append(", ".join((VALUES_HI if hi else FEELING_EN).get(f, f) for f in found["feeling"]))
    if found.get("nap"):
        parts.append("दोपहर में आराम" if hi else "afternoon rest")
    return " · ".join(parts)


def _context(today: dict, slot: str | None) -> str:
    asked = f"You just asked her: {QUESTIONS[slot][0]}" if slot else "Her plan is already made."
    return f"{asked}\nWhat you know so far: {today['collected'] or 'nothing yet'}"


# --- reading her answer -------------------------------------------------------------------------------------

def _read(text: str, today: dict, slot: str | None, plan_exists: bool) -> dict:
    words = len(text.split())
    found = understand(text, plan_exists=plan_exists, context=_context(today, slot), llm=None)
    useful = {k for k in found if k not in ("nothing",)}
    if words > 6 or not useful:  # the model helps with free-form sentences the rules can't read
        llm = get_llm("companion")
        if llm.available:
            found = understand(text, plan_exists=plan_exists, context=_context(today, slot), llm=llm) | (
                {"nothing": True} if found.get("nothing") else {})
            found["_model"] = True
    times = [t for _, t in parse_times(text)]
    # Answers to the question just asked, without the cue words ("6 am to 10 pm", "1 baje")
    if slot == "routine" and times and not any(k in found for k in ("wake", "sleep")):
        found["wake"] = times[0]
        if len(times) > 1:
            found["sleep"] = times[1]
    if slot == "food" and times and "lunch" not in found:
        found["lunch"] = times[0]
    if slot == "work" and not found.get("work") and re.search(r"\bno\b|nahi|नहीं|none|ghar pe|at home", text.lower()):
        found["work"] = "home"
    if slot == "work" and found.get("work") and "work_start" not in found and times:
        found["work_start"] = times[0]
        if len(times) > 1:
            found["work_end"] = times[1]
    return found


def _merge(collected: dict, found: dict) -> dict:
    out = dict(collected)
    for k, v in found.items():
        if k in ("edits", "make_plan", "same_as_usual", "nothing", "_model"):
            continue
        if k == "activities":
            have = {(a["time"], a["title"]) for a in out.get("activities", [])}
            out["activities"] = out.get("activities", []) + [a for a in v if (a["time"], a["title"]) not in have]
        else:
            out[k] = v
    return out


# --- building the personal plan -----------------------------------------------------------------------------

def _task(tid, time, title, title_hi, category, detail=None, detail_hi=None):
    return {"id": tid, "time": time, "title": title, "title_hi": title_hi, "category": category, "goal": False,
            "detail": detail, "detail_hi": detail_hi, "done": False}


WORK_TASKS = {
    "office": ("Work", "काम", "Stand up and stretch every hour; keep water and a snack with you.",
               "हर घंटे उठकर थोड़ा चलें; पानी और नाश्ता साथ रखें।"),
    "farm": ("Farm work", "खेत का काम", "Rest in the shade often, drink water every hour, no heavy lifting or bending for long.",
             "बार-बार छाँव में आराम करें, हर घंटे पानी पिएँ, भारी बोझ न उठाएँ, देर तक न झुकें।"),
    "home": ("Housework", "घर का काम", "Sit while cutting vegetables, no heavy buckets, rest 10 minutes every hour.",
             "सब्ज़ी बैठकर काटें, भारी बाल्टी न उठाएँ, हर घंटे 10 मिनट आराम करें।"),
}
FEELING_TASKS = {
    "nausea": ("feel-nausea", 0, "Eat a dry biscuit or toast before getting up", "उठने से पहले सूखा बिस्कुट या टोस्ट खाएँ",
               "Small meals every 2–3 hours help nausea; sip water or nimbu pani.", "हर 2–3 घंटे थोड़ा-थोड़ा खाएँ; पानी या नींबू पानी घूँट-घूँट पिएँ।"),
    "back_pain": ("feel-back", 60, "Gentle back stretch (5 min)", "कमर का हल्का व्यायाम (5 मिनट)",
                  "On hands and knees, gently arch and round your back 10 times. Sit with a cushion behind your back.",
                  "घुटनों और हाथों के बल धीरे से कमर ऊपर-नीचे करें, 10 बार। बैठते समय कमर के पीछे तकिया रखें।"),
    "swelling": ("feel-swelling", None, "Feet up for 15 minutes", "15 मिनट पैर ऊपर करके आराम",
                 "Less salt today, and lie on your left side. If your face or hands swell, tell your ASHA today.",
                 "आज नमक कम, और बाईं करवट लेटें। चेहरे या हाथों पर सूजन हो तो आज ही आशा दीदी को बताएँ।"),
    "low": ("feel-low", None, "Talk with someone you love (15 min)", "किसी अपने से बात करें (15 मिनट)",
            "Sit in the sun with family or call a friend. You can also open 'Talk To Me' any time.",
            "परिवार के साथ धूप में बैठें या किसी सहेली को फ़ोन करें। 'मुझसे बात करें' भी कभी भी खोलें।"),
}


def personalise(data: dict, c: dict) -> dict:
    """Re-time the health plan around her day and add her own activities. ``data`` is build_plan() output."""
    wake = c.get("wake") or "06:00"
    sleep = c.get("sleep") or "22:00"
    if _min(sleep) < _min(wake) + 12 * 60:  # "sleep 10" without pm, or a typo: keep a sensible night
        sleep = _hhmm(min(_min(wake) + 16 * 60, 23 * 60 + 30))
    breakfast = c.get("breakfast") or _hhmm(_round(_min(wake) + 90))
    lunch = c.get("lunch") or "13:00"
    dinner = c.get("dinner") or _hhmm(_round(min(_min(sleep) - 150, 20 * 60)))
    if _min(lunch) <= _min(breakfast) + 120:
        lunch = _hhmm(_min(breakfast) + 240)
    if _min(dinner) <= _min(lunch) + 180:
        dinner = _hhmm(_min(lunch) + 360)
    feeling = set(c.get("feeling") or [])
    work, w_start, w_end = c.get("work"), c.get("work_start"), c.get("work_end")
    if work in ("office", "farm") and not w_start:
        w_start, w_end = (_add(breakfast, 60), _add(breakfast, 540)) if work == "office" else (_add(wake, 30), _add(wake, 270))
    busy = (_min(w_start), _min(w_end)) if w_start and w_end and work in ("office", "farm") else None

    def free(t: str) -> bool:
        return not busy or not (busy[0] <= _min(t) < busy[1])

    meal_times = [breakfast, _hhmm(_round((_min(breakfast) + _min(lunch)) // 2)), lunch,
                  _hhmm(_round((_min(lunch) + _min(dinner)) // 2)), dinner]
    meals = [dict(m) for m in data["meals"]]
    for m, t in zip(meals, meal_times, strict=False):
        m["time"] = t
    diet = c.get("diet")
    for m in meals:
        if diet == "veg":
            m["food"] = m["food"].replace("paneer or egg curry", "paneer curry")
            m["food_hi"] = m["food_hi"].replace("पनीर या अंडा करी", "पनीर करी")
        elif diet in ("egg", "nonveg") and m["meal"] == "Breakfast":
            m["food"] += " + 1 boiled egg"
            m["food_hi"] += " + 1 उबला अंडा"
    meds = [dict(m) for m in data["medications"]]
    for m in meds:
        if m["code"] == "ifa":
            m["time"] = _add(lunch, 30)
        elif m["code"] == "calcium":
            m["time"] = _add(sleep, -60)
        elif m["code"] == "folic_acid":
            m["time"] = _add(breakfast, 30)

    walk_pref = c.get("walk")
    if isinstance(walk_pref, str) and re.match(r"^\d\d:\d\d$", walk_pref):
        walk = walk_pref
    elif walk_pref == "morning":
        walk = _add(wake, 30)
    elif busy and 15 * 60 <= _min(w_end) < _min(dinner) - 60:  # after a day job: walk on the way home / after work
        walk = _add(w_end, 45)
    else:
        walk = _add(dinner, -120) if walk_pref == "evening" or not walk_pref else _add(wake, 30)
    tired = "tired" in feeling or c.get("nap")

    tasks = []
    med_time = {m["code"]: m["time"] for m in meds}
    for t in data["tasks"]:
        t = dict(t)
        tid = t["id"]
        if tid.startswith("meal-") and tid[5:].isdigit() and int(tid[5:]) < len(meals):
            m = meals[int(tid[5:])]
            t.update(time=m["time"], title=f"{m['meal']}: {m['food']}", title_hi=f"{m['meal_hi']}: {m['food_hi']}")
        elif tid == "goal-iron":
            t["time"] = med_time.get("ifa") or med_time.get("folic_acid") or t["time"]
        elif tid.startswith("med-"):
            t["time"] = med_time.get(tid[4:], t["time"])
        elif tid == "goal-walk":
            t["time"] = walk
            if tired:
                t["detail"], t["detail_hi"] = "A gentle 10 min stroll today, only if comfortable", "आज धीरे-धीरे 10 मिनट टहलें, ठीक लगे तो"
        elif tid == "goal-sleep":
            t["time"] = sleep
        elif tid == "kegel":
            t["time"] = _add(sleep, -90)
        elif tid == "kick-count":
            t["time"] = _add(dinner, -60) if free(_add(dinner, -60)) else _add(dinner, 60)
        elif tid == "bp-check":
            t["time"] = _add(breakfast, 60) if free(_add(breakfast, 60)) else (_add(w_end, 15) if busy else t["time"])
        tasks.append(t)

    tasks.append(_task("wake", wake, "Wake up: a glass of water and a slow stretch", "उठें: एक गिलास पानी और धीरे से अंगड़ाई", "routine"))
    if work in WORK_TASKS:
        title, title_hi, d, d_hi = WORK_TASKS[work]
        start = w_start or _add(breakfast, 45)
        if w_end:
            title, title_hi = f"{title} (till {w_end})", f"{title_hi} ({w_end} तक)"
        tasks.append(_task("work", start, title, title_hi, "routine", d, d_hi))
    if tired or "swelling" in feeling or "back_pain" in feeling:
        rest = _add(lunch, 60)
        if free(rest):
            tasks.append(_task("rest", rest, "Afternoon rest, lying on your left side (30 min)", "दोपहर का आराम, बाईं करवट (30 मिनट)", "rest"))
        else:
            tasks.append(_task("rest", _add(lunch, 30), "Sit with your feet up for 10 minutes", "10 मिनट पैर ऊपर करके बैठें", "rest"))
    for f in ("nausea", "back_pain", "swelling", "low"):
        if f in feeling:
            tid, offset, title, title_hi, d, d_hi = FEELING_TASKS[f]
            at = (wake if offset == 0 else _add(wake, offset) if offset else
                  _add(walk, 60) if f == "swelling" else _add(dinner, -45))
            tasks.append(_task(tid, at, title, title_hi, "wellbeing", d, d_hi))
    for i, a in enumerate(c.get("activities") or []):
        tasks.append(_task(f"act-{i}", a["time"], a["title"], a.get("title_hi") or a["title"], "personal",
                           "Carry water and a snack; sit and rest if you feel tired." if a["title"] != "Check-up / ANC visit" else
                           "Take your MCP card and reports.",
                           "पानी और कुछ खाने को साथ रखें; थकान हो तो बैठ जाएँ।" if a["title"] != "Check-up / ANC visit" else
                           "MCP कार्ड और रिपोर्ट साथ ले जाएँ।"))
    tasks.sort(key=lambda t: (t["time"] is None, t["time"] or ""))
    out = dict(data)
    out.update(tasks=tasks, meals=meals, medications=meds)
    if "nausea" in feeling:
        out["tip"], out["tip_hi"] = FEELING_TASKS["nausea"][4], FEELING_TASKS["nausea"][5]
    elif "low" in feeling:
        out["tip"], out["tip_hi"] = ("Be gentle with yourself today; small steps count. I'm here whenever you want to talk.",
                                     "आज अपने साथ नरमी रखें; छोटे कदम भी मायने रखते हैं। जब मन हो, बात करें।")
    return out


def make_plan(db: Session, mother: Mother, day: date, collected: dict, generated_by: str = "chat") -> CompanionPlan:
    data = personalise(service.build_plan(db, mother, day), collected)
    data["generated_by"] = generated_by
    plan = db.scalar(select(CompanionPlan).where(CompanionPlan.mother_id == mother.id, CompanionPlan.plan_date == day))
    if plan:
        done = {t["id"] for t in plan.tasks if t.get("done")}
        for t in data["tasks"]:
            t["done"] = t["id"] in done
        for k, v in data.items():
            setattr(plan, k, v)
    else:
        plan = CompanionPlan(mother_id=mother.id, plan_date=day, hydration_count=0, **data)
        db.add(plan)
    db.flush()
    return plan


# --- editing ----------------------------------------------------------------------------------------------

def find_task(tasks: list[dict], title: str) -> dict | None:
    low = (title or "").lower().strip()
    if not low:
        return None
    for rx, tid in ALIASES:
        if re.search(rx, low):
            hit = next((t for t in tasks if t["id"] == tid), None)
            if hit:
                return hit
    names = {t["id"]: f"{t['title']} {t['title_hi']}".lower() for t in tasks}
    for tid, name in names.items():
        if low in name:
            return next(t for t in tasks if t["id"] == tid)
    best = difflib.get_close_matches(low, list(names.values()), n=1, cutoff=0.45)
    if best:
        tid = next(k for k, v in names.items() if v == best[0])
        return next(t for t in tasks if t["id"] == tid)
    return None


def apply_edits(plan: CompanionPlan, eds: list[dict], hi: bool) -> tuple[list[str], list[str]]:
    """Apply chat edits to the plan; returns (what changed, what could not be done), in her language."""
    tasks = [dict(t) for t in plan.tasks]
    done, failed = [], []
    for e in eds:
        t = find_task(tasks, e["title"]) if e["op"] != "add" else None
        if e["op"] == "add":
            title = (e.get("title") or "").strip(" .").capitalize() or ("Activity" if not hi else "काम")
            tid = f"my-{uuid.uuid4().hex[:6]}"
            tasks.append(_task(tid, e["time"], title, title, "personal"))
            done.append(f"जोड़ा: {title}, {say_time(e['time'], hi)}" if hi else f"added {title} at {say_time(e['time'], hi)}")
        elif not t:
            failed.append(e["title"] or "?")
        elif e["op"] == "move":
            t["time"] = e["time"]
            done.append(f"{t['title_hi']} अब {say_time(e['time'], hi)}" if hi else f"{t['title']} moved to {say_time(e['time'], hi)}")
        elif e["op"] == "remove":
            if t["category"] == "medication":
                failed.append("दवा की गोली प्लान से नहीं हटती; समय बदल सकते हैं" if hi else
                               "tablets stay in the plan (I can change their time); ask your doctor before stopping any")
                continue
            tasks.remove(t)
            done.append(f"हटाया: {t['title_hi']}" if hi else f"removed {t['title']}")
    tasks.sort(key=lambda t: (t["time"] is None, t["time"] or ""))
    plan.tasks = tasks
    return done, failed


# --- the conversation ---------------------------------------------------------------------------------------

def _history(db: Session, mother: Mother, day: date) -> list[ConversationMessage]:
    if not mother.user_id:
        return []
    return list(db.scalars(select(ConversationMessage).where(
        ConversationMessage.user_id == mother.user_id, ConversationMessage.channel == CHANNEL,
        ConversationMessage.thread_id == thread_for(mother, day)).order_by(ConversationMessage.created_at)).all())


def greeting(mother: Mother, state: dict, hi: bool) -> str:
    name = (mother.name or "").split(" ")[0]
    if state["today"]["stage"] == "planned":
        return ("आज की योजना तैयार है। कुछ बदलना हो तो बताइए, जैसे 'walk 7 बजे कर दो' या '3 बजे आराम जोड़ो'।" if hi else
                "Your plan for today is ready. Tell me if you'd like to change anything, like 'move walk to 7 pm' or 'add rest at 3 pm'.")
    if state["usual"]:
        return (f"नमस्ते{' ' + name if name else ''}! आज का दिन कैसा रखना है? रोज़ जैसा, या आज कुछ अलग है?" if hi else
                f"Good to see you{', ' + name if name else ''}! How should today look: same as usual, or is something different today?")
    return (f"नमस्ते{' ' + name if name else ''}! चलिए साथ मिलकर आज का दिन प्लान करते हैं। " + QUESTIONS["routine"][1] if hi else
            f"Hi{' ' + name if name else ''}! Let's plan your day together, the way you want it. " + QUESTIONS["routine"][0])


def _first_slot(state: dict) -> str:
    return "usual" if state["usual"] else "routine"


def chat_state(db: Session, mother: Mother, day: date, lang: str | None = None) -> dict:
    hi = _hi(mother, lang)
    state = _state(mother, day)
    msgs = [{"role": m.role, "content": m.content, "input_mode": m.input_mode, "created_at": m.created_at}
            for m in _history(db, mother, day)]
    today = state["today"]
    stage = today["stage"]
    pending = today.get("pending") or (_first_slot(state) if not today["asked"] and stage == "asking" else _next_slot(today))
    quick = _quick("planned" if stage == "planned" else (pending or "planned"), hi)
    return {"thread_id": thread_for(mother, day), "date": day, "stage": stage, "greeting": greeting(mother, state, hi),
            "messages": msgs, "quick_replies": quick, "asking": pending if stage == "asking" else None,
            "collected": today["collected"], "has_usual": bool(state["usual"])}


def _ai_reply(text: str, summary: str, question: str, lang: str) -> str | None:
    llm = get_llm("tip")
    if not llm.available:
        return None
    out = llm.generate([ChatMessage("system", REPLY_SYSTEM.format(language="Hindi" if lang == "hi" else "English", question=question)),
                        ChatMessage("user", f"She said: {text}\nYou understood: {summary or 'nothing specific'}")],
                       temperature=0.6, max_tokens=120)
    out = scrub(out or "").strip()
    return out if 8 <= len(out) <= 400 else None


def _plan_summary(plan: CompanionPlan, hi: bool) -> str:
    items = [t for t in plan.tasks if t["time"] and t["category"] not in ("meal",)][:7]
    lines = [f"{say_time(t['time'], hi)} · {t['title_hi'] if hi else t['title']}" for t in items]
    head = "आपकी आज की योजना तैयार है:" if hi else "Here's your day, built around what you told me:"
    tail = ("कुछ भी बदलना हो तो बोलिए या नीचे किसी काम पर टैप करके समय बदलिए।" if hi else
            "Want to change anything? Just tell me, or tap an item below to change its time.")
    return head + "\n" + "\n".join(lines) + "\n\n" + tail


def _free_reply(text: str, plan: CompanionPlan, lang: str, hi: bool) -> tuple[str, bool]:
    llm = get_llm("companion")
    if llm.available:
        brief = "; ".join(f"{t['time']} {t['title']}" for t in plan.tasks if t["time"])[:900]
        out = llm.generate([ChatMessage("system", FREE_SYSTEM.format(plan=brief, language="Hindi" if lang == "hi" else "English")),
                            ChatMessage("user", text)], max_tokens=220)
        if out:
            return scrub(out), True
    return (("मैं आपकी आज की योजना बदल सकती हूँ। बोलिए जैसे 'walk 7 बजे कर दो', '3 बजे आराम जोड़ो' या 'kegel हटा दो'। "
             "सेहत के सवाल 'स्वास्थ्य सहायक' या 'गर्भावस्था गाइड' में पूछिए।") if hi else
            ("I can change today's plan for you. Say things like 'move walk to 7 pm', 'add rest at 3 pm' or 'remove kegel'. "
             "For health questions, ask the Health Assistant or the Pregnancy Guide."), False)


def chat(db: Session, mother: Mother, day: date, text: str, lang: str | None, input_mode: str = "text") -> dict:
    text = text.strip()
    lang = lang or detect_language(text, default=mother.language if mother.language in ("hi", "en") else "en")
    hi = lang == "hi"
    check = check_message(text)
    state = _state(mother, day)
    today = state["today"]
    slot = today.get("pending") or (_first_slot(state) if not today["asked"] else _next_slot(today))
    planned = today["stage"] == "planned"
    found = _read(text, today, None if planned or slot == "usual" else slot, plan_exists=planned)
    used_ai = bool(found.pop("_model", False))
    plan = None
    changed: list[str] = []

    restart = re.search(r"plan again|start (fresh|again|over)|new plan|fir se|phir se|फिर से|नए सिरे|naye sire", text.lower())
    if restart:
        today = {"date": day.isoformat(), "asked": [], "collected": {}, "stage": "asking"}
        if re.search(r"start fresh|नए सिरे|naye sire", text.lower()):
            state["usual"] = {}
        planned, slot, found = False, None, {}
    elif slot == "usual" and not planned:
        today["asked"].append("usual")
        if found.get("same_as_usual") or found.get("nothing") or found.get("make_plan"):
            today["collected"] = _merge(dict(state["usual"]), found)
            today["asked"] += ["routine", "work", "food"]
        else:
            today["collected"] = _merge({}, found)
    elif planned:
        if found.get("edits"):
            plan = service.get_or_create(db, mother, day)
            changed, failed = apply_edits(plan, found["edits"], hi)
            if failed and not changed:
                changed = []
                today["note"] = failed
        elif any(k in found for k in (*ROUTINE_KEYS, "activities", "feeling")) or found.get("make_plan"):
            today["collected"] = _merge(today["collected"], found)
            plan = make_plan(db, mother, day, today["collected"], "chat+ai" if used_ai else "chat")
            changed = ["updated"]
    else:
        if slot:
            today["asked"].append(slot)
        today["collected"] = _merge(today["collected"], found)

    reply_parts: list[str] = []
    understood = _summary(found, hi)
    next_slot = None
    if not planned and not restart:
        next_slot = None if found.get("make_plan") else _next_slot(today)
        if next_slot is None:
            plan = make_plan(db, mother, day, today["collected"], "chat+ai" if used_ai else "chat")
            today["stage"] = "planned"
            usual = {k: v for k, v in today["collected"].items() if k in ROUTINE_KEYS}
            if usual:
                state["usual"] = {**state["usual"], **usual}
            reply_parts.append(_plan_summary(plan, hi))
        else:
            q = QUESTIONS[next_slot][1 if hi else 0]
            ai = _ai_reply(text, understood, q, lang)
            if ai:
                used_ai = True
                reply_parts.append(ai)
            else:
                ack = (f"ठीक है, समझ गई: {understood}।" if understood else "ठीक है।") if hi else \
                      (f"Got it: {understood}." if understood else "Okay.")
                reply_parts.append(f"{ack} {q}")
    elif restart:
        reply_parts.append(("ठीक है, फिर से शुरू करते हैं। " + QUESTIONS["routine"][1]) if hi else
                           ("Sure, let's start again. " + QUESTIONS["routine"][0]))
        next_slot = "routine"
    elif changed == ["updated"]:
        reply_parts.append((f"समझ गई: {understood}। मैंने योजना बदल दी है।" if hi else f"Got it: {understood}. I've updated your plan.")
                           if understood else ("योजना फिर से बना दी है।" if hi else "I've made your plan again."))
    elif changed:
        reply_parts.append(("हो गया: " if hi else "Done: ") + "; ".join(changed) + ".")
    elif today.get("note"):
        note = today.pop("note")
        reply_parts.append(("यह काम प्लान में नहीं मिला: " if hi else "I couldn't find that in your plan: ") + ", ".join(note)
                           + (". नीचे सूची में टैप करके भी बदल सकती हैं।" if hi else ". You can also tap an item in the list to change it."))
    else:
        plan = service.get_or_create(db, mother, day)
        free, ai = _free_reply(text, plan, lang, hi)
        used_ai = used_ai or ai
        reply_parts.append(free)

    today["pending"] = next_slot if today["stage"] == "asking" else None
    state["today"] = today
    _save_state(mother, state)
    reply = apply("\n\n".join(reply_parts), check, lang, disclaimer=False)

    if mother.user_id:
        tid = thread_for(mother, day)
        db.add(ConversationMessage(user_id=mother.user_id, mother_id=mother.id, channel=CHANNEL, thread_id=tid, role="user",
                                   content=text, language=lang, input_mode=input_mode, safety_flags=check.flags))
        db.add(ConversationMessage(user_id=mother.user_id, mother_id=mother.id, channel=CHANNEL, thread_id=tid,
                                   role="assistant", content=reply, language=lang))
    db.commit()
    plan = plan or service.get_or_create(db, mother, day)
    actions = []
    if check.emergency or check.self_harm:
        actions.append({"type": "sos", "label": "SOS · Call 108", "label_hi": "SOS · 108 पर कॉल"})
    elif check.danger_signs:
        actions.append({"type": "assess", "label": "Health check", "label_hi": "स्वास्थ्य जाँच"})
    if "low" in (found.get("feeling") or []):
        actions.append({"type": "talk", "label": "Talk To Me", "label_hi": "मुझसे बात करें"})
    stage = today["stage"]
    return {
        "reply": reply, "language": lang, "stage": stage, "understood": understood or None,
        "asking": next_slot if stage == "asking" else None,
        "quick_replies": _quick("planned" if stage == "planned" else (next_slot or "planned"), hi),
        "generated_by": "chat+ai" if used_ai else "chat",
        "safety": {"emergency": check.emergency, "danger_signs": check.danger_signs, "self_harm": check.self_harm},
        "actions": actions, "plan": service.plan_out(db, mother, plan),
    }

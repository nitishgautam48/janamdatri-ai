"""Understanding layer for the Companion: turns what she says (English, Hindi, Hinglish; typed or spoken)
into a structured picture of her day, and into edits of an existing plan.

Two passes, merged:
1. Rules (always): times ("6 baje", "subah 6:30", "7 pm", "रात 10 बजे"), wake / sleep / meal times, work,
   food preference, today's activities and how she feels.
2. The language model (when a key is set): the same fields as JSON, which catches free-form sentences the
   rules miss. Its output is validated field by field; anything invalid is ignored, so a model mistake can
   never break the plan.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

TIME_RX = re.compile(
    r"(?P<pre>subah|savere|सुबह|morning|dopahar|दोपहर|afternoon|shaam|sham|शाम|evening|raat|रात|night)?\s*"
    r"(?P<h>\b[01]?\d|2[0-3])(?:[:.](?P<m>[0-5]\d))?\s*"
    r"(?P<suf>a\.?m\.?|p\.?m\.?|baje|बजे|o'?clock|hrs?)?"
    r"(?P<post>\s*(?:subah|सुबह|morning|dopahar|दोपहर|afternoon|shaam|sham|शाम|evening|raat|रात|night|ko|को)?)", re.I)

CUES = {
    "wake": r"wake|woke|get up|getting up|\buth(ti|ta|na|ungi|ti hoon)?\b|उठ|jaag|जाग",
    "sleep": r"\bsleep|\bsoti|so jaati|so jati|sone|सोती|सोने|so jaungi|bed ?time|letti",
    "breakfast": r"breakfast|nashta|nasta|नाश्ता",
    "lunch": r"lunch|dopahar ka khana|दोपहर का खाना|din ka khana",
    "dinner": r"dinner|raat ka khana|रात का खाना|supper",
}
WORK = [("office", r"office|job|naukri|नौकरी|school|teacher|padhati|work from|duty|shift|dukaan|shop|दुकान"),
        ("farm", r"farm|khet|खेत|field work|fasal|mazdoori|मज़दूरी|labou?r work"),
        ("home", r"house ?work|household|ghar ka kaam|घर का काम|ghar pe|घर पर|home ?maker|housewife|cooking|khana banana")]
DIET = [("nonveg", r"non[- ]?veg|chicken|mutton|meat|fish|machli|मछली|मांस|मीट"),
        ("egg", r"\begg|anda|अंडा|अंडे"),
        ("veg", r"\bveg(etarian)?\b|shakahari|शाकाहारी|no egg|anda nahi")]
FEELINGS = [("nausea", r"nause|vomit|ulti|उल्टी|ji machal|जी मिचल|ghabrahat"),
            ("tired", r"tired|thak|थक|weak|kamzor|कमज़ोर|no energy|neend nahi"),
            ("back_pain", r"back ?pain|kamar (mein |me )?dard|कमर (में )?दर्द"),
            ("swelling", r"swell|sujan|soojan|सूजन|pair (sooj|suj)"),
            ("low", r"\bsad\b|udaas|udas|उदास|low|akela|अकेल|lonely|stress|tension|worried|chinta|चिंता"),
            ("good", r"\bfine\b|\bgood\b|great|theek|thik|ठीक|accha|achha|अच्छा|badhiya|बढ़िया")]
ACTIVITIES = [("Temple / puja", "मंदिर / पूजा", r"temple|mandir|मंदिर|puja|pooja|पूजा|masjid|namaz|gurudwara|church"),
              ("Market", "बाज़ार", r"market|bazaar|bazar|बाज़ार|shopping|sabzi lene"),
              ("Visit family", "परिवार से मिलना", r"visit|milne|मिलने|maayke|mayke|मायके|relative|rishtedar"),
              ("Check-up / ANC visit", "जाँच / ANC", r"check ?up|anc\b|doctor|hospital|clinic|jaanch|जाँच|phc|chc|asha didi"),
              ("Yoga / exercise", "योग / व्यायाम", r"yoga|योग|exercise|vyayam|व्यायाम"),
              ("Office / work", "काम", r"\bmeeting\b|class (at|ko)|duty (at|from)"),
              ("Rest / nap", "आराम", r"\bnap\b|rest|aaram|आराम|lete|let jaungi")]
WALK = r"\bwalk|tahal|टहल|ghoom|stroll"
# A bare "ok / no / nothing" only answers the question just asked; MAKE is an explicit "make my plan".
NOTHING = (r"^(ok|okay|no|nahi|nahin|nope|ji|haan|ha|han|yes|हाँ|हां|नहीं|ठीक है|theek hai|bas|बस|"
           r"kuch nahi|kuch nahin|कुछ नहीं|nothing( special)?|none|no plans?)\W*$")
MAKE = (r"make (my |the )?(plan|day)|plan (bana|banao|banaiye|bana do|bana dijiye)|(haan |ha |ok )?bana (do|dijiye|dena)|\bbanao\b|"
        r"प्लान (बना|बनाओ|बना दो)|बना (दो|दीजिए)|create (the |my )?plan|go ahead|that'?s all|that is all|nothing else|i'?m ready|\bdone\b")
SAME = r"same (as|day)|wahi|वही|jaisa kal|पहले जैसा|usual|roz jaisa|रोज़ जैसा"
EDIT_ADD = r"\badd\b|\bjodo\b|जोड़|\binclude\b|daal do|डाल दो"
EDIT_REMOVE = r"remove|delete|hata(o|na|do)?|हटा|cancel|nahi karna|मत (रखो|करो)|skip"
EDIT_MOVE = r"\bmove\b|change|shift|badal|बदल|instead|ki jagah|को .* कर दो|reschedule|(se|to) (pehle|baad)"


def _hhmm(h: int, m: int) -> str | None:
    if 0 <= h <= 23 and 0 <= m <= 59:
        return f"{h:02d}:{m:02d}"
    return None


def parse_times(text: str) -> list[tuple[int, str]]:
    """[(position, "HH:MM")] for every clock time in the text, using am/pm or morning/evening words."""
    out = []
    for mt in TIME_RX.finditer(text):
        h, m = int(mt.group("h")), int(mt.group("m") or 0)
        suf = (mt.group("suf") or "").lower().replace(".", "")
        ctx = ((mt.group("pre") or "") + " " + (mt.group("post") or "")).lower()
        if not (suf or mt.group("m") or re.sub(r"\b(ko)\b|को", "", ctx).strip()):
            continue  # a bare number ("2 rotis", "week 24") is not a time
        if suf.startswith("p") or re.search(r"shaam|sham|शाम|evening|raat|रात|night", ctx):
            if h < 12 and not (re.search(r"raat|रात|night", ctx) and h in (1, 2, 3, 4)):
                h += 12
        elif re.search(r"dopahar|दोपहर|afternoon", ctx) and h < 6:
            h += 12
        elif suf.startswith("a") and h == 12:
            h = 0
        if (t := _hhmm(h % 24, m)) is not None:
            out.append((mt.start(), t))
    return out


def _part_of_day(text: str) -> str | None:
    """A usual time for "in the morning / afternoon / evening" when she gives no clock time."""
    for rx, t in ((r"morning|subah|सुबह|savere", "07:30"), (r"afternoon|dopahar|दोपहर", "15:00"),
                  (r"evening|shaam|sham|शाम", "18:00"), (r"today|aaj|आज", "11:00")):
        if re.search(rx, text):
            return t
    return None


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,.;।\n]|\band\b|\baur\b|\bphir\b|\bthen\b|और|फिर", text) if c.strip()]


def rules(text: str) -> dict:
    """Rule-based extraction. Returns only the fields it is sure about."""
    t = (text or "").strip()
    low = t.lower()
    out: dict = {}
    for clause in _clauses(t):
        cl = clause.lower()
        times = parse_times(clause)
        for slot, cue in CUES.items():
            if re.search(cue, cl) and times and slot not in out:
                out[slot] = times[0][1]
        if re.search(WALK, cl):
            out["walk"] = times[0][1] if times else ("morning" if re.search(r"morning|subah|सुबह", cl) else
                                                     "evening" if re.search(r"evening|shaam|sham|शाम", cl) else out.get("walk"))
        for title, title_hi, rx in ACTIVITIES:
            if re.search(rx, cl) and not any(re.search(c, cl) for c in CUES.values()):
                when = times[0][1] if times else _part_of_day(cl)
                if when:
                    out.setdefault("activities", []).append({"time": when, "title": title, "title_hi": title_hi})
    hours = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|बजे)?\s*(?:se|to|-|–|से)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|बजे)?", low)
    for kind, rx in WORK:
        if re.search(rx, low):
            out["work"] = kind
            break
    if hours and out.get("work"):
        start_h, end_h = int(hours.group(1)), int(hours.group(4))
        if end_h <= start_h and end_h < 12:
            end_h += 12
        out["work_start"], out["work_end"] = _hhmm(start_h, int(hours.group(2) or 0)), _hhmm(end_h % 24, int(hours.group(5) or 0))
    for kind, rx in DIET:
        if re.search(rx, low):
            out["diet"] = "veg" if kind == "veg" and re.search(r"no egg|anda nahi", low) else kind
            break
    feelings = [k for k, rx in FEELINGS if re.search(rx, low)]
    if feelings:
        out["feeling"] = [f for f in feelings if f != "good"] or ["good"]
    if re.search(r"\bnap\b|dopahar (mein |me )?(so|aaram)|afternoon (rest|nap)|दोपहर में आराम", low):
        out["nap"] = True
    if re.search(SAME, low):
        out["same_as_usual"] = True
    if re.search(MAKE, low):
        out["make_plan"] = True
    elif re.search(NOTHING, low):
        out["nothing"] = True
    return out


def edits(text: str) -> list[dict]:
    """Plan edits by rules: add / remove / move an item ("add temple at 6", "walk ko 7 baje kar do", "remove nap")."""
    low = (text or "").lower()
    times = parse_times(text or "")
    t = times[-1][1] if times else None
    item = re.sub(r"\b(please|add|remove|delete|move|change|shift|at|to|ko|kar do|karo|baje|बजे|am|pm|my|the|plan|from|se)\b", " ", low)
    item = re.sub(r"[\d:.]+", " ", item)
    item = " ".join(w for w in item.split() if len(w) > 1)[:60]
    if re.search(EDIT_REMOVE, low):
        return [{"op": "remove", "title": item}]
    if re.search(EDIT_ADD, low) and t:
        return [{"op": "add", "title": item or "Activity", "time": t}]
    if t and (re.search(EDIT_MOVE, low) or len(times) == 1):
        return [{"op": "move", "title": item, "time": t}]
    return []


LLM_SYSTEM = """You read what a pregnant woman in India says about her day (English, Hindi or Hinglish) and return ONLY a JSON object.
Fields (omit what she did not say; times as 24-hour "HH:MM"):
{"wake": "HH:MM", "sleep": "HH:MM", "breakfast": "HH:MM", "lunch": "HH:MM", "dinner": "HH:MM",
 "work": "home|farm|office|none", "work_start": "HH:MM", "work_end": "HH:MM", "diet": "veg|egg|nonveg",
 "walk": "morning|evening|HH:MM", "nap": true,
 "feeling": ["good","tired","nausea","back_pain","swelling","low"],
 "activities": [{"time": "HH:MM", "title": "short English title"}],
 "edits": [{"op": "add|remove|move", "title": "item", "time": "HH:MM"}],
 "make_plan": true, "same_as_usual": true}
"edits" only if she asks to change an existing plan. "make_plan" if she says to go ahead / make the plan / that's all.
No text outside the JSON."""

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FEELING_KEYS = {"good", "tired", "nausea", "back_pain", "swelling", "low"}


def _valid(data: dict) -> dict:
    out: dict = {}
    for k in ("wake", "sleep", "breakfast", "lunch", "dinner", "work_start", "work_end"):
        if isinstance(data.get(k), str) and _HHMM.match(data[k]):
            out[k] = data[k]
    if data.get("work") in ("home", "farm", "office", "none"):
        out["work"] = data["work"]
    if data.get("diet") in ("veg", "egg", "nonveg"):
        out["diet"] = data["diet"]
    w = data.get("walk")
    if isinstance(w, str) and (w in ("morning", "evening") or _HHMM.match(w)):
        out["walk"] = w
    for k in ("nap", "make_plan", "same_as_usual"):
        if data.get(k) is True:
            out[k] = True
    if isinstance(data.get("feeling"), list):
        f = [x for x in data["feeling"] if x in FEELING_KEYS]
        if f:
            out["feeling"] = f
    acts = [a for a in data.get("activities") or [] if isinstance(a, dict) and isinstance(a.get("title"), str)
            and isinstance(a.get("time"), str) and _HHMM.match(a["time"])]
    if acts:
        out["activities"] = [{"time": a["time"], "title": a["title"][:60], "title_hi": a["title"][:60]} for a in acts[:6]]
    eds = [e for e in data.get("edits") or [] if isinstance(e, dict) and e.get("op") in ("add", "remove", "move")
           and isinstance(e.get("title"), str) and (e["op"] == "remove" or (isinstance(e.get("time"), str) and _HHMM.match(e["time"])))]
    if eds:
        out["edits"] = [{"op": e["op"], "title": e["title"][:60], **({"time": e["time"]} if e.get("time") else {})} for e in eds[:5]]
    return out


def with_model(text: str, context: str, llm) -> dict:
    """The language model's reading of the message, validated; {} if no model or no valid answer."""
    if not llm or not llm.available:
        return {}
    from app.ai.llm import ChatMessage

    raw = llm.generate([ChatMessage("system", LLM_SYSTEM), ChatMessage("user", f"{context}\nShe said: {text}")],
                       temperature=0, max_tokens=400)
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return _valid(json.loads(m.group(0))) if m else {}
    except (ValueError, TypeError):
        log.info("Companion model returned non-JSON; using the rules only")
        return {}


def understand(text: str, *, plan_exists: bool, context: str = "", llm=None) -> dict:
    """Merged reading of one message: rules first, the model fills gaps and adds what the rules missed."""
    found = rules(text)
    if plan_exists:
        e = edits(text)
        if e:
            found["edits"] = e
    model = with_model(text, context, llm)
    for k, v in model.items():
        if k == "activities":
            have = {(a["time"], a["title"].lower()) for a in found.get("activities", [])}
            found["activities"] = found.get("activities", []) + [a for a in v if (a["time"], a["title"].lower()) not in have]
        elif k == "edits" and not plan_exists:
            continue
        elif k not in found or k in ("edits", "feeling"):
            found[k] = v
    return found

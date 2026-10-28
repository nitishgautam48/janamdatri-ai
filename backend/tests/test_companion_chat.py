"""'Plan My Day' by conversation: the companion asks, understands and builds / edits the plan around her day."""
from __future__ import annotations

import uuid

import pytest

from app.companion.understand import parse_times, rules
from tests.conftest import auth, create_passport, signup_mother

API = "/api/v1"


@pytest.fixture(scope="module")
def meena(client):
    token, _ = signup_mother(client, name="Meena Devi")
    create_passport(client, token, week=30)
    return auth(token)


def _say(client, h, text, **kw):
    r = client.post(f"{API}/companion/chat", json={"message": text, **kw}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _task(plan, tid):
    return next((t for t in plan["tasks"] if t["id"] == tid), None)


def test_understanding_layer_reads_hinglish_times():
    assert [t for _, t in parse_times("subah 6 baje aur raat 10 baje")] == ["06:00", "22:00"]
    assert [t for _, t in parse_times("2 rotis in week 24")] == []
    r = rules("Main subah 5:30 baje uthti hoon, office 9 se 5, I eat eggs, feeling tired and some back pain")
    assert r["wake"] == "05:30" and r["work"] == "office" and r["work_start"] == "09:00" and r["work_end"] == "17:00"
    assert r["diet"] == "egg" and set(r["feeling"]) == {"tired", "back_pain"}
    assert rules("haan bana do")["make_plan"] and rules("nothing special") == {"nothing": True}


def test_conversation_builds_her_plan(client, meena):
    start = client.get(f"{API}/companion/chat", headers=meena).json()
    assert start["stage"] == "asking" and start["asking"] == "routine" and "wake up" in start["greeting"]
    assert start["quick_replies"] and start["plan"]["tasks"]

    r = _say(client, meena, "I wake up at 5:30 am and sleep by 9:30 pm")
    assert r["asking"] == "work" and "5:30 AM" in r["understood"]
    r = _say(client, meena, "Farm work 7 to 11")
    assert r["asking"] == "food"
    r = _say(client, meena, "Vegetarian, lunch at 1:30 pm")
    assert r["asking"] == "today"
    r = _say(client, meena, "Temple in the evening")
    assert r["asking"] == "feeling"
    r = _say(client, meena, "a bit tired today")
    assert r["stage"] == "planned" and r["generated_by"] == "chat"
    plan = r["plan"]
    assert _task(plan, "wake")["time"] == "05:30"
    assert _task(plan, "goal-sleep")["time"] == "21:30"
    assert _task(plan, "goal-iron")["time"] == "14:00"  # 30 min after her lunch
    assert _task(plan, "work")["title"].startswith("Farm work")
    assert _task(plan, "rest") and _task(plan, "act-0")["title"] == "Temple / puja"
    assert "10 min" in _task(plan, "goal-walk")["detail"]  # tired → gentle walk
    assert not any("egg" in m["food"] for m in plan["meals"])
    assert plan["generated_by"] == "chat"

    # The conversation is saved and resumes as "planned"
    again = client.get(f"{API}/companion/chat", headers=meena).json()
    assert again["stage"] == "planned" and len(again["messages"]) == 10


def test_she_can_change_the_plan_by_chat_and_by_tap(client, meena):
    r = _say(client, meena, "walk ko 6 baje shaam kar do")
    assert _task(r["plan"], "goal-walk")["time"] == "18:00" and "moved" in r["reply"]
    r = _say(client, meena, "add sewing class at 4 pm")
    mine = [t for t in r["plan"]["tasks"] if t["id"].startswith("my-")]
    assert mine and mine[0]["time"] == "16:00" and "Sewing class" in mine[0]["title"]
    r = _say(client, meena, "remove kegel")
    assert not _task(r["plan"], "kegel")
    r = _say(client, meena, "remove iron tablet")
    assert _task(r["plan"], "goal-iron")  # tablets are never dropped by chat

    h = meena
    tid = mine[0]["id"]
    p = client.put(f"{API}/companion/today/tasks/{tid}", json={"time": "16:30", "title": "Sewing"}, headers=h).json()
    assert _task(p, tid)["time"] == "16:30" and _task(p, tid)["title"] == "Sewing"
    p = client.post(f"{API}/companion/today/tasks", json={"time": "12:00", "title": "Call Maa"}, headers=h)
    assert p.status_code == 201
    new = next(t for t in p.json()["tasks"] if t["title"] == "Call Maa")
    assert client.delete(f"{API}/companion/today/tasks/{new['id']}", headers=h).status_code == 200
    assert client.delete(f"{API}/companion/today/tasks/goal-iron", headers=h).status_code == 422

    # "Plan My Day" again keeps the day she described
    regen = client.post(f"{API}/companion/plan", json={}, headers=h).json()
    assert _task(regen, "wake")["time"] == "05:30"


def test_danger_signs_in_the_chat_come_first(client):
    token, _ = signup_mother(client, name="Rani")
    create_passport(client, token, week=32)
    h = auth(token)
    r = _say(client, h, "I have bleeding since morning")
    assert r["safety"]["emergency"] and r["reply"].startswith("This could be a danger sign")
    assert r["actions"][0]["type"] == "sos"


def test_next_day_offers_same_as_usual(client):
    from datetime import date, timedelta

    from app.companion import dayplan
    from app.database.models import Mother
    from app.database.session import SessionLocal

    token, _ = signup_mother(client, name="Kavita")
    mother_id = create_passport(client, token, week=20)["mother_id"]
    h = auth(token)
    _say(client, h, "6 am to 10 pm")
    _say(client, h, "make my plan")
    with SessionLocal() as db:
        m = db.get(Mother, uuid.UUID(str(mother_id)))
        tomorrow = date.today() + timedelta(days=1)
        st = dayplan.chat_state(db, m, tomorrow)
        assert st["has_usual"] and st["asking"] == "usual" and "same as usual" in st["greeting"]
        out = dayplan.chat(db, m, tomorrow, "same as usual", "en")
        assert out["asking"] == "today"  # routine, work and food are already known
        out = dayplan.chat(db, m, tomorrow, "nothing special", "en")
        out = dayplan.chat(db, m, tomorrow, "feeling good", "en")
        assert out["stage"] == "planned" and _task(out["plan"], "wake")["time"] == "06:00"


def test_guests_cannot_plan(client):
    assert client.post(f"{API}/companion/chat", json={"message": "hi"}).status_code in (401, 403)


def test_the_model_reads_free_form_answers(client):
    from app.ai import llm as llm_mod

    class Fake:
        available = True
        model = "fake"

        def generate(self, messages, **kw):
            if "return ONLY a JSON object" in messages[0].content:
                return 'Sure: {"wake": "05:30", "sleep": "22:00", "walk": "evening", "bogus": 1, "lunch": "25:99"}'
            return "Lovely, an early start! What does your day look like: housework, farm or a job?"

    llm_mod.set_llm(Fake())
    try:
        token, _ = signup_mother(client, name="Pooja")
        create_passport(client, token, week=22)
        r = _say(client, auth(token), "I usually get going around half past five and crash by ten, love an evening stroll")
        assert r["generated_by"] == "chat+ai" and r["asking"] == "work"
        assert r["reply"].startswith("Lovely") and "5:30 AM" in r["understood"] and "25:99" not in r["understood"]
    finally:
        llm_mod._client = None

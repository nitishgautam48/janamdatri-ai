"""Ask the Pregnancy Guide: answers pregnancy questions for her week, politely declines anything else."""
from __future__ import annotations

from app.ai import llm as llm_mod
from tests.conftest import auth, create_passport, signup_mother

API = "/api/v1/guide/ask"


def test_guest_can_ask_a_pregnancy_question(client):
    r = client.post(API, json={"question": "Can I eat papaya?", "week": 12})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["in_scope"] and j["answer"] and j["sources"] and j["suggestions"]
    assert j["generated_by"] == "knowledge_base" and j["week"] == 12


def test_off_topic_questions_are_declined(client):
    for q in ("Who won the cricket match yesterday?", "Write python code for a calculator", "What is the capital of France?"):
        j = client.post(API, json={"question": q, "week": 20}).json()
        assert not j["in_scope"] and j["generated_by"] == "scope_guard" and "pregnancy" in j["answer"]
    hi = client.post(API, json={"question": "आज का मौसम कैसा है? mausam batao", "week": 20, "language": "hi"}).json()
    assert not hi["in_scope"] and "गर्भावस्था" in hi["answer"]


def test_danger_signs_are_always_answered(client):
    j = client.post(API, json={"question": "I have bleeding and severe headache", "week": 30}).json()
    assert j["in_scope"] and j["safety"]["emergency"] and "108" in j["answer"]


def test_model_classifies_unclear_questions_and_answers_for_her_week(client):
    calls = []

    class Fake:
        available = True
        model = "fake"

        def generate(self, messages, **kw):
            calls.append(messages)
            if "YES or NO" in messages[0].content:
                return "NO" if "lottery" in messages[-1].content or "neighbour" in messages[-1].content else "YES"
            return "At week 28 your baby can open its eyes. Rest on your left side."

    llm_mod.set_llm(Fake())
    try:
        token, _ = signup_mother(client, name="Asha Rani")
        create_passport(client, token, week=28, name="Asha Rani")
        h = auth(token)
        j = client.post(API, json={"question": "My neighbour's car is broken, what to do?"}, headers=h).json()
        assert not j["in_scope"] and j["scope_check"] == "ai"
        j = client.post(API, json={"question": "And is it normal to feel hot all the time?",
                                   "history": [{"role": "user", "content": "Can I eat mango?"},
                                               {"role": "assistant", "content": "Yes, in moderation."}]}, headers=h).json()
        assert j["in_scope"] and j["generated_by"] == "ai+retrieval" and j["week"] == 28
        system = calls[-1][0].content
        assert "week 28" in system and "Asha" in system
        assert [m.role for m in calls[-1][1:]] == ["user", "assistant", "user"]
    finally:
        llm_mod._client = None


def test_greeting(client):
    j = client.post(API, json={"question": "namaste", "week": 16}).json()
    assert j["in_scope"] and "16" in j["answer"]

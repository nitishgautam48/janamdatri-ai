"""Mother ↔ ASHA chat works both ways; the ASHA inbox lists every mother she looks after."""
from __future__ import annotations

from tests.conftest import asha_login, auth, create_passport, signup_mother

API = "/api/v1"


def test_mother_writes_asha_reads_and_replies(client):
    token, _ = signup_mother(client, name="Chat Mother")
    mid = create_passport(client, token, week=22, name="Chat Mother")["mother_id"]  # Rampur → Sunita
    m = auth(token)
    asha = auth(asha_login(client))
    inbox = client.get(f"{API}/messages/conversations", headers=asha).json()["conversations"]
    row = next(c for c in inbox if c["mother_id"] == mid)
    assert row["last_message"] is None and row["unread"] == 0 and row["week"] == 22  # listed before any message

    assert client.post(f"{API}/messages/send", json={"body": "Didi, pair mein sujan hai"}, headers=m).status_code == 201
    inbox = client.get(f"{API}/messages/conversations", headers=asha).json()
    assert inbox["conversations"][0]["mother_id"] == mid and inbox["conversations"][0]["unread"] == 1 and inbox["unread_total"] >= 1

    hist = client.get(f"{API}/messages/history?mother_id={mid}", headers=asha).json()
    assert hist["messages"][-1]["body"] == "Didi, pair mein sujan hai" and hist["messages"][-1]["from"] == "mother"
    r = client.post(f"{API}/messages/send", json={"mother_id": mid, "body": "Main aaj shaam aaungi, pair upar rakhiye"}, headers=asha)
    assert r.status_code == 201
    assert client.get(f"{API}/messages/conversations", headers=asha).json()["conversations"][0]["unread"] == 0  # read now
    mine = client.get(f"{API}/messages/history", headers=m).json()["messages"]
    assert mine[-1]["body"].startswith("Main aaj shaam") and mine[-1]["from"] == "asha"


def test_mother_without_asha_gets_a_clear_message(client):
    token, _ = signup_mother(client, name="Far Away")
    client.post(f"{API}/passport/create", headers=auth(token), json={
        "about": {"name": "Far Away", "age": 25, "village": "Leh", "pincode": "194101", "language": "en", "latitude": 34.15, "longitude": 77.58},
        "pregnancy": {"first_pregnancy": True}})
    r = client.get(f"{API}/messages/history", headers=auth(token))
    assert r.status_code == 404 and r.json()["error"]["code"] == "asha_not_assigned" and "automatically" in r.json()["error"]["message"]

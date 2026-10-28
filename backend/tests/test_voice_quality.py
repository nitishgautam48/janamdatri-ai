"""Speech-to-text never puts words in her mouth: language is detected, phantom phrases dropped, unsure text checked."""
from __future__ import annotations

import pytest

from app.ai import whisper
from app.core.config import settings
from tests.conftest import auth, create_passport, signup_mother
from tests.test_cloud import Fake


@pytest.fixture
def groq(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "whisper_provider", "auto")
    monkeypatch.setattr(settings, "groq_api_key", "q-test")
    monkeypatch.setattr(settings, "azure_speech_key", "")
    audio = tmp_path / "v.webm"
    audio.write_bytes(b"\x1aE\xdf\xa3fake")

    def use(*answers):
        queue = list(answers)
        def answer(kw):
            a = queue.pop(0) if len(queue) > 1 else queue[0]
            return a if isinstance(a, tuple) else (200, a)
        fake = Fake({"audio/transcriptions": answer})
        monkeypatch.setattr(whisper.httpx, "Client", fake)
        return fake
    return audio, use


def seg(text, no_speech=0.05, logprob=-0.2, ratio=1.2):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob, "compression_ratio": ratio}


def test_language_is_detected_not_forced_and_a_hint_is_sent(groq):
    audio, use = groq
    fake = use({"text": "main subah 6 baje uthti hoon", "language": "hindi", "segments": [seg("main subah 6 baje uthti hoon")]})
    t = whisper.WhisperService().transcribe(audio, "en")  # app in English, she speaks Hindi
    assert t.text == "main subah 6 baje uthti hoon" and t.language == "hi" and t.confident
    data = fake.calls[0]["data"]
    assert "language" not in data and "iron tablet" in data["prompt"] and data["model"] == "whisper-large-v3"


def test_hindi_heard_as_urdu_is_retried_as_hindi(groq):
    audio, use = groq
    fake = use({"text": "میں صبح", "language": "urdu", "segments": [seg("میں صبح")]},
               {"text": "मैं सुबह 6 बजे उठती हूँ", "language": "hindi", "segments": [seg("मैं सुबह 6 बजे उठती हूँ")]})
    t = whisper.WhisperService().transcribe(audio, "hi")
    assert t.text == "मैं सुबह 6 बजे उठती हूँ" and fake.calls[1]["data"]["language"] == "hi"


def test_silence_and_phantom_phrases_give_no_text(groq):
    audio, use = groq
    use({"text": "Thank you for watching.", "language": "english", "segments": [seg("Thank you for watching.")]})
    assert whisper.WhisperService().transcribe(audio, "en").text == ""
    use({"text": "I'm not going to stop outside.", "language": "english",
         "segments": [seg("I'm not going to stop outside.", no_speech=0.8, logprob=-0.9)]})
    assert whisper.WhisperService().transcribe(audio, "en").text == ""


def test_unsure_text_is_marked_and_the_assistant_asks_her_to_check(client, groq):
    audio, use = groq
    use({"text": "pet mein dard", "language": "hindi", "segments": [seg("pet mein dard", logprob=-1.3)]})
    t = whisper.WhisperService().transcribe(audio, "hi")
    assert t.text == "pet mein dard" and not t.confident
    token, _ = signup_mother(client, name="Voice Check")
    create_passport(client, token, week=20)
    r = client.post("/api/v1/voice/transcribe", headers=auth(token), files={"file": ("a.webm", b"\x1aE\xdf\xa3x", "audio/webm")})
    assert r.status_code == 200 and r.json()["confident"] is False
    r = client.post("/api/v1/assistant/voice", headers=auth(token), files={"file": ("a.webm", b"\x1aE\xdf\xa3x", "audio/webm")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unclear_speech"
    assert r.json()["error"]["details"]["text"] == "pet mein dard"


def test_a_repeated_hint_is_heard_again_without_it(groq):
    audio, use = groq
    echo = "गर्भावस्था, आशा दीदी, डॉक्टर, दर्द"
    fake = use({"text": echo, "language": "hindi", "segments": [seg(echo)]},
               {"text": "मुझे सिर में दर्द है", "language": "hindi", "segments": [seg("मुझे सिर में दर्द है")]})
    t = whisper.WhisperService().transcribe(audio, "hi")
    assert t.text == "मुझे सिर में दर्द है"
    assert "prompt" in fake.calls[0]["data"] and "prompt" not in fake.calls[1]["data"]


def test_a_name_gets_no_hint_and_loses_my_name_is(groq):
    audio, use = groq
    fake = use({"text": "My name is Kavita Meena.", "language": "english", "segments": [seg("My name is Kavita Meena.")]})
    t = whisper.WhisperService().transcribe(audio, "en", context="name")
    assert t.text == "Kavita Meena" and "prompt" not in fake.calls[0]["data"]
    use({"text": "मेरा नाम सुनीता देवी है।", "language": "hindi", "segments": [seg("मेरा नाम सुनीता देवी है।")]})
    assert whisper.WhisperService().transcribe(audio, "hi", context="name").text == "सुनीता देवी"


def test_busy_accurate_model_falls_back_to_turbo(groq):
    audio, use = groq
    fake = use((429, {"error": {"message": "rate limit"}}),
               {"text": "kamar mein dard", "language": "hindi", "segments": [seg("kamar mein dard")]})
    t = whisper.WhisperService().transcribe(audio, "hi")
    assert t.text == "kamar mein dard" and t.engine == "groq-whisper-large-v3-turbo"
    assert [c["data"]["model"] for c in fake.calls] == ["whisper-large-v3", "whisper-large-v3-turbo"]


def test_unsure_in_the_other_language_tries_the_apps_language(groq):
    audio, use = groq
    fake = use({"text": "Petme dart hai", "language": "english", "segments": [seg("Petme dart hai", logprob=-1.2)]},
               {"text": "पेट में दर्द है", "language": "hindi", "segments": [seg("पेट में दर्द है", logprob=-0.3)]})
    t = whisper.WhisperService().transcribe(audio, "hi")
    assert t.text == "पेट में दर्द है" and t.language == "hi" and t.confident
    assert fake.calls[1]["data"]["language"] == "hi"


def test_transcribe_api_takes_a_context(client, groq):
    audio, use = groq
    fake = use({"text": "Rekha Yadav", "language": "english", "segments": [seg("Rekha Yadav")]})
    token, _ = signup_mother(client, name="Ctx Check")
    r = client.post("/api/v1/voice/transcribe", headers=auth(token), data={"context": "name"},
                    files={"file": ("a.webm", b"\x1aE\xdf\xa3x", "audio/webm")})
    assert r.status_code == 200 and r.json()["text"] == "Rekha Yadav" and "prompt" not in fake.calls[-1]["data"]

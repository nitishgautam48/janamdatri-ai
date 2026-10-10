"""Unit tests: assessment engine, risk model guard-rails, OCR extraction/validation, safety, sentiment."""
from __future__ import annotations

import json
import re

from app.ai import safety
from app.ai.risk_model import get_risk_model
from app.ai.sentiment import analyze, concern_level
from app.assessment.engine import EngineInput, evaluate
from app.core.enums import CareLevel
from app.ocr.extractor import extract, report_date
from app.ocr.validator import validate

PCT = re.compile(r"\d\s*%|percent|probabilit|confidence", re.I)


def level(**kw) -> CareLevel:
    return evaluate(EngineInput(**kw), model=get_risk_model()).level


def test_design_scenarios():
    assert level(week=24, age=23, bp_systolic=112, bp_diastolic=72, hemoglobin=11.6, taking_ifa=True, anc_visits_completed=1) == CareLevel.ROUTINE_MONITORING
    assert level(week=24, age=23, bp_systolic=116, bp_diastolic=74, hemoglobin=10.4, taking_ifa=True, anc_visits_completed=1) == CareLevel.NEEDS_ATTENTION
    assert level(week=24, age=23, bp_systolic=118, bp_diastolic=76, hemoglobin=9.8, symptoms=["dizziness", "swelling_feet"],
                 taking_ifa=True, anc_visits_completed=1) == CareLevel.MEDICAL_REVIEW
    assert level(week=34, age=26, bp_systolic=160, bp_diastolic=110, symptoms=["severe_headache", "blurred_vision"],
                 anc_visits_completed=2) == CareLevel.IMMEDIATE_CARE


def test_danger_ladder_and_worst_signal_wins():
    assert level(week=20, hemoglobin=12.5, bleeding_this_week=True) == CareLevel.IMMEDIATE_CARE
    assert level(week=30, baby_moving_normally=False) == CareLevel.IMMEDIATE_CARE
    assert level(week=22, baby_moving_normally=False) == CareLevel.MEDICAL_REVIEW
    assert level(week=24, hemoglobin=6.5) == CareLevel.IMMEDIATE_CARE
    assert level(week=26, sugar_ogtt_2h=150) == CareLevel.MEDICAL_REVIEW
    assert level(week=26, bp_systolic=145, bp_diastolic=92, symptoms=["blurred_vision"]) == CareLevel.IMMEDIATE_CARE


def test_epds_rules():
    assert level(week=20, epds=[1] * 8 + [2, 0]) == CareLevel.NEEDS_ATTENTION  # 10, no self-harm
    assert level(week=20, epds=[2] * 9 + [0]) == CareLevel.MEDICAL_REVIEW  # 18, no self-harm
    assert level(week=20, epds=[0] * 9 + [1]) == CareLevel.IMMEDIATE_CARE  # item 10 > 0


def test_history_engine():
    assert level(week=30, conditions=["hypertension"], bp_systolic=118, bp_diastolic=76, anc_visits_completed=2) >= CareLevel.NEEDS_ATTENTION
    assert level(week=20, conditions=["heart_disease"], anc_visits_completed=1) == CareLevel.MEDICAL_REVIEW
    r = evaluate(EngineInput(week=24, hemoglobin=10.2, anc_visits_completed=1,
                             hb_history=[(__import__("datetime").date(2026, 7, 1), 11.6), (__import__("datetime").date(2026, 9, 1), 10.2)]))
    assert any(s.code == "hb_falling" for s in r.signals)


class _AlwaysImmediate:
    def predict_level(self, _features):
        return CareLevel.IMMEDIATE_CARE


def test_model_can_never_trigger_emergency_or_jump_two_levels():
    r = evaluate(EngineInput(week=20, bp_systolic=112, bp_diastolic=70, hemoglobin=12, anc_visits_completed=1),
                 model=_AlwaysImmediate())
    assert r.level == CareLevel.NEEDS_ATTENTION  # lifted one step only, never to immediate care
    r = evaluate(EngineInput(week=24, hemoglobin=10.5, anc_visits_completed=1), model=_AlwaysImmediate())
    assert r.level == CareLevel.MEDICAL_REVIEW


def test_outputs_never_contain_percentages():
    r = evaluate(EngineInput(week=34, bp_systolic=165, bp_diastolic=112, hemoglobin=8.1, symptoms=["severe_headache"],
                             epds=[2] * 10), model=get_risk_model())
    blob = json.dumps([r.noticed, r.concerns, r.recommendations, [s.as_dict() for s in r.signals]], ensure_ascii=False)
    assert not PCT.search(blob)


def test_ocr_extraction_and_validation():
    text = """CBC Report 07/10/2026
    Haemoglobin: 9.8 g/dL
    TLC 8,200 /cumm
    Platelets 240000 /cumm
    BP: 118/76 mmHg
    Fasting Blood Sugar: 96 mg/dl
    Urine Albumin: ++
    FHR 142 bpm  Placenta: posterior, not low lying. Cephalic presentation.
    Diagnosis: Moderate anaemia, GDM
    Rx: Tab Ferrous Ascorbate 100 mg 1-0-1 ; Tab Shelcal 500 mg HS ; Tab Metformin 500 mg BD
    Advice: repeat CBC after 4 weeks"""
    items = {(e.kind, e.code): e for e in extract(text)}
    assert items[("lab_value", "hemoglobin")].value_num == 9.8
    assert items[("lab_value", "wbc")].value_num == 8200
    assert items[("lab_value", "platelets")].value_num == 2.4
    assert items[("vital", "bp")].value_num2 == 76
    assert items[("lab_value", "blood_sugar_fasting")].value_num == 96
    assert items[("lab_value", "urine_protein")].value_text == "2+"
    assert ("medicine", "ifa") in items and ("medicine", "calcium") in items and ("medicine", "metformin") in items
    assert items[("medicine", "ifa")].extra["frequency"] == "twice a day"
    assert ("diagnosis", "anemia") in items and ("diagnosis", "gdm") in items
    assert ("recommendation", "advice") in items
    assert report_date(text).isoformat() == "2026-10-07"
    assert validate(items[("lab_value", "hemoglobin")]).flag == "low"
    assert validate(items[("lab_value", "blood_sugar_fasting")]).flag == "high"
    assert validate(items[("lab_value", "platelets")]).flag == "normal"
    assert validate(items[("lab_value", "urine_protein")]).flag == "critical"


def test_ocr_rejects_implausible_values():
    hb = next(e for e in extract("Hb 98 g/dL") if e.code == "hemoglobin")
    assert validate(hb).valid is False


def test_safety_layer():
    c = safety.check_message("I am bleeding a lot since morning")
    assert c.emergency and "heavy_bleeding" in c.flags
    assert safety.check_message("मुझे खून आ रहा है").emergency
    assert safety.check_message("bachcha hil nahi raha").emergency
    assert safety.check_message("I don't want to live anymore").self_harm
    out = safety.apply("There is a 89% chance of anaemia. The probability is high. Take 100 mg iron.", c)
    assert "%" not in out and "probability" not in out and "108" in out and "dose your doctor prescribed" in out
    assert "Take 100 mg iron." in out


def test_sentiment_signals():
    a = analyze("Today was difficult. I feel lonely and nobody understands me")
    assert a.signals["isolation"] >= 2 and a.sentiment < 0
    assert concern_level({"sadness": 1}, -0.5, False) == "mild"
    assert concern_level({}, 0, True) == "high"
    assert analyze("aaj main bahut khush hoon, accha din tha").sentiment > 0



def test_sms_goes_to_mailpit(monkeypatch):
    import smtplib

    from app.core.config import settings
    from app.notifications import sms

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append({"host": host, "port": port})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, msg):
            sent[-1]["msg"] = msg

    monkeypatch.setattr(settings, "sms_channel", "mailpit")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    assert sms.send_sms("+919876544210", "123456 is your Janamdatri-AI verification code. Do not share it.", critical=True)
    msg = sent[-1]["msg"]
    assert (sent[-1]["host"], sent[-1]["port"]) == (settings.smtp_host, settings.smtp_port)
    assert msg["To"] == "+919876544210 <919876544210@sms.janamdatri.local>"
    assert "123456" in msg["Subject"] and "123456" in msg.get_content()

    def refuse(*a, **k):
        raise ConnectionRefusedError("mailpit not running")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    assert sms.send_sms("+919876544210", "alert") is False  # alerts never crash the request
    import pytest

    with pytest.raises(sms.SMSDeliveryError):
        sms.send_sms("+919876544210", "otp", critical=True)

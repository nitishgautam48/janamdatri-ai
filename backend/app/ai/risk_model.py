"""Gradient-boosting risk model.

A ``HistGradientBoostingClassifier`` (native missing-value support, so skipped vitals
are fine) that maps vitals, symptoms, history and EPDS to one of the four care levels.

It is one *signal* in the assessment engine, never the decision on its own:
- it outputs only a care level (no probability ever leaves this module);
- its signal is capped at Medical Review: "Immediate Care" can only come from an explicit
  clinical danger rule, so a statistical model can never trigger an emergency by itself,
  nor hide one (the worst signal wins).

Out of the box it is trained on synthetic cohorts generated from clinical guidance
(WHO/MoHFW thresholds plus combination effects). Replace with a model trained on real,
de-identified programme data via ``train(X, y)`` and ``save()`` when available.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.core.enums import CareLevel

log = logging.getLogger(__name__)

FEATURES = [
    "age", "week", "sbp", "dbp", "hb", "sugar_fasting", "sugar_random", "epds",
    "headache", "blurred_vision", "swelling", "bleeding", "reduced_movement", "dizziness", "fever",
    "breathlessness", "convulsions", "abdominal_pain", "tiredness",
    "hx_hypertension", "hx_diabetes", "hx_complications", "hx_anemia", "first_pregnancy", "no_ifa",
]
SYMPTOM_FEATURES = FEATURES[8:19]
MAX_MODEL_LEVEL = CareLevel.MEDICAL_REVIEW


def _teacher(r: dict) -> int:
    """Clinical labelling function used to synthesise training data."""
    lvl, minor = 0, 0
    sbp, dbp, hb = r["sbp"], r["dbp"], r["hb"]
    if not np.isnan(sbp):
        if sbp >= 160 or dbp >= 110:
            lvl = max(lvl, 3)
        elif sbp >= 140 or dbp >= 90:
            lvl = max(lvl, 2)
        elif sbp >= 130 or dbp >= 85:
            minor += 1
    if not np.isnan(hb):
        if hb < 7:
            lvl = max(lvl, 3)
        elif hb < 10:
            lvl = max(lvl, 2)
        elif hb < 11:
            lvl = max(lvl, 1)
            minor += 1
    if r["bleeding"] or r["convulsions"] or r["breathlessness"]:
        lvl = 3
    if r["reduced_movement"] and r["week"] >= 28:
        lvl = 3
    if r["headache"] and r["blurred_vision"]:
        lvl = max(lvl, 3 if (not np.isnan(sbp) and sbp >= 140) else 2)
    if not np.isnan(r["sugar_fasting"]) and r["sugar_fasting"] >= 92:
        lvl = max(lvl, 2)
    if not np.isnan(r["sugar_random"]) and r["sugar_random"] >= 200:
        lvl = max(lvl, 2)
    if not np.isnan(r["epds"]):
        if r["epds"] >= 13:
            lvl = max(lvl, 2)
        elif r["epds"] >= 10:
            minor += 1
    minor += sum(int(r[k]) for k in ("headache", "swelling", "dizziness", "fever", "abdominal_pain", "tiredness"))
    minor += sum(int(r[k]) for k in ("hx_hypertension", "hx_diabetes", "hx_complications", "no_ifa"))
    if r["age"] < 18 or r["age"] > 35:
        minor += 1
    if minor >= 1:
        lvl = max(lvl, 1)
    if minor >= 3:  # several moderate signals together warrant a doctor's review
        lvl = max(lvl, 2)
    return lvl


def synth_cohort(n: int = 6000, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        r = {
            "age": float(rng.integers(16, 42)), "week": float(rng.integers(6, 41)),
            "sbp": float(rng.normal(118, 16)), "dbp": float(rng.normal(76, 11)),
            "hb": float(rng.normal(10.8, 1.5)), "sugar_fasting": float(rng.normal(84, 12)),
            "sugar_random": float(rng.normal(120, 30)), "epds": float(rng.integers(0, 22)),
        }
        r["dbp"] = min(r["dbp"], r["sbp"] - 20)
        for k in ("sbp", "hb", "sugar_fasting", "sugar_random", "epds"):
            if rng.random() < 0.25:
                r[k] = np.nan
        if np.isnan(r["sbp"]):
            r["dbp"] = np.nan
        for k in SYMPTOM_FEATURES:
            base = 0.03 if k in ("bleeding", "convulsions", "breathlessness", "reduced_movement", "blurred_vision") else 0.15
            r[k] = float(rng.random() < base)
        for k in ("hx_hypertension", "hx_diabetes", "hx_complications", "hx_anemia", "no_ifa"):
            r[k] = float(rng.random() < 0.1)
        r["first_pregnancy"] = float(rng.random() < 0.4)
        label = _teacher(r)
        if rng.random() < 0.03:  # label noise keeps the model from memorising thresholds exactly
            label = int(np.clip(label + rng.choice([-1, 1]), 0, 3))
        rows.append(([r[f] for f in FEATURES], label))
    X = np.array([x for x, _ in rows], dtype="float64")
    y = np.array([lbl for _, lbl in rows], dtype="int64")
    return X, y


class RiskModel:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.risk_model_path
        self._clf = None
        self._lock = threading.Lock()

    def train(self, X: np.ndarray | None = None, y: np.ndarray | None = None):
        from sklearn.ensemble import HistGradientBoostingClassifier

        if X is None or y is None:
            X, y = synth_cohort()
        clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, max_depth=5, random_state=7)
        clf.fit(X, y)
        self._clf = clf
        return clf

    def save(self) -> None:
        import joblib

        self.path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"features": FEATURES, "model": self._clf}, self.path)

    def _ensure(self):
        if self._clf is None:
            with self._lock:
                if self._clf is None:
                    try:
                        import joblib

                        if self.path.exists():
                            bundle = joblib.load(self.path)
                            if bundle.get("features") == FEATURES:
                                self._clf = bundle["model"]
                    except Exception as exc:  # pragma: no cover
                        log.warning("Could not load risk model (%s); retraining", exc)
                    if self._clf is None:
                        self.train()
                        try:
                            self.save()
                        except OSError:  # read-only filesystem: keep in memory
                            pass
        return self._clf

    def predict_level(self, features: dict[str, float | None]) -> CareLevel | None:
        """Return the model's care level (capped at Medical Review), or None if unavailable."""
        try:
            clf = self._ensure()
        except Exception as exc:  # pragma: no cover - sklearn missing
            log.warning("Risk model unavailable: %s", exc)
            return None
        row = np.array([[np.nan if features.get(f) is None else float(features[f]) for f in FEATURES]])
        level = int(clf.predict(row)[0])
        return CareLevel(min(level, int(MAX_MODEL_LEVEL)))


_model: RiskModel | None = None


def get_risk_model() -> RiskModel:
    global _model
    if _model is None:
        _model = RiskModel()
    return _model

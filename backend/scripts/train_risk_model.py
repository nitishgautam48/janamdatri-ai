"""Train (or retrain) the gradient-boosting risk model and save it to RISK_MODEL_PATH.

    python -m scripts.train_risk_model                 # synthetic clinical cohort
    python -m scripts.train_risk_model data.csv        # your de-identified data (FEATURES columns + 'level')
"""
from __future__ import annotations

import sys

import numpy as np

from app.ai.risk_model import FEATURES, RiskModel, synth_cohort


def main() -> None:
    if len(sys.argv) > 1:
        import csv

        with open(sys.argv[1], newline="") as fh:
            rows = list(csv.DictReader(fh))
        X = np.array([[float(r[f]) if r.get(f) not in (None, "") else np.nan for f in FEATURES] for r in rows])
        y = np.array([int(r["level"]) for r in rows])
    else:
        X, y = synth_cohort()
    m = RiskModel()
    clf = m.train(X, y)
    m.save()
    Xh, yh = synth_cohort(2000, seed=99)
    print(f"Saved {m.path} · holdout agreement with clinical rules: {(clf.predict(Xh) == yh).mean():.3f}")


if __name__ == "__main__":
    main()

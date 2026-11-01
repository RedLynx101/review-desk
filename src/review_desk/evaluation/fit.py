"""Fit confidence weights and pick the review threshold from scored documents with known answers.

This is plain logistic regression over the signal outcomes, pulled toward the hand-set weights so a
signal the data rarely shows keeps a sensible value. Only ever fit on the dev split. The held-out
split exists to test the result, and it stops being held out the moment it's used here.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..confidence import DEFAULT_WEIGHTS, FIELD_CLASS


def fit_weights(records: list[dict], *, prior: dict[str, float] = DEFAULT_WEIGHTS, shrinkage: float = 2.0, iterations: int = 40) -> dict[str, float]:
    """Newton's method on a penalized logistic loss. Returns the prior with fitted values replaced."""
    usable = [r for r in records if not r.get("missing_row") and r["field"] in FIELD_CLASS]

    def key(record: dict, signal: str) -> str:
        """A class-specific weight if the prior has one, else the signal's shared weight."""
        specific = f"{signal}@{FIELD_CLASS[record['field']]}"
        return specific if specific in prior else signal

    names = sorted({f"base.{FIELD_CLASS[r['field']]}" for r in usable} | {key(r, s) for r in usable for s in r["signals"]})
    if not names:
        return dict(prior)
    index = {name: i for i, name in enumerate(names)}
    features = np.zeros((len(usable), len(names)))
    labels = np.zeros(len(usable))
    for row, record in enumerate(usable):
        features[row, index[f"base.{FIELD_CLASS[record['field']]}"]] = 1.0
        for signal in record["signals"]:
            features[row, index[key(record, signal)]] += 1.0
        labels[row] = 1.0 if record["correct"] else 0.0

    center = np.array([prior.get(name, 0.0) for name in names])
    weights = center.copy()
    for _ in range(iterations):
        scores = features @ weights
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(scores, -30, 30)))
        gradient = features.T @ (probabilities - labels) + shrinkage * (weights - center)
        curvature = np.maximum(probabilities * (1 - probabilities), 1e-6)
        hessian = (features * curvature[:, None]).T @ features + shrinkage * np.eye(len(names))
        step = np.linalg.solve(hessian, gradient)
        weights = weights - step
        if np.max(np.abs(step)) < 1e-6:
            break
    fitted = dict(prior)
    fitted.update({name: round(float(weights[index[name]]), 3) for name in names})
    return fitted


def choose_threshold(records: list[dict], *, target_error: float = 0.01, candidates=None) -> dict:
    """The lowest threshold whose skipped values stay under the target error on this data."""
    candidates = candidates if candidates is not None else [round(0.5 + i * 0.005, 3) for i in range(100)]
    outcomes = []
    for threshold in candidates:
        passed = [r for r in records if r["confidence"] >= threshold]
        wrong = sum(not r["correct"] for r in passed)
        error = wrong / len(passed) if passed else 0.0
        outcomes.append({"threshold": threshold, "coverage": len(passed) / max(len(records), 1), "error": error, "skipped": len(passed), "wrong": wrong})
    for outcome in outcomes:
        if outcome["error"] <= target_error and outcome["skipped"]:
            return outcome
    return outcomes[-1]


def save_weights(path: Path, weights: dict[str, float], threshold: float, *, fitted_on: str, note: str) -> None:
    payload = {"weights": weights, "threshold": threshold, "fitted_on": fitted_on, "note": note}
    path.write_bytes((json.dumps(payload, indent=1, sort_keys=True) + "\n").encode("utf-8"))

from __future__ import annotations

import pytest

from review_desk.synth.content import make_document
from review_desk.synth.generate import DEFAULT_SEED, render_all


def _render(split: str, count: int, conditions):
    corpus = []
    for index in range(count):
        doc = make_document(split, index, DEFAULT_SEED)
        for condition, (pdf, truth) in render_all(doc, conditions).items():
            corpus.append((doc, condition, pdf, truth))
    return corpus


@pytest.fixture(scope="session")
def clean_corpus():
    """Clean renderings across both splits, enough to reach every layout several times."""
    return _render("dev", 45, ("clean",)) + _render("heldout", 45, ("clean",))


@pytest.fixture(scope="session")
def scan_corpus():
    """Every condition for a handful of documents. Scanning is slow, so keep this small."""
    return _render("dev", 8, ("scanned", "degraded"))

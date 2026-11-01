# Review Desk

Upload an invoice or receipt PDF and get checked data out, with no outside connections. Fields are
extracted against a strict schema, each with a confidence score built from checks you can inspect and
its source highlighted on the page. Low-confidence fields go to a review queue, nothing exports until a
person approves it, and a hash-chained audit log records what happened.

**Status: under construction.** Milestone 1 of 5 is done: the repo skeleton and the synthetic test
documents. The extractor, review screen and accuracy report don't exist yet, so this README makes no
claims about them. No client deployment yet.

Built with AI coding agents under Noah Hicks's review.

## Synthetic test documents

Accuracy needs known answers, and real invoices can't be published. `review_desk.synth` generates
invoices and receipts from a seed and writes each PDF beside a truth file listing every field's value and
where it's printed. **Everything it produces is synthetic**: invented companies and people, phone numbers
in the 555-01xx range, `.example` domains, a visible notice on every page, and "synthetic" in file names
and PDF metadata.

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt -e .   # .venv/bin/python on macOS and Linux
.venv/Scripts/python -m review_desk.synth generate --split dev --count 60
.venv/Scripts/python -m review_desk.synth overlay data/synthetic/dev/<name>.degraded.pdf --out overlay.png
.venv/Scripts/python -m pytest
```

Each document renders in three conditions, so a difference in scores between them comes from image
quality and not from the content:

| Condition | What it is |
|---|---|
| `clean` | A digital PDF with its text layer. |
| `scanned` | An image with no text layer: 170 to 240 DPI, skew under 0.4 degrees, light noise. |
| `degraded` | A bad scan, moderate or heavy: as low as 84 DPI, skew up to 4 degrees, blur, noise, speckle, fading, an edge shadow, hard JPEG compression. |

The skew and padding go through a transform this code computes, so the truth boxes move with the pixels
and field locations stay exact on a skewed page.

The documents carry the traps that catch extractors out:

- Five invoice layouts and three receipt layouts, with label wording, fonts, page sizes and locales that
  vary by seed.
- Two more invoice layouts and one receipt layout appear only in the held-out split, along with a separate
  pool of vendor names. One states the details in a sentence, and another splits tax across two lines.
- Numbers that aren't the answer: balance due after a deposit, cash tendered and change, customer IDs,
  order dates, ship-to names, remit-to companies, bank details.
- Day-first dates, decimal commas, multi-page invoices, wrapped descriptions, white text on color.
- Printed mistakes, flagged in the truth: a total that doesn't add up, a column that doesn't sum, a line
  amount that disagrees with its quantity and price, a missing invoice number. The truth records what's
  printed, not what should have been.

`samples/` holds a few generated documents to look at. Everything else regenerates from the seed.

## Licensing

MIT, `Copyright (c) 2026 Noah Hicks`. Every pinned dependency is under a permissive license, and
`scripts/check_licenses.py` fails if that stops being true. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
PyMuPDF is deliberately not used, because it's AGPL or commercial.

Public datasets considered for evaluation, and why some were rejected: [docs/DATASETS.md](docs/DATASETS.md).

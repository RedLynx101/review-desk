# Review Desk

Upload an invoice or receipt PDF and get checked data out, with no outside connections. Fields are
extracted against a strict schema, each with a confidence score built from checks you can inspect and
its source highlighted on the page. Low-confidence fields go to a review queue, nothing exports until a
person approves it, and a hash-chained audit log records what happened.

**Status: under construction.** Milestones 1 and 2 of 5 are done: synthetic test documents and the
extraction pipeline. The review screen, the audit log and the held-out accuracy report don't exist yet, so
this README makes no claims about them. No client deployment yet.

Built with AI coding agents under Noah Hicks's review.

## How a document gets read

```
.venv/Scripts/python -m review_desk extract invoice.pdf
```

1. **Intake.** Uploads are untrusted: size and page limits, encrypted and malformed files refused, active
   content detected and reported. Scripts never run, because the code never creates the form environment
   PDFium needs to run them.
2. **Words with coordinates.** Digital PDFs use their text layer. Scans go through Tesseract after being
   straightened and cleaned at their native resolution, with word boxes mapped back to the original page.
   Digital PDFs get OCR'd too, as a check: a text layer can claim anything, so text that doesn't match the
   visible page is flagged, and a page whose hidden text mostly isn't visible gets read from its pixels.
3. **The model copies, it doesn't compute.** A local model (LM Studio by default, any OpenAI-compatible
   endpoint set by environment variables) gets numbered lines and returns literal values with line
   citations, under a strict JSON schema. Dates and amounts get parsed in Python. Document text is treated as
   data, and this is the only network call in the app.
4. **Earned confidence.** The model's opinion of itself is never used. Each field is scored on whether it's
   printed where the model said, how sure OCR was, whether a matching label sits beside it, and whether the
   numbers add up. [docs/CONFIDENCE.md](docs/CONFIDENCE.md) has the signals and what they can't catch.

## First numbers, on the tuning set

These come from [results/runs/dev-first](results/runs/dev-first/summary.md): `prism-ml/bonsai-27b` (1-bit
quantization, 8192 context, temperature 0, no page images) on 60 synthetic renderings from the dev split. The
scoring rules were tuned while looking at these same documents, so expect a held-out run to do worse. **The
held-out set hasn't been run yet.**

| Scan quality | Documents | Printed values right | Printed values OCR could read at all |
|---|---|---|---|
| Clean PDF | 30 | 99.7% (1213/1217) | 100% |
| Light scan | 15 | 93.8% (502/535) | 97.4% |
| Moderate damage | 9 | 52.6% (191/363) | 74.7% |
| Heavy damage | 6 | 2.9% (5/172) | 12.2% |

Heavily damaged scans are hard for a person to read too. What matters is whether those values reach a reviewer:

| Condition | Values | Skip review at 0.90 | Wrong among those skipped |
|---|---|---|---|
| Clean PDF | 1346 | 96.2% | 0 of 1295 |
| Scanned | 599 | 83.1% | 0 of 498 |
| Degraded | 627 | 15.6% | 5 of 98 |

Weak spots show in those tables. On degraded scans, the model got only 62.6% of the values OCR could read,
and 5 of the 98 values that skipped review were wrong. During this run the model server also crashed 3 times
and then went down for a while, on a machine short of memory and shared with another job. Every affected
document succeeded when retried. Those failures are recorded in the run's files.

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

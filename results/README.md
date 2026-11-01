# Results

Start with [REPORT.md](REPORT.md), which `python -m review_desk.evaluation.compare` writes from the runs here.
[model-choice.json](model-choice.json) records how the default model was chosen, and it was committed before the held-out runs.

| Run | What it is |
|---|---|
| `heldout-bonsai`, `heldout-granite` | The held-out set, run once per model. |
| `dev-bonsai`, `dev-granite` | The dev runs used to pick the default model. |
| `dev-first` | The first dev run, from before the prompt and scoring settled. Kept as history, with numbers that stand as of commit `eb20086`. |

One answer is edited by hand. On a degraded receipt in `heldout-granite`, OCR garbled a synthetic `(555) 555-01xx` number
into digits that could belong to someone real, and Granite returned them as the vendor name. The first six digits are masked
with X in that reading and in `scored.jsonl`. The value was wrong and flagged for review either way, and no reported number changes.

Every number this repo reports traces to a file here, written by a real run. Runs live under `runs/<name>/`:

| File | What it holds |
|---|---|
| `run.json` | The model, its settings, the split, the generator seed, the git commit. |
| `readings/<doc_id>.json` | The model's raw answer for one document, or the failure it hit, with timings and token counts. |
| `scored.jsonl` | One line per field or table cell: truth, prediction, whether it was right, confidence, the signals behind it, whether its highlight landed on the ink. |
| `documents.jsonl` | One line per document: every value right or not, whether anything was flagged, rows missed, model failures. |
| `summary.json`, `summary.md` | The tables above rolled up. |

To rescore without calling the model again:

```
python -m review_desk.synth generate --split dev --count 60
python -m review_desk.evaluation score --split dev --run results/runs/<name>
```

OCR output is cached under `data/cache/`, which isn't committed, because it regenerates. The documents are synthetic throughout. Nothing here comes from a client.

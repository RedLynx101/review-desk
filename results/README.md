# Results

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

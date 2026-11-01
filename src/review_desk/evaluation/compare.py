"""Write the accuracy report and the model choice from finished runs. Every number comes from their files.

    python -m review_desk.evaluation.compare results/runs/dev-bonsai results/runs/dev-granite \\
        --heldout results/runs/heldout-bonsai results/runs/heldout-granite --out results/REPORT.md
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ..schema import HEADER_FIELDS, ITEM_FIELDS

THRESHOLD = 0.90
HELDOUT_ONLY_LAYOUTS = {"letterhead", "summary_first", "kiosk"}
CONDITIONS = ("clean", "scanned", "degraded")
BAR_MAX_FAILED = 2
BAR_MAX_ERROR = 0.01
BAR_TIE_POINTS = 0.01


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class Run:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "run.json").read_text(encoding="utf-8"))
        self.records = _rows(self.folder / "scored.jsonl")
        self.documents = _rows(self.folder / "documents.jsonl")
        model = self.meta.get("model", {})
        self.model = model.get("id", "unknown")
        self.quantization = model.get("quantization")
        self.short = self.model.split("/")[-1]


def wilson_upper(wrong: int, total: int, z: float = 1.96) -> float | None:
    """Upper end of a 95% interval. Renderings of one document aren't independent, so read it as a floor."""
    if not total:
        return None
    p = wrong / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (center + margin) / denominator


def _percent(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _ratio(hits: int, total: int) -> str:
    return f"{_percent(hits / total if total else None)} ({hits}/{total})"


def skip_stats(records: list[dict], threshold: float = THRESHOLD) -> dict:
    skipped = [r for r in records if r["confidence"] >= threshold]
    wrong = sum(not r["correct"] for r in skipped)
    return {
        "values": len(records), "skipped": len(skipped), "coverage": len(skipped) / len(records) if records else None,
        "wrong": wrong, "error": wrong / len(skipped) if skipped else None, "error_upper": wilson_upper(wrong, len(skipped)),
    }


def by_condition(records: list[dict], condition: str) -> list[dict]:
    return [r for r in records if r["condition"] == condition]


def by_quality(records: list[dict], quality: str) -> list[dict]:
    if quality == "clean":
        return [r for r in records if r["condition"] == "clean"]
    return [r for r in records if r["severity"] == quality]


def choose_default(dev_runs: list[Run]) -> dict:
    """Apply the bar in docs/MODEL_CHOICE.md. Nothing here looks at held-out results."""
    rows = []
    for run in dev_runs:
        stats = skip_stats(run.records)
        seconds = [d["model_seconds"] for d in run.documents if d["model_seconds"]]
        rows.append({
            "model": run.model,
            "documents": len(run.documents),
            "failed": sum(d["status"] != "ok" for d in run.documents),
            "failure_events": sum(len(d.get("failure_history", [])) for d in run.documents),
            "values": stats["values"], "skipped": stats["skipped"], "coverage": stats["coverage"],
            "wrong": stats["wrong"], "error": stats["error"],
            "mean_seconds": sum(seconds) / len(seconds) if seconds else None,
        })
    for row in rows:
        row["finishes"] = row["failed"] <= BAR_MAX_FAILED
        row["trustworthy"] = row["error"] is not None and row["error"] <= BAR_MAX_ERROR
        row["clears_bar"] = row["finishes"] and row["trustworthy"]
    clearing = [row for row in rows if row["clears_bar"]]
    if clearing:
        clearing.sort(key=lambda row: -row["coverage"])
        winner = clearing[0]
        reason = "Only model that cleared the bar." if len(clearing) == 1 else "Higher share of values skipping review."
        if len(clearing) > 1 and clearing[0]["coverage"] - clearing[1]["coverage"] < BAR_TIE_POINTS:
            winner = min(clearing[:2], key=lambda row: row["mean_seconds"] or math.inf)
            reason = "Coverage tied within a point, so the faster model wins."
        return {"default": winner["model"], "bar_met": True, "reason": reason, "rows": rows}
    finishing = [row for row in rows if row["finishes"]] or rows
    winner = min(finishing, key=lambda row: row["error"] if row["error"] is not None else math.inf)
    return {"default": winner["model"], "bar_met": False, "reason": "Neither model cleared the bar. The lower error wins by default.", "rows": rows}


def field_table(runs: list[Run]) -> list[str]:
    names = [*HEADER_FIELDS, *(f"item.{name}" for name in ITEM_FIELDS)]
    head = "| Field |" + "".join(f" {run.short} {condition} |" for run in runs for condition in CONDITIONS)
    lines = [head, "|---|" + "---|" * (len(runs) * 3)]
    for name in names:
        kind = "cell" if name.startswith("item.") else "field"
        short = name.removeprefix("item.")
        cells = []
        for run in runs:
            for condition in CONDITIONS:
                selected = [r for r in by_condition(run.records, condition) if r["kind"] == kind and r["field"] == short]
                cells.append(_percent(sum(r["correct"] for r in selected) / len(selected) if selected else None, 0))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return lines


def quality_table(runs: list[Run]) -> list[str]:
    lines = ["| Model | Scan quality | Documents | Printed values right | OCR could read | Right when readable |", "|---|---|---|---|---|---|"]
    for run in runs:
        for quality in ("clean", "light", "moderate", "heavy"):
            printed = [r for r in by_quality(run.records, quality) if r["truth"] is not None]
            documents = [d for d in run.documents if (d["condition"] == "clean" if quality == "clean" else d["severity"] == quality)]
            known = [r for r in printed if r["readable"] is not None]
            readable = [r for r in known if r["readable"]]
            lines.append(
                f"| {run.short} | {quality} | {len(documents)} | {_ratio(sum(r['correct'] for r in printed), len(printed))} | "
                f"{_percent(len(readable) / len(known) if known else None)} | {_ratio(sum(r['correct'] for r in readable), len(readable))} |"
            )
    return lines


def skip_table(runs: list[Run]) -> list[str]:
    lines = ["| Model | Set | Values | Skip review | Wrong among skipped | Error | Upper 95% |", "|---|---|---|---|---|---|---|"]
    for run in runs:
        groups = [(condition, by_condition(run.records, condition)) for condition in CONDITIONS]
        groups.append(("all", run.records))
        groups.append(("printed values only", [r for r in run.records if r["truth"] is not None]))
        for label, records in groups:
            s = skip_stats(records)
            lines.append(f"| {run.short} | {label} | {s['values']} | {_percent(s['coverage'])} | {s['wrong']} of {s['skipped']} | {_percent(s['error'], 2)} | {_percent(s['error_upper'], 2)} |")
    return lines


def layout_table(runs: list[Run]) -> list[str]:
    lines = ["| Model | Layouts | Documents | Values right | Skip review | Wrong among skipped |", "|---|---|---|---|---|---|"]
    for run in runs:
        for label, test in (("seen in tuning", lambda layout: layout not in HELDOUT_ONLY_LAYOUTS), ("never seen in tuning", lambda layout: layout in HELDOUT_ONLY_LAYOUTS)):
            records = [r for r in run.records if test(r["layout"])]
            documents = [d for d in run.documents if test(d["layout"])]
            s = skip_stats(records)
            lines.append(f"| {run.short} | {label} | {len(documents)} | {_ratio(sum(r['correct'] for r in records), len(records))} | {_percent(s['coverage'])} | {s['wrong']} of {s['skipped']} |")
    return lines


def threshold_table(run: Run) -> list[str]:
    lines = [f"| Threshold | Skip review | Wrong among skipped | Error |", "|---|---|---|---|"]
    for threshold in (0.5, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98):
        s = skip_stats(run.records, threshold)
        lines.append(f"| {threshold} | {_percent(s['coverage'])} | {s['wrong']} of {s['skipped']} | {_percent(s['error'], 2)} |")
    return lines


def document_table(runs: list[Run]) -> list[str]:
    lines = ["| Model | Condition | Documents | Failed | Every value right | Nothing flagged | Wrong though nothing flagged | Rows missed | Mean seconds |", "|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        for condition in CONDITIONS:
            documents = [d for d in run.documents if d["condition"] == condition]
            unflagged = [d for d in documents if not d["any_flagged"]]
            seconds = [d["model_seconds"] for d in documents if d["model_seconds"]]
            lines.append(
                f"| {run.short} | {condition} | {len(documents)} | {sum(d['status'] != 'ok' for d in documents)} | {sum(d['all_correct'] for d in documents)} | "
                f"{len(unflagged)} | {sum(not d['all_correct'] for d in unflagged)} | {sum(d['rows_missing'] for d in documents)} of {sum(d['rows_truth'] for d in documents)} | "
                f"{sum(seconds) / len(seconds) if seconds else 0:.0f} |"
            )
    return lines


def anomaly_table(runs: list[Run]) -> list[str]:
    kinds = (
        ("total_mismatch", "Total doesn't add up"), ("subtotal_mismatch", "Column doesn't reach the subtotal"),
        ("line_amount_mismatch", "Line amount isn't quantity times price"), ("missing_number", "No document number printed"),
    )
    lines = ["| Model | Printed mistake | Documents | Flagged for review |", "|---|---|---|---|"]
    for run in runs:
        for kind, label in kinds:
            documents = [d for d in run.documents if kind in d["anomalies"]]
            if documents:
                lines.append(f"| {run.short} | {label} | {len(documents)} | {sum(d['any_flagged'] for d in documents)} |")
    return lines


def anomaly_note(runs: list[Run]) -> str:
    """Says plainly when a set has no sums that fail, so the table isn't read as testing them."""
    note = "Printed mistakes are built into some documents. A sum that doesn't work should reach a person. A missing number is different: leaving it blank is the right answer, so nothing has to be flagged."
    if not any(kind != "missing_number" for run in runs for d in run.documents for kind in d["anomalies"]):
        note += " No document in this set carries a bad sum, so it doesn't test whether those reach a person."
    return note


def summary_lines(default_run: Run, runs: list[Run]) -> list[str]:
    def at(run: Run, condition: str) -> dict:
        return skip_stats(by_condition(run.records, condition))

    clean, scanned, degraded = (at(default_run, condition) for condition in CONDITIONS)
    heavy = [r for r in by_quality(default_run.records, "heavy") if r["truth"] is not None]
    known = [r for r in heavy if r["readable"] is not None]
    lines = [
        f"At a {THRESHOLD} threshold on the held-out set, {default_run.short} let {_percent(clean['coverage'])} of values on clean PDFs skip review, and {clean['wrong']} of those {clean['skipped']} were wrong. "
        f"On light scans it was {_percent(scanned['coverage'])}, with {scanned['wrong']} of {scanned['skipped']} wrong.",
        "",
        f"**The weak spot is degraded scans.** Only {_percent(degraded['coverage'])} of values skip review there, which is the system working, but {degraded['wrong']} of those {degraded['skipped']} were wrong ({_percent(degraded['error'], 2)}). "
        f"A bad scan needs a person's eyes on every value, whatever the score says. On the heaviest damage, OCR could read {_percent(sum(bool(r['readable']) for r in known) / len(known) if known else None)} of the printed values, and {_percent(sum(r['correct'] for r in heavy) / len(heavy) if heavy else None)} came out right.",
    ]
    others = [run for run in runs if run is not default_run]
    for other in others:
        total = skip_stats(other.records)
        ours = skip_stats(default_run.records)
        ours_seconds = [d["model_seconds"] for d in default_run.documents if d["model_seconds"]]
        other_seconds = [d["model_seconds"] for d in other.documents if d["model_seconds"]]
        lines += [
            "",
            f"Across all conditions {default_run.short} skipped review on {_percent(ours['coverage'])} of values with {_percent(ours['error'], 2)} wrong. {other.short} skipped {_percent(total['coverage'])} with {_percent(total['error'], 2)} wrong, "
            f"and took {sum(other_seconds) / len(other_seconds):.0f} seconds per document against {sum(ours_seconds) / len(ours_seconds):.0f}.",
        ]
    return lines


def failure_lines(runs: list[Run]) -> list[str]:
    lines = []
    for run in runs:
        counts: dict[str, int] = {}
        for document in run.documents:
            for failure in document.get("failure_history", []):
                counts[failure["code"]] = counts.get(failure["code"], 0) + 1
        text = ", ".join(f"{code} {count}" for code, count in sorted(counts.items())) or "none"
        lines.append(f"- {run.short}: {text}.")
    return lines


def wrong_examples(run: Run, limit: int = 12) -> list[str]:
    wrong = [r for r in run.records if not r["correct"] and r["confidence"] >= THRESHOLD]
    lines = ["| Document | Field | Truth | Read | Score |", "|---|---|---|---|---|"]
    for record in wrong[:limit]:
        lines.append(f"| {record['doc_id'].removeprefix('synthetic-')} | {record['field']} | {str(record['truth'])[:40]} | {str(record['predicted'])[:40]} | {record['confidence']:.2f} |")
    if len(wrong) > limit:
        lines.append(f"| {len(wrong) - limit} more in `scored.jsonl` | | | | |")
    return lines if wrong else ["None."]


def render(dev_runs: list[Run], heldout_runs: list[Run], choice: dict) -> str:
    default_run = next((run for run in heldout_runs if run.model == choice["default"]), heldout_runs[0])
    dev_rows = ["| Model | Documents | No answer | Skip review | Wrong among skipped | Error | Mean seconds | Clears the bar |", "|---|---|---|---|---|---|---|---|"]
    for row in choice["rows"]:
        dev_rows.append(
            f"| {row['model']} | {row['documents']} | {row['failed']} | {_percent(row['coverage'])} | {row['wrong']} of {row['skipped']} | "
            f"{_percent(row['error'], 2)} | {row['mean_seconds']:.0f} | {'yes' if row['clears_bar'] else 'no'} |"
        )
    settings = heldout_runs[0].meta.get("model_settings", {})
    base = len({d["base_id"] for d in heldout_runs[0].documents})
    parts = [
        "# Accuracy report",
        "",
        "Every document here is synthetic: generated from a seed, with invented names and known answers. Nothing here comes from a client, and "
        "there's no client deployment yet. These numbers say how the pipeline does on these documents. They don't predict any particular business's paperwork.",
        "",
        f"**Default model: `{choice['default']}`.** {choice['reason']}" + ("" if choice["bar_met"] else " The bar wasn't met."),
        "",
        *summary_lines(default_run, heldout_runs),
        "",
        "## Setup",
        "",
        f"- Held-out set: {base} base documents, each rendered clean, scanned and degraded, {len(heldout_runs[0].documents)} renderings per model. The split has its own vendor names and three layouts that were never rendered while tuning. Each model ran it once.",
        f"- Models: " + "; ".join(f"`{run.model}` ({run.quantization})" for run in heldout_runs) + f". Through LM Studio, {heldout_runs[0].meta.get('model', {}).get('loaded_context_length')} context, temperature {settings.get('temperature')}, max tokens {settings.get('max_tokens')}, strict JSON schema, no page images.",
        "- OCR: Tesseract 5.3.3 on image pages, after straightening and denoising. Digital PDFs use their text layer, checked against the rendered page.",
        f"- Review threshold {THRESHOLD}, with hand-set weights. Both were fixed before these runs, and neither was refit per model. They were tuned against Bonsai's answers on dev, which may favor Bonsai.",
        "- Public datasets weren't run. FATURA and CORD passed the license review in [DATASETS.md](../docs/DATASETS.md), but this round used no downloads, so nothing here has been tested on documents this repo's generator didn't make. That's the largest gap in this report.",
        "",
        "## What matters most: values that skipped review",
        "",
        "Coverage is the share of values at or above the threshold, which a person isn't asked to check. Error is how many of those were wrong. A null counts as a value, so the printed-only row is the stricter reading. The upper bound assumes independence, which renderings of one document don't quite have, so treat it as a floor.",
        "",
        *skip_table(heldout_runs),
        "",
        f"Coverage against error for the default model, {default_run.short}:",
        "",
        *threshold_table(default_run),
        "",
        "## By scan quality",
        "",
        "Printed values only, so an easy null can't flatter a bad scan. \"OCR could read\" is the share whose exact characters survive in the extracted words: the ceiling for any text-only model.",
        "",
        *quality_table(heldout_runs),
        "",
        "## Accuracy by field and condition",
        "",
        *field_table(heldout_runs),
        "",
        "## Layouts never seen in tuning",
        "",
        *layout_table(heldout_runs),
        "",
        "## Whole documents",
        "",
        "A document nobody needs to touch is the real saving, and a single flagged value sends it to a person. Seconds include any queueing.",
        "",
        *document_table(heldout_runs),
        "",
        anomaly_note(heldout_runs),
        "",
        *anomaly_table(heldout_runs),
        "",
        "## Wrong values that skipped review",
        "",
    ]
    for run in heldout_runs:
        parts += [f"### {run.short}", "", *wrong_examples(run), ""]
    parts += [
        "## Model failures during the held-out runs",
        "",
        "Counts every attempt, including ones a retry got past.",
        "",
        *failure_lines(heldout_runs),
        "",
        "## How the default was chosen",
        "",
        "From dev only, by the bar committed before any Granite run in [MODEL_CHOICE.md](../docs/MODEL_CHOICE.md).",
        "",
        *dev_rows,
        "",
        "## Where these numbers come from",
        "",
        "`python -m review_desk.evaluation.compare` wrote this file from the runs below. Each folder holds every model answer, every scored value and the commit it ran at.",
        "",
        *[f"- [{run.folder.name}](runs/{run.folder.name}/summary.md)" for run in [*dev_runs, *heldout_runs]],
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m review_desk.evaluation.compare", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dev", nargs="+", type=Path, help="dev runs, one per model")
    parser.add_argument("--heldout", nargs="*", type=Path, default=[], help="held-out runs, one per model")
    parser.add_argument("--out", type=Path, default=Path("results/REPORT.md"))
    args = parser.parse_args(argv)
    dev_runs = [Run(path) for path in args.dev]
    choice = choose_default(dev_runs)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    (Path(args.out).parent / "model-choice.json").write_bytes((json.dumps(choice, indent=1) + "\n").encode("utf-8"))
    print(json.dumps(choice, indent=1))
    if args.heldout:
        Path(args.out).write_bytes(render(dev_runs, [Run(path) for path in args.heldout], choice).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

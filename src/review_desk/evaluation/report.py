"""Roll scored records up into the tables a report needs. Weak results stay in."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from ..schema import HEADER_FIELDS, ITEM_FIELDS

CONDITIONS = ("clean", "scanned", "degraded")
THRESHOLDS = (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.92, 0.95, 0.97, 0.98, 0.99)


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def accuracy_table(records: list[dict]) -> dict:
    """Accuracy per field per condition, plus each condition overall."""
    table: dict[str, dict] = {}
    for name in [*HEADER_FIELDS, *(f"item.{field}" for field in ITEM_FIELDS)]:
        kind = "cell" if name.startswith("item.") else "field"
        short = name.removeprefix("item.")
        row = {}
        for condition in (*CONDITIONS, "all"):
            selected = [
                r for r in records
                if r["kind"] == kind and r["field"] == short and (condition == "all" or r["condition"] == condition)
            ]
            row[condition] = {"n": len(selected), "correct": sum(r["correct"] for r in selected), "accuracy": _rate(sum(r["correct"] for r in selected), len(selected))}
        table[name] = row
    return table


def coverage_table(records: list[dict], thresholds=THRESHOLDS) -> list[dict]:
    """Coverage against error: of the values that would skip review, what share were wrong?"""
    rows = []
    for threshold in thresholds:
        passed = [r for r in records if r["confidence"] >= threshold]
        wrong = sum(not r["correct"] for r in passed)
        rows.append({
            "threshold": threshold,
            "values": len(records),
            "skip_review": len(passed),
            "coverage": _rate(len(passed), len(records)),
            "wrong_among_skipped": wrong,
            "error_among_skipped": _rate(wrong, len(passed)),
        })
    return rows


def document_table(documents: list[dict]) -> dict:
    table = {}
    for condition in (*CONDITIONS, "all"):
        selected = [d for d in documents if condition == "all" or d["condition"] == condition]
        unflagged = [d for d in selected if not d["any_flagged"]]
        table[condition] = {
            "documents": len(selected),
            "failed": sum(d["status"] != "ok" for d in selected),
            "all_values_correct": sum(d["all_correct"] for d in selected),
            "all_correct_rate": _rate(sum(d["all_correct"] for d in selected), len(selected)),
            "no_field_flagged": len(unflagged),
            "wrong_though_unflagged": sum(not d["all_correct"] for d in unflagged),
            "rows_truth": sum(d["rows_truth"] for d in selected),
            "rows_missing": sum(d["rows_missing"] for d in selected),
            "rows_extra": sum(d["rows_extra"] for d in selected),
            "mean_model_seconds": _rate(sum(d["model_seconds"] or 0 for d in selected), sum(1 for d in selected if d["model_seconds"])),
            "model_failures": dict(sorted(Counter(f["code"] for d in selected for f in d.get("failure_history", [])).items())),
        }
    return table


def severity_table(records: list[dict], documents: list[dict]) -> dict:
    """Accuracy and skipped-value error per scan severity. Printed values only, so nulls can't flatter it."""
    table = {}
    for severity in ("clean", "light", "moderate", "heavy"):
        match = (lambda item: item["condition"] == "clean") if severity == "clean" else (lambda item, s=severity: item["severity"] == s)
        selected = [r for r in records if match(r)]
        printed = [r for r in selected if r["truth"] is not None]
        skipped = [r for r in selected if r["confidence"] >= 0.9]
        table[severity] = {
            "documents": sum(1 for d in documents if match(d)),
            "printed_values": len(printed),
            "printed_right": sum(r["correct"] for r in printed),
            "printed_accuracy": _rate(sum(r["correct"] for r in printed), len(printed)),
            "all_values": len(selected),
            "readable_rate": _rate(sum(1 for r in printed if r["readable"]), sum(1 for r in printed if r["readable"] is not None)),
        }
    return table


def location_table(records: list[dict]) -> dict:
    """Of the values that were right and printed, how often did the highlight land on the ink?"""
    table = {}
    for condition in (*CONDITIONS, "all"):
        selected = [
            r for r in records
            if r["correct"] and r["truth"] is not None and r["field"] not in ("document_type", "currency")
            and (condition == "all" or r["condition"] == condition)
        ]
        hits = sum(r["location"] == "hit" for r in selected)
        table[condition] = {"n": len(selected), "hit": hits, "hit_rate": _rate(hits, len(selected))}
    return table


def readable_table(records: list[dict]) -> dict:
    """Separate OCR losses from model errors: could the value be read, and was it right if so?"""
    table = {}
    for condition in (*CONDITIONS, "all"):
        selected = [
            r for r in records
            if r["readable"] is not None and r["field"] not in ("document_type", "currency")
            and (condition == "all" or r["condition"] == condition)
        ]
        readable = [r for r in selected if r["readable"]]
        table[condition] = {
            "printed_values": len(selected),
            "readable": len(readable),
            "readable_rate": _rate(len(readable), len(selected)),
            "right_when_readable": sum(r["correct"] for r in readable),
            "accuracy_when_readable": _rate(sum(r["correct"] for r in readable), len(readable)),
            "right_when_unreadable": sum(r["correct"] for r in selected if not r["readable"]),
        }
    return table


def layout_table(records: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record["layout"]].append(record)
    return {
        layout: {"n": len(rows), "accuracy": _rate(sum(r["correct"] for r in rows), len(rows))}
        for layout, rows in sorted(grouped.items())
    }


def summarize(records: list[dict], documents: list[dict], metadata: dict) -> dict:
    fields_only = [r for r in records if r["kind"] == "field"]
    printed_only = [r for r in records if r["truth"] is not None]
    by_condition = {
        condition: coverage_table([r for r in records if r["condition"] == condition])
        for condition in CONDITIONS
    }
    return {
        "synthetic": True,
        "run": metadata,
        "documents": document_table(documents),
        "by_severity": severity_table(records, documents),
        "accuracy": accuracy_table(records),
        "coverage_all_values": coverage_table(records),
        "coverage_header_fields": coverage_table(fields_only),
        "coverage_printed_values_only": coverage_table(printed_only),
        "coverage_by_condition": by_condition,
        "location": location_table(records),
        "readable": readable_table(records),
        "by_layout": layout_table(records),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_markdown(summary: dict) -> str:
    run = summary["run"]
    model = run.get("model", {})
    lines = [
        f"# Run summary: {run.get('split')} split (synthetic documents)",
        "",
        f"Model: `{model.get('id')}`, quantization {model.get('quantization')}, loaded context {model.get('loaded_context_length')}. "
        f"Temperature {run.get('model_settings', {}).get('temperature')}, strict JSON schema, images for scans: {run.get('model_settings', {}).get('images_sent_for_scans')}.",
        "",
        "## Documents",
        "",
        "Model seconds include time queued behind other requests on a shared server.",
        "",
        "| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for condition, row in summary["documents"].items():
        lines.append(
            f"| {condition} | {row['documents']} | {row['failed']} | {row['all_values_correct']} ({_percent(row['all_correct_rate'])}) | "
            f"{row['no_field_flagged']} | {row['wrong_though_unflagged']} | {row['rows_missing']} / {row['rows_truth']} | {row['mean_model_seconds']} |"
        )
    failures = summary["documents"]["all"]["model_failures"]
    if failures:
        lines += ["", "Model failures during this run, counting every attempt, including ones a retry got past: " + ", ".join(f"{code} {count}" for code, count in failures.items()) + "."]
    lines += ["", "## By scan quality", "", "Values that are actually printed, so an easy null can't flatter a bad scan.", "", "| Quality | Documents | Printed values right | Printed values OCR could read |", "|---|---|---|---|"]
    for severity, row in summary["by_severity"].items():
        lines.append(f"| {severity} | {row['documents']} | {_percent(row['printed_accuracy'])} ({row['printed_right']}/{row['printed_values']}) | {_percent(row['readable_rate'])} |")
    lines += ["", "## Accuracy by field", "", "| Field | Clean | Scanned | Degraded | All |", "|---|---|---|---|---|"]
    for name, row in summary["accuracy"].items():
        cells = [f"{_percent(row[c]['accuracy'])} ({row[c]['correct']}/{row[c]['n']})" for c in (*CONDITIONS, "all")]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Coverage against error, all values",
        "",
        "Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.",
        "",
        "| Threshold | Skip review | Coverage | Wrong among skipped | Error |",
        "|---|---|---|---|---|",
    ]
    for row in summary["coverage_all_values"]:
        lines.append(f"| {row['threshold']} | {row['skip_review']} / {row['values']} | {_percent(row['coverage'])} | {row['wrong_among_skipped']} | {_percent(row['error_among_skipped'])} |")
    lines += [
        "",
        "## OCR losses against model errors",
        "",
        "Printed values whose exact characters survive in the extracted words, and how often the model got those right.",
        "",
        "| Condition | Readable | Right when readable |",
        "|---|---|---|",
    ]
    for condition, row in summary["readable"].items():
        lines.append(f"| {condition} | {_percent(row['readable_rate'])} ({row['readable']}/{row['printed_values']}) | {_percent(row['accuracy_when_readable'])} ({row['right_when_readable']}/{row['readable']}) |")
    lines += ["", "## Source highlight", "", "Of right values that are printed, how often the highlight landed on the ink.", "", "| Condition | Hit rate |", "|---|---|"]
    for condition, row in summary["location"].items():
        lines.append(f"| {condition} | {_percent(row['hit_rate'])} ({row['hit']}/{row['n']}) |")
    lines += ["", "## By layout", "", "| Layout | Accuracy | Values |", "|---|---|---|"]
    for layout, row in summary["by_layout"].items():
        lines.append(f"| {layout} | {_percent(row['accuracy'])} | {row['n']} |")
    lines.append("")
    return "\n".join(lines)


def write_summary(run_dir: Path, records: list[dict], documents: list[dict]) -> dict:
    metadata = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    summary = summarize(records, documents, metadata)
    (run_dir / "summary.json").write_bytes((json.dumps(summary, indent=1) + "\n").encode("utf-8"))
    (run_dir / "summary.md").write_bytes(render_markdown(summary).encode("utf-8"))
    (run_dir / "scored.jsonl").write_bytes(("\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n").encode("utf-8"))
    (run_dir / "documents.jsonl").write_bytes(("\n".join(json.dumps(d, sort_keys=True) for d in documents) + "\n").encode("utf-8"))
    return summary

"""Evaluate the pipeline on generated documents with known answers.

    python -m review_desk.evaluation run --split dev --count 24 --run results/runs/dev-v1
    python -m review_desk.evaluation score --split dev --run results/runs/dev-v1
    python -m review_desk.evaluation fit --split dev --run results/runs/dev-v1

`run` calls the model and can be stopped and restarted at any point. `score` reads saved answers
and doesn't call the model. `fit` sets confidence weights and the review threshold from a scored run,
and only ever belongs on the dev split.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import Settings
from ..confidence import CALIBRATION_FILE, DEFAULT_THRESHOLD, DEFAULT_WEIGHTS, load_calibration
from .fit import choose_threshold, fit_weights, save_weights
from .report import write_summary
from .runner import result_for, run_split
from .score import score_document

ALL_CONDITIONS = ("clean", "scanned", "degraded")


def _read_records(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "scored.jsonl").read_text(encoding="utf-8").splitlines() if line]


def fit_run(run_dir: Path, split: str, data_root: Path, cache_dir: Path, settings: Settings, *, target_error: float, out: Path = CALIBRATION_FILE) -> dict:
    """Fit weights on a scored run, then choose the lowest threshold that meets the target error."""
    if split == "heldout":
        raise SystemExit("Refusing to fit on the held-out split. It only stays useful if it's never tuned on.")
    score_run(run_dir, split, data_root, cache_dir, settings, weights=DEFAULT_WEIGHTS, threshold=DEFAULT_THRESHOLD)
    weights = fit_weights(_read_records(run_dir))
    score_run(run_dir, split, data_root, cache_dir, settings, weights=weights, threshold=DEFAULT_THRESHOLD)
    chosen = choose_threshold(_read_records(run_dir), target_error=target_error)
    save_weights(out, weights, chosen["threshold"], fitted_on=run_dir.as_posix(), note=f"Lowest threshold with at most {target_error:.1%} wrong among values skipping review, on the fit data.")
    score_run(run_dir, split, data_root, cache_dir, settings, weights=weights, threshold=chosen["threshold"])
    return chosen


def score_run(run_dir: Path, split: str, data_root: Path, cache_dir: Path, settings: Settings, *, weights=None, threshold=None) -> dict:
    fitted_weights, fitted_threshold = load_calibration()
    weights = fitted_weights if weights is None else weights
    threshold = fitted_threshold if threshold is None else threshold
    folder = data_root / split
    records: list[dict] = []
    documents: list[dict] = []
    for path in sorted((run_dir / "readings").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc_id = payload["doc_id"]
        truth = json.loads((folder / f"{doc_id}.truth.json").read_text(encoding="utf-8"))
        pdf = (folder / f"{doc_id}.pdf").read_bytes()
        result, words = result_for(truth, payload, pdf, settings, cache_dir, weights=weights, threshold=threshold)
        doc_records, document = score_document(truth, result, words=words)
        document["failure_history"] = payload.get("failure_history", [])
        if payload.get("error_code"):
            document["failure_history"] = [*document["failure_history"], {"code": payload["error_code"], "message": payload.get("error_message")}]
        # Early runs filed LM Studio's crash response under the generic 400 code.
        for failure in document["failure_history"]:
            if "crashed" in (failure.get("message") or ""):
                failure["code"] = "model_crashed"
        records += doc_records
        documents.append(document)
    return write_summary(run_dir, records, documents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m review_desk.evaluation", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "score", "fit"))
    parser.add_argument("--split", required=True)
    parser.add_argument("--run", type=Path, required=True, help="folder for this run's outputs")
    parser.add_argument("--count", type=int, default=None, help="base documents to use, from the start of the split")
    parser.add_argument("--conditions", default=",".join(ALL_CONDITIONS))
    parser.add_argument("--data", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--cache", type=Path, default=Path("data/cache/text"))
    parser.add_argument("--images", action="store_true", help="send page images for scanned pages (vision models)")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--target-error", type=float, default=0.01, help="fit: allowed share of wrong values among those skipping review")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    conditions = tuple(name.strip() for name in args.conditions.split(",") if name.strip())
    if args.command == "run":
        outcome = run_split(
            args.split, count=args.count, conditions=conditions, run_dir=args.run, data_root=args.data,
            cache_dir=args.cache, settings=settings, send_images=args.images, retry_failures=args.retry_failures,
            log=lambda message: print(message, flush=True),
        )
        print(outcome)
    if args.command == "fit":
        chosen = fit_run(args.run, args.split, args.data, args.cache, settings, target_error=args.target_error)
        print(f"Threshold {chosen['threshold']}: coverage {chosen['coverage']:.1%}, error {chosen['error']:.2%} on the fit data. Saved to {CALIBRATION_FILE}")
    summary = score_run(args.run, args.split, args.data, args.cache, settings)
    documents = summary["documents"]["all"]
    print(f"Scored {documents['documents']} documents: {documents['all_values_correct']} with every value right. See {args.run / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

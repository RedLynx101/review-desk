"""Score the pipeline on documents this repo didn't generate, using whatever labels they come with.

    python -I -m review_desk.evaluation.outside prepare-cord TEST.parquet --out DIR/cord-test
    python -I -m review_desk.evaluation.outside prepare-fatura FATURA2.zip --out DIR/fatura-sample --count 50
    python -m review_desk.evaluation.outside run --data DIR/cord-test --run RUN_DIR --cache CACHE_DIR
    python -m review_desk.evaluation.outside score --data DIR/cord-test --run RUN_DIR --cache CACHE_DIR --out results/outside/cord-test.json

Downloaded files are untrusted, so `prepare-*` reads them with Python's `-I` flag and treats them only as
data: images get decoded and re-encoded, archive members are read by name and never unpacked to disk.
Prepared documents, model answers and scored values all hold the dataset's contents, so they stay outside
this repo. Only the aggregate counts written by `score` get committed. A field the labels don't give isn't scored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image

from ..confidence import load_calibration
from ..config import Settings
from ..normalize import parse_date, squash
from .compare import skip_stats, wilson_upper
from .runner import result_for, run_split
from .score import align_rows, values_match

CONDITION = "image"
ITEM_LABELS = ("description", "quantity", "unit_price", "amount")
AMOUNT_NAMES = {"subtotal", "discount", "tax", "total", "shipping", "unit_price", "amount"}


# Labels ------------------------------------------------------------------------------------------------


def _text(value) -> str | None:
    """A CORD field can be a string, a list of strings or absent."""
    if isinstance(value, list):
        value = " ".join(str(part) for part in value if part is not None)
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def plain_amount(text: str | None) -> str | None:
    """Read an amount from a dataset label, whose notation the dataset controls rather than the pipeline.

    Rupiah have no cents and group thousands with either mark, so 41,000 and 41.000 are the same number.
    A two-digit tail is cents, as in 41,000.00, and gets dropped only when it's all zeros.
    """
    tokens = re.findall(r"\d[\d.,]*", text or "")
    if not tokens:
        return None
    body = "".join(tokens).rstrip(".,")
    tail = re.search(r"[.,](\d{2})$", body)
    cents = ""
    if tail:
        body = body[: tail.start()]
        cents = "" if tail.group(1) == "00" else f".{tail.group(1)}"
    return re.sub(r"[.,]", "", body) + cents


def plain_quantity(text: str | None) -> str | None:
    match = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not match:
        return None
    return format(Decimal(match.group().replace(",", ".")).normalize(), "f")


def _label(value: str | None, printed: str | None) -> dict | None:
    return {"value": value, "printed": printed} if value is not None else None


def _amount_label(raw) -> dict | None:
    printed = _text(raw)
    return _label(plain_amount(printed), printed)


def cord_labels(parse: dict) -> dict:
    """Map CORD's receipt parse onto this repo's schema. Service charges, cash and change have no field here."""

    def section(name: str) -> dict:
        value = parse.get(name)
        if isinstance(value, list):
            value = next((entry for entry in value if isinstance(entry, dict)), None)
        return value if isinstance(value, dict) else {}

    menu = parse.get("menu")
    rows = menu if isinstance(menu, list) else [menu] if isinstance(menu, dict) else []
    items = []
    for row in rows:
        if not isinstance(row, dict) or _text(row.get("nm")) is None:
            continue
        count = _text(row.get("cnt"))
        items.append({
            "description": _label(_text(row.get("nm")), _text(row.get("nm"))),
            "quantity": _label(plain_quantity(count), count),
            "unit_price": _amount_label(row.get("unitprice")),
            "amount": _amount_label(row.get("price")),
        })
    fields = {
        "subtotal": _amount_label(section("sub_total").get("subtotal_price")),
        "tax": _amount_label(section("sub_total").get("tax_price")),
        "discount": _amount_label(section("sub_total").get("discount_price")),
        "total": _amount_label(section("total").get("total_price")),
    }
    return {"fields": {name: label for name, label in fields.items() if label}, "items": items}


_FATURA_PREFIX = re.compile(r"^[^:]*:\s*")


def _after_label(text: str | None) -> str | None:
    """FATURA boxes often hold their printed label too, as in "Total: 1234.50 USD"."""
    return _text(_FATURA_PREFIX.sub("", text or "")) if text else None


def fatura_labels(annotation: dict) -> dict:
    """Map FATURA's labeled boxes onto this repo's schema. Its table is one box, so rows aren't scored."""

    def box(*names: str) -> str | None:
        for name in names:
            entry = annotation.get(name)
            if isinstance(entry, dict) and _text(entry.get("text")):
                return _text(entry["text"])
        return None

    fields: dict[str, dict | None] = {}
    seller = box("SELLER_NAME")
    if seller:
        fields["vendor_name"] = _label(_after_label(seller), seller)
    buyer = box("BUYER")
    if buyer:
        # The buyer box runs name, then address, on separate lines that the labels flatten. Only the printed form can be checked.
        fields["bill_to_name"] = None
    for name, source in (("issue_date", "DATE"), ("due_date", "DUE_DATE")):
        raw = box(source)
        parsed = parse_date(_after_label(raw)) if raw else None
        if parsed is not None and not parsed.ambiguous:
            fields[name] = _label(parsed.value.isoformat(), raw)
    for name, source in (("subtotal", "SUB_TOTAL"), ("tax", "TAX"), ("discount", "DISCOUNT"), ("total", "TOTAL")):
        raw = box(source)
        if raw and re.search(r"\d", raw):
            fields[name] = _label(_fatura_amount(_after_label(raw)), raw)
    return {"fields": {name: label for name, label in fields.items() if label and label["value"] is not None}, "items": []}


def _fatura_amount(text: str | None) -> str | None:
    """FATURA prints plain amounts such as 1234.5 or 1,234.50 with a currency code or symbol beside them."""
    tokens = re.findall(r"\d[\d.,]*", text or "")
    if not tokens:
        return None
    body = tokens[-1].rstrip(".,")
    mark = max(body.rfind("."), body.rfind(","))
    if mark >= 0 and len(body) - mark - 1 in (1, 2):
        whole, fraction = re.sub(r"[.,]", "", body[:mark]), body[mark + 1 :]
        return format(Decimal(f"{whole or '0'}.{fraction}").normalize(), "f")
    return format(Decimal(re.sub(r"[.,]", "", body)).normalize(), "f")


# Preparing documents -----------------------------------------------------------------------------------


def image_pdf(image_bytes: bytes, *, dpi: float) -> bytes:
    """Decode an untrusted image and write it fresh as a one-page PDF with no text layer."""
    with Image.open(BytesIO(image_bytes)) as image:
        if image.format not in ("JPEG", "PNG"):
            raise ValueError(f"unexpected image format: {image.format}")
        buffer = BytesIO()
        image.convert("RGB").save(buffer, "PDF", resolution=dpi, quality=95)
    return buffer.getvalue()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_document(out: Path, doc_id: str, pdf: bytes, labels: dict, document_type: str) -> dict:
    (out / f"{doc_id}.pdf").write_bytes(pdf)
    (out / f"{doc_id}.labels.json").write_bytes((json.dumps({"doc_id": doc_id, **labels}, indent=1) + "\n").encode("utf-8"))
    return {"doc_id": doc_id, "pdf": f"{doc_id}.pdf", "pdf_sha256": hashlib.sha256(pdf).hexdigest(), "condition": CONDITION, "document_type": document_type}


def _write_manifest(out: Path, dataset: dict, documents: list[dict]) -> dict:
    manifest = {"synthetic": False, "split": out.name, "dataset": dataset, "conditions": [CONDITION], "documents": documents}
    (out / "manifest.json").write_bytes((json.dumps(manifest, indent=1) + "\n").encode("utf-8"))
    return manifest


CORD_DATASET = {
    "name": "CORD v2, test split",
    "source": "https://huggingface.co/datasets/naver-clova-ix/cord-v2",
    "license": "CC BY 4.0",
    "license_checked": "2026-11-01",
    "attribution": "CORD: A Consolidated Receipt Dataset for Post-OCR Parsing. Park, Shin, Lee, Lee, Park, Seo, Oh, Kim, Lee. Document Intelligence Workshop at NeurIPS 2019.",
    "what_it_is": "Real Indonesian shop and restaurant receipts, photographed and scanned. Store and payment details were removed by its authors, so only line items, subtotal, tax, discount and total have labels.",
}
FATURA_DATASET = {
    "name": "FATURA v2, sample",
    "source": "https://zenodo.org/records/10371464",
    "license": "CC BY 4.0",
    "license_checked": "2026-11-01",
    "attribution": "FATURA: A Multi-Layout Invoice Image Dataset for Document Analysis and Understanding. Limam, Dhiaf, Kessentini. Zenodo, 2023.",
    "what_it_is": "Template-generated invoice images from 50 templates, made by another team. Not real paper. Labels are boxes with text, so only the fields that map cleanly get scored, and its tables are one box, so line items aren't.",
}
CORD_DPI = 200.0
A4_WIDTH_INCHES = 8.27


def prepare_cord(parquet: Path, out: Path) -> dict:
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=False)
    documents = []
    source = pq.ParquetFile(parquet)
    for batch in source.iter_batches(batch_size=8, columns=["image", "ground_truth"]):
        for row in batch.to_pylist():
            labels = cord_labels(json.loads(row["ground_truth"]).get("gt_parse") or {})
            doc_id = f"cord-test-{len(documents):04d}"
            documents.append(_write_document(out, doc_id, image_pdf(row["image"]["bytes"], dpi=CORD_DPI), labels, "receipt"))
    dataset = {**CORD_DATASET, "source_file": parquet.name, "source_sha256": _sha256_file(parquet), "documents": len(documents), "sampling": "The whole test split, in order. The train and validation splits were never read."}
    return _write_manifest(out, dataset, documents)


def _fatura_members(names: list[str]) -> dict[str, tuple[str, str]]:
    """Pair each invoice image with its JSON annotation, by file stem, keyed by template."""
    images = {Path(name).stem: name for name in names if name.lower().endswith((".jpg", ".jpeg", ".png")) and "/images/" in f"/{name.lower()}"}
    annotations = {Path(name).stem: name for name in names if name.lower().endswith(".json") and "original_format" in name.lower()}
    return {stem: (images[stem], annotations[stem]) for stem in sorted(images) if stem in annotations}


def _template_of(stem: str) -> str:
    match = re.search(r"(?:template|layout)\s*_?(\d+)", stem, flags=re.IGNORECASE)
    return match.group(1) if match else "unknown"


def prepare_fatura(archive: Path, out: Path, *, count: int, seed: int = 20261101) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    random_source = random.Random(seed)
    documents = []
    with zipfile.ZipFile(archive) as bundle:
        pairs = _fatura_members(bundle.namelist())
        by_template: dict[str, list[str]] = {}
        for stem in pairs:
            by_template.setdefault(_template_of(stem), []).append(stem)
        chosen: list[str] = []
        templates = sorted(by_template)
        round_number = 0
        while len(chosen) < count and any(len(by_template[t]) > round_number for t in templates):
            for template in templates:
                if len(chosen) < count and len(by_template[template]) > round_number:
                    chosen.append(random_source.choice(sorted(set(by_template[template]) - set(chosen))))
            round_number += 1
        for stem in chosen:
            image_name, annotation_name = pairs[stem]
            image_bytes = bundle.read(image_name)
            annotation = json.loads(bundle.read(annotation_name).decode("utf-8"))
            with Image.open(BytesIO(image_bytes)) as image:
                dpi = image.width / A4_WIDTH_INCHES
            doc_id = f"fatura-{len(documents):04d}"
            documents.append(_write_document(out, doc_id, image_pdf(image_bytes, dpi=dpi), {**fatura_labels(annotation), "template": _template_of(stem)}, "invoice"))
    dataset = {
        **FATURA_DATASET, "source_file": archive.name, "source_sha256": _sha256_file(archive), "documents": len(documents),
        "sampling": f"One invoice per template in template order, chosen with seed {seed}, up to {count} documents.",
        "templates_in_sample": len({_template_of(stem) for stem in chosen}),
    }
    return _write_manifest(out, dataset, documents)


# Scoring -----------------------------------------------------------------------------------------------


def _digits(name: str, text: str | None) -> str | None:
    """The number a printed string spells, ignoring how its separators get read. Separates OCR from parsing."""
    if name in AMOUNT_NAMES:
        value = plain_amount(text)
        return value.rstrip("0").rstrip(".") if value and "." in value else value
    return None


def _outside_record(doc_id: str, kind: str, name: str, label: dict, field, page_text: str, **extra) -> dict:
    predicted = field.value if field else None
    correct = values_match(name, label["value"], predicted)
    wanted = _digits(name, label["printed"])
    digits_right = None
    if name in AMOUNT_NAMES:
        digits_right = correct or (wanted is not None and wanted == _digits(name, field.printed if field else None))
    return {
        "doc_id": doc_id, "kind": kind, "field": name, "truth": label["value"], "predicted": predicted,
        "correct": correct, "digits_right": digits_right, "confidence": field.confidence if field else 0.0,
        "needs_review": field.needs_review if field else True, "readable": squash(label["printed"]) in page_text, **extra,
    }


def score_outside(labels: dict, result, words: str) -> tuple[list[dict], dict]:
    """One record per labeled value, and one summary per document. Unlabeled fields aren't scored."""
    doc_id, page_text = labels["doc_id"], squash(words)
    records = [_outside_record(doc_id, "field", name, label, result.fields.get(name), page_text) for name, label in labels["fields"].items()]
    rows = [{key: (row.get(key) or {"value": None}) for key in ITEM_LABELS} for row in labels["items"]]
    pairs = align_rows(rows, result.line_items)
    for index, position in pairs:
        predicted = result.line_items[position] if position is not None else None
        for name in ITEM_LABELS:
            label = labels["items"][index].get(name)
            if label:
                cell = predicted.cells.get(name) if predicted else None
                records.append(_outside_record(doc_id, "cell", name, label, cell, page_text, row=index, missing_row=predicted is None))
    flagged = any(field.needs_review for field in result.fields.values()) or any(cell.needs_review for row in result.line_items for cell in row.cells.values())
    document = {
        "doc_id": doc_id, "status": result.status, "rows_labeled": len(rows), "rows_predicted": len(result.line_items),
        "rows_missing": sum(1 for _, position in pairs if position is None), "rows_extra": len(result.line_items) - sum(1 for _, position in pairs if position is not None),
        "all_labeled_right": bool(records) and all(record["correct"] for record in records) and result.status == "ok",
        "any_flagged": flagged or result.status != "ok", "labeled_values": len(records), "model_seconds": result.model_seconds,
    }
    return records, document


def _value_block(records: list[dict], threshold: float) -> dict:
    stats = skip_stats(records, threshold)
    with_digits = [record for record in records if record["digits_right"] is not None]
    return {
        "values": len(records), "right": sum(record["correct"] for record in records), "readable": sum(record["readable"] for record in records),
        "skipped": stats["skipped"], "coverage": stats["coverage"], "wrong_among_skipped": stats["wrong"], "error": stats["error"],
        "error_upper_95": wilson_upper(stats["wrong"], stats["skipped"]),
        "digits_right": sum(record["digits_right"] for record in with_digits), "with_digits": len(with_digits),
    }


def aggregate(manifest: dict, run_meta: dict, records: list[dict], documents: list[dict], failures: dict, threshold: float) -> dict:
    """The only output that gets committed: counts, with no text from any document."""
    fields = sorted({record["field"] for record in records})
    seconds = [document["model_seconds"] for document in documents if document["model_seconds"]]
    return {
        "dataset": manifest["dataset"],
        "added_after_heldout": True,
        "no_tuning": "Run once with the pipeline, prompt, weights and threshold used on the held-out set.",
        "model": run_meta.get("model"), "model_settings": run_meta.get("model_settings"),
        "git": run_meta.get("invocations", [{}])[0].get("git"), "run_created": run_meta.get("created"), "threshold": threshold,
        "all": _value_block(records, threshold),
        "by_field": {name: _value_block([record for record in records if record["field"] == name], threshold) for name in fields},
        "documents": {
            "documents": len(documents), "failed": sum(document["status"] != "ok" for document in documents),
            "with_labels": sum(document["labeled_values"] > 0 for document in documents),
            "all_labeled_right": sum(document["all_labeled_right"] for document in documents),
            "nothing_flagged": sum(not document["any_flagged"] for document in documents),
            "wrong_though_nothing_flagged": sum(not document["any_flagged"] and not document["all_labeled_right"] for document in documents if document["labeled_values"]),
            "rows_labeled": sum(document["rows_labeled"] for document in documents), "rows_missing": sum(document["rows_missing"] for document in documents),
            "rows_extra": sum(document["rows_extra"] for document in documents),
            "mean_seconds": round(sum(seconds) / len(seconds), 1) if seconds else None,
        },
        "model_failures": failures,
    }


def score_run(data: Path, run_dir: Path, cache_dir: Path, settings: Settings, out: Path) -> dict:
    weights, threshold = load_calibration()
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    records: list[dict] = []
    documents: list[dict] = []
    failures: dict[str, int] = {}
    for path in sorted((run_dir / "readings").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc_id = payload["doc_id"]
        labels = json.loads((data / f"{doc_id}.labels.json").read_text(encoding="utf-8"))
        pdf = (data / f"{doc_id}.pdf").read_bytes()
        result, words = result_for(None, payload, pdf, settings, cache_dir, weights=weights, threshold=threshold)
        doc_records, document = score_outside(labels, result, words)
        records += doc_records
        documents.append(document)
        for failure in [*payload.get("failure_history", []), {"code": payload.get("error_code")}]:
            if failure.get("code"):
                failures[failure["code"]] = failures.get(failure["code"], 0) + 1
    # The details hold the dataset's text, so they stay beside the answers, outside the repo.
    (run_dir / "scored.jsonl").write_bytes("".join(json.dumps(record) + "\n" for record in records).encode("utf-8"))
    (run_dir / "documents.jsonl").write_bytes("".join(json.dumps(document) + "\n" for document in documents).encode("utf-8"))
    run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    summary = aggregate(manifest, run_meta, records, documents, failures, threshold)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(summary, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m review_desk.evaluation.outside", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cord = sub.add_parser("prepare-cord")
    cord.add_argument("source", type=Path)
    cord.add_argument("--out", type=Path, required=True)
    fatura = sub.add_parser("prepare-fatura")
    fatura.add_argument("source", type=Path)
    fatura.add_argument("--out", type=Path, required=True)
    fatura.add_argument("--count", type=int, default=50)
    for name in ("run", "score"):
        command = sub.add_parser(name)
        command.add_argument("--data", type=Path, required=True)
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--cache", type=Path, required=True)
        if name == "score":
            command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "prepare-cord":
        print(f"Prepared {len(prepare_cord(args.source, args.out)['documents'])} documents in {args.out}")
    elif args.command == "prepare-fatura":
        print(f"Prepared {len(prepare_fatura(args.source, args.out, count=args.count)['documents'])} documents in {args.out}")
    elif args.command == "run":
        settings = Settings.from_env()
        outcome = run_split(
            args.data.name, count=None, conditions=(CONDITION,), run_dir=args.run, data_root=args.data.parent, cache_dir=args.cache,
            settings=settings, retry_failures=True, log=lambda message: print(message, flush=True),
        )
        print(outcome, flush=True)
    else:
        summary = score_run(args.data, args.run, args.cache, Settings.from_env(), args.out)
        print(json.dumps(summary["all"]), json.dumps(summary["documents"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

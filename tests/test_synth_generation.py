"""The generator is reproducible and its splits don't leak into each other."""

from __future__ import annotations

import json

from review_desk.synth import content
from review_desk.synth.__main__ import main as cli_main
from review_desk.synth.content import make_document, planned_layout
from review_desk.synth.generate import DEFAULT_SEED, generate_split, render_all


def _bytes_for(split, index, seed):
    doc = make_document(split, index, seed)
    return {condition: (pdf, json.dumps(truth, sort_keys=True)) for condition, (pdf, truth) in render_all(doc).items()}


def test_same_seed_gives_identical_files_in_every_condition():
    for index in (0, 2, 7):
        assert _bytes_for("dev", index, DEFAULT_SEED) == _bytes_for("dev", index, DEFAULT_SEED)


def test_different_seed_gives_different_documents():
    first = make_document("dev", 0, DEFAULT_SEED)
    second = make_document("dev", 0, DEFAULT_SEED + 1)
    assert (first.vendor, first.total) != (second.vendor, second.total)


def test_generate_split_writes_files_that_match_the_manifest(tmp_path):
    import hashlib

    manifest = generate_split("dev", 3, tmp_path)
    assert manifest["renderings"] == 9
    assert manifest["synthetic"] is True
    folder = tmp_path / "dev"
    for entry in manifest["documents"]:
        assert hashlib.sha256((folder / entry["pdf"]).read_bytes()).hexdigest() == entry["pdf_sha256"]
        truth_file = folder / f"{entry['doc_id']}.truth.json"
        assert hashlib.sha256(truth_file.read_bytes()).hexdigest() == entry["truth_sha256"]
        assert entry["doc_id"].startswith("synthetic-")
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8")) == manifest


def test_rerun_of_a_split_reproduces_the_manifest(tmp_path):
    assert generate_split("dev", 2, tmp_path / "a") == generate_split("dev", 2, tmp_path / "b")


def test_dev_split_never_uses_held_out_only_layouts():
    reserved = set(content.HELDOUT_ONLY_INVOICE_LAYOUTS) | set(content.HELDOUT_ONLY_RECEIPT_LAYOUTS)
    used = {planned_layout("dev", index)[1] for index in range(300)}
    assert not used & reserved
    used_samples = {planned_layout("samples", index)[1] for index in range(300)}
    assert not used_samples & reserved
    assert reserved <= {planned_layout("heldout", index)[1] for index in range(300)}


def test_every_layout_is_reached_and_balanced():
    counts: dict[str, int] = {}
    for index in range(210):
        counts[planned_layout("dev", index)[1]] = counts.get(planned_layout("dev", index)[1], 0) + 1
    assert set(counts) == set(content.INVOICE_LAYOUTS) | set(content.RECEIPT_LAYOUTS)
    assert max(counts.values()) - min(counts.values()) <= 7 * 4  # Two invoices per receipt by design.


def test_vendor_pools_are_disjoint_between_splits():
    assert not set(content._VENDOR_STEMS["dev"]) & set(content._VENDOR_STEMS["heldout"])
    for index in range(150):
        doc = make_document("heldout", index, DEFAULT_SEED)
        assert not any(stem in doc.vendor.name for stem in content._VENDOR_STEMS["dev"])


def test_document_ids_are_labeled_synthetic_and_unique():
    ids = [make_document(split, i, DEFAULT_SEED).base_id for split in ("dev", "heldout") for i in range(100)]
    assert len(ids) == len(set(ids))
    assert all(doc_id.startswith("synthetic-") for doc_id in ids)


def test_contact_details_are_reserved_for_fiction():
    for split in ("dev", "heldout"):
        for index in range(120):
            vendor = make_document(split, index, DEFAULT_SEED).vendor
            assert vendor.email.endswith(".example")
            assert vendor.web.endswith(".example")
            assert vendor.phone.startswith("(555) 555-01")


def test_command_line_generates_and_draws_overlay(tmp_path, capsys):
    assert cli_main(["generate", "--split", "samples", "--count", "1", "--out", str(tmp_path), "--conditions", "clean"]) == 0
    pdf = next((tmp_path / "samples").glob("*.pdf"))
    out = tmp_path / "overlay.png"
    assert cli_main(["overlay", str(pdf), "--out", str(out)]) == 0
    assert out.stat().st_size > 1000
    assert "synthetic" in capsys.readouterr().out

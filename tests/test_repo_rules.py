"""Repo-level rules: permissive dependencies, nothing internal tracked, no real contact details."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
#: Only reserved test domains and the git noreply style are acceptable in a public repo.
ALLOWED_EMAIL = re.compile(r"(\.example|@example\.com|@users\.noreply\.github\.com|@anthropic\.com)$", re.IGNORECASE)
BINARY = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webm", ".mp4", ".ico"}


def _load_license_checker():
    spec = importlib.util.spec_from_file_location("check_licenses", REPO / "scripts" / "check_licenses.py")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tracked_files() -> list[Path]:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    output = subprocess.run(
        ["git", "-c", "safe.directory=*", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / line for line in output.splitlines() if line]


def test_all_pinned_dependencies_are_permissively_licensed():
    checker = _load_license_checker()
    dependencies = checker.inspect()
    assert dependencies
    assert [d.name for d in dependencies if d.problem] == []


def test_license_checker_catches_copyleft_and_unknown_packages(tmp_path, monkeypatch):
    checker = _load_license_checker()
    pins = tmp_path / "requirements.txt"
    pins.write_text("PyMuPDF==1.24.0\nsome-unknown-package==1.0\n", encoding="utf-8")
    monkeypatch.setattr(checker, "PIN_FILES", {"runtime": pins})
    problems = {d.name: d.problem for d in checker.inspect()}
    assert problems["PyMuPDF"] == "banned package"
    assert problems["some-unknown-package"] == "no hand-verified license entry"


def test_third_party_license_list_is_current():
    checker = _load_license_checker()
    assert (REPO / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8") == checker.render(checker.inspect())


def test_requirements_are_exact_pins():
    checker = _load_license_checker()
    for path in checker.PIN_FILES.values():
        assert checker.read_pins(path)


def test_internal_and_local_files_are_ignored():
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    for name in ("HANDOFF.md", ".env", "uploads/x.pdf", "data/synthetic/x.pdf", "review.db"):
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "check-ignore", "-q", name], cwd=REPO,
        )
        assert result.returncode == 0, f"{name} should be ignored"


def test_no_real_email_addresses_in_tracked_text():
    offenders = []
    for path in _tracked_files():
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in EMAIL.findall(text):
            if not ALLOWED_EMAIL.search(match):
                offenders.append((path.relative_to(REPO).as_posix(), match))
    assert offenders == []


def test_pdfs_are_never_treated_as_text_by_git():
    """Git's autocrlf can rewrite a PDF's bytes if it guesses the file is text."""
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    output = subprocess.run(
        ["git", "-c", "safe.directory=*", "check-attr", "text", "diff", "--", "samples/x.pdf"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    assert "text: unset" in output
    assert "diff: unset" in output


def test_committed_samples_match_their_manifest_byte_for_byte():
    manifest = json.loads((REPO / "samples" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert manifest["renderings"] == len(manifest["documents"]) > 0
    for entry in manifest["documents"]:
        pdf = (REPO / "samples" / entry["pdf"]).read_bytes()
        assert hashlib.sha256(pdf).hexdigest() == entry["pdf_sha256"], entry["pdf"]
        truth = (REPO / "samples" / f"{entry['doc_id']}.truth.json").read_bytes()
        assert hashlib.sha256(truth).hexdigest() == entry["truth_sha256"], entry["doc_id"]
        assert json.loads(truth)["synthetic"] is True


def test_license_is_mit_and_names_the_copyright_holder():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Noah Hicks" in text

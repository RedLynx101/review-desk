"""Command line for the pipeline.

    python -m review_desk extract invoice.pdf
    python -m review_desk extract invoice.pdf --out result.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Settings
from .intake import RejectedDocument
from .pipeline import extract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m review_desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    extract_command = commands.add_parser("extract", help="read one PDF and print the checked fields as JSON")
    extract_command.add_argument("pdf", type=Path)
    extract_command.add_argument("--out", type=Path, help="write the JSON here instead of printing it")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    try:
        result = extract(args.pdf.read_bytes(), settings)
    except RejectedDocument as rejected:
        print(f"Rejected ({rejected.code}): {rejected.message}", file=sys.stderr)
        return 2
    text = result.model_dump_json(indent=1)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

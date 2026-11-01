"""Command line for the synthetic document generator.

    python -m review_desk.synth generate --split dev --count 60
    python -m review_desk.synth overlay data/synthetic/dev/some.clean.pdf --out overlay.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import ImageDraw

from .content import SPLITS
from .degrade import CONDITIONS, rasterize
from .generate import DEFAULT_SEED, generate_split

DEFAULT_OUT = Path("data") / "synthetic"


def overlay(pdf_path: Path, out_path: Path, dpi: int = 110) -> list[Path]:
    """Draw the truth boxes on each page, to check by eye that they sit on the right ink."""
    truth_path = pdf_path.parent / (pdf_path.name[: -len(".pdf")] + ".truth.json")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    pages = rasterize(pdf_path.read_bytes(), dpi)
    scale = dpi / 72.0

    def draw_entries(entries, color):
        for entry in entries:
            draw = ImageDraw.Draw(pages[entry["page"]])
            x0, top, x1, bottom = (value * scale for value in entry["bbox"])
            draw.rectangle((x0, top, x1, bottom), outline=color, width=1)

    for field in truth["fields"].values():
        draw_entries(field["locations"], (220, 40, 40))
    for row in truth["line_items"]:
        for cell in row.values():
            draw_entries(cell["locations"], (30, 120, 220))

    written = []
    for number, page in enumerate(pages):
        path = out_path if len(pages) == 1 else out_path.with_name(f"{out_path.stem}-p{number + 1}{out_path.suffix}")
        page.save(path)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m review_desk.synth", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="write synthetic PDFs and truth files")
    generate.add_argument("--split", choices=SPLITS, required=True)
    generate.add_argument("--count", type=int, required=True, help="base documents; each renders once per condition")
    generate.add_argument("--start", type=int, default=0, help="first document index")
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    generate.add_argument("--out", type=Path, default=DEFAULT_OUT)
    generate.add_argument("--conditions", default=",".join(CONDITIONS), help="comma separated subset of clean,scanned,degraded")

    draw = commands.add_parser("overlay", help="draw truth boxes over a generated PDF")
    draw.add_argument("pdf", type=Path)
    draw.add_argument("--out", type=Path, required=True)
    draw.add_argument("--dpi", type=int, default=110)

    args = parser.parse_args(argv)
    if args.command == "generate":
        conditions = tuple(name.strip() for name in args.conditions.split(",") if name.strip())
        unknown = set(conditions) - set(CONDITIONS)
        if unknown:
            parser.error(f"unknown conditions: {sorted(unknown)}")
        manifest = generate_split(args.split, args.count, args.out, master_seed=args.seed, conditions=conditions, start=args.start)
        print(f"{manifest['renderings']} synthetic renderings of {args.count} documents in {args.out / args.split}")
    else:
        for path in overlay(args.pdf, args.out, args.dpi):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

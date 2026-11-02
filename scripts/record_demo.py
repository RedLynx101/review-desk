"""Record the demo and gallery images from the real app, on synthetic documents, with no model call.

    python -m review_desk.synth generate --split dev --count 15
    python scripts/record_demo.py --out docs/media

The server runs in replay mode: it answers from a committed evaluation run. Screencast frames from the browser
become an H.264 MP4 through ffmpeg, which has to be on PATH or passed with --ffmpeg. The closing numbers are
read from the committed results at recording time, so they can't drift from the report.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from review_desk.evaluation.compare import Run, by_condition, by_quality, skip_stats

PORT = 8793
WIDTH, HEIGHT = 1440, 900
REVIEWER = "Fictitia Sampleton"
FILES = (
    "synthetic-dev-0009-invoice-banner.scanned.pdf",
    "synthetic-dev-0012-invoice-ledger.scanned.pdf",
    "synthetic-dev-0008-receipt-card_slip.clean.pdf",
    "synthetic-dev-0000-invoice-classic.degraded.pdf",
)
CORRECTED_VENDOR = "Larkspindle Catering Kitchen & Sons Ltd"
BROWSER_CHANNELS = ("chrome", "msedge", None)

OVERLAY = """
window.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.id = 'demo-cursor';
  Object.assign(dot.style, {position: 'fixed', left: '-40px', top: '-40px', width: '18px', height: '18px', borderRadius: '50%',
    background: 'rgba(29,78,216,.28)', border: '2px solid #1d4ed8', zIndex: 100000, pointerEvents: 'none', transform: 'translate(-50%,-50%)'});
  document.documentElement.appendChild(dot);
  window.addEventListener('mousemove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
  const bar = document.createElement('div');
  bar.id = 'demo-caption';
  Object.assign(bar.style, {position: 'fixed', left: '50%', bottom: '18px', transform: 'translateX(-50%)', maxWidth: '1120px', padding: '11px 20px',
    background: 'rgba(15,23,42,.94)', color: '#fff', font: '500 18px/1.35 "Segoe UI", system-ui, sans-serif', borderRadius: '8px', zIndex: 100001,
    pointerEvents: 'none', textAlign: 'center', display: 'none'});
  document.documentElement.appendChild(bar);
});
"""


def wait_for_server(port: int, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/info", timeout=2):
                return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("The demo server didn't start.")


def launch(playwright):
    for channel in BROWSER_CHANNELS:
        try:
            return playwright.chromium.launch(channel=channel) if channel else playwright.chromium.launch()
        except Exception:  # A channel that isn't installed raises at launch. Try the next one.
            continue
    raise SystemExit("No browser found. Install Chrome or Edge, or run `playwright install chromium`.")


class Recorder:
    """Save each screencast frame with its time, then turn them into a video."""

    def __init__(self, context, page, folder: Path):
        self.folder = folder
        self.frames: list[tuple[Path, float]] = []
        self.cuts: set[int] = set()
        self.session = context.new_cdp_session(page)
        self.session.on("Page.screencastFrame", self._frame)
        self.resume()

    def resume(self) -> None:
        # A late frame can arrive after a pause, so the cut goes on whichever frame came last.
        if self.frames:
            self.cuts.add(len(self.frames) - 1)
        self.session.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "everyNthFrame": 1})

    def pause(self) -> None:
        """Leave a wait out of the video. The frame before it holds briefly rather than for the whole wait."""
        self.session.send("Page.stopScreencast")

    def _frame(self, event: dict) -> None:
        path = self.folder / f"f{len(self.frames):05d}.jpg"
        path.write_bytes(base64.b64decode(event["data"]))
        self.frames.append((path, event["metadata"]["timestamp"]))
        self.session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def stop(self) -> None:
        self.session.send("Page.stopScreencast")

    def encode(self, ffmpeg: str, out: Path, tail_seconds: float = 1.0) -> None:
        listing = self.folder / "frames.txt"
        lines = []
        for index, ((path, stamp), (_, following)) in enumerate(zip(self.frames, self.frames[1:])):
            seconds = 0.8 if index in self.cuts else max(following - stamp, 0.01)
            lines += [f"file '{path.name}'", f"duration {seconds:.3f}"]
        lines += [f"file '{self.frames[-1][0].name}'", f"duration {tail_seconds}", f"file '{self.frames[-1][0].name}'"]
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-vf", "fps=30,format=yuv420p",
             "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-movflags", "+faststart", str(out)],
            check=True,
        )


def closing_card(results: Path) -> str:
    """The numbers a person checking scanned paperwork needs, from the committed held-out run."""
    run = Run(results / "runs" / "heldout-bonsai")
    rows = []
    for label, condition in (("Clean PDFs", "clean"), ("Light scans", "scanned"), ("Degraded scans", "degraded")):
        stats = skip_stats(by_condition(run.records, condition))
        rows.append((label, f"{stats['coverage']:.1%}", f"{stats['wrong']} of {stats['skipped']}", f"{stats['error']:.2%}", condition == "degraded"))
    heavy = [record for record in by_quality(run.records, "heavy") if record["truth"] is not None]
    heavy_right = sum(record["correct"] for record in heavy) / len(heavy)
    cells = "".join(
        f"<tr class='{'warn' if warn else ''}'><th>{html.escape(label)}</th><td>{skip}</td><td>{wrong}</td><td>{error}</td></tr>"
        for label, skip, wrong, error, warn in rows
    )
    outside = []
    receipts_note = ""
    for name, label in (("cord-test.json", "Real receipts (CORD v2, labeled fields only)"), ("fatura-sample.json", "Template-generated invoices (FATURA, sample)")):
        path = results / "outside" / name
        if path.is_file():
            block = json.loads(path.read_text(encoding="utf-8"))["all"]
            if name == "cord-test.json":
                receipts_note = f"<p class='heavy'>Real receipt photos: {block['right'] / block['values']:.1%} of labeled values came out right, so a person checks nearly everything.</p>"
            outside.append(
                f"<tr><th>{html.escape(label)}</th><td>{block['coverage']:.1%}</td><td>{block['wrong_among_skipped']} of {block['skipped']}</td><td>{block['error']:.2%}</td></tr>"
            )
    outside_table = (
        "<h2>Added after the held-out run, on documents this repo didn't generate</h2><table>"
        "<thead><tr><th></th><td>Skip review</td><td>Wrong among skipped</td><td>Error</td></tr></thead><tbody>" + "".join(outside) + "</tbody></table>" if outside else ""
    )
    return f"""<!doctype html><meta charset="utf-8"><title>Results</title><style>
    html,body{{margin:0;height:100%;background:#f8fafc;color:#0f172a;font-family:"Segoe UI",system-ui,sans-serif}}
    main{{box-sizing:border-box;width:{WIDTH}px;height:{HEIGHT}px;padding:36px 110px;display:flex;flex-direction:column;justify-content:center}}
    h1{{font-size:38px;margin:0 0 8px;font-weight:650}} p{{margin:0}} .sub{{font-size:19px;color:#475569;margin-bottom:26px}}
    h2{{font-size:18px;font-weight:600;color:#334155;margin:24px 0 6px}}
    table{{border-collapse:collapse;width:100%;font-size:22px;background:#fff;border:1px solid #e2e8f0}}
    th,td{{padding:12px 20px;border-bottom:1px solid #e2e8f0;text-align:right;font-weight:400}} th{{text-align:left}}
    thead th,thead td{{font-size:15px;color:#475569;font-weight:600;text-align:right}} thead th:first-child{{text-align:left}}
    tr.warn th,tr.warn td{{background:#fff7ed;color:#9a3412;font-weight:650}}
    .heavy{{font-size:22px;line-height:1.35;margin-top:16px;color:#9a3412;font-weight:600}}
    .foot{{font-size:18px;color:#475569;margin-top:20px}}
    </style><main>
    <h1>What the held-out test measured</h1>
    <p class="sub">{len(run.documents)} synthetic renderings the pipeline wasn't tuned on. {html.escape(run.short)} on a local GPU, threshold 0.90.</p>
    <table><thead><tr><th></th><td>Skip review</td><td>Wrong among skipped</td><td>Error</td></tr></thead><tbody>{cells}</tbody></table>
    {outside_table}
    <p class="heavy">Heavy scan damage: {heavy_right:.1%} of printed values came out right. A damaged scan needs a person on every value, whatever its score says.</p>
    {receipts_note}
    <p class="foot">No client deployment yet. Built with AI coding agents under Noah Hicks's review.</p>
    </main>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("docs/media"))
    parser.add_argument("--documents", type=Path, default=Path("data/synthetic/dev"))
    parser.add_argument("--replay", type=Path, default=Path("results/runs/dev-bonsai"))
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"

    # On Windows the server can hold its database open for a moment after it stops.
    with tempfile.TemporaryDirectory(prefix="review-desk-demo-", ignore_cleanup_errors=True) as scratch:
        scratch_path = Path(scratch)
        frames = scratch_path / "frames"
        frames.mkdir()
        server = subprocess.Popen(
            [sys.executable, "-m", "review_desk.desk", "serve", "--port", str(args.port), "--data", str(scratch_path / "desk"), "--replay", str(args.replay)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_server(args.port)
            with sync_playwright() as playwright:
                browser = launch(playwright)
                context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, accept_downloads=True)
                context.add_init_script(f"try {{ localStorage.setItem('reviewer', {json.dumps(REVIEWER)}); }} catch (e) {{}}")
                context.add_init_script(OVERLAY)
                page = context.new_page()
                page.goto(f"{base}/")
                page.wait_for_selector(".empty")
                recorder = Recorder(context, page, frames)

                def say(text: str, hold: int = 0) -> None:
                    page.evaluate("(t) => { const b = document.getElementById('demo-caption'); b.textContent = t; b.style.display = t ? 'block' : 'none'; }", text)
                    if hold:
                        page.wait_for_timeout(hold)

                def point(locator, hold: int = 350) -> None:
                    box = locator.bounding_box()
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18)
                    page.wait_for_timeout(hold)

                def click(locator, hold: int = 350) -> None:
                    point(locator, hold)
                    locator.click()

                def shoot(name: str) -> None:
                    """Gallery images leave out the demo's own caption and cursor."""
                    page.evaluate("for (const id of ['demo-caption', 'demo-cursor']) { const e = document.getElementById(id); if (e) e.style.visibility = 'hidden'; }")
                    page.screenshot(path=args.out / name)
                    page.evaluate("for (const id of ['demo-caption', 'demo-cursor']) { const e = document.getElementById(id); if (e) e.style.visibility = 'visible'; }")

                def export_status(document_id: str) -> int:
                    return page.evaluate(f"fetch('/api/documents/{document_id}/export.csv', {{headers: {{'X-Reviewer': {json.dumps(REVIEWER)}}}}}).then(r => r.status)")

                say("Review Desk runs on one machine, with no outside connections. Every document in this demo is synthetic.", 3800)
                say("Upload invoices and receipts as PDFs. This recording replays saved answers from a real evaluation run, so no model is called.", 800)
                page.set_input_files("#file-input", [str(args.documents / name) for name in FILES])
                page.wait_for_timeout(2600)
                recorder.pause()
                for _ in range(200):
                    page.wait_for_timeout(600)
                    if not page.evaluate("fetch('/api/documents').then(r => r.json()).then(d => d.documents.some(x => x.status === 'queued' || x.status === 'reading'))"):
                        break
                documents = {entry["filename"]: entry for entry in page.evaluate("fetch('/api/documents').then(r => r.json()).then(d => d.documents)")}
                page.goto(f"{base}/#/")
                page.reload()
                page.wait_for_selector("table.docs")
                recorder.resume()
                say("The queue shows which documents need a person, and how many values on each.", 3200)
                shoot("02-queue.png")

                document = documents[FILES[0]]
                say("")
                click(page.get_by_text(FILES[0]).first)
                page.wait_for_selector(".panel")
                page.wait_for_timeout(700)
                say("Flagged values come first. Each score comes from checks you can read, not from the model's opinion of itself.", 3200)
                click(page.locator('.field[data-path="vendor_name"]'))
                say("Pick a value and its source lights up on the page. OCR dropped spaces in this name, so it scored low and waits for a person.", 4400)
                shoot("01-review-screen.png")

                status = export_status(document["id"])
                point(page.locator("#export-csv"), 300)
                say(f"Export stays locked until a person approves, and the server enforces it: a direct request gets HTTP {status}.", 4000)

                page.locator('.field[data-path="vendor_name"]').click()
                say("E corrects the value. The old and new values both go in the audit log.", 900)
                page.keyboard.press("e")
                page.locator("input.edit").wait_for()
                page.locator("input.edit").fill("")
                page.locator("input.edit").type(CORRECTED_VENDOR, delay=32)
                page.wait_for_timeout(500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(900)

                say("N jumps to the next flagged value, Enter confirms it. Every flagged value has to be cleared before approval.", 600)
                for _ in range(40):
                    current = page.evaluate(f"fetch('/api/documents/{document['id']}').then(r => r.json())")
                    if not current["remaining"]:
                        break
                    page.keyboard.press("n")
                    page.wait_for_timeout(450)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(650)
                page.locator('.field[data-path="vendor_name"]').click()
                page.wait_for_timeout(1000)

                say("A approves. The approval names the reviewer and covers these exact values.", 500)
                page.keyboard.press("a")
                page.locator("#approve-dialog").wait_for()
                page.wait_for_timeout(2500)
                shoot("03-approval.png")
                page.keyboard.press("Enter")
                page.locator("#approved-note").wait_for()
                say("Approved. Now the export works.", 1000)
                point(page.locator("#export-csv"), 400)
                with page.expect_download():
                    page.locator("#export-csv").click()
                page.wait_for_timeout(2000)

                say("Change anything after approval and the approval is void.", 700)
                click(page.locator('.field[data-path="purchase_order"]'))
                page.keyboard.press("e")
                page.locator("input.edit").wait_for()
                page.locator("input.edit").type("PO-4417", delay=60)
                page.wait_for_timeout(400)
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                status = export_status(document["id"])
                say(f"The same export request now gets HTTP {status}. It takes a fresh approval to unlock it.", 3800)

                page.goto(f"{base}/#/audit/{document['id']}")
                page.locator("table.audit").wait_for()
                page.wait_for_timeout(500)
                say("Every action lands in a hash-chained log, including the refused exports.", 2400)
                click(page.locator("#verify"))
                page.locator(".verify.good").wait_for()
                say("Verify recomputes the chain. Editing, removing or reordering an event breaks it.", 3400)
                shoot("04-audit-log.png")

                page.goto(f"{base}/#/doc/{documents[FILES[3]]['id']}")
                page.wait_for_selector(".panel")
                page.wait_for_timeout(700)
                say("This is a badly damaged scan. OCR can't read most of it, so nearly every value is flagged, and even the rest need checking.", 5000)
                shoot("05-damaged-scan.png")

                page.goto("about:blank")
                page.set_content(closing_card(args.results))
                page.wait_for_timeout(9000)
                page.screenshot(path=args.out / "06-results.png")
                page.wait_for_timeout(300)
                recorder.stop()
                browser.close()
            recorder.encode(args.ffmpeg, args.out / "demo.mp4")
        finally:
            server.terminate()
            server.wait(timeout=15)
    print(f"Wrote {args.out / 'demo.mp4'} and the gallery images.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

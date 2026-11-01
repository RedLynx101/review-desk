"""Run the review desk.

    python -m review_desk.desk serve
    python -m review_desk.desk serve --replay results/runs/dev-bonsai
    python -m review_desk.desk verify

`--replay` answers from a finished evaluation run instead of calling a model, so the screen can be tried
without one. `verify` recomputes the audit chain and prints the newest hash. Keep that hash somewhere
else if you need to prove later that the log wasn't rebuilt.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sqlite3
import sys
from pathlib import Path

from ..config import Settings, load_dotenv

DEFAULT_ROOT = Path("data") / "desk"
DEFAULT_PORT = 8765


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(args) -> int:
    import uvicorn

    from .app import create_app
    from .store import Store
    from .worker import Pipeline, Worker, live_model_step, replay_model_step

    if not _is_loopback(args.host) and not args.allow_network:
        print(f"Refusing to listen on {args.host}. There's no login, so this stays on localhost unless you pass --allow-network.", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    store = Store(args.data)
    model_step = replay_model_step(args.replay) if args.replay else live_model_step(settings)
    worker = Worker(Pipeline(store, settings, model_step))
    hosts = {"localhost", "127.0.0.1", "::1"}
    if args.allow_network:
        hosts.add(args.host.lower())
    app = create_app(store, settings, on_new_document=worker.notify, allowed_hosts=hosts, mode="replay" if args.replay else "live")
    worker.start()
    print(f"Review Desk: http://{args.host}:{args.port}  data in {args.data}" + (f"  replaying {args.replay}" if args.replay else f"  model {settings.model_name} at {settings.model_base_url}"), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    worker.stop()
    return 0


def verify(args) -> int:
    from .audit import AuditLog

    database = Path(args.data) / "desk.db"
    if not database.is_file():
        print(f"No audit log at {args.data}.", file=sys.stderr)
        return 2
    # Read-only, because a check shouldn't be able to change what it's checking.
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        status = AuditLog(connection, Path(args.data) / "audit.head").verify()
    finally:
        connection.close()
    if status.ok:
        print(f"OK: {status.events} events, chain intact. Newest hash: {status.head}")
        return 0
    print(f"BROKEN after {status.events} good events: {status.problem}")
    return 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m review_desk.desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    serve_command = commands.add_parser("serve", help="start the review screen")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=int(os.environ.get("REVIEW_DESK_PORT", DEFAULT_PORT)))
    serve_command.add_argument("--data", type=Path, default=DEFAULT_ROOT)
    serve_command.add_argument("--replay", type=Path, help="answer from a finished evaluation run instead of calling a model")
    serve_command.add_argument("--allow-network", action="store_true", help="listen on a non-local address. There's no login, so put your own access control in front.")
    verify_command = commands.add_parser("verify", help="recompute the audit chain")
    verify_command.add_argument("--data", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    return serve(args) if args.command == "serve" else verify(args)


if __name__ == "__main__":
    raise SystemExit(main())

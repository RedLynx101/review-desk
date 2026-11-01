"""The HTTP layer. Thin on purpose: every rule lives in `store.py`, and this only translates.

No login exists, because this binds to localhost for one person or a small trusted group. A web page
open in the same browser could still try to reach a localhost server, so state-changing requests need
a custom header (which forces a CORS preflight this server never approves), the Host header is checked
against DNS rebinding, and a strict content security policy keeps page scripts local.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import Settings
from .export import to_csv, to_json
from .store import ReviewError, Store

STATIC_DIR = Path(__file__).with_name("static")
JSON_BODY_LIMIT = 64 * 1024
ANONYMOUS = "unnamed reviewer"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def _hostname(authority: str) -> str:
    """The host part of "host:port", including IPv6 forms such as "[::1]:8765"."""
    value = authority.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if ":" in value else value


class Guard(BaseHTTPMiddleware):
    def __init__(self, app, allowed_hosts: set[str]):
        super().__init__(app)
        self.allowed_hosts = allowed_hosts

    async def dispatch(self, request: Request, call_next):
        if _hostname(request.headers.get("host", "")) not in self.allowed_hosts:
            return JSONResponse({"code": "bad_host", "message": "This server only answers on its own address."}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-review-desk") != "1":
                return JSONResponse({"code": "missing_header", "message": "State-changing requests must come from the app."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and _hostname(origin.rsplit("//", 1)[-1]) not in self.allowed_hosts:
                return JSONResponse({"code": "bad_origin", "message": "Requests from other sites aren't accepted."}, status_code=403)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            if name == "Cache-Control" and "cache-control" in response.headers:
                continue
            response.headers[name] = value
        return response


async def _read_json(request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > JSON_BODY_LIMIT:
            raise ReviewError("too_large", "That request is too large.", status=413)
    if not body:
        return {}
    try:
        data = json.loads(bytes(body))
    except ValueError as error:
        raise ReviewError("bad_json", "The request body wasn't valid JSON.", status=400) from error
    if not isinstance(data, dict):
        raise ReviewError("bad_json", "The request body must be a JSON object.", status=400)
    return data


def _review_error(_: Request, error: ReviewError) -> JSONResponse:
    return JSONResponse({"code": error.code, "message": error.message, **error.extra}, status_code=error.status)


def create_app(store: Store, settings: Settings, *, on_new_document: Callable[[], None] = lambda: None, allowed_hosts: set[str] | None = None, mode: str = "live") -> Starlette:
    """Build the app around a store. `on_new_document` wakes the worker."""
    hosts = allowed_hosts or {"localhost", "127.0.0.1", "::1"}

    async def index(_: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def info(_: Request) -> JSONResponse:
        return JSONResponse({"model": settings.model_name, "endpoint": settings.model_base_url, "mode": mode})

    async def list_documents(request: Request) -> JSONResponse:
        return JSONResponse({"documents": store.list_documents(status=request.query_params.get("status") or None)})

    async def upload(request: Request) -> JSONResponse:
        """The body is the PDF itself. Streaming it means an oversized upload stops at the limit."""
        filename = request.query_params.get("filename", "")
        actor = request.headers.get("x-reviewer") or ANONYMOUS
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > settings.max_upload_bytes:
                store.record_refused_upload(filename, code="too_large", byte_size=len(data), actor=actor)
                limit = settings.max_upload_bytes // (1024 * 1024)
                raise ReviewError("too_large", f"The file is larger than the {limit} MB limit.", status=413)
        if not data:
            store.record_refused_upload(filename, code="empty", byte_size=0, actor=actor)
            raise ReviewError("empty", "The file is empty.", status=400)
        if b"%PDF-" not in bytes(data[:1024]):
            store.record_refused_upload(filename, code="not_a_pdf", byte_size=len(data), actor=actor)
            raise ReviewError("not_a_pdf", "That file isn't a PDF.", status=415)
        document = store.add_document(filename, bytes(data), actor=actor)
        on_new_document()
        return JSONResponse(document, status_code=201)

    async def get_document(request: Request) -> JSONResponse:
        return JSONResponse(store.get_document(request.path_params["document_id"]))

    async def page_image(request: Request) -> Response:
        document = store.get_document(request.path_params["document_id"])
        number = request.path_params["number"]
        for page in document["pages"]:
            if page["number"] == number:
                return FileResponse(store.pages / page["image"], headers={"Cache-Control": "private, max-age=3600"})
        raise ReviewError("not_found", "No such page.", status=404)

    async def edit_field(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.edit_field(
            request.path_params["document_id"], request.path_params["path"], body.get("value"),
            reviewer=body.get("reviewer"), revision=body.get("revision"),
        ))

    async def confirm_field(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.confirm_field(request.path_params["document_id"], request.path_params["path"], reviewer=body.get("reviewer"), revision=body.get("revision")))

    async def add_row(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.add_row(request.path_params["document_id"], reviewer=body.get("reviewer"), revision=body.get("revision")))

    async def delete_row(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.delete_row(request.path_params["document_id"], request.path_params["row_key"], reviewer=body.get("reviewer"), revision=body.get("revision")))

    async def acknowledge_check(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.acknowledge_check(request.path_params["document_id"], request.path_params["name"], reviewer=body.get("reviewer"), revision=body.get("revision")))

    async def approve(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.approve(request.path_params["document_id"], reviewer=body.get("reviewer"), revision=body.get("revision")))

    async def retry(request: Request) -> JSONResponse:
        body = await _read_json(request)
        document = store.retry(request.path_params["document_id"], reviewer=body.get("reviewer"))
        on_new_document()
        return JSONResponse(document)

    async def manual(request: Request) -> JSONResponse:
        body = await _read_json(request)
        return JSONResponse(store.begin_manual_entry(request.path_params["document_id"], reviewer=body.get("reviewer")))

    def _export(request: Request, format_name: str) -> tuple:
        actor = request.headers.get("x-reviewer") or ANONYMOUS
        return store.export_record(request.path_params["document_id"], actor=actor[:80], format_name=format_name)

    async def export_json(request: Request) -> Response:
        record = _export(request, "json")
        name = f"review-desk-{record.document_id[:8]}.json"
        return Response(to_json(record), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def export_csv(request: Request) -> Response:
        record = _export(request, "csv")
        name = f"review-desk-{record.document_id[:8]}.csv"
        return Response(to_csv(record), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def audit_events(request: Request) -> JSONResponse:
        return JSONResponse(store.audit_events(document_id=request.query_params.get("document") or None))

    async def audit_verify(_: Request) -> JSONResponse:
        return JSONResponse(store.audit_verify())

    routes = [
        Route("/", index),
        Route("/api/info", info),
        Route("/api/documents", list_documents, methods=["GET"]),
        Route("/api/documents", upload, methods=["POST"]),
        Route("/api/documents/{document_id}", get_document),
        Route("/api/documents/{document_id}/pages/{number:int}", page_image),
        Route("/api/documents/{document_id}/fields/{path}", edit_field, methods=["PATCH"]),
        Route("/api/documents/{document_id}/fields/{path}/confirm", confirm_field, methods=["POST"]),
        Route("/api/documents/{document_id}/rows", add_row, methods=["POST"]),
        Route("/api/documents/{document_id}/rows/{row_key:int}", delete_row, methods=["DELETE"]),
        Route("/api/documents/{document_id}/checks/{name}/acknowledge", acknowledge_check, methods=["POST"]),
        Route("/api/documents/{document_id}/approve", approve, methods=["POST"]),
        Route("/api/documents/{document_id}/retry", retry, methods=["POST"]),
        Route("/api/documents/{document_id}/manual", manual, methods=["POST"]),
        Route("/api/documents/{document_id}/export.json", export_json),
        Route("/api/documents/{document_id}/export.csv", export_csv),
        Route("/api/audit", audit_events),
        Route("/api/audit/verify", audit_verify),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(Guard, allowed_hosts=hosts)],
        exception_handlers={ReviewError: _review_error},
    )

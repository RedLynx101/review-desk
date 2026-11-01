"""Build small DocumentText objects by hand, so checks can be tested without PDFs or OCR."""

from __future__ import annotations

import asyncio
import json

from review_desk.intake import Intake
from review_desk.model_client import HttpResponse
from review_desk.pagetext import DocumentText, PageText, Word, group_lines


class StubTransport:
    """Stands in for the network. The handler gets a dict and returns an HttpResponse, or raises OSError."""

    def __init__(self, handler):
        self.handler = handler

    def send(self, method, url, headers, body, timeout):
        request = {"method": method, "url": url, "headers": headers, "timeout": timeout}
        if body is not None:
            request["json"] = json.loads(body)
        return self.handler(request)


def model_reply(content: str, finish: str = "stop", **message_extra) -> HttpResponse:
    payload = {
        "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content, **message_extra}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    return HttpResponse(200, json.dumps(payload).encode())


class AsgiClient:
    """Calls an ASGI app directly, with no network. Keeps httpx (and certifi, MPL-2.0) out of the repo."""

    def __init__(self, app, *, host: str = "127.0.0.1:8765"):
        self.app = app
        self.host = host

    def request(self, method: str, path: str, *, json_body=None, body: bytes | None = None, headers: dict | None = None, host: str | None = None):
        query = b""
        if "?" in path:
            path, query_text = path.split("?", 1)
            query = query_text.encode()
        payload = body if body is not None else (json.dumps(json_body).encode() if json_body is not None else b"")
        merged = {"host": host or self.host}
        if method not in ("GET", "HEAD"):
            merged["x-review-desk"] = "1"
        merged.update({k.lower(): v for k, v in (headers or {}).items()})
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": query, "root_path": "", "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 50000),
            "headers": [(k.encode(), str(v).encode()) for k, v in merged.items() if v is not None],
        }
        return asyncio.run(self._call(scope, payload))

    async def _call(self, scope, payload):
        sent_body = False
        response = AsgiResponse()

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                response.status = message["status"]
                response.headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                response.body += message.get("body", b"")

        await self.app(scope, receive, send)
        return response

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)


class AsgiResponse:
    def __init__(self):
        self.status = 0
        self.headers: dict = {}
        self.body = b""

    def json(self):
        return json.loads(self.body)


def document(rows: list[str], *, source: str = "text_layer", confidence: float = 1.0, width: float = 612.0, height: float = 792.0) -> DocumentText:
    """One page. Each string is a visual row. Use two or more spaces to start a separate column."""
    words = []
    y = 60.0
    for row in rows:
        x = 40.0
        for column in row.split("    "):
            for token in column.split():
                word_width = len(token) * 5.0
                words.append(Word(token, x, y, x + word_width, y + 10, 0, confidence))
                x += word_width + 4.0
            x += 40.0
        y += 16.0
    page = PageText(number=0, width=width, height=height, source=source, words=words)
    return DocumentText(pages=[page], lines=group_lines([page]), intake=Intake(1, 1000))


INVOICE_ROWS = [
    "Fernwhistle Gasket Works LLC    INVOICE",
    "12 Imaginary Lane",
    "Invoice No.: A1047    Date: March 5, 2026",
    "Due Date: April 4, 2026    PO Number: PO-77821",
    "Bill To",
    "Mock Harbor Rowing Club",
    "Item    Qty    Rate    Amount",
    "Neoprene gasket, 4 in.    4    10.00    40.00",
    "Rush cutting fee    1    25.50    25.50",
    "Subtotal    $65.50",
    "Sales Tax 10%    $6.55",
    "Total    $72.05",
    "Deposit Received    -$20.00",
    "Balance Due    $52.05",
]


def invoice_reading(**overrides) -> dict:
    """A correct model answer for INVOICE_ROWS. Line numbers match the rows above."""
    reading = {
        "document_type": "invoice",
        "vendor_name": {"text": "Fernwhistle Gasket Works LLC", "line": 1},
        "document_number": {"text": "A1047", "line": 3},
        "issue_date": {"text": "March 5, 2026", "line": 3},
        "due_date": {"text": "April 4, 2026", "line": 4},
        "purchase_order": {"text": "PO-77821", "line": 4},
        "bill_to_name": {"text": "Mock Harbor Rowing Club", "line": 6},
        "currency": {"text": "$", "line": 12},
        "subtotal": {"text": "$65.50", "line": 10},
        "discount": {"text": None, "line": None},
        "shipping": {"text": None, "line": None},
        "tax": [{"text": "$6.55", "line": 11}],
        "total": {"text": "$72.05", "line": 12},
        "line_items": [
            {"description": "Neoprene gasket, 4 in.", "quantity": "4", "unit_price": "10.00", "amount": "40.00", "line": 8},
            {"description": "Rush cutting fee", "quantity": "1", "unit_price": "25.50", "amount": "25.50", "line": 9},
        ],
    }
    reading.update(overrides)
    return reading

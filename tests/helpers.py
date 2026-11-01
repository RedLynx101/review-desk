"""Build small DocumentText objects by hand, so checks can be tested without PDFs or OCR."""

from __future__ import annotations

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

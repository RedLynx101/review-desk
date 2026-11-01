"""Seeded document content: who bills whom, for what, and the traps a real document has.

All names are invented on purpose and should read that way. Phone numbers use the 555-01xx
range reserved for fiction, web and email domains use the reserved `.example` TLD, and tax
identifiers use prefixes that are never issued.

Vendor names and some layouts are split, so no held-out vendor or held-out-only layout is
seen while tuning. Customer names and item descriptions are shared across splits.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .formats import DATE_FORMATS, MONEY_STYLES, MoneyStyle, format_date, round_cents

SPLITS = ("dev", "heldout", "samples")

INVOICE_LAYOUTS = ("classic", "banner", "sidebar", "ledger", "boxed")
RECEIPT_LAYOUTS = ("thermal", "pos_wide", "card_slip")
#: Never rendered while tuning. The held-out report can then say how unseen layouts fare.
HELDOUT_ONLY_INVOICE_LAYOUTS = ("letterhead", "summary_first")
HELDOUT_ONLY_RECEIPT_LAYOUTS = ("kiosk",)

_VENDOR_STEMS = {
    "dev": (
        "Thistlewick", "Quillfeather", "Bramblecrest", "Mossgrove", "Puddlefern",
        "Wobblestone", "Larkspindle", "Nimblethorn", "Copperwhistle", "Oatenbridge",
        "Tumbledown", "Fernwhistle", "Dapplewood", "Mistlebrook",
    ),
    "heldout": (
        "Snugglebrook", "Pennywhistle", "Marrowfen", "Turnipshire", "Cobbleworth",
        "Figgleton", "Ravelstitch", "Bumblewick", "Hazelknot", "Wrenbury",
        "Sprocketmoor", "Tinderfoot",
    ),
}
_VENDOR_STEMS["samples"] = _VENDOR_STEMS["dev"]

_ORGANIZATIONS = (
    "Placeholder Falls Community Theatre", "Mock Harbor Rowing Club",
    "Sampleton Bake Society", "Fictionville Parks Office",
    "Testwick Model Railway Guild", "Imaginary Orchard Holdings",
    "Lorem Springs Dental Cooperative", "Nonesuch Freight Partners",
    "Exampleburg Pickle Exchange", "Dummyport Lantern Society",
)
_PEOPLE = (
    "Ima Placeholder", "Testa McExample", "Sample Q. Customer",
    "Dummy Mae Ipsum", "Notta Realperson", "Fictitia Sampleton",
)
_STREETS = (
    "Imaginary Lane", "Nowhere Road", "Hypothetical Way", "Pretend Parkway",
    "Invented Avenue", "Make-Believe Court", "Fable Street", "Nonesuch Boulevard",
    "Placeholder Drive", "Sample Circle",
)
_TOWNS = (
    "Mockford", "Sampleton", "Fictionville", "Placeholder Falls", "Testwick",
    "Exampleburg", "Dummyport", "Lorem Springs", "Nonesuch",
)
_REMIT_PARTNERS = ("Nonesuch Factoring Partners", "Imaginary Receivables Trust")
_BANKS = ("Imaginary Savings & Loan", "Bank of Nowhere")

#: description, min quantity, max quantity, min price, max price, whether quantity is in hours
_Item = tuple[str, int, int, int, int, bool]

_INVOICE_KINDS: dict[str, tuple[_Item, ...]] = {
    "Gasket Works": (
        ("Neoprene gasket, 4 in.", 2, 40, 3, 18, False),
        ("EPDM flange seal, 2 in.", 5, 60, 2, 9, False),
        ("Spiral-wound gasket, 6 in. class 150", 1, 12, 22, 70, False),
        ("Custom die-cut seal, per drawing 44-B", 1, 8, 45, 180, False),
        ("Gasket sheet, 1/8 in. x 36 in. x 36 in.", 1, 6, 38, 120, False),
        ("Rush cutting fee", 1, 1, 25, 90, False),
    ),
    "Paper Goods": (
        ("Bond paper, 20 lb, case of 10 reams", 1, 20, 28, 64, False),
        ("Kraft envelopes, 9 x 12, box of 250", 1, 14, 18, 42, False),
        ("Index tabs, assorted colors", 3, 40, 2, 7, False),
        ("Ledger book, 300 pages, hardbound", 1, 10, 12, 38, False),
        ("Custom letterhead printing, two color, per thousand", 1, 5, 80, 240, False),
        ("Pallet delivery handling", 1, 2, 30, 75, False),
    ),
    "Lawn & Hedge": (
        ("Hedge trimming, labor", 2, 16, 28, 58, True),
        ("Mowing and edging visit", 1, 8, 35, 95, False),
        ("Mulch, shredded hardwood, cubic yard", 1, 18, 32, 58, False),
        ("Aeration and overseed, front and rear beds", 1, 3, 140, 380, False),
        ("Debris haul-away", 1, 3, 45, 160, False),
        ("Irrigation head replacement", 1, 12, 14, 36, False),
    ),
    "Signal Repair": (
        ("On-site diagnostic visit", 1, 2, 95, 240, False),
        ("Technician labor", 1, 14, 62, 118, True),
        ("Replacement relay module, 24 V", 1, 8, 28, 94, False),
        ("Preventive maintenance, includes filter swap and belt inspection", 1, 4, 180, 420, False),
        ("Cable harness, 12 ft", 1, 6, 34, 88, False),
        ("After-hours call-out surcharge", 1, 1, 75, 200, False),
    ),
    "Field Instruments": (
        ("Pressure gauge, 0-300 psi", 1, 14, 34, 96, False),
        ("Thermocouple probe, type K", 2, 20, 18, 58, False),
        ("Annual calibration certificate", 1, 6, 55, 145, False),
        ("Data logger, four channel", 1, 3, 240, 680, False),
        ("Carrying case, foam lined", 1, 5, 42, 110, False),
    ),
    "Catering Kitchen": (
        ("Box lunches, per person", 8, 90, 11, 19, False),
        ("Coffee service, per gallon", 1, 12, 16, 28, False),
        ("Fruit and cheese platter, large", 1, 8, 48, 92, False),
        ("Event staffing", 3, 24, 22, 38, True),
        ("Linen rental, per table", 2, 20, 6, 15, False),
    ),
    "Sign Painters": (
        ("Hand-lettered window sign", 1, 4, 180, 620, False),
        ("Vinyl banner, 3 ft x 8 ft", 1, 6, 65, 160, False),
        ("Design proofing", 1, 6, 45, 85, True),
        ("Installation, two-person crew", 1, 8, 90, 170, True),
        ("Removal of old signage", 1, 2, 60, 220, False),
    ),
    "Cartage & Freight": (
        ("Local cartage, per stop", 1, 16, 38, 96, False),
        ("Pallet storage, per week", 1, 12, 9, 24, False),
        ("Liftgate service", 1, 4, 35, 80, False),
        ("Fuel surcharge", 1, 1, 12, 75, False),
        ("Crating, custom size", 1, 5, 70, 210, False),
    ),
}

_RECEIPT_KINDS: dict[str, tuple[_Item, ...]] = {
    "Bakehouse": (
        ("Sourdough loaf", 1, 3, 5, 9, False), ("Almond croissant", 1, 6, 3, 5, False),
        ("Rye rolls, half doz", 1, 2, 4, 8, False), ("Oat cookie", 1, 8, 1, 3, False),
        ("Drip coffee, large", 1, 3, 2, 4, False), ("Quiche slice", 1, 3, 5, 8, False),
    ),
    "Hardware": (
        ("Wood screws, box", 1, 4, 4, 11, False), ("Painter's tape", 1, 5, 3, 8, False),
        ("Pipe wrench, 10 in", 1, 1, 14, 32, False), ("Hinge, brass, pair", 1, 6, 3, 9, False),
        ("Work gloves", 1, 3, 6, 16, False), ("Drill bit set", 1, 1, 12, 38, False),
    ),
    "Noodle Bar": (
        ("Miso noodle bowl", 1, 4, 10, 16, False), ("Pork dumplings, 6", 1, 3, 6, 10, False),
        ("Pickled radish", 1, 3, 3, 5, False), ("Barley tea", 1, 4, 2, 4, False),
        ("Extra broth", 1, 2, 2, 4, False),
    ),
    "Stationers": (
        ("Fountain pen ink", 1, 3, 8, 22, False), ("Sketchbook, A5", 1, 4, 6, 18, False),
        ("Gel pens, 6 pack", 1, 3, 5, 13, False), ("Wax seal kit", 1, 1, 14, 34, False),
        ("Paper clips, tin", 1, 5, 1, 4, False),
    ),
    "Garden Stand": (
        ("Tomato seedling", 1, 12, 2, 5, False), ("Potting soil, bag", 1, 4, 6, 15, False),
        ("Terracotta pot", 1, 6, 3, 12, False), ("Herb bundle", 1, 5, 2, 6, False),
        ("Twine, spool", 1, 3, 2, 6, False),
    ),
}

_ACCENTS = (
    (0.13, 0.29, 0.46), (0.11, 0.38, 0.33), (0.45, 0.17, 0.20),
    (0.27, 0.27, 0.32), (0.42, 0.28, 0.08), (0.20, 0.20, 0.55),
)

_LOCALE_WEIGHTS = (("us", 60), ("ca", 12), ("gb", 14), ("eu", 14))

_INVOICE_LABELS = {
    "title": ("INVOICE", "Invoice", "TAX INVOICE", "Bill"),
    "number": ("Invoice No.", "Invoice #", "Invoice Number", "Inv. No.", "Reference", "Bill No."),
    "date": ("Invoice Date", "Date", "Date Issued", "Issued", "Billing Date"),
    "due": ("Due Date", "Payment Due", "Pay By", "Due"),
    "po": ("PO Number", "P.O. #", "Purchase Order", "Your Ref", "Customer PO"),
    "bill_to": ("Bill To", "Billed To", "Customer", "Invoice To", "Sold To"),
    "ship_to": ("Ship To", "Deliver To", "Job Site"),
    "description": ("Description", "Item", "Service", "Details"),
    "quantity": ("Qty", "Quantity", "Units"),
    "unit_price": ("Unit Price", "Rate", "Price", "Each"),
    "amount": ("Amount", "Line Total", "Ext. Price"),
    "subtotal": ("Subtotal", "Sub-total", "Net Amount", "Total Before Tax"),
    "discount": ("Discount", "Less Discount", "Early-Pay Discount"),
    "shipping": ("Shipping", "Freight", "Delivery"),
    "total": ("Total", "Invoice Total", "Grand Total", "Total Due"),
    "deposit": ("Deposit Received", "Less Payment", "Paid to Date"),
    "balance": ("Balance Due", "Amount Outstanding", "Remaining Balance"),
}
_RECEIPT_LABELS = {
    "title": ("RECEIPT", "Sales Receipt", "CASH RECEIPT", "Customer Copy"),
    "number": ("Receipt", "Receipt #", "Trans", "Ticket", "Order"),
    "date": ("Date", "Sold", "Sale Date"),
    "description": ("Item", "Description"),
    "quantity": ("Qty", "Ct"),
    "unit_price": ("Each", "Price"),
    "amount": ("Amount", "Total"),
    "subtotal": ("Subtotal", "Sub-total", "Net"),
    "discount": ("Discount", "Loyalty Discount", "Coupon"),
    "shipping": ("Delivery", "Delivery Fee"),
    "total": ("Total", "TOTAL", "Amount Paid", "Sale Total"),
}


@dataclass(frozen=True)
class Party:
    name: str
    address: tuple[str, ...]
    phone: str | None = None
    email: str | None = None
    web: str | None = None
    tax_id: str | None = None


@dataclass
class LineItem:
    description: str
    quantity: Decimal | None  # None when the document prints no quantity for this line.
    unit_price: Decimal | None  # None when the document prints no unit price for this line.
    amount: Decimal


@dataclass(frozen=True)
class Fonts:
    regular: str
    bold: str


FONT_SETS = {
    "helvetica": Fonts("Helvetica", "Helvetica-Bold"),
    "times": Fonts("Times-Roman", "Times-Bold"),
    "courier": Fonts("Courier", "Courier-Bold"),
    "vera": Fonts("Vera", "VeraBd"),
}


@dataclass
class Document:
    """Everything a layout needs to draw one document, and everything the truth records."""

    base_id: str
    split: str
    index: int
    seed: int
    document_type: str  # "invoice" or "receipt"
    layout: str
    locale: str
    page_size: tuple[float, float]
    vendor: Party
    bill_to: Party | None
    ship_to: Party | None
    number: str | None
    issue_date: date
    due_date: date | None
    purchase_order: str | None
    money: MoneyStyle
    date_style: str
    items: list[LineItem]
    subtotal: Decimal
    discount: Decimal | None
    shipping: Decimal | None
    tax_lines: list[tuple[str, Decimal]]
    total: Decimal
    deposit: Decimal | None = None
    balance: Decimal | None = None
    tendered: Decimal | None = None
    change: Decimal | None = None
    card_last4: str | None = None
    cashier: str | None = None
    sale_time: str | None = None
    customer_id: str | None = None
    order_date: date | None = None
    terms: str | None = None
    remit_to: str | None = None
    bank_note: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    fonts: Fonts = FONT_SETS["helvetica"]
    accent: tuple[float, float, float] = (0.1, 0.1, 0.1)
    anomalies: list[str] = field(default_factory=list)

    @property
    def tax(self) -> Decimal | None:
        if not self.tax_lines:
            return None
        return sum((amount for _, amount in self.tax_lines), Decimal("0"))

    def date_text(self, value: date) -> str:
        return format_date(value, self.date_style)


def document_seed(master_seed: int, split: str, index: int) -> int:
    """Derive a per-document seed that doesn't depend on Python's randomized `hash()`."""
    digest = hashlib.sha256(f"{master_seed}:{split}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def layouts_for(split: str, document_type: str) -> tuple[str, ...]:
    if document_type == "invoice":
        extra = HELDOUT_ONLY_INVOICE_LAYOUTS if split == "heldout" else ()
        return INVOICE_LAYOUTS + extra
    extra = HELDOUT_ONLY_RECEIPT_LAYOUTS if split == "heldout" else ()
    return RECEIPT_LAYOUTS + extra


def planned_layout(split: str, index: int) -> tuple[str, str]:
    """Two invoices for each receipt, cycling through layouts so counts stay balanced."""
    if index % 3 == 2:
        layouts = layouts_for(split, "receipt")
        return "receipt", layouts[(index // 3) % len(layouts)]
    layouts = layouts_for(split, "invoice")
    invoices_before = index - index // 3
    return "invoice", layouts[invoices_before % len(layouts)]


def _money(rng: random.Random, low: int, high: int) -> Decimal:
    return round_cents(Decimal(rng.randint(low * 100, high * 100 - 1)) / 100)


def _slug(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _pick_labels(rng: random.Random, table: dict[str, tuple[str, ...]]) -> dict[str, str]:
    return {key: rng.choice(options) for key, options in table.items()}


def _address(rng: random.Random, locale: str) -> tuple[str, ...]:
    street = f"{rng.randint(1, 990)} {rng.choice(_STREETS)}"
    town = rng.choice(_TOWNS)
    if locale == "gb":
        return (street, f"{town} ZZ{rng.randint(1, 99)} {rng.randint(0, 9)}XX")
    if locale == "eu":
        return (street, f"{rng.randint(0, 99):05d} {town}")
    return (street, f"{town}, ZZ {rng.randint(0, 99):05d}")


def _phone(rng: random.Random) -> str:
    return f"(555) 555-01{rng.randint(0, 99):02d}"


def _tax_id(rng: random.Random, locale: str) -> str:
    if locale in ("gb", "eu"):
        return f"VAT ZZ 000 {rng.randint(0, 9999):04d} {rng.randint(0, 99):02d}"
    return f"EIN 00-00{rng.randint(0, 99999):05d}"


def _vendor(rng: random.Random, split: str, locale: str, kind: str) -> Party:
    stem = rng.choice(_VENDOR_STEMS[split])
    legal = rng.choice({"gb": ("Ltd", "Ltd", "& Sons Ltd"), "eu": ("GmbH", "S.A.R.L.", "B.V.")}.get(
        locale, ("LLC", "Inc.", "Co.")))
    name = f"{stem} {kind} {legal}"
    slug = _slug(stem)
    return Party(
        name=name,
        address=_address(rng, locale),
        phone=_phone(rng),
        email=f"billing@{slug}.example",
        web=f"www.{slug}.example",
        tax_id=_tax_id(rng, locale),
    )


def _party(rng: random.Random, locale: str) -> Party:
    name = rng.choice(_ORGANIZATIONS) if rng.random() < 0.7 else rng.choice(_PEOPLE)
    return Party(name=name, address=_address(rng, locale))


def _invoice_number(rng: random.Random, year: int) -> str:
    n = rng.randint(1, 99999)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    patterns = (
        f"INV-{year}-{n:05d}", f"A{n:06d}", f"{year}/{n % 10000:04d}",
        f"{n:08d}", f"ZZ-{n % 10000:04d}-{rng.choice(letters)}",
        f"{rng.choice(letters)}{rng.choice(letters)}{n:05d}",
    )
    return rng.choice(patterns)


def _receipt_number(rng: random.Random) -> str:
    n = rng.randint(1, 999999)
    patterns = (
        f"R-{n:06d}", f"{n % 100000:05d}", f"TXN{n:07d}",
        f"{rng.randint(1, 40):02d}-{rng.randint(1, 8)}-{n % 100000:05d}",
    )
    return rng.choice(patterns)


def _tax_rate(rng: random.Random, locale: str) -> tuple[str, Decimal] | None:
    if rng.random() < 0.14:
        return None
    if locale == "gb":
        return "VAT 20%", Decimal("0.20")
    if locale == "eu":
        return rng.choice((("MwSt. 19%", Decimal("0.19")), ("TVA 20%", Decimal("0.20")), ("BTW 21%", Decimal("0.21"))))
    if locale == "ca":
        return rng.choice((("GST 5%", Decimal("0.05")), ("HST 13%", Decimal("0.13"))))
    label, rate = rng.choice((("7%", "0.07"), ("6.5%", "0.065"), ("8.25%", "0.0825"), ("5.5%", "0.055"), ("10%", "0.10")))
    return f"{rng.choice(('Tax', 'Sales Tax'))} {label}", Decimal(rate)


def _invoice_items(rng: random.Random, kind: str, long_document: bool) -> list[LineItem]:
    pool = _INVOICE_KINDS[kind]
    count = rng.randint(26, 42) if long_document else rng.choices(
        range(1, 13), weights=(5, 10, 12, 12, 10, 8, 6, 4, 3, 2, 2, 1))[0]
    rows = []
    seen: dict[str, int] = {}
    for _ in range(count):
        description, qmin, qmax, pmin, pmax, hours = rng.choice(pool)
        seen[description] = seen.get(description, 0) + 1
        if seen[description] > 1:
            description = f"{description} (lot {seen[description]})"
        quantity = Decimal(rng.randint(qmin, qmax))
        if hours and rng.random() < 0.6:
            quantity += Decimal(rng.choice(("0.25", "0.5", "0.75")))
        price = _money(rng, pmin, pmax)
        rows.append(LineItem(description, quantity, price, round_cents(quantity * price)))
    return rows


#: Which line details each receipt layout prints. Thermal tickets skip the "1 x" line.
_RECEIPT_ITEM_POLICY = {
    "thermal": "multi_only",
    "pos_wide": "always",
    "card_slip": "quantity_only",
    "kiosk": "quantity_only",
}


def _receipt_items(rng: random.Random, kind: str, layout: str) -> list[LineItem]:
    pool = _RECEIPT_KINDS[kind]
    count = rng.choices(range(1, 10), weights=(6, 12, 14, 12, 8, 5, 3, 2, 1))[0]
    policy = _RECEIPT_ITEM_POLICY[layout]
    rows = []
    for description, qmin, qmax, pmin, pmax, _ in rng.sample(pool, k=min(count, len(pool))):
        quantity = Decimal(rng.randint(qmin, qmax))
        price = _money(rng, pmin, pmax)
        amount = round_cents(quantity * price)
        if policy == "always":
            printed_qty, printed_price = quantity, price
        elif policy == "quantity_only":
            printed_qty, printed_price = (quantity if quantity > 1 else None), None
        else:
            printed_qty, printed_price = (quantity, price) if quantity > 1 else (None, None)
        rows.append(LineItem(description, printed_qty, printed_price, amount))
    return rows


def _inject_line_anomaly(rng: random.Random, items: list[LineItem], anomalies: list[str]) -> None:
    """Make one printed line disagree with its own quantity and price, as a keying slip would."""
    candidates = [row for row in items if row.quantity is not None and row.unit_price is not None]
    if not candidates:
        return
    row = rng.choice(candidates)
    row.amount = round_cents(row.amount + Decimal(rng.choice((1, 2, 5, 9, 10, 20))))
    anomalies.append("line_amount_mismatch")


def _transposed_total(rng: random.Random, total: Decimal) -> Decimal:
    delta = Decimal(rng.choice(("0.09", "0.90", "0.18", "9.00", "10.00", "27.00", "1.00")))
    return round_cents(total + delta)


def make_document(split: str, index: int, master_seed: int) -> Document:
    """Build the content of one document. The same inputs always produce the same result."""
    if split not in SPLITS:
        raise ValueError(f"unknown split: {split}")
    seed = document_seed(master_seed, split, index)
    rng = random.Random(seed)
    document_type, layout = planned_layout(split, index)

    locale = rng.choices(*zip(*_LOCALE_WEIGHTS))[0]
    money = rng.choice(MONEY_STYLES[locale])
    date_style = rng.choice(DATE_FORMATS[locale])
    anomalies: list[str] = []

    issue = date(2024, 1, 1) + timedelta(days=rng.randint(0, 1000))
    if document_type == "invoice":
        kind = rng.choice(tuple(_INVOICE_KINDS))
        items = _invoice_items(rng, kind, long_document=rng.random() < 0.10)
    else:
        kind = rng.choice(tuple(_RECEIPT_KINDS))
        items = _receipt_items(rng, kind, layout)

    if rng.random() < 0.03:
        _inject_line_anomaly(rng, items, anomalies)

    subtotal = sum((row.amount for row in items), Decimal("0"))
    if rng.random() < 0.03 and len(items) > 1:
        # The subtotal was computed before this line was mistyped, so the column no longer adds up.
        row = rng.choice(items)
        row.amount = round_cents(row.amount + Decimal(rng.choice((1, 3, 7))))
        anomalies.append("subtotal_mismatch")

    discount = None
    if rng.random() < (0.12 if document_type == "invoice" else 0.15):
        discount = round_cents(subtotal * Decimal(rng.choice(("0.05", "0.10", "0.15"))))
    shipping = None
    if document_type == "invoice" and rng.random() < 0.25:
        shipping = _money(rng, 8, 140)

    tax_lines: list[tuple[str, Decimal]] = []
    taxable = subtotal - (discount or Decimal("0"))
    rate = _tax_rate(rng, locale)
    if layout == "kiosk":
        # This held-out layout always splits tax in two, so `tax` is printed nowhere as one number.
        first = round_cents(taxable * Decimal("0.04"))
        second = round_cents(taxable * Decimal("0.0325"))
        tax_lines = [("Tax A 4%", first), ("Tax B 3.25%", second)]
    elif rate is not None:
        label, value = rate
        if locale == "us" and document_type == "invoice" and rng.random() < 0.15:
            tax_lines = [
                ("State Tax 4%", round_cents(taxable * Decimal("0.04"))),
                ("County Tax 2.5%", round_cents(taxable * Decimal("0.025"))),
            ]
        else:
            tax_lines = [(label, round_cents(taxable * value))]

    tax_total = sum((amount for _, amount in tax_lines), Decimal("0"))
    total = subtotal - (discount or Decimal("0")) + (shipping or Decimal("0")) + tax_total
    if rng.random() < 0.04:
        total = _transposed_total(rng, total)
        anomalies.append("total_mismatch")

    width, height = (595.28, 841.89) if locale in ("gb", "eu") else (612.0, 792.0)
    font_key = {
        "ledger": "courier",
        "thermal": "courier",
        "letterhead": "times",
        "summary_first": "vera",
        "card_slip": "vera",
        "kiosk": "helvetica",
    }.get(layout) or rng.choice(("helvetica", "helvetica", "times", "vera"))

    doc = Document(
        base_id=f"synthetic-{split}-{index:04d}-{document_type}-{layout}",
        split=split,
        index=index,
        seed=seed,
        document_type=document_type,
        layout=layout,
        locale=locale,
        page_size=(width, height),
        vendor=_vendor(rng, split, locale, kind),
        bill_to=None,
        ship_to=None,
        number=None,
        issue_date=issue,
        due_date=None,
        purchase_order=None,
        money=money,
        date_style=date_style,
        items=items,
        subtotal=subtotal,
        discount=discount,
        shipping=shipping,
        tax_lines=tax_lines,
        total=total,
        fonts=FONT_SETS[font_key],
        accent=rng.choice(_ACCENTS),
        anomalies=anomalies,
    )

    if document_type == "invoice":
        _fill_invoice_details(rng, doc)
    else:
        _fill_receipt_details(rng, doc)
    return doc


def _fill_invoice_details(rng: random.Random, doc: Document) -> None:
    doc.labels = _pick_labels(rng, _INVOICE_LABELS)
    doc.bill_to = _party(rng, doc.locale)
    if rng.random() < 0.35:
        doc.ship_to = _party(rng, doc.locale)
    doc.number = _invoice_number(rng, doc.issue_date.year)
    if rng.random() < 0.03:
        doc.number = None
        doc.anomalies.append("missing_number")

    if rng.random() < 0.08:
        doc.terms = "Due on receipt"
    else:
        days = rng.choice((7, 14, 15, 30, 30, 30, 45, 60))
        doc.due_date = doc.issue_date + timedelta(days=days)
        doc.terms = f"Net {days}"

    if rng.random() < 0.55:
        n = rng.randint(1, 99999)
        doc.purchase_order = rng.choice((f"PO-{n:05d}", f"{n:06d}", f"K{n % 10000:04d}"))
    if rng.random() < 0.5:
        doc.customer_id = f"CUST-{rng.randint(1, 99999):05d}"
    if rng.random() < 0.3:
        doc.order_date = doc.issue_date - timedelta(days=rng.randint(1, 20))
    if rng.random() < 0.2 and doc.total > 40:
        doc.deposit = round_cents(doc.total * Decimal(rng.choice(("0.25", "0.30", "0.50"))))
        doc.balance = doc.total - doc.deposit
    if rng.random() < 0.08:
        doc.remit_to = rng.choice(_REMIT_PARTNERS)
    if rng.random() < 0.35:
        doc.bank_note = (
            f"Pay to: {rng.choice(_BANKS)}, acct 000{rng.randint(0, 999999):06d}, "
            f"routing 000{rng.randint(0, 999999):06d}"
        )


def _fill_receipt_details(rng: random.Random, doc: Document) -> None:
    doc.labels = _pick_labels(rng, _RECEIPT_LABELS)
    doc.number = _receipt_number(rng)
    if rng.random() < 0.03:
        doc.number = None
        doc.anomalies.append("missing_number")
    doc.sale_time = f"{rng.randint(7, 20):02d}:{rng.randint(0, 59):02d}"
    doc.cashier = rng.choice(("Testa", "Ima", "Notta", "Fictitia", "Dummy"))
    if doc.layout == "card_slip" or rng.random() < 0.5:
        doc.card_last4 = f"00{rng.randint(0, 99):02d}"
    else:
        cash_steps = (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("50"), Decimal("100"))
        doc.tendered = next((step for step in cash_steps if step >= doc.total), doc.total)
        doc.change = doc.tendered - doc.total

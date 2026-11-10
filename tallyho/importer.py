"""Bringing sales in from a CSV or Excel file.

Four steps, each a plain function you can test on its own:

1. read_table(path)        the header row and the rows, from CSV or Excel
2. auto_match(headers)     which column holds which field ("Receipt #" is the order number)
3. parse_file(...)         every row checked and turned into orders with lines;
                           a row with a problem leaves its whole order out and says why
4. Writer / run_import()   orders saved in batches of BATCH_SIZE, in one transaction

A file is many lines; lines with the same order number are one order. Order-level
things (date, customer, region, channel) come from the order's first line, and a
later line that disagrees is a problem.

Speed matters here (it is one of this project's lessons):
- dimensions (products, customers...) are looked up in Python dicts, not one query per row;
- orders go in with one INSERT ... RETURNING per batch ("executemany"), lines with one INSERT;
- nothing is committed until the end, so a crash halfway leaves the database as it was.
"""
import csv
import io
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha1
from pathlib import Path

from sqlalchemy import bindparam, delete, func, insert, select, text, update

from .dates import utcnow
from .models import NOT_SET, Category, Channel, Customer, Import, Order, OrderLine, Product, Region

BATCH_SIZE = 5000          # orders per INSERT
MAX_PROBLEMS_KEPT = 500    # problem rows remembered for the error file
MAX_QUANTITY = 1_000_000
MAX_MONEY = 10_000_000_00  # P10 million per unit: more is surely a typo


# ---------- The fields a file can have ----------

@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str
    required: bool = False
    aliases: tuple = ()


FIELDS = [
    Field("order_no", "Order number", "The receipt or order ID. Rows with the same number are one order.", True,
          ("order", "order no", "order number", "order id", "receipt", "receipt no", "receipt number", "invoice",
           "invoice no", "invoice number", "transaction id", "or number", "sales order")),
    Field("line_no", "Line number", "Only if your file numbers the lines of each order.", False,
          ("line", "line no", "line number", "line item", "seq")),
    Field("order_date", "Date", "When it was sold. A time in the same column is kept too.", True,
          ("date", "order date", "sale date", "sales date", "transaction date", "ordered at", "created at", "datetime",
           "timestamp", "date sold", "sold on", "sold at", "date and time", "date time", "day")),
    Field("order_time", "Time", "Only if the time is in its own column.", False, ("time", "order time", "hour")),
    Field("sku", "Product code", "The SKU or item code. The same code is the same product.", False,
          ("sku", "product code", "item code", "code", "product id", "item id", "barcode", "item no")),
    Field("product", "Product name", "Needed if there is no product code.", False,
          ("product", "product name", "item", "item name", "description", "item description", "name")),
    Field("category", "Category", "", False, ("category", "product category", "type", "group", "department")),
    Field("quantity", "Quantity", "How many were sold on this line.", True,
          ("qty", "quantity", "units", "count", "pcs", "pieces", "quantity sold")),
    Field("unit_price", "Unit price", "The price of one. Or give the line total instead.", False,
          ("price", "unit price", "selling price", "srp", "price each", "sale price", "amount each")),
    Field("line_total", "Line total", "Only if there is no unit price: quantity x price.", False,
          ("total", "line total", "amount", "subtotal", "sales", "net sales", "line amount", "total price")),
    Field("unit_cost", "Unit cost", "What one cost you. Without it, profit can't be worked out.", False,
          ("cost", "unit cost", "cost each", "cost price", "cogs", "purchase price")),
    Field("discount", "Discount", "Money off this line (an amount, or a percent like 10%).", False,
          ("discount", "line discount", "discount amount", "less", "voucher")),
    Field("customer", "Customer ID", "The same ID is the same customer.", False,
          ("customer", "customer id", "customer code", "customer no", "client", "client id", "member id",
           "account", "buyer")),
    Field("customer_name", "Customer name", "", False, ("customer name", "client name", "buyer name", "full name")),
    Field("region", "Region", "Where it was sold: a region, city or branch.", False,
          ("region", "area", "province", "city", "branch", "location", "territory", "store")),
    Field("channel", "Channel", "How it was sold: store, website, phone...", False,
          ("channel", "sales channel", "source", "platform", "order source", "via")),
]
FIELD_BY_KEY = {f.key: f for f in FIELDS}

DATE_FORMATS = [
    ("auto", "Find out from the dates"),
    ("ymd", "Year-month-day (2026-03-25)"),
    ("mdy", "Month/day/year (03/25/2026)"),
    ("dmy", "Day/month/year (25/03/2026)"),
]


def normalize_header(text):
    """ "Order No." -> "order no", "Unit_Price" -> "unit price" """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def auto_match(headers):
    """{field key: header} for every field we can recognise, each header used once."""
    normalized = {normalize_header(h): h for h in headers}
    matched, used = {}, set()

    def take(key, header):
        matched[key] = header
        used.add(header)

    # Exact names first (for every field), then "contains" guesses for the rest.
    for f in FIELDS:
        for name in (f.key.replace("_", " "), normalize_header(f.label), *f.aliases):
            header = normalized.get(name)
            if header and header not in used:
                take(f.key, header)
                break
    for f in FIELDS:
        if f.key in matched:
            continue
        for norm, header in normalized.items():
            if header not in used and any(alias in norm.split() or (len(alias) > 4 and alias in norm)
                                          for alias in f.aliases):
                take(f.key, header)
                break
    return matched


def headers_key(headers):
    """A fingerprint of a header row: the same layout gives the same key."""
    return sha1("\x1f".join(normalize_header(h) for h in headers).encode()).hexdigest()


def mapping_problems(mapping):
    """What is missing before a file can be imported (an empty list if nothing)."""
    problems = []
    for key in ("order_no", "order_date", "quantity"):
        if not mapping.get(key):
            problems.append(f"Choose the column for {FIELD_BY_KEY[key].label}.")
    if not mapping.get("sku") and not mapping.get("product"):
        problems.append("Choose the column for Product code or Product name.")
    if not mapping.get("unit_price") and not mapping.get("line_total"):
        problems.append("Choose the column for Unit price or Line total.")
    columns = [c for c in mapping.values() if c]
    if len(columns) != len(set(columns)):
        problems.append("Each column can be used for only one field.")
    return problems


# ---------- Reading files ----------

ALLOWED_SUFFIXES = {".csv", ".txt", ".tsv", ".xlsx"}


class FileProblem(Exception):
    """The whole file can't be read."""


def decode(raw):
    """Bytes -> text. Excel's "CSV UTF-8" starts with a BOM (utf-8-sig drops it); older
    Windows programs write cp1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileProblem("We couldn't read the letters in this file. Save it as CSV UTF-8 and try again.")


def clean_headers(raw_headers):
    headers, seen = [], {}
    for number, header in enumerate(raw_headers, start=1):
        name = str(header).strip() if header not in (None, "") else f"Column {number}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def read_table(path):
    """(headers, rows) from a CSV or .xlsx file. rows is a list of lists (Excel cells keep
    their types: dates are dates, numbers are numbers). Blank rows are dropped."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _read_excel(path)
    text_value = decode(path.read_bytes())
    sample = text_value[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text_value, newline=""), dialect)
    header = next(reader, None)
    if not header:
        raise FileProblem("This file is empty.")
    rows = [(number, row) for number, row in enumerate(reader, start=2) if any(cell.strip() for cell in row)]
    return clean_headers(header), rows


def _read_excel(path):
    from openpyxl import load_workbook
    try:
        book = load_workbook(path, read_only=True, data_only=True)
    except Exception as error:  # noqa: BLE001 (openpyxl raises many kinds)
        raise FileProblem("We couldn't open this Excel file. Is it a .xlsx file?") from error
    try:
        sheet = book.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header or not any(h not in (None, "") for h in header):
            raise FileProblem("The first sheet is empty, or its first row has no column names.")
        rows = [(number, list(row)) for number, row in enumerate(rows_iter, start=2)
                if any(cell not in (None, "") for cell in row)]
    finally:
        book.close()
    # Excel often reports trailing empty header cells; drop them.
    while header and header[-1] in (None, ""):
        header = header[:-1]
    return clean_headers(header), [(n, r[:len(header)]) for n, r in rows]


# ---------- Reading values ----------

class Problem(Exception):
    """One row can't be read. The message says why, in plain words."""


def text_of(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel gives 1001.0 for 1001
    return str(value).strip()


TIME_PATTERN = re.compile(r"^(\d{1,2})(?::(\d{2}))?(?::\d{2}(?:\.\d+)?)?\s*([AaPp][Mm])?$")
DATE_PATTERN = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})$")


def parse_hour(text_value):
    """ "14:35" -> 14, "2:35 PM" -> 14, "12 AM" -> 0. None if empty."""
    text_value = text_value.strip()
    if not text_value:
        return None
    match = TIME_PATTERN.match(text_value)
    if not match:
        raise Problem(f"The time {text_value!r} isn't a time we know (like 14:35 or 2:35 PM).")
    hour = int(match.group(1))
    if match.group(3):
        if not 1 <= hour <= 12:
            raise Problem(f"The time {text_value!r} isn't a real time.")
        hour = hour % 12 + (12 if match.group(3).lower() == "pm" else 0)
    if hour > 23 or (match.group(2) and int(match.group(2)) > 59):
        raise Problem(f"The time {text_value!r} isn't a real time.")
    return hour


def detect_date_format(values):
    """Look at sample dates and say how they are written: (format, note).

    "25/03/2026" can only be day/month; "03/25/2026" only month/day. If every
    sample could be both (03/04/2026), we say month/day and the page says so.
    """
    texts = [text_of(v).split()[0].split("T")[0] for v in values
             if v not in (None, "") and not isinstance(v, (date, datetime))]
    parts = [m.groups() for m in (DATE_PATTERN.match(t) for t in texts) if m]
    if not parts:
        return "ymd", None
    if all(len(a) == 4 for a, _, _ in parts):
        return "ymd", None
    if any(int(a) > 12 for a, _, _ in parts if len(a) <= 2):
        return "dmy", None
    if any(int(b) > 12 for _, b, _ in parts):
        return "mdy", None
    return "mdy", "Dates like 03/04/2026 could be read two ways. We read them as month/day (March 4). " \
                  "If they are day/month, choose Day/month/year."


def parse_date(value, date_format):
    """A cell -> (date, hour or None)."""
    if isinstance(value, datetime):
        hour = value.hour if (value.hour, value.minute, value.second) != (0, 0, 0) else None
        return value.date(), hour
    if isinstance(value, date):
        return value, None
    text_value = text_of(value)
    if not text_value:
        raise Problem("The date is empty.")
    if re.fullmatch(r"\d{5}(\.\d+)?", text_value):
        # An Excel day number that lost its date format (45736 = 2025-03-20).
        serial = float(text_value)
        if 20000 <= serial <= 80000:
            moment = datetime(1899, 12, 30) + timedelta(days=serial)
            return moment.date(), (moment.hour if serial % 1 else None)
    day_part, _, time_part = text_value.replace("T", " ", 1).partition(" ")
    match = DATE_PATTERN.match(day_part)
    if not match:
        raise Problem(f"The date {text_value!r} isn't a date we know (like 2026-03-25).")
    a, b, c = match.groups()
    if len(a) == 4:
        year, month, day = a, b, c
    elif date_format == "dmy":
        day, month, year = a, b, c
    else:
        month, day, year = a, b, c
    year = int(year)
    if year < 100:
        year += 2000
    try:
        result = date(year, int(month), int(day))
    except ValueError:
        raise Problem(f"The date {text_value!r} doesn't exist.") from None
    if not 1990 <= result.year <= 2100:
        raise Problem(f"The date {text_value!r} is too far in the past or future.")
    return result, parse_hour(time_part) if time_part.strip() else None


def parse_money(value, label):
    """ "P1,234.50", "1234.5", 1234.5 -> 123450 centavos. None if empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        cleaned = re.sub(r"(?i)^(php|₱|p)\s*|[,\s₱]", "", value.strip())
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            raise Problem(f"{label} {value!r} isn't an amount of money.") from None
    if not amount.is_finite():
        raise Problem(f"{label} {value!r} isn't an amount of money.")
    if amount < 0:
        raise Problem(f"{label} can't be less than 0 (it is {value}).")
    centavos = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if centavos > MAX_MONEY:
        raise Problem(f"{label} {value!r} is too big. Is it a typo?")
    return centavos


def parse_quantity(value):
    text_value = text_of(value).replace(",", "")
    if not text_value:
        raise Problem("The quantity is empty.")
    try:
        amount = Decimal(text_value)
    except InvalidOperation:
        raise Problem(f"The quantity {text_value!r} isn't a number.") from None
    if amount != amount.to_integral_value():
        raise Problem(f"The quantity {text_value!r} isn't a whole number.")
    quantity = int(amount)
    if quantity < 1:
        raise Problem(f"The quantity must be at least 1 (it is {text_value}). Returns can't be imported yet.")
    if quantity > MAX_QUANTITY:
        raise Problem(f"The quantity {text_value} is too big. Is it a typo?")
    return quantity


def short_text(value, label, limit, required=False):
    text_value = " ".join(text_of(value).split())
    if required and not text_value:
        raise Problem(f"The {label.lower()} is empty.")
    if len(text_value) > limit:
        raise Problem(f"The {label.lower()} is longer than {limit} characters.")
    return text_value


# ---------- Rows -> orders ----------

@dataclass(slots=True)
class ParsedLine:
    line_no: int
    sku: str
    product: str
    category: str
    quantity: int
    unit_price: int
    unit_cost: int
    discount: int

    @property
    def gross(self):
        return self.quantity * self.unit_price

    @property
    def revenue(self):
        return self.gross - self.discount

    @property
    def cost(self):
        return self.quantity * self.unit_cost


@dataclass(slots=True)
class ParsedOrder:
    order_no: str
    order_date: date
    hour: int | None
    customer_code: str
    customer_name: str
    region: str
    channel: str
    lines: list = field(default_factory=list)
    first_row: int = 0


@dataclass
class ParseResult:
    orders: list
    problems: list   # [{"row": 7, "problem": "...", "values": {...}}], every bad row
    rows_read: int
    orders_left_out: int
    no_cost: bool


def parse_line(cells, index, date_format):
    """One row -> (order-level values, ParsedLine). Raises Problem."""
    def cell(key):
        position = index.get(key)
        return cells[position] if position is not None and position < len(cells) else None

    order_no = short_text(cell("order_no"), "Order number", 64, required=True)
    order_date, hour = parse_date(cell("order_date"), date_format)
    if "order_time" in index and text_of(cell("order_time")):
        time_value = cell("order_time")
        hour = time_value.hour if hasattr(time_value, "hour") else parse_hour(text_of(time_value))
    sku = short_text(cell("sku"), "Product code", 64)
    product = short_text(cell("product"), "Product name", 160)
    if not sku and not product:
        raise Problem("The product code and name are both empty.")
    quantity = parse_quantity(cell("quantity"))
    unit_price = parse_money(cell("unit_price"), "The unit price")
    if unit_price is None:
        total = parse_money(cell("line_total"), "The line total")
        if total is None:
            raise Problem("The price is empty.")
        unit_price = int((Decimal(total) / quantity).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    unit_cost = parse_money(cell("unit_cost"), "The unit cost") or 0
    gross = quantity * unit_price
    discount_text = text_of(cell("discount"))
    if discount_text.endswith("%"):
        try:
            rate = Decimal(discount_text[:-1].strip())
        except InvalidOperation:
            raise Problem(f"The discount {discount_text!r} isn't a percent.") from None
        if not 0 <= rate <= 100:
            raise Problem(f"The discount {discount_text!r} must be between 0% and 100%.")
        discount = int((gross * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        discount = parse_money(cell("discount"), "The discount") or 0
    if discount > gross:
        raise Problem("The discount is bigger than the line's price.")
    line_no_text = text_of(cell("line_no")) if "line_no" in index else ""
    try:
        line_no = int(line_no_text) if line_no_text else 0
    except ValueError:
        raise Problem(f"The line number {line_no_text!r} isn't a whole number.") from None
    customer_code = short_text(cell("customer"), "Customer ID", 64)
    customer_name = " ".join(text_of(cell("customer_name")).split())[:120]
    order_values = (order_no, order_date, hour, customer_code or customer_name[:64], customer_name or customer_code,
                    " ".join(text_of(cell("region")).split())[:80] or NOT_SET,
                    " ".join(text_of(cell("channel")).split())[:80] or NOT_SET)
    line = ParsedLine(line_no, sku or product[:64], product or sku,
                      " ".join(text_of(cell("category")).split())[:80],
                      quantity, unit_price, unit_cost, discount)
    return order_values, line


ORDER_PARTS = ("date", "hour", "customer", "customer name", "region", "channel")


def parse_file(headers, rows, mapping, date_format):
    """Every row checked and grouped into orders. An order with any bad row is left out whole,
    so its totals are never half there."""
    index = {key: headers.index(column) for key, column in mapping.items() if column in headers}
    orders, problems, bad = {}, [], {}
    rows_read = 0
    for number, cells in rows:
        rows_read += 1
        values = {headers[i]: text_of(v) for i, v in enumerate(cells) if i < len(headers)}
        try:
            order_values, line = parse_line(cells, index, date_format)
        except Problem as problem:
            order_no = text_of(cells[index["order_no"]]) if index.get("order_no", 99999) < len(cells) else ""
            problems.append({"row": number, "problem": str(problem), "values": values, "order_no": order_no})
            if order_no:
                bad.setdefault(order_no, number)
            continue
        order_no = order_values[0]
        order = orders.get(order_no)
        if order is None:
            order = orders[order_no] = ParsedOrder(*order_values, first_row=number)
        else:
            mine = order_values[1:]
            theirs = (order.order_date, order.hour, order.customer_code, order.customer_name, order.region,
                      order.channel)
            for part, a, b in zip(ORDER_PARTS, mine, theirs):
                # A time is optional per line: only two different times disagree.
                if a != b and not (part == "hour" and (a is None or b is None)) and part != "customer name":
                    problems.append({"row": number, "order_no": order_no, "values": values,
                                     "problem": f"Order {order_no} has a different {part} on row {order.first_row}."})
                    bad.setdefault(order_no, number)
                    break
        if not line.line_no:
            line.line_no = len(order.lines) + 1
        if any(other.line_no == line.line_no for other in order.lines):
            problems.append({"row": number, "order_no": order_no, "values": values,
                             "problem": f"Order {order_no} has line {line.line_no} twice."})
            bad.setdefault(order_no, number)
            continue
        order.lines.append(line)
    for order_no, row in bad.items():
        if order_no in orders:
            del orders[order_no]
    for problem in problems:
        if problem.get("order_no") in bad:
            problem["problem"] += f" Order {problem['order_no']} was left out."
    good = sorted(orders.values(), key=lambda o: (o.order_date, o.order_no))
    return ParseResult(good, problems, rows_read, len(bad), no_cost="unit_cost" not in index)


# ---------- Saving ----------

def _chunks(items, size):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


class Writer:
    """Saves orders for one import, a batch at a time. Call write() any number of times,
    then finish(). Nothing is committed here: the caller commits once at the end."""

    def __init__(self, s, workspace, record, mode="skip"):
        self.s, self.workspace, self.record, self.mode = s, workspace, record, mode
        self.ws = workspace.id
        self.categories = self._names(Category)
        self.regions = self._names(Region)
        self.channels = self._names(Channel)
        self.products = {sku.lower(): [pid, name, cat] for pid, sku, name, cat in s.execute(
            select(Product.id, Product.sku, Product.name, Product.category_id).where(Product.workspace_id == self.ws))}
        self.customers = {code.lower(): [cid, name] for cid, code, name in s.execute(
            select(Customer.id, Customer.code, Customer.name).where(Customer.workspace_id == self.ws))}
        self.touched_customers = set()
        self.first = self.last = None

    def _names(self, model):
        return {name.lower(): id_ for id_, name in self.s.execute(
            select(model.id, model.name).where(model.workspace_id == self.ws))}

    def _ensure_names(self, model, known, names):
        missing = {}
        for name in names:
            if name and name.lower() not in known:
                missing.setdefault(name.lower(), name)
        if missing:
            self.s.execute(insert(model), [{"workspace_id": self.ws, "name": n} for n in missing.values()])
            known.update(self._names(model))

    def _see(self, day):
        self.first = day if self.first is None or day < self.first else self.first
        self.last = day if self.last is None or day > self.last else self.last

    def _dimensions(self, orders):
        lines = [line for order in orders for line in order.lines]
        self._ensure_names(Category, self.categories, {line.category for line in lines})
        self._ensure_names(Region, self.regions, {order.region for order in orders})
        self._ensure_names(Channel, self.channels, {order.channel for order in orders})
        # Products: new codes are added; a known code takes the file's newest name and category.
        new, changed = {}, {}
        for line in lines:
            key = line.sku.lower()
            category_id = self.categories.get(line.category.lower()) if line.category else None
            known = self.products.get(key)
            if known is None:
                new[key] = {"workspace_id": self.ws, "sku": line.sku, "name": line.product, "category_id": category_id}
            elif (line.product != known[1] and line.product != line.sku) or (category_id and category_id != known[2]):
                known[1] = line.product if line.product != line.sku else known[1]
                known[2] = category_id or known[2]
                changed[known[0]] = {"id": known[0], "name": known[1], "category_id": known[2]}
            elif key in new and category_id and not new[key]["category_id"]:
                new[key]["category_id"] = category_id
        if new:
            rows = self.s.execute(insert(Product).returning(Product.id, Product.sku, sort_by_parameter_order=True),
                                  list(new.values()))
            for (pid, sku), values in zip(rows, new.values()):
                self.products[sku.lower()] = [pid, values["name"], values["category_id"]]
        if changed:
            self.s.execute(update(Product), list(changed.values()))  # ORM bulk UPDATE by primary key
        # Customers, the same way.
        new, changed = {}, {}
        for order in orders:
            if not order.customer_code:
                continue
            key = order.customer_code.lower()
            known = self.customers.get(key)
            if known is None:
                new.setdefault(key, {"workspace_id": self.ws, "code": order.customer_code, "name": order.customer_name})
            elif order.customer_name and order.customer_name != order.customer_code and order.customer_name != known[1]:
                known[1] = order.customer_name
                changed[known[0]] = {"id": known[0], "name": order.customer_name}
        if new:
            rows = self.s.execute(insert(Customer).returning(Customer.id, Customer.code, sort_by_parameter_order=True),
                                  list(new.values()))
            for (cid, code), values in zip(rows, new.values()):
                self.customers[code.lower()] = [cid, values["name"]]
        if changed:
            self.s.execute(update(Customer), list(changed.values()))

    def write(self, orders):
        """Save one batch of ParsedOrders (at most BATCH_SIZE is best)."""
        if not orders:
            return
        self._dimensions(orders)
        # Orders already in the database: leave them (skip) or swap them (replace).
        numbers = [order.order_no for order in orders]
        existing = {}
        for chunk in _chunks(numbers, 1000):
            for oid, number, day, customer_id in self.s.execute(
                    select(Order.id, Order.order_no, Order.order_date, Order.customer_id)
                    .where(Order.workspace_id == self.ws, Order.order_no.in_(chunk))):
                existing[number] = (oid, day, customer_id)
        if existing and self.mode == "replace":
            for oid, day, customer_id in existing.values():
                self._see(day)
                if customer_id:
                    self.touched_customers.add(customer_id)
            for chunk in _chunks([v[0] for v in existing.values()], 1000):
                # The lines go too: their foreign key says ON DELETE CASCADE.
                self.s.execute(delete(Order).where(Order.id.in_(chunk)).execution_options(synchronize_session=False))
            self.record.orders_replaced += len(existing)
            existing = {}
        elif existing:
            self.record.orders_skipped += len(existing)
        fresh = [order for order in orders if order.order_no not in existing]
        if not fresh:
            return
        order_rows = []
        for order in fresh:
            customer = self.customers.get(order.customer_code.lower()) if order.customer_code else None
            if customer:
                self.touched_customers.add(customer[0])
            self._see(order.order_date)
            order_rows.append({
                "workspace_id": self.ws, "import_id": self.record.id, "order_no": order.order_no,
                "order_date": order.order_date, "order_hour": order.hour,
                "customer_id": customer[0] if customer else None,
                "region_id": self.regions[order.region.lower()], "channel_id": self.channels[order.channel.lower()],
                "line_count": len(order.lines), "units": sum(line.quantity for line in order.lines),
                "gross": sum(line.gross for line in order.lines),
                "discount": sum(line.discount for line in order.lines),
                "revenue": sum(line.revenue for line in order.lines),
                "cost": sum(line.cost for line in order.lines),
            })
        ids = {}
        table = Order.__table__
        for chunk in _chunks(order_rows, BATCH_SIZE):
            for oid, number in self.s.execute(table.insert().returning(table.c.id, table.c.order_no), chunk):
                ids[number] = oid
        line_rows = [{
            "order_id": ids[order.order_no], "workspace_id": self.ws, "line_no": line.line_no,
            "product_id": self.products[line.sku.lower()][0], "quantity": line.quantity,
            "unit_price": line.unit_price, "unit_cost": line.unit_cost, "discount": line.discount,
            "revenue": line.revenue, "cost": line.cost,
        } for order in fresh for line in order.lines]
        for chunk in _chunks(line_rows, BATCH_SIZE * 3):
            self.s.execute(OrderLine.__table__.insert(), chunk)
        self.record.orders_added += len(fresh)
        self.record.lines_added += len(line_rows)

    def finish(self):
        """New-customer dates, then everything that depends on the numbers."""
        if self.touched_customers:
            # A customer's old first day changes the "new customers" count of that day too.
            old_first = earliest_first_order(self.s, self.ws, self.touched_customers)
            if old_first:
                self._see(old_first)
            update_first_orders(self.s, self.ws, self.touched_customers)
        self.record.first_day = self.first
        self.record.last_day = self.last
        after_change(self.s, self.workspace, self.first, self.last)


def update_first_orders(s, workspace_id, customer_ids=None):
    """Each customer's first order (the earliest day, then the lowest id).

    One UPDATE with "correlated subqueries": for every customer row, the database
    looks up that customer's orders (fast, thanks to ix_orders_workspace_customer_day).
    customer_ids=None does every customer in the workspace.
    """
    sql = """
        UPDATE customers SET
          first_order_date = (SELECT MIN(o.order_date) FROM orders o
                              WHERE o.workspace_id = :ws AND o.customer_id = customers.id),
          first_order_id = (SELECT o.id FROM orders o
                            WHERE o.workspace_id = :ws AND o.customer_id = customers.id
                            ORDER BY o.order_date, o.id LIMIT 1)
        WHERE workspace_id = :ws
    """
    if customer_ids is None or len(customer_ids) > 5000:
        s.execute(text(sql), {"ws": workspace_id})  # many customers: one statement for all is quicker
        return
    for chunk in _chunks(sorted(customer_ids), 500):
        s.execute(text(sql + " AND id IN :ids").bindparams(bindparam("ids", expanding=True)),
                  {"ws": workspace_id, "ids": chunk})


def earliest_first_order(s, workspace_id, customer_ids):
    """The earliest first-order day among these customers (before any update)."""
    days = []
    for chunk in _chunks(sorted(customer_ids), 1000):
        day = s.scalar(select(func.min(Customer.first_order_date)).where(
            Customer.workspace_id == workspace_id, Customer.id.in_(chunk)))
        if day:
            days.append(day)
    return min(days) if days else None


def after_change(s, workspace, first, last):
    """Sales between first and last changed: everything built from them must follow."""
    from .workspaces import bump_data_version
    bump_data_version(workspace)


def new_record(s, workspace, user, filename, mode="skip"):
    record = Import(workspace_id=workspace.id, user_id=user.id if user else None, filename=filename[:255],
                    mode=mode, orders_added=0, lines_added=0, orders_skipped=0, orders_replaced=0,
                    rows_read=0, rows_failed=0, problems="[]", status="done", created_at=utcnow())
    s.add(record)
    s.flush()
    return record


def run_import(s, workspace, user, filename, result, mode="skip"):
    """Save a parsed file. Returns the Import record. The caller commits."""
    started = time.perf_counter()
    record = new_record(s, workspace, user, filename, mode)
    record.rows_read = result.rows_read
    record.rows_failed = len(result.problems)
    record.no_cost = result.no_cost
    record.problems = json.dumps(result.problems[:MAX_PROBLEMS_KEPT], default=str)
    writer = Writer(s, workspace, record, mode)
    for chunk in _chunks(result.orders, BATCH_SIZE):
        writer.write(chunk)
    writer.finish()
    record.seconds = round(time.perf_counter() - started, 3)
    return record


def undo_import(s, workspace, record):
    """Delete the orders this import added (their lines go with them), then mend what depended on them."""
    if record.status != "done":
        return
    started = time.perf_counter()
    first, last = s.execute(select(func.min(Order.order_date), func.max(Order.order_date))
                            .where(Order.import_id == record.id)).one()
    customer_ids = set(s.scalars(select(Order.customer_id).where(Order.import_id == record.id,
                                                                 Order.customer_id.is_not(None)).distinct()))
    if customer_ids:
        old_first = earliest_first_order(s, workspace.id, customer_ids)
        if old_first and (first is None or old_first < first):
            first = old_first
    s.execute(delete(Order).where(Order.import_id == record.id).execution_options(synchronize_session=False))
    if customer_ids:
        update_first_orders(s, workspace.id, customer_ids)
    if first is not None:
        after_change(s, workspace, first, last)
    else:
        from .workspaces import bump_data_version
        bump_data_version(workspace)
    record.status = "undone"
    record.undone_at = utcnow()
    record.seconds = round(record.seconds + (time.perf_counter() - started), 3)


def problems_of(record):
    try:
        return json.loads(record.problems or "[]")
    except ValueError:
        return []

"""The numbers: KPIs, trends and breakdowns, asked of the database in hand-written SQL.

Everything takes a Filters (workspace, dates, and optional region, channel,
category, product or customer) and picks where to read from:

    "orders"    daily_order_stats    one row per day x region x channel (fastest)
    "products"  daily_product_stats  one row per day x product x region x channel
    "raw"       orders / order_lines the real sales, one row per order or line

A rollup can only answer questions whose numbers add up (see models.py).
"Orders in the Coffee Beans category" can't come from the product rollup:
an order with two kinds of beans would count twice. So a category filter
reads the raw lines, and plan() says so. tests/test_analytics.py checks that
the rollups and the raw tables always give the same answers.

The SQL is written out in full on purpose (this project is for practising it):
GROUP BY, SUM(CASE ...), COUNT(DISTINCT ...), WITH RECURSIVE, and window
functions: SUM() OVER () for a share of the total, RANK() OVER (...) for
places, and SUM() OVER (ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) for a
7-day moving average.
"""
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import text

from . import dialect as sql
from .cache import memo

SUMS = ("orders", "units", "discount", "revenue", "cost", "new_customers")


@dataclass(frozen=True)
class Filters:
    ws: int
    version: int        # the workspace's data_version: part of every cache key
    first: date
    last: date
    region_id: int | None = None
    channel_id: int | None = None
    category_id: int | None = None
    product_id: int | None = None
    customer_id: int | None = None

    def during(self, period):
        return replace(self, first=period.first, last=period.last)

    @property
    def line_level(self):
        return self.category_id is not None or self.product_id is not None


def filters_for(workspace, period, **extra):
    return Filters(workspace.id, workspace.data_version or 0, period.first, period.last,
                   **{k: v for k, v in extra.items() if v is not None})


# ---------- Choosing a source ----------

def plan(f, prefer="auto"):
    """Which table answers order-level questions for these filters."""
    if f.customer_id is not None or f.category_id is not None:
        possible = None  # needs the real orders or lines
    elif f.product_id is not None:
        possible = "products"
    else:
        possible = "orders"
    if prefer == "raw":
        return "raw"
    if prefer == "rollup" and possible is None:
        raise ValueError("No rollup can answer this: it needs the raw sales.")
    return possible or "raw"


@dataclass
class Source:
    kind: str
    tables: str           # the FROM part
    day: str              # the day column
    where: list           # conditions, joined with AND
    sums: dict            # measure -> SQL
    columns: dict         # region / channel / product / category -> column


def make_source(d, f, kind):
    """The FROM, WHERE and SUMs for a kind of source: "orders", "products", "raw" (orders,
    or lines when a category or product is chosen) or "raw_lines" (always lines)."""
    first, last = sql.date_param(d, "first"), sql.date_param(d, "last")
    if kind == "orders":
        src = Source(kind, "daily_order_stats r", "r.day", ["r.workspace_id = :ws", f"r.day BETWEEN {first} AND {last}"],
                     {"orders": "SUM(r.orders)", "units": "SUM(r.units)", "discount": "SUM(r.discount)",
                      "revenue": "SUM(r.revenue)", "cost": "SUM(r.cost)", "new_customers": "SUM(r.new_customers)"},
                     {"region": "r.region_id", "channel": "r.channel_id"})
    elif kind == "products":
        src = Source(kind, "daily_product_stats r JOIN products p ON p.id = r.product_id", "r.day",
                     ["r.workspace_id = :ws", f"r.day BETWEEN {first} AND {last}"],
                     {"orders": "SUM(r.orders)", "units": "SUM(r.units)", "discount": "SUM(r.discount)",
                      "revenue": "SUM(r.revenue)", "cost": "SUM(r.cost)", "new_customers": "NULL"},
                     {"region": "r.region_id", "channel": "r.channel_id", "product": "r.product_id",
                      "category": "p.category_id"})
    elif kind == "raw" and not f.line_level:
        src = Source("raw", "orders o LEFT JOIN customers c ON c.id = o.customer_id", "o.order_date",
                     ["o.workspace_id = :ws", f"o.order_date BETWEEN {first} AND {last}"],
                     {"orders": "COUNT(*)", "units": "SUM(o.units)", "discount": "SUM(o.discount)",
                      "revenue": "SUM(o.revenue)", "cost": "SUM(o.cost)",
                      "new_customers": "SUM(CASE WHEN c.first_order_id = o.id THEN 1 ELSE 0 END)"},
                     {"region": "o.region_id", "channel": "o.channel_id"})
    else:
        src = Source("raw", "order_lines l JOIN orders o ON o.id = l.order_id JOIN products p ON p.id = l.product_id "
                            "LEFT JOIN customers c ON c.id = o.customer_id", "o.order_date",
                     ["o.workspace_id = :ws", f"o.order_date BETWEEN {first} AND {last}"],
                     {"orders": "COUNT(DISTINCT l.order_id)", "units": "SUM(l.quantity)",
                      "discount": "SUM(l.discount)", "revenue": "SUM(l.revenue)", "cost": "SUM(l.cost)",
                      "new_customers": "COUNT(DISTINCT CASE WHEN c.first_order_id = o.id THEN o.id END)"},
                     {"region": "o.region_id", "channel": "o.channel_id", "product": "l.product_id",
                      "category": "p.category_id"})
    if f.region_id is not None:
        src.where.append(f"{src.columns['region']} = :region")
    if f.channel_id is not None:
        src.where.append(f"{src.columns['channel']} = :channel")
    if f.product_id is not None:
        src.where.append(f"{src.columns['product']} = :product")
    if f.category_id is not None:
        src.where.append(f"{src.columns['category']} = :category")
    if f.customer_id is not None:
        src.where.append("o.customer_id = :customer")
    return src


def params_for(f, **extra):
    params = {"ws": f.ws, "first": f.first, "last": f.last, "region": f.region_id, "channel": f.channel_id,
              "category": f.category_id, "product": f.product_id, "customer": f.customer_id}
    params.update(extra)
    return params


def _row_sums(row):
    return {name: int(row[name] or 0) if row[name] is not None else None for name in SUMS}


# ---------- KPIs ----------

KPIS = [
    # key, label, format, a rise is good?, what it means
    ("revenue", "Revenue", "money", True, "What customers paid, after discounts."),
    ("orders", "Orders", "number", True, "How many orders (receipts)."),
    ("aov", "Average Order", "money", True, "Revenue divided by orders."),
    ("profit", "Gross Profit", "money", True, "Revenue minus what the items cost you."),
    ("units", "Units Sold", "number", True, "How many items, adding up every quantity."),
    ("customers", "Customers", "number", True, "Different customers who ordered."),
    ("new_customers", "New Customers", "number", True, "Customers whose very first order is in these dates."),
    ("repeat_rate", "Returning Customers", "percent", True,
     "Customers who had ordered before these dates, as a share of all customers."),
    ("discount_rate", "Discount Rate", "percent", False, "Discounts as a share of the price before discounts."),
]
KPI_BY_KEY = {k[0]: k for k in KPIS}
# Shown inside another tile, not as its own (the margin sits under Gross Profit).
EXTRA_KPIS = [("margin", "Profit Margin", "percent", True, "Gross profit as a share of revenue.")]


def derive(sums):
    """The ratios, from the sums (never average an average: divide the totals)."""
    out = dict(sums)
    revenue, orders, cost = out.get("revenue") or 0, out.get("orders") or 0, out.get("cost") or 0
    out["profit"] = revenue - cost
    out["margin"] = (revenue - cost) / revenue if revenue else None
    out["aov"] = round(revenue / orders) if orders else None
    gross = revenue + (out.get("discount") or 0)
    out["discount_rate"] = (out.get("discount") or 0) / gross if gross else None
    customers = out.get("customers")
    if customers is not None and out.get("new_customers") is not None:
        out["returning_customers"] = customers - out["new_customers"]
        out["repeat_rate"] = out["returning_customers"] / customers if customers else None
    return out


def kpi_query(d, f, kind):
    """The SQL (and its parameters) for the KPI sums from one kind of source.
    The Performance Lab times and EXPLAINs exactly this."""
    src = make_source(d, f, kind)
    sums = ", ".join(f"{expression} AS {name}" for name, expression in src.sums.items())
    return f"SELECT {sums} FROM {src.tables} WHERE {' AND '.join(src.where)}", params_for(f)


@memo
def kpis(s, f, source="auto"):
    d = sql.name_of(s)
    kind = plan(f, "raw" if source == "raw" else ("rollup" if source == "rollup" else "auto"))
    query, params = kpi_query(d, f, kind)
    row = s.execute(text(query), params).mappings().one()
    out = _row_sums(row)
    # Different customers can't come from a rollup (see models.py): ask the orders.
    people = customer_counts(s, f)
    out["customers"] = people["customers"]
    out["new_customers"] = people["new_customers"]
    out["source"] = kind
    return derive(out)


def customer_counts(s, f):
    """How many different customers ordered, and how many of them for the first time ever.

    Two lessons from the benchmark (docs/BENCHMARKS.md):
    - COUNT(DISTINCT o.customer_id) needs no join: with the customer in the date index, it
      is read from the index alone;
    - with no filter, "new customers" are simply the customers whose first order is in
      these dates: one count on the customers table instead of a join with every order.
    """
    d = sql.name_of(s)
    src = make_source(d, f, "raw")
    tables = src.tables if f.line_level else "orders o"
    customers = s.execute(text(f"SELECT COUNT(DISTINCT o.customer_id) FROM {tables} WHERE {' AND '.join(src.where)}"),
                          params_for(f)).scalar()
    first, last = sql.date_param(d, "first"), sql.date_param(d, "last")
    if f.region_id is None and f.channel_id is None and not f.line_level and f.customer_id is None:
        new = s.execute(text(f"SELECT COUNT(*) FROM customers WHERE workspace_id = :ws "
                             f"AND first_order_date BETWEEN {first} AND {last}"), params_for(f)).scalar()
    else:
        new = s.execute(text(f"SELECT COUNT(DISTINCT o.customer_id) FROM {src.tables} "
                             f"WHERE {' AND '.join(src.where)} AND c.first_order_date >= {first}"),
                        params_for(f)).scalar()
    return {"customers": int(customers or 0), "new_customers": int(new or 0)}


def change(now, before):
    """The change as a share of before: 0.12 is 12% up. None when there is nothing to compare with."""
    if now is None or before is None or before == 0:
        return None
    return (now - before) / abs(before)


def pick(cards, keys):
    """These cards, in this order."""
    by_key = {card["key"]: card for card in cards}
    return [by_key[key] for key in keys if key in by_key]


def kpi_cards(now, before, sparks=None):
    """What the KPI tiles show: value, change, whether that change is good, and a sparkline."""
    cards = []
    for key, label, kind, rise_is_good, meaning in KPIS:
        value = now.get(key)
        old = before.get(key) if before else None
        if kind == "percent":
            moved = None if value is None or old is None else value - old  # in points, not percent of percent
        else:
            moved = change(value, old)
        good = None if not moved else (moved > 0) == rise_is_good
        cards.append({"key": key, "label": label, "kind": kind, "value": value, "before": old,
                      "change": moved, "good": good, "meaning": meaning,
                      "spark": (sparks or {}).get(key)})
    return cards


# ---------- Trends ----------

@memo
def trend(s, f, grain="day", source="auto"):
    """One row per day (or week, month...) from first to last, zeros where nothing sold.

    For days, each row also has the 7-day sums ending that day (for the moving
    average), worked out by a window function over a gap-free day series.
    """
    d = sql.name_of(s)
    kind = plan(f, "raw" if source == "raw" else ("rollup" if source == "rollup" else "auto"))
    src = make_source(d, f, kind)
    if grain == "day":
        rows = _daily_with_window(s, d, f, src)
    else:
        rows = _bucketed(s, d, f, src, grain)
    return rows


def _daily_with_window(s, d, f, src):
    sums = ", ".join(f"{expression} AS {name}" for name, expression in src.sums.items())
    totals = ", ".join(f"COALESCE(x.{name}, 0) AS {name}" for name in SUMS)
    windows = ", ".join(f"SUM(COALESCE(x.{name}, 0)) OVER seven AS {name}_7" for name in ("orders", "revenue", "cost",
                                                                                            "units"))
    early = f.first - timedelta(days=6)  # the first day's average needs the 6 days before it
    query = f"""
        WITH RECURSIVE {sql.day_series(d, 'early', 'last')},
        daily AS (
            SELECT {src.day} AS day, {sums}
            FROM {src.tables}
            WHERE {' AND '.join(src.where)}
            GROUP BY {src.day}
        )
        SELECT d.day, {totals}, {windows}
        FROM days d LEFT JOIN daily x ON x.day = d.day
        WINDOW seven AS (ORDER BY d.day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
        ORDER BY d.day
    """
    out = []
    for row in s.execute(text(query), params_for(f, first=early, early=early)).mappings():
        day = sql.as_date(row["day"])
        if day < f.first:
            continue
        item = {"start": day, **{name: int(row[name] or 0) for name in SUMS}}
        item["avg7"] = {name: row[f"{name}_7"] / 7 for name in ("orders", "revenue", "cost", "units")}
        out.append(item)
    return out


def _bucketed(s, d, f, src, grain):
    sums = ", ".join(f"{expression} AS {name}" for name, expression in src.sums.items())
    start = sql.bucket(d, src.day, grain)
    query = f"""
        SELECT {start} AS start, {sums}
        FROM {src.tables}
        WHERE {' AND '.join(src.where)}
        GROUP BY {start}
        ORDER BY start
    """
    found = {sql.as_date(row["start"]): row for row in s.execute(text(query), params_for(f)).mappings()}
    out = []
    for begin in sql.buckets(f.first, f.last, grain):
        row = found.get(begin)
        out.append({"start": begin, **{name: int(row[name] or 0) if row else 0 for name in SUMS}})
    return out


@memo
def customers_trend(s, f, grain="day"):
    """Different customers per bucket (always from the orders: distinct counts don't roll up)."""
    d = sql.name_of(s)
    src = make_source(d, f, "raw")
    start = sql.bucket(d, "o.order_date", grain)
    found = {sql.as_date(r["start"]): int(r["customers"]) for r in s.execute(text(f"""
        SELECT {start} AS start, COUNT(DISTINCT o.customer_id) AS customers
        FROM {src.tables} WHERE {' AND '.join(src.where)}
        GROUP BY {start}"""), params_for(f)).mappings()}
    return [{"start": b, "customers": found.get(b, 0)} for b in sql.buckets(f.first, f.last, grain)]


def measure_of(row, measure):
    """One measure from a trend row (ratios from the row's own sums)."""
    if measure == "profit":
        return row["revenue"] - row["cost"]
    if measure == "aov":
        return round(row["revenue"] / row["orders"]) if row["orders"] else None
    if measure == "margin":
        return (row["revenue"] - row["cost"]) / row["revenue"] if row["revenue"] else None
    return row.get(measure)


def moving_average(row, measure):
    seven = row.get("avg7")
    if not seven:
        return None
    if measure == "profit":
        return seven["revenue"] - seven["cost"]
    if measure == "aov":
        return seven["revenue"] / seven["orders"] if seven["orders"] else None
    if measure == "margin":
        return (seven["revenue"] - seven["cost"]) / seven["revenue"] if seven["revenue"] else None
    return seven.get(measure)


# ---------- Breakdowns ----------

DIMENSIONS = {
    # dimension -> (names table, label)
    "category": ("categories", "Category"),
    "product": ("products", "Product"),
    "region": ("regions", "Region"),
    "channel": ("channels", "Channel"),
}
ORDER_BY = {"revenue": "t.revenue", "profit": "(t.revenue - t.cost)", "units": "t.units", "orders": "t.orders"}


@memo
def breakdown(s, f, dimension, measure="revenue", limit=None, source="auto"):
    """Totals per region, channel, category or product, biggest first, each with its
    share of the total and its place (RANK). The window functions see every group,
    even when LIMIT keeps only the top few."""
    d = sql.name_of(s)
    names_table, _label = DIMENSIONS[dimension]
    if dimension in ("region", "channel"):
        kind = plan(f, "raw" if source == "raw" else ("rollup" if source == "rollup" else "auto"))
        src = make_source(d, f, kind)
    else:
        # Per product (or category), sums add up over days, regions and channels.
        if source == "raw" or f.customer_id is not None:
            src = make_source(d, f, "raw_lines")
        else:
            src = make_source(d, f, "products")
    key = src.columns[dimension]
    orders = src.sums["orders"]
    if dimension == "category" and src.kind == "products":
        orders = "NULL"  # can't be added up over products (one order can have two from a category)
    order_by = ORDER_BY.get(measure, "t.revenue")
    extra = ", n.sku" if dimension == "product" else ""
    query = f"""
        WITH t AS (
            SELECT {key} AS id, {src.sums['revenue']} AS revenue, {src.sums['cost']} AS cost,
                   {src.sums['units']} AS units, {orders} AS orders
            FROM {src.tables}
            WHERE {' AND '.join(src.where)}
            GROUP BY {key}
        )
        SELECT t.id, n.name{extra}, t.revenue, t.cost, t.units, t.orders,
               t.revenue * 1.0 / NULLIF(SUM(t.revenue) OVER (), 0) AS share,
               RANK() OVER (ORDER BY {order_by} DESC) AS place
        FROM t LEFT JOIN {names_table} n ON n.id = t.id
        ORDER BY {order_by} DESC, n.name
        {'LIMIT :limit' if limit else ''}
    """
    rows = []
    for row in s.execute(text(query), params_for(f, limit=limit)).mappings():
        item = dict(row)
        item["name"] = item["name"] or ("No category" if dimension == "category" else "Unknown")
        item["revenue"], item["cost"], item["units"] = int(item["revenue"] or 0), int(item["cost"] or 0), \
            int(item["units"] or 0)
        item["orders"] = int(item["orders"]) if item["orders"] is not None else None
        item["profit"] = item["revenue"] - item["cost"]
        item["margin"] = item["profit"] / item["revenue"] if item["revenue"] else None
        item["share"] = float(item["share"]) if item["share"] is not None else 0.0
        item["place"] = int(item["place"])
        rows.append(item)
    return rows


def top_with_rest(rows, keep=7, measure="revenue"):
    """The biggest few, plus one "Everything else" row adding up the rest."""
    if len(rows) <= keep + 1:
        return rows
    head, rest = rows[:keep], rows[keep:]
    other = {"id": None, "name": f"{len(rest)} others", "is_rest": True,
             **{k: sum(r[k] or 0 for r in rest) for k in ("revenue", "cost", "units", "profit", "share")},
             "orders": None}
    return head + [other]


# ---------- The workspace's data ----------

@memo
def bounds(s, ws, version):
    """The first and last day with a sale (version is only for the cache key)."""
    first, last = s.execute(text("SELECT MIN(order_date), MAX(order_date) FROM orders WHERE workspace_id = :ws"),
                            {"ws": ws}).one()
    return sql.as_date(first), sql.as_date(last)


@memo
def choices(s, ws, version):
    """The regions, channels and categories, for the filter menus."""
    out = {}
    for name, table in (("regions", "regions"), ("channels", "channels"), ("categories", "categories")):
        out[name] = [(row.id, row.name) for row in s.execute(
            text(f"SELECT id, name FROM {table} WHERE workspace_id = :ws ORDER BY name"), {"ws": ws})]
    return out


# ---------- Drilling down ----------

def with_change(rows, old_rows, key="id", measure="revenue"):
    """Add each row's change against the same row in the earlier period (None if it is new)."""
    before = {row[key]: row for row in old_rows or []}
    for row in rows:
        old = before.get(row[key])
        row["before"] = old[measure] if old else None
        row["change"] = change(row[measure], old[measure]) if old else None
    return rows


@memo
def top_customers(s, f, limit=10):
    """The customers who spent the most in these dates (with a product or category: on it)."""
    d = sql.name_of(s)
    src = make_source(d, f, "raw")
    orders = "COUNT(DISTINCT o.id)"
    query = f"""
        SELECT c.id, c.name, c.code, c.first_order_date, {orders} AS orders,
               {src.sums['revenue']} AS revenue, {src.sums['units']} AS units, MAX(o.order_date) AS last_order
        FROM {src.tables}
        WHERE {' AND '.join(src.where)} AND o.customer_id IS NOT NULL
        GROUP BY c.id, c.name, c.code, c.first_order_date
        ORDER BY revenue DESC, c.name
        LIMIT :limit
    """
    rows = []
    for row in s.execute(text(query), params_for(f, limit=limit)).mappings():
        item = dict(row)
        item["first_order_date"] = sql.as_date(item["first_order_date"])
        item["last_order"] = sql.as_date(item["last_order"])
        item["revenue"], item["units"], item["orders"] = int(item["revenue"] or 0), int(item["units"] or 0), \
            int(item["orders"])
        item["is_new"] = item["first_order_date"] is not None and item["first_order_date"] >= f.first
        item["initial"] = (str(item["name"] or "?").strip()[:1] or "?").upper()
        rows.append(item)
    return rows


@memo
def heatmap(s, f):
    """Orders by weekday (0 = Monday) and hour, for orders whose file had a time."""
    d = sql.name_of(s)
    src = make_source(d, f, "raw")
    weekday = sql.weekday(d, "o.order_date")
    query = f"""
        SELECT {weekday} AS weekday, o.order_hour AS hour, COUNT(DISTINCT o.id) AS orders,
               {src.sums['revenue']} AS revenue
        FROM {src.tables}
        WHERE {' AND '.join(src.where)} AND o.order_hour IS NOT NULL
        GROUP BY {weekday}, o.order_hour
    """
    return {(int(r["weekday"]), int(r["hour"])): {"orders": int(r["orders"]), "revenue": int(r["revenue"] or 0)}
            for r in s.execute(text(query), params_for(f)).mappings()}

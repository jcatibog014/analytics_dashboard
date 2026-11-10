"""Reports: a pivot table you build, customer cohorts, and the 80/20 product list.

The pivot has totals for every row, every column and the whole table.
PostgreSQL could do that in one GROUP BY with GROUPING SETS, but SQLite
can't, so we write the portable version: four GROUP BYs joined with
UNION ALL (cell, row total, column total, grand total), each marked with
a "level". The totals are asked of the database, not added up in Python,
because some measures don't add up: a customer who bought in two regions
is one customer in the total, not two.
"""
import csv
import io
from dataclasses import dataclass

from sqlalchemy import bindparam, text

from . import analytics, charts
from . import dialect as sql
from .analytics import make_source, params_for

DIMENSIONS = [
    ("category", "Category"),
    ("product", "Product"),
    ("region", "Region"),
    ("channel", "Channel"),
    ("customer", "Customer"),
    ("month", "Month"),
    ("quarter", "Quarter"),
    ("week", "Week"),
    ("weekday", "Weekday"),
]
DIMENSION_NAMES = dict(DIMENSIONS)
COLUMN_DIMENSIONS = ["", "region", "channel", "category", "month", "quarter", "weekday"]  # few enough to be columns

MEASURES = [
    ("revenue", "Revenue", "money"),
    ("profit", "Gross profit", "money"),
    ("orders", "Orders", "number"),
    ("units", "Units", "number"),
    ("customers", "Customers", "number"),
    ("aov", "Average order", "money"),
]
MEASURE_BY_KEY = {m[0]: m for m in MEASURES}
MAX_ROWS = 100
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LINE_DIMENSIONS = ("category", "product")


@dataclass(frozen=True)
class Spec:
    rows: str = "category"
    columns: str = ""
    measure: str = "revenue"

    @classmethod
    def from_args(cls, args):
        rows = args.get("rows", "category")
        rows = rows if rows in DIMENSION_NAMES else "category"
        columns = args.get("columns", "")
        columns = columns if columns in COLUMN_DIMENSIONS and columns != rows else ""
        measure = args.get("measure", "revenue")
        measure = measure if measure in MEASURE_BY_KEY else "revenue"
        return cls(rows, columns, measure)

    @property
    def dims(self):
        return [d for d in (self.rows, self.columns) if d]


def choose_source(f, spec):
    """The fastest table that can answer (see analytics.plan for why some can't)."""
    lines = f.line_level or any(d in LINE_DIMENSIONS for d in spec.dims)
    needs_raw = (spec.measure in ("customers",) or "customer" in spec.dims or f.customer_id is not None
                 or f.category_id is not None
                 # An order with two products would count twice in a total over products.
                 or (spec.measure in ("orders", "aov") and any(d in LINE_DIMENSIONS for d in spec.dims)))
    if needs_raw:
        return "raw_lines" if lines else "raw"
    return "products" if lines else "orders"


def dimension_sql(d, src, dimension):
    if dimension in ("region", "channel", "product", "category"):
        return src.columns[dimension]
    if dimension == "customer":
        return "o.customer_id"
    if dimension == "weekday":
        return sql.weekday(d, src.day)
    return sql.bucket(d, src.day, dimension)


def measure_sql(src, measure):
    """The SQL for each part a measure needs (aov is worked out from two of them)."""
    out = {"revenue": src.sums["revenue"], "cost": src.sums["cost"], "orders": src.sums["orders"],
           "units": src.sums["units"]}
    if measure == "customers":
        out["customers"] = "COUNT(DISTINCT o.customer_id)"
    return out


def value_of(row, measure):
    if measure == "profit":
        return (row["revenue"] or 0) - (row["cost"] or 0)
    if measure == "aov":
        return round(row["revenue"] / row["orders"]) if row["orders"] else None
    return int(row[measure] or 0) if row.get(measure) is not None else 0


@analytics.memo
def pivot(s, f, spec):
    """{"rows": [...], "columns": [...], "cells": {(r, c): value}, "row_totals", "column_totals", "total"}."""
    d = sql.name_of(s)
    kind = choose_source(f, spec)
    src = make_source(d, f, kind)
    r = dimension_sql(d, src, spec.rows)
    c = dimension_sql(d, src, spec.columns) if spec.columns else "NULL"
    parts = measure_sql(src, spec.measure)
    selects = ", ".join(f"{expression} AS {name}" for name, expression in parts.items())
    where = " AND ".join(src.where)
    if spec.rows == "customer" or spec.columns == "customer":
        where += " AND o.customer_id IS NOT NULL"
    pieces = [f"SELECT 0 AS level, {r} AS r, {c} AS c, {selects} FROM {src.tables} WHERE {where} GROUP BY {r}"
              + (f", {c}" if spec.columns else "")]
    if spec.columns:
        pieces.append(f"SELECT 1 AS level, {r} AS r, NULL AS c, {selects} FROM {src.tables} WHERE {where} GROUP BY {r}")
        pieces.append(f"SELECT 2 AS level, NULL AS r, {c} AS c, {selects} FROM {src.tables} WHERE {where} GROUP BY {c}")
    pieces.append(f"SELECT 3 AS level, NULL AS r, NULL AS c, {selects} FROM {src.tables} WHERE {where}")
    found = [dict(row) for row in s.execute(text("\nUNION ALL\n".join(pieces)), params_for(f)).mappings()]

    cells, row_totals, column_totals, total = {}, {}, {}, None
    for row in found:
        value = value_of(row, spec.measure)
        key_r, key_c = _key(row["r"], spec.rows), _key(row["c"], spec.columns)
        if row["level"] == 0:
            cells[(key_r, key_c)] = value
            if not spec.columns:
                row_totals[key_r] = value
        elif row["level"] == 1:
            row_totals[key_r] = value
        elif row["level"] == 2:
            column_totals[key_c] = value
        else:
            total = value
    ordered_rows = sorted(row_totals, key=lambda k: _sort_key(spec.rows, k, row_totals))
    truncated = max(0, len(ordered_rows) - MAX_ROWS)
    ordered_rows = ordered_rows[:MAX_ROWS]
    ordered_columns = sorted(column_totals, key=lambda k: _sort_key(spec.columns, k, column_totals))
    names = _names(s, f.ws, spec.rows, ordered_rows)
    names.update(_names(s, f.ws, spec.columns, ordered_columns) if spec.columns else {})
    return {"rows": [(k, names.get((spec.rows, k), str(k))) for k in ordered_rows],
            "columns": [(k, names.get((spec.columns, k), str(k))) for k in ordered_columns],
            "cells": cells, "row_totals": row_totals, "column_totals": column_totals,
            "total": total, "truncated": truncated, "source": kind, "kind": MEASURE_BY_KEY[spec.measure][2]}


def _key(value, dimension):
    if value is None or not dimension:
        return None
    if dimension in ("month", "quarter", "week"):
        return sql.as_date(value)
    return int(value)


def _sort_key(dimension, key, totals):
    if dimension in ("month", "quarter", "week", "weekday"):
        return (0, key)  # time goes in time order
    value = totals.get(key)
    return (0, -(value or 0), str(key))  # everything else: biggest first


def _names(s, ws, dimension, keys):
    if not dimension:
        return {}
    if dimension == "weekday":
        return {(dimension, k): WEEKDAY_NAMES[k] for k in keys if k is not None}
    if dimension in ("month", "quarter", "week"):
        return {(dimension, k): sql.bucket_label(k, dimension) for k in keys if k is not None}
    table = {"category": "categories", "product": "products", "region": "regions", "channel": "channels",
             "customer": "customers"}[dimension]
    ids = [k for k in keys if k is not None]
    out = {(dimension, None): "No category" if dimension == "category" else "Unknown"}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        query = text(f"SELECT id, name FROM {table} WHERE workspace_id = :ws AND id IN :ids").bindparams(
            bindparam("ids", expanding=True))
        for id_, name in s.execute(query, {"ws": ws, "ids": chunk}):
            out[(dimension, id_)] = name
    return out


def pivot_table(result, spec):
    """The pivot as rows (for the page, CSV and Excel): header, body, footer."""
    kind = result["kind"]
    columns = result["columns"]
    header = [DIMENSION_NAMES[spec.rows]] + ([name for _k, name in columns] + ["Total"] if columns
                                            else [MEASURE_BY_KEY[spec.measure][1]])
    body = []
    for key, name in result["rows"]:
        values = [result["cells"].get((key, c)) for c, _n in columns] if columns else []
        body.append({"name": name, "key": key, "values": values, "total": result["row_totals"].get(key)})
    footer = {"values": [result["column_totals"].get(c) for c, _n in columns], "total": result["total"]}
    return {"header": header, "body": body, "footer": footer, "kind": kind}


def to_csv(result, spec, title):
    table = pivot_table(result, spec)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([title])
    writer.writerow([safe(h) for h in table["header"]])
    for row in table["body"]:
        writer.writerow([safe(row["name"]), *(plain(v, table["kind"]) for v in row["values"]),
                         plain(row["total"], table["kind"])])
    writer.writerow(["Total", *(plain(v, table["kind"]) for v in table["footer"]["values"]),
                     plain(table["footer"]["total"], table["kind"])])
    return "﻿" + out.getvalue()  # the BOM makes Excel read the peso sign right


def to_xlsx(result, spec, title):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    table = pivot_table(result, spec)
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    sheet.append([title])
    sheet["A1"].font = Font(bold=True, size=13)
    sheet.append(table["header"])
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    money_format = '"₱"#,##0.00'
    for row in table["body"]:
        sheet.append([row["name"], *(plain(v, table["kind"]) for v in row["values"]), plain(row["total"], table["kind"])])
    sheet.append(["Total", *(plain(v, table["kind"]) for v in table["footer"]["values"]),
                  plain(table["footer"]["total"], table["kind"])])
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
    if table["kind"] == "money":
        for row in sheet.iter_rows(min_row=3, min_col=2):
            for cell in row:
                cell.number_format = money_format
    sheet.column_dimensions["A"].width = 32
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def plain(value, kind):
    """A number for a file: pesos (not centavos) for money, so spreadsheets can add them up."""
    if value is None:
        return ""
    return round(value / 100, 2) if kind == "money" else value


def safe(value):
    """A spreadsheet runs a cell that starts with = + - @ as a formula; a quote in front stops that."""
    value = str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


# ---------- Cohorts ----------

@analytics.memo
def cohorts(s, ws, version, first, last, max_age=12):
    """Customers grouped by the month of their first order, and how many ordered again
    0, 1, 2 ... months later. One query: a CTE for each customer's first month, then
    DISTINCT (customer, months later) pairs, then a GROUP BY."""
    d = sql.name_of(s)
    first_month = sql.month_number(d, "c.first_order_date")
    order_month = sql.month_number(d, "o.order_date")
    query = f"""
        WITH firsts AS (
            SELECT c.id, {first_month} AS cohort
            FROM customers c
            WHERE c.workspace_id = :ws
              AND c.first_order_date BETWEEN {sql.date_param(d, 'first')} AND {sql.date_param(d, 'last')}
        ),
        active AS (
            SELECT DISTINCT f.id, f.cohort, {order_month} - f.cohort AS age
            FROM firsts f
            JOIN orders o ON o.workspace_id = :ws AND o.customer_id = f.id
        )
        SELECT cohort, age, COUNT(*) AS customers
        FROM active
        WHERE age <= :max_age
        GROUP BY cohort, age
        ORDER BY cohort, age
    """
    grid = {}
    for row in s.execute(text(query), {"ws": ws, "first": first, "last": last, "max_age": max_age}).mappings():
        grid.setdefault(int(row["cohort"]), {})[int(row["age"])] = int(row["customers"])
    from datetime import date
    out = []
    latest = last.year * 12 + last.month - 1  # the last month that has happened
    for cohort in sorted(grid):
        size = grid[cohort].get(0, 0)
        start = date(cohort // 12, cohort % 12 + 1, 1)
        ages = []
        for age in range(0, max_age + 1):
            if cohort + age > latest:
                ages.append(None)  # that month hasn't happened yet
                continue
            count = grid[cohort].get(age, 0)
            share = count / size if size else 0
            ages.append({"count": count, "share": share})
        out.append({"start": start, "size": size, "ages": ages})
    # Shade against the best return rate in the table (month 0 is always 100%, so it's left out).
    best = max([cell["share"] for row in out for cell in row["ages"][1:] if cell] or [0])
    for row in out:
        for cell in row["ages"]:
            if cell:
                cell["level"] = charts.heat_level(cell["share"], best)
    return out


# ---------- The 80/20 list ----------

@analytics.memo
def pareto(s, f):
    """Products by revenue with the running share of the total, worked out by a window
    function: SUM(revenue) OVER (ORDER BY revenue DESC ROWS UNBOUNDED PRECEDING)."""
    d = sql.name_of(s)
    src = make_source(d, f, "raw_lines" if f.customer_id is not None or f.category_id is not None else "products")
    query = f"""
        WITH t AS (
            SELECT {src.columns['product']} AS id, {src.sums['revenue']} AS revenue
            FROM {src.tables} WHERE {' AND '.join(src.where)}
            GROUP BY {src.columns['product']}
        )
        SELECT t.id, p.name, p.sku, t.revenue,
               SUM(t.revenue) OVER (ORDER BY t.revenue DESC, t.id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                 * 1.0 / NULLIF(SUM(t.revenue) OVER (), 0) AS running_share,
               ROW_NUMBER() OVER (ORDER BY t.revenue DESC, t.id) AS place,
               COUNT(*) OVER () AS products
        FROM t JOIN products p ON p.id = t.id
        ORDER BY t.revenue DESC, t.id
    """
    rows = [dict(r) for r in s.execute(text(query), params_for(f)).mappings()]
    for row in rows:
        row["revenue"] = int(row["revenue"] or 0)
        row["running_share"] = float(row["running_share"] or 0)
    head = next((r["place"] for r in rows if r["running_share"] >= 0.8 - 1e-9), len(rows))
    return {"rows": rows, "head": head, "products": len(rows)}

"""Drilling down: Products -> one product, Customers -> one customer, and when people buy."""
from flask import Blueprint, abort, g, render_template, request
from sqlalchemy import func, select

from .. import analytics, charts, db, scope
from .. import dialect as sql
from ..auth import login_required
from ..models import Customer, Order, Product
from .main import partial_note, trend_points, trend_table

bp = Blueprint("explore", __name__)

SORTS = [("revenue", "Revenue"), ("profit", "Gross profit"), ("units", "Units"), ("orders", "Orders")]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def trend_for(s, sc, f, before, measure="revenue", kind="money", label="Revenue"):
    grain = sc.choice.grain
    rows = analytics.trend(s, f, grain)
    old = analytics.trend(s, before, grain) if before else None
    points = trend_points(rows, old, measure, kind, grain, label, sc.compare_label, False, f, sc.bounds[0])
    series = [("main", label)] + ([("compare", sc.compare_label)] if old else [])
    return {"charts": charts.line_charts(points, kind, compare=bool(old)), "series": series,
            "note": partial_note(rows, grain, f),
            "table": trend_table(points, label, series),
            "summary": f"{label} by {grain} for these dates."}


def bars_for(s, sc, f, dimension, link=None):
    rows = analytics.top_with_rest(analytics.breakdown(s, f, dimension), keep=6)
    drawn = charts.bars(rows)
    if link:
        for row in drawn:
            if row.get("id") is not None:
                row["href"] = link(row)
    table = [[r["name"], charts.fmt("money", r["revenue"]), charts.fmt("percent", r["share"])] for r in rows]
    return {"bars": drawn, "rows": table, "headers": [dimension.capitalize(), "Revenue", "Share"]}


# ---------- Products ----------

@bp.get("/products")
@login_required
def products():
    s = db.session()
    sc = scope.current()
    sort = request.args.get("sort", "revenue")
    if sort not in dict(SORTS):
        sort = "revenue"
    rows = analytics.breakdown(s, sc.filters, "product", measure=sort)
    if sc.before:
        analytics.with_change(rows, analytics.breakdown(s, sc.before, "product"))
    categories = None
    if "category" not in sc.chosen:
        categories = bars_for(s, sc, sc.filters, "category", lambda row: sc.url("explore.products", category=row["id"]))
    names = {}
    if rows:
        names = dict(s.execute(select(Product.id, Product.category_id).where(Product.workspace_id == g.workspace.id)).all())
    category_names = dict(sc.options["categories"])
    for row in rows:
        row["category"] = category_names.get(names.get(row["id"]), "")
    return render_template("explore/products.html", sc=sc, rows=rows, sort=sort, sorts=SORTS, categories=categories)


@bp.get("/products/<int:product_id>")
@login_required
def product(product_id):
    s = db.session()
    item = s.get(Product, product_id)
    if item is None or item.workspace_id != g.workspace.id:
        abort(404)
    sc = scope.current(product_id=item.id)
    f, before = sc.filters, sc.before
    now = analytics.kpis(s, f)
    old = analytics.kpis(s, before) if before else None
    cards = analytics.pick(analytics.kpi_cards(now, old),
                           ("revenue", "orders", "units", "profit", "customers", "new_customers"))
    return render_template(
        "explore/product.html", sc=sc, product=item, now=now, cards=cards,
        trend=trend_for(s, sc, f, before),
        regions=bars_for(s, sc, f, "region", lambda row: sc.url(region=row["id"])),
        channels=bars_for(s, sc, f, "channel", lambda row: sc.url(channel=row["id"])),
        buyers=analytics.top_customers(s, f, 10))


# ---------- Customers ----------

def heat_cells(found):
    """7 x 24 cells (only the hours anyone ordered), each with its ramp step."""
    hours = sorted({hour for _day, hour in found}) or list(range(8, 21))
    hours = list(range(hours[0], hours[-1] + 1))
    biggest = max([cell["orders"] for cell in found.values()] or [0])
    grid = []
    for day, name in enumerate(WEEKDAYS):
        cells = []
        for hour in hours:
            cell = found.get((day, hour), {"orders": 0, "revenue": 0})
            cells.append({"hour": hour, "orders": cell["orders"], "revenue": cell["revenue"],
                          "level": charts.heat_level(cell["orders"], biggest)})
        grid.append({"name": name, "cells": cells, "total": sum(c["orders"] for c in cells)})
    table = []
    for row in grid:
        busiest = max(row["cells"], key=lambda c: c["orders"]) if row["total"] else None
        table.append([row["name"], charts.fmt("number", row["total"]),
                      hour_label(busiest["hour"]) if busiest else "—"])
    return {"hours": hours, "rows": grid, "biggest": biggest, "table": table}


def hour_label(hour):
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


@bp.get("/customers")
@login_required
def customers():
    s = db.session()
    sc = scope.current()
    f, before = sc.filters, sc.before
    now = analytics.kpis(s, f)
    old = analytics.kpis(s, before) if before else None
    cards = analytics.pick(analytics.kpi_cards(now, old), ("customers", "new_customers", "repeat_rate", "aov"))
    per_customer = now["revenue"] / now["customers"] if now["customers"] else None
    grain = sc.choice.grain
    rows = analytics.trend(s, f, grain)
    people = analytics.customers_trend(s, f, grain)
    points = []
    for row, count in zip(rows, people):
        title = sql.bucket_label(row["start"], grain)
        points.append({"label": sql.bucket_label(row["start"], grain, short=True), "value": count["customers"],
                       "second": row["new_customers"],
                       "tip": {"label": title, "rows": [("main", "Customers", charts.fmt("number", count["customers"])),
                                                        ("second", "New customers",
                                                         charts.fmt("number", row["new_customers"]))]}})
    series = [("main", "Customers"), ("second", "New customers")]
    heat = heat_cells(analytics.heatmap(s, f))
    return render_template(
        "explore/customers.html", sc=sc, now=now, cards=cards, per_customer=per_customer,
        chart=charts.line_charts(points, "number", second=True), series=series,
        table=trend_table(points, "Customers", series), heat=heat, hour_label=hour_label,
        partial_note=partial_note(rows, grain, f),
        buyers=analytics.top_customers(s, f, 15))


@bp.get("/customers/<int:customer_id>")
@login_required
def customer(customer_id):
    s = db.session()
    person = s.get(Customer, customer_id)
    if person is None or person.workspace_id != g.workspace.id:
        abort(404)
    sc = scope.current(customer_id=person.id)
    f = sc.filters
    now = analytics.kpis(s, f)
    old = analytics.kpis(s, sc.before) if sc.before else None
    cards = analytics.pick(analytics.kpi_cards(now, old), ("revenue", "orders", "aov", "units"))
    ever = s.execute(select(func.count(Order.id), func.coalesce(func.sum(Order.revenue), 0), func.max(Order.order_date))
                     .where(Order.workspace_id == g.workspace.id, Order.customer_id == person.id)).one()
    recent = s.scalars(select(Order).where(Order.workspace_id == g.workspace.id, Order.customer_id == person.id)
                       .order_by(Order.order_date.desc(), Order.id.desc()).limit(15)).all()
    favourites = analytics.breakdown(s, f, "product", limit=8)
    return render_template("explore/customer.html", sc=sc, person=person, now=now, cards=cards, ever=ever,
                           recent=recent, favourites=favourites)

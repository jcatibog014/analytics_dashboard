"""The Dashboard: KPI tiles, the trend, and sales by category, region, channel and product."""
from datetime import timedelta

from flask import Blueprint, render_template, request

from .. import analytics, charts, db, scope
from .. import dialect as sql
from ..auth import login_required
from ..formatting import date_range, nice_date

bp = Blueprint("main", __name__)

TREND_MEASURES = [
    ("revenue", "Revenue", "money"),
    ("orders", "Orders", "number"),
    ("profit", "Gross Profit", "money"),
    ("aov", "Average Order", "money"),
    ("customers", "Customers", "number"),
]
TREND_BY_KEY = {m[0]: m for m in TREND_MEASURES}
SPARK_MEASURES = ("revenue", "orders", "aov", "profit", "margin", "units", "new_customers", "discount_rate")


def bucket_title(start, grain):
    if grain == "day":
        return f"{start:%a}, {nice_date(start)}"
    if grain == "week":
        return f"Week of {nice_date(start)}"
    return sql.bucket_label(start, grain)


def trend_points(rows, old_rows, measure, kind, grain, label, compare_label, with_average, period=None,
                 data_first=None):
    """Rows from analytics.trend -> points for charts.line_chart, with hover text."""
    points = []
    for index, row in enumerate(rows):
        title = bucket_title(row["start"], grain)
        if period is not None and grain != "day":
            first = max(row["start"], period.first)
            last = min(sql.next_bucket(row["start"], grain) - timedelta(days=1), period.last)
            if first != row["start"] or last != sql.next_bucket(row["start"], grain) - timedelta(days=1):
                title += f" (only {date_range(first, last)})"
        value = row["customers"] if measure == "customers" else analytics.measure_of(row, measure)
        average = analytics.moving_average(row, measure) if with_average else None
        old = old_rows[index] if old_rows and index < len(old_rows) else None
        old_value = None
        if old is not None and data_first and sql.next_bucket(old["start"], grain) <= data_first:
            old = None  # before the first sale there is no data, which isn't the same as zero sales
        if old is not None:
            old_value = old["customers"] if measure == "customers" else analytics.measure_of(old, measure)
        tip_rows = [("main", label, charts.fmt(kind, value))]
        if with_average and average is not None:
            tip_rows.append(("avg", "7-day average", charts.fmt(kind, average)))
        if old is not None:
            tip_rows.append(("compare", f"{compare_label}, {sql.bucket_label(old['start'], grain)}",
                             charts.fmt(kind, old_value)))
        points.append({"label": sql.bucket_label(row["start"], grain, short=True), "value": value,
                       "avg": average, "compare": old_value,
                       "tip": {"label": title, "rows": tip_rows}})
    return points


def trend_summary(points, label, kind, grain, period):
    values = [p["value"] for p in points if p["value"] is not None]
    if not values:
        return f"{label}: no sales."
    best = max(points, key=lambda p: p["value"] or 0)
    total = "" if kind == "percent" or label == "Average Order" or label == "Customers" \
        else f" Total {charts.fmt(kind, sum(values))}."
    return (f"{label} by {grain}, {date_range(period.first, period.last)}.{total} "
            f"Highest: {charts.fmt(kind, best['value'])} ({best['tip']['label']}).")


def spark_values(rows, customers_rows, key):
    if key == "customers":
        return [r["customers"] for r in customers_rows]
    values = []
    for row in rows:
        if key == "discount_rate":
            gross = row["revenue"] + row["discount"]
            values.append(row["discount"] / gross if gross else None)
        else:
            values.append(analytics.measure_of(row, key))
    return values


@bp.get("/")
@login_required
def home():
    s = db.session()
    sc = scope.current()
    if sc.bounds[0] is None:
        return render_template("home.html", sc=sc, has_sales=False)
    f, grain = sc.filters, sc.choice.grain
    now = analytics.kpis(s, f)
    before = analytics.kpis(s, sc.before) if sc.before else None
    if not now["orders"]:
        latest = sc.bounds[1]
        latest_args = sc.args(range="custom", **{"from": (latest - timedelta(days=29)).isoformat(),
                                                 "to": latest.isoformat()})
        return render_template("home.html", sc=sc, has_sales=True, nothing_here=True, latest=latest,
                               latest_args=latest_args)

    rows = analytics.trend(s, f, grain)
    old_rows = analytics.trend(s, sc.before, grain) if sc.before else None
    customers_rows = analytics.customers_trend(s, f, grain)

    measure = request.args.get("measure", "revenue")
    if measure not in TREND_BY_KEY:
        measure = "revenue"
    _key, label, kind = TREND_BY_KEY[measure]
    if measure == "customers":
        rows_for_chart = customers_rows
        old_for_chart = analytics.customers_trend(s, sc.before, grain) if sc.before else None
    else:
        rows_for_chart, old_for_chart = rows, old_rows
    with_average = grain == "day" and len(rows) >= 14 and measure != "customers"
    points = trend_points(rows_for_chart, old_for_chart, measure, kind, grain, label, sc.compare_label, with_average,
                          f, sc.bounds[0])
    chart = charts.line_charts(points, kind, compare=bool(old_for_chart), average=with_average)
    series = [("main", label)]
    if with_average:
        series.append(("avg", "7-day average"))
    if old_for_chart:
        series.append(("compare", sc.compare_label))

    sparks = {key: charts.sparkline(spark_values(rows, customers_rows, key)) for key in SPARK_MEASURES + ("customers",)}
    cards = analytics.kpi_cards(now, before, sparks)

    parts = [breakdown_part(s, sc, dimension, title) for dimension, title in
             (("category", "Sales by Category"), ("region", "Sales by Region"), ("channel", "Sales by Channel"))]
    top_products = analytics.breakdown(s, f, "product", limit=10)
    old_products = {r["id"]: r for r in analytics.breakdown(s, sc.before, "product")} if sc.before else {}
    for row in top_products:
        old = old_products.get(row["id"])
        row["change"] = analytics.change(row["revenue"], old["revenue"]) if old else None
    return render_template(
        "dashboard.html", sc=sc, now=now, before=before, cards=cards, chart=chart, series=series, points=points,
        summary=trend_summary(points, label, kind, grain, f), measure=measure, measures=TREND_MEASURES,
        partial_note=partial_note(rows, grain, f),
        trend_kind=kind, trend_label=label, trend_table=trend_table(points, label, series), parts=parts,
        top_products=top_products)


def partial_note(rows, grain, period):
    """A week or month only partly inside the dates looks like a drop. Say so."""
    if grain == "day" or not rows:
        return None
    first_partial = rows[0]["start"] < period.first
    last_partial = sql.next_bucket(rows[-1]["start"], grain) - timedelta(days=1) > period.last
    names = {"week": "week", "month": "month", "quarter": "quarter", "year": "year"}[grain]
    if first_partial and last_partial:
        return f"The first and last {names}s are only partly inside these dates, so they look smaller."
    if last_partial:
        return f"The last {names} is only partly inside these dates (so far), so it looks smaller."
    if first_partial:
        return f"The first {names} is only partly inside these dates, so it looks smaller."
    return None


def trend_table(points, label, series):
    names = {"main": label, "avg": "7-day average"}
    headers = ["When"] + [names.get(key, name) for key, name in series]
    rows = []
    for point in points:
        values = {key: text for key, _name, text in point["tip"]["rows"]}
        rows.append([point["tip"]["label"]] + [values.get(key, "—") for key, _name in series])
    return {"headers": headers, "rows": rows}


def breakdown_part(s, sc, dimension, title):
    """One "Sales by ..." card: bars (each name filters the page by it) and its table."""
    rows = analytics.top_with_rest(analytics.breakdown(s, sc.filters, dimension), keep=6)
    drawn = charts.bars(rows)
    for row in drawn:
        if row.get("id") is None or dimension in sc.chosen:
            continue
        if dimension == "category":
            row["href"] = sc.url("explore.products", category=row["id"])  # drill down to its products
        else:
            row["href"] = sc.url(**{dimension: row["id"]})
    table = [[row["name"], charts.fmt("money", row["revenue"]), charts.fmt("percent", row["share"]),
              charts.fmt("money", row["profit"])] for row in rows]
    return {"title": title, "bars": drawn, "rows": table,
            "headers": [dimension.capitalize(), "Revenue", "Share", "Gross profit"]}

"""The Dashboard page and the chart geometry."""
import re
from datetime import timedelta

import pytest

from tallyho import charts, db, sample, workspaces
from tallyho.dates import today

from .conftest import post


@pytest.fixture
def stocked(app):
    """Four months of sample sales up to today."""
    with app.app_context():
        s = db.session()
        sample.load(s, workspaces.main_workspace(s), None, today() - timedelta(days=120), today(), orders_per_day=10)
        s.commit()
    return app


def test_empty_dashboard_asks_for_an_import(owner):
    page = owner.get("/").get_data(as_text=True)
    assert "No sales yet" in page and "/import" in page


def test_dashboard_shows_kpis_trend_and_breakdowns(stocked, owner):
    response = owner.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    for text in ("Revenue", "Orders", "Average Order", "Gross Profit", "New Customers", "Returning Customers",
                 "Revenue by day", "7-day average", "Previous period", "Sales by Category", "Sales by Region",
                 "Sales by Channel", "Top Products", "Show as table", "Coffee Beans", "Metro Manila"):
        assert text in page, text
    assert page.count('class="hit"') == 2 * 30  # a hover strip per day, in the wide and the phone drawing
    assert 'role="img"' in page and "Highest:" in page


def test_every_range_and_compare_works(stocked, owner):
    for key in ("today", "7d", "90d", "this_month", "last_month", "this_quarter", "ytd", "12m", "all"):
        response = owner.get(f"/?range={key}")
        assert response.status_code == 200, key
    page = owner.get("/?range=90d&compare=year").get_data(as_text=True)
    assert "Same dates last year" in page or "No earlier data" in page
    assert "Revenue by week" in owner.get("/?range=90d").get_data(as_text=True)
    assert "Revenue by month" in owner.get("/?range=90d&grain=month").get_data(as_text=True)
    first = (today() - timedelta(days=10)).isoformat()
    page = owner.get(f"/?range=custom&from={first}&to={today().isoformat()}").get_data(as_text=True)
    assert page.count('class="hit"') == 2 * 11


def test_measures(stocked, owner):
    for measure, title in (("orders", "Orders by day"), ("profit", "Gross Profit by day"),
                           ("aov", "Average Order by day"), ("customers", "Customers by day")):
        page = owner.get(f"/?measure={measure}").get_data(as_text=True)
        assert title in page
    assert "Revenue by day" in owner.get("/?measure=nonsense").get_data(as_text=True)


def test_filters_and_chips(stocked, owner):
    page = owner.get("/").get_data(as_text=True)
    region = re.search(r'<a href="(/\?[^"]*region=\d+[^"]*)">Metro Manila</a>', page)
    assert region, "region bars link to a filtered dashboard"
    filtered = owner.get(region.group(1).replace("&amp;", "&")).get_data(as_text=True)
    assert 'class="chip"' in filtered and "Metro Manila" in filtered
    assert "Sales by Region" in filtered
    # A filter from another workspace (or nonsense) is ignored.
    assert 'class="chip"' not in owner.get("/?region=99999").get_data(as_text=True)


def test_no_sales_in_the_chosen_dates(stocked, owner):
    page = owner.get("/?range=custom&from=2001-01-01&to=2001-01-31").get_data(as_text=True)
    assert "No sales in these dates" in page and "Show the 30 days before it" in page


def test_no_javascript_needed_and_no_inline_styles(stocked, owner):
    page = owner.get("/").get_data(as_text=True)
    assert "style=" not in page
    assert "<script>" not in page
    assert "data-apply-button" in page  # the Apply button for browsers without JS


def test_nice_scales():
    assert charts.nice_scale(87) == (100, [0, 25, 50, 75, 100])
    assert charts.nice_scale(1234) == (1500, [0, 500, 1000, 1500])
    top, ticks = charts.money_scale(1_234_567_00)
    assert top % 100 == 0 and ticks[0] == 0 and top >= 1_234_567_00


def test_line_chart_geometry():
    points = [{"label": str(n), "value": v, "tip": {"label": str(n), "rows": []}} for n, v in enumerate((0, 50, 100))]
    chart = charts.line_chart(points, "number")
    assert chart["main"].startswith("M64.0,")
    assert chart["area"].endswith("Z")
    assert len(chart["columns"]) == 3
    assert chart["columns"][0]["x0"] == chart["left"]
    assert chart["y_ticks"][0]["text"] == "0"
    single = charts.line_chart(points[:1], "number")
    assert single["main"]


def test_sparkline_and_bars():
    assert charts.sparkline([1]) is None
    line = charts.sparkline([1, None, 3, 2])
    assert line["d"].count("M") == 2  # a gap where a value is missing
    rows = charts.bars([{"name": "A", "revenue": 200}, {"name": "B", "revenue": 50}])
    assert rows[0]["width"] == 100 and rows[1]["width"] == 25 and rows[1]["half"] == 12.5
    assert charts.heat_level(0, 10) == 0 and charts.heat_level(10, 10) == 5 and charts.heat_level(1, 10) == 1


def test_the_menu_keeps_the_dates(stocked, owner):
    page = owner.get("/?range=90d").get_data(as_text=True)
    assert 'href="/?range=90d"' in page
    assert post(owner, "/logout").status_code == 302


def test_part_months_are_explained(stocked, owner):
    page = owner.get("/?range=90d&grain=month").get_data(as_text=True)
    assert "only partly inside these dates" in page
    assert "(only " in page  # in the hover text too

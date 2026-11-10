"""Reports: the pivot and its totals, downloads, saved reports, cohorts, 80/20, the weekly email."""
import io
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tallyho import db, mailer, reports, sample, tick, workspaces
from tallyho.dates import today
from tallyho.models import SavedReport, User

from .conftest import owner_id, post
from .test_analytics import HAND, MARCH, filters, load

MARCH_ARGS = "range=custom&from=2026-03-01&to=2026-03-03"


def pivot(app, **spec):
    with app.app_context():
        return reports.pivot(db.session(), filters(app, *MARCH), reports.Spec(**spec))


def by_name(result):
    names = dict(result["rows"])
    return {names[k]: v for k, v in result["row_totals"].items()}


def test_pivot_rows_only(app):
    load(app, HAND)
    result = pivot(app, rows="region", measure="revenue")
    assert by_name(result) == {"North": 216000, "South": 185000}
    assert result["total"] == 401000
    assert result["source"] == "orders"


def test_pivot_with_columns_and_totals(app):
    load(app, HAND)
    result = pivot(app, rows="region", columns="channel", measure="orders")
    names = dict(result["rows"])
    columns = dict(result["columns"])
    cells = {(names[r], columns[c]): v for (r, c), v in result["cells"].items()}
    assert cells == {("North", "Store"): 1, ("North", "Web"): 1, ("South", "Web"): 1, ("South", "Store"): 1}
    assert {columns[k]: v for k, v in result["column_totals"].items()} == {"Store": 2, "Web": 2}
    assert result["total"] == 4


def test_totals_that_dont_add_up_come_from_the_database(app):
    load(app, HAND)
    # C1 bought in the Store and on the Web: one customer, not two, in the total.
    result = pivot(app, rows="channel", measure="customers")
    assert sum(result["row_totals"].values()) == 4
    assert result["total"] == 3
    # Orders by category: A4 has two coffee lines but is one order.
    by_category = pivot(app, rows="category", measure="orders")
    assert by_name(by_category) == {"Coffee": 3, "Merch": 2}
    assert by_category["total"] == 4 and by_category["source"] == "raw_lines"
    # Orders by product (any order with two products would count twice in a sum).
    by_product = pivot(app, rows="product", columns="region", measure="orders")
    assert by_product["total"] == 4


def test_time_and_weekday_dimensions(app):
    load(app, HAND)
    result = pivot(app, rows="weekday", measure="revenue")
    assert by_name(result) == {"Sunday": 126000, "Monday": 120000, "Tuesday": 155000}
    assert [name for _k, name in result["rows"]] == ["Monday", "Tuesday", "Sunday"]  # in weekday order
    monthly = pivot(app, rows="month", measure="aov")
    assert list(monthly["row_totals"].values()) == [round(401000 / 4)]


def test_builder_page_downloads_and_save(app, owner):
    load(app, HAND)
    page = owner.get(f"/reports/build?rows=region&columns=channel&measure=revenue&{MARCH_ARGS}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Revenue by Region and Channel" in html and "North" in html and "₱4,010" in html
    csv_text = owner.get(f"/reports/build?rows=region&measure=revenue&format=csv&{MARCH_ARGS}").get_data(as_text=True)
    assert csv_text.startswith("﻿") and "North,2160.0" in csv_text and "Total,4010.0" in csv_text
    xlsx = owner.get(f"/reports/build?rows=region&measure=revenue&format=xlsx&{MARCH_ARGS}")
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(xlsx.data)).active
    assert sheet["A3"].value == "North" and sheet["B3"].value == 2160
    params = {"rows": "region", "measure": "revenue", "range": "custom", "from": "2026-03-01", "to": "2026-03-03",
              "evil": "x"}
    response = post(owner, "/reports/save", {"name": "  Regions in March ", "params": json.dumps(params)})
    assert response.status_code == 302
    with app.app_context():
        report = db.session().scalar(select(SavedReport))
        assert report.name == "Regions in March" and "evil" not in json.loads(report.params)
        report_id = report.id
    opened = owner.get(f"/reports/{report_id}", follow_redirects=True).get_data(as_text=True)
    assert "<h1>Regions in March</h1>" in opened and "North" in opened
    index = owner.get("/reports").get_data(as_text=True)
    assert "Regions in March" in index and "Revenue by region" in index
    post(owner, f"/reports/{report_id}/delete")
    with app.app_context():
        assert db.session().scalar(select(SavedReport)) is None


def test_formulas_in_names_are_made_harmless(app, owner):
    load(app, "Order,Date,SKU,Product,Qty,Price,Region\nZ,2026-03-02,X,Mug,1,100,=HYPERLINK(1)\n")
    csv_text = owner.get(f"/reports/build?rows=region&format=csv&{MARCH_ARGS}").get_data(as_text=True)
    assert "'=HYPERLINK(1)" in csv_text


def test_print_view_and_no_inline_styles(app, owner):
    load(app, HAND)
    html = owner.get(f"/reports/build?{MARCH_ARGS}").get_data(as_text=True)
    assert "data-print" in html and "print-head" in html and "style=" not in html


def test_cohorts_by_hand(app):
    load(app, HAND + "B1,2026-04-15,BEAN,Beans,Coffee,1,300,100,,C1,North,Store\n"
                      "B2,2026-05-02,BEAN,Beans,Coffee,1,300,100,,C3,South,Store\n")
    with app.app_context():
        s = db.session()
        workspace = workspaces.main_workspace(s)
        rows = reports.cohorts(s, workspace.id, workspace.data_version, date(2026, 2, 1), date(2026, 5, 31))
    by_month = {row["start"]: row for row in rows}
    february, march = by_month[date(2026, 2, 1)], by_month[date(2026, 3, 1)]
    assert february["size"] == 1  # C2
    assert february["ages"][1]["count"] == 1  # C2 ordered again in March
    assert march["size"] == 2  # C1, C3
    assert march["ages"][1]["count"] == 1 and march["ages"][2]["count"] == 1  # C1 in April, C3 in May
    assert march["ages"][1]["share"] == pytest.approx(0.5)
    assert march["ages"][3] is None  # June hasn't happened (in these dates)


def test_cohort_and_pareto_pages(app, owner):
    with app.app_context():
        s = db.session()
        sample.load(s, workspaces.main_workspace(s), None, today() - timedelta(days=200), today(), orders_per_day=8)
        s.commit()
    page = owner.get("/reports/cohorts").get_data(as_text=True)
    assert "Customer Cohorts" in page and "1 mo" in page and "Average" in page
    page = owner.get("/reports/pareto?range=90d").get_data(as_text=True)
    assert "bring in" in page and "Top 80%" in page and "Running share" in page


def test_pareto_running_share(app):
    load(app, HAND)
    with app.app_context():
        result = reports.pareto(db.session(), filters(app, *MARCH))
    rows = result["rows"]
    assert [r["sku"] for r in rows] == ["BEAN", "MUG", "TEA"]
    assert rows[0]["running_share"] == pytest.approx(225000 / 401000)
    assert rows[-1]["running_share"] == pytest.approx(1)
    assert result["head"] == 2


# ---------- The weekly email ----------

def test_weekly_email_setting(app, owner):
    post(owner, "/settings", {"action": "weekly", "weekly_email": "1"})
    with app.app_context():
        assert db.session().get(User, owner_id(app)).weekly_email is True
    post(owner, "/settings", {"action": "weekly"})
    with app.app_context():
        assert db.session().get(User, owner_id(app)).weekly_email is False


def test_the_timer_sends_last_weeks_numbers_once(app):
    monday = today() - timedelta(days=today().weekday())
    first = monday - timedelta(days=7)
    load(app, f"Order,Date,SKU,Qty,Price\nW1,{first.isoformat()},X,2,500\nW0,{(first - timedelta(days=3)).isoformat()},X,1,500\n")
    with app.app_context():
        s = db.session()
        s.get(User, owner_id(app)).weekly_email = True
        s.commit()
    with app.test_request_context():
        report = tick.run(db.session(), None)
        assert report["summaries"] == 1
        sent = mailer.outbox()[-1]
        body = sent.get_content()
        assert "Revenue: ₱1,000 (+100% on the week before)" in body
        assert "Orders: 1" in body and "Settings" in body
        assert tick.run(db.session(), None)["summaries"] == 0  # once a week


def test_the_timer_needs_its_key(app, client):
    assert client.post("/tasks/tick").status_code == 404
    app.config["TICK_KEY"] = "secret"
    assert client.post("/tasks/tick", headers={"X-Tick-Key": "wrong"}).status_code == 404
    response = client.post("/tasks/tick", headers={"X-Tick-Key": "secret"})
    assert response.status_code == 200 and "summaries" in response.get_json()


def test_tick_command(app):
    result = app.test_cli_runner().invoke(args=["tick"])
    assert result.exit_code == 0 and "summaries" in result.output

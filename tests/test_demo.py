"""Try Demo: a copy of the sample per visitor, an example on every page, and clean-up."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from tallyho import analytics, db, demo
from tallyho.dates import today, utcnow
from tallyho.models import Alert, Goal, Import, Order, SavedReport, Workspace

from .conftest import post


@pytest.fixture
def visitor(app):
    client = app.test_client()
    response = post(client, "/demo")
    assert response.status_code == 302 and response.headers["Location"].endswith("/")
    return client


def demo_workspace(app):
    with app.app_context():
        return db.session().scalar(select(Workspace).where(Workspace.is_demo.is_(True)))


def test_the_login_page_offers_the_demo(client):
    assert 'action="/demo"' in client.get("/login").get_data(as_text=True)


def test_the_demo_is_a_copy_ending_today(app, visitor):
    workspace = demo_workspace(app)
    with app.app_context():
        s = db.session()
        template = demo.template(s)
        mine = s.scalar(select(func.count()).select_from(Order).where(Order.workspace_id == workspace.id))
        assert 5000 < mine <= s.scalar(select(func.count()).select_from(Order).where(Order.workspace_id == template.id))
        first, last = analytics.bounds(s, workspace.id, workspace.data_version)
        assert last == today() and (last - first).days >= 400
        # The copy points at its own regions, products and customers, never the template's.
        for column, table in (("region_id", "regions"), ("channel_id", "channels"), ("customer_id", "customers")):
            stray = s.execute(text(f"SELECT COUNT(*) FROM orders o JOIN {table} x ON x.id = o.{column} "
                                   f"WHERE o.workspace_id = :w AND x.workspace_id != :w"), {"w": workspace.id}).scalar()
            assert stray == 0, table
        stray = s.execute(text("SELECT COUNT(*) FROM order_lines l JOIN products p ON p.id = l.product_id "
                               "WHERE l.workspace_id = :w AND p.workspace_id != :w"), {"w": workspace.id}).scalar()
        assert stray == 0
        # Weekdays are kept: the copy moved by whole weeks.
        moved = s.execute(text("SELECT o2.order_date, o1.order_date FROM orders o1 JOIN orders o2 "
                               "ON o2.order_no = o1.order_no AND o2.workspace_id = :w WHERE o1.workspace_id = :t "
                               "LIMIT 1"), {"w": workspace.id, "t": template.id}).one()
        from tallyho import dialect
        assert (dialect.as_date(moved[0]) - dialect.as_date(moved[1])).days % 7 == 0
        # Rollups were made for the copy, and they agree with its raw sales.
        f = analytics.Filters(workspace.id, workspace.data_version, today() - timedelta(days=60), today())
        assert analytics.kpis(s, f, source="raw")["revenue"] == analytics.kpis(s, f, source="rollup")["revenue"]


DEMO_PAGES = {
    # page -> words that only show when the page has examples
    "/": ["Top Products", "Sales by Category", "Revenue by day"],
    "/?range=12m&compare=year": ["Same dates last year", "Revenue by month"],
    "/products": ["Espresso Roast Beans 1kg", "Margin"],
    "/customers": ["When Customers Order", "Top Customers", "heat-5"],
    "/reports": ["Monthly sales by category", "Orders by region and weekday"],
    "/reports/build?rows=category&columns=month&range=90d": ["Coffee Beans", "Total"],
    "/reports/cohorts": ["mo", "Average"],
    "/reports/pareto": ["Top 80%"],
    "/goals": ["Expected by today", "Cebu", "Other Months"],
    "/alerts": ["the usual", "Rules"],
    "/import": ["Sample data (Kape Corner).csv", "Website orders (fix 2 rows).csv"],
    "/records": ["KC-"],
    # The imported example is yesterday's; on a busy day today's 50+ lines push it to page 2.
    "/records?q=WEB-9001": ["WEB-9001", "Kape Corner Mug"],
    "/lab": ["Raw Tables or Rollups?", "USING INDEX"],
    "/settings": ["Kape Corner", "disabled"],
}


def test_every_demo_page_shows_an_example(app, visitor):
    post(visitor, "/settings", {"action": "developer", "developer_mode": "1"})  # for the Performance Lab
    for page, words in DEMO_PAGES.items():
        response = visitor.get(page)
        assert response.status_code == 200, page
        html = response.get_data(as_text=True)
        for word in words:
            assert word in html, (page, word)
        assert "No sales yet" not in html and "Nothing to show" not in html, page
    with app.app_context():
        s = db.session()
        workspace = demo_workspace(app)
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.workspace_id == workspace.id)) > 0
        record = s.scalar(select(Import).where(Import.workspace_id == workspace.id, Import.rows_failed > 0))
        assert record.rows_failed == 2
    detail = visitor.get(f"/imports/{record.id}").get_data(as_text=True)
    assert "Rows With Problems" in detail


def test_product_customer_and_order_pages_in_the_demo(app, visitor):
    page = visitor.get("/products").get_data(as_text=True)
    product = re.search(r'href="(/products/\d+)', page).group(1)
    assert "Top Buyers" in visitor.get(product).get_data(as_text=True)
    page = visitor.get("/customers").get_data(as_text=True)
    customer = re.search(r'href="(/customers/\d+)', page).group(1)
    assert "Latest Orders" in visitor.get(customer + "?range=all").get_data(as_text=True)
    assert "Kape Corner Mug" in visitor.get("/orders/WEB-9001").get_data(as_text=True)


def test_two_visitors_never_see_each_other(app, visitor):
    other = app.test_client()
    post(other, "/demo")
    with app.app_context():
        s = db.session()
        assert s.scalar(select(func.count()).select_from(Workspace).where(Workspace.is_demo.is_(True))) == 2
    post(visitor, "/reports/save", {"name": "Only mine", "params": "{}"})
    assert "Only mine" in visitor.get("/reports").get_data(as_text=True)
    assert "Only mine" not in other.get("/reports").get_data(as_text=True)
    # Neither sees the owner's real workspace.
    from .conftest import owner_id, log_in_as
    owner = log_in_as(app.test_client(), owner_id(app))
    assert "Only mine" not in owner.get("/reports").get_data(as_text=True)
    assert "No sales yet" in owner.get("/").get_data(as_text=True)


def test_the_template_is_made_once(app, visitor):
    post(app.test_client(), "/demo")
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Workspace)
                                   .where(Workspace.is_template.is_(True))) == 1


def test_a_second_try_demo_keeps_the_same_demo(app, visitor):
    post(visitor, "/demo")
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Workspace).where(Workspace.is_demo.is_(True))) == 1


def test_finished_demos_are_deleted_with_everything_in_them(app, visitor):
    workspace = demo_workspace(app)
    with app.app_context():
        s = db.session()
        s.get(Workspace, workspace.id).demo_expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert visitor.get("/").status_code == 302  # logged out: the demo is over
    from tallyho import tick
    with app.test_request_context():
        assert tick.run(db.session(), None)["demos_deleted"] == 1
    with app.app_context():
        s = db.session()
        for model in (Order, Goal, SavedReport, Alert):
            assert s.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace.id)) == 0


def test_too_many_demos_wait(app, monkeypatch):
    monkeypatch.setattr(demo, "MAX_DEMOS", 1)
    first = app.test_client()
    post(first, "/demo")
    second = post(app.test_client(), "/demo", follow_redirects=True)
    assert "very busy" in second.get_data(as_text=True)


def test_shift_for_moves_by_whole_weeks():
    from datetime import date
    assert demo.shift_for(date(2026, 3, 10), date(2026, 3, 10)) == 0
    assert demo.shift_for(date(2026, 3, 10), date(2026, 3, 11)) == 7
    assert demo.shift_for(date(2026, 3, 10), date(2026, 3, 24)) == 14
    assert demo.shift_for(date(2026, 3, 16), date(2026, 3, 10)) == 0

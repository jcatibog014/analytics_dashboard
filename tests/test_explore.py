"""Drilling down: Products, a product, Customers, a customer, the heatmap."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from tallyho import analytics, auth, db, sample, workspaces
from tallyho.dates import today
from tallyho.models import Customer, Product, Workspace

from .conftest import log_in_as
from .test_analytics import HAND, MARCH, filters, load


@pytest.fixture
def stocked(app):
    with app.app_context():
        s = db.session()
        sample.load(s, workspaces.main_workspace(s), None, today() - timedelta(days=60), today(), orders_per_day=10)
        s.commit()
    return app


def ids(app, model, column):
    with app.app_context():
        return {getattr(row, column): row.id for row in db.session().scalars(select(model))}


def test_products_page_lists_and_sorts(stocked, owner):
    page = owner.get("/products").get_data(as_text=True)
    assert "Categories" in page and "Espresso Roast Beans 1kg" in page and "Margin" in page
    by_units = owner.get("/products?sort=units").get_data(as_text=True)
    assert 'aria-current="true">Units' in by_units


def test_category_drills_down_to_its_products(stocked, owner):
    page = owner.get("/products").get_data(as_text=True)
    link = re.search(r'<a href="(/products\?[^"]*category=\d+[^"]*)">Brewing Gear</a>', page).group(1)
    inside = owner.get(link.replace("&amp;", "&")).get_data(as_text=True)
    assert "<h1>Brewing Gear</h1>" in inside and "French Press" in inside
    assert "Ensaymada" not in inside  # a pastry
    dashboard = owner.get("/").get_data(as_text=True)
    assert re.search(r'href="/products\?[^"]*category=\d+', dashboard)  # the dashboard's bars drill down too


def test_product_page(stocked, owner):
    product_id = ids(stocked, Product, "sku")["BN-ESP-1KG"]
    page = owner.get(f"/products/{product_id}").get_data(as_text=True)
    for text in ("Espresso Roast Beans 1kg", "Coffee Beans", "Revenue by day", "By Region", "By Channel",
                 "Top Buyers", "Average price"):
        assert text in page, text
    assert owner.get("/products/99999").status_code == 404


def test_customers_page(stocked, owner):
    page = owner.get("/customers").get_data(as_text=True)
    for text in ("Revenue per Customer", "Returning Customers", "New customers", "When Customers Order",
                 "Top Customers", "Busiest hour", "heat-5"):
        assert text in page, text
    assert page.count('class="heat heat-') >= 7 * 10


def test_customer_page(stocked, owner):
    with stocked.app_context():
        customer = db.session().scalar(select(Customer).order_by(Customer.id))
        customer_id, name = customer.id, customer.name
    page = owner.get(f"/customers/{customer_id}?range=all").get_data(as_text=True)
    assert name in page and "Customer since" in page and "Latest Orders" in page and "What They Buy" in page


def test_another_workspace_cannot_drill_into_ours(stocked, app):
    product_id = ids(app, Product, "sku")["BN-ESP-1KG"]
    with app.app_context():
        s = db.session()
        customer_id = s.scalar(select(Customer.id))
        other = Workspace(name="Other")
        s.add(other)
        s.flush()
        user_id = auth.create_user(s, other, "Other", "other@example.com", "correct horse").id
        s.commit()
    stranger = log_in_as(app.test_client(), user_id)
    assert stranger.get(f"/products/{product_id}").status_code == 404
    assert stranger.get(f"/customers/{customer_id}").status_code == 404
    assert "Espresso" not in stranger.get("/products").get_data(as_text=True)


def test_top_customers_and_heatmap_by_hand(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        top = analytics.top_customers(s, filters(app, *MARCH))
        assert [(r["code"], r["orders"], r["revenue"]) for r in top] == [("C1", 2, 216000), ("C3", 1, 155000),
                                                                         ("C2", 1, 30000)]
        assert [r["is_new"] for r in top] == [True, True, False]
        cells = analytics.heatmap(s, filters(app, *MARCH))
        assert cells[(6, 9)]["orders"] == 1  # Sunday Mar 1, 9 AM
        assert cells[(6, 15)]["orders"] == 1
        assert cells[(1, 11)]["revenue"] == 155000  # Tuesday Mar 3


def test_with_change():
    rows = analytics.with_change([{"id": 1, "revenue": 150}, {"id": 2, "revenue": 10}], [{"id": 1, "revenue": 100}])
    assert rows[0]["change"] == pytest.approx(0.5) and rows[1]["change"] is None


def test_the_date_range_follows_into_drill_downs(stocked, owner):
    page = owner.get("/products?range=7d").get_data(as_text=True)
    assert re.search(r'href="/products/\d+\?range=7d"', page)

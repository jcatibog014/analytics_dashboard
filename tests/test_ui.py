"""The look and the words from the UI review: logo colours, labels, the theme button, Settings."""
import re
from pathlib import Path

from .conftest import post

CSS = (Path(__file__).parent.parent / "tallyho" / "static" / "style.css").read_text(encoding="utf-8")


def test_the_logo_colours():
    assert "--accent: #2563eb;" in CSS            # logo blue for every action
    assert "--bg: #0f172a;" in CSS and "--surface: #1e293b;" in CSS  # dark slate page and cards
    assert "--bg: #f8fafc;" in CSS and "--text: #1e293b;" in CSS     # cool off-white, dark navy text
    assert "--chart-avg: #facc15;" in CSS          # the 7-day average in gold (dark)
    assert "--positive-fill: #10b981;" in CSS and "--negative-fill: #ef4444;" in CSS
    assert "--warning-fill: #f59e0b;" in CSS


def test_the_theme_button_is_in_the_menu_and_settings_keeps_the_choice(owner):
    page = owner.get("/").get_data(as_text=True)
    assert "data-theme-button" in page
    settings = owner.get("/settings").get_data(as_text=True)
    assert "data-theme-settings" in settings and 'value="system"' in settings


def test_settings_wording(owner):
    page = owner.get("/settings").get_data(as_text=True)
    for words in ("Profile", "Business name", "Your name", "Save profile", "Save changes", "Update password",
                  "Developer Mode", 'role="switch"'):
        assert words in page, words
    assert "Save name" not in page


def test_alert_and_import_wording(app, owner):
    page = owner.get("/import").get_data(as_text=True)
    assert "Upload &amp; map columns" in page and 'aria-current="step"' in page
    post(owner, "/import/sample")
    page = owner.get("/import").get_data(as_text=True)
    assert "Revert import" in page and "can't be undone" in page
    goals = owner.get("/goals").get_data(as_text=True)
    assert "Create goal" in goals
    post(owner, "/alerts/rules", {"measure": "revenue", "direction": "drop", "percent": "5"})
    alerts = owner.get("/alerts").get_data(as_text=True)
    assert "Mark all as read" in alerts and "View details" in alerts
    assert "See the day" not in alerts


def test_dashboard_wording(app, owner):
    post(owner, "/import/sample")
    page = owner.get("/").get_data(as_text=True)
    assert "Compare to previous period" in page and "View as table" in page
    assert 'class="spark-area"' in page  # the sparklines' soft fill


def test_on_a_phone_the_filters_fold_behind_a_button(app, owner):
    post(owner, "/import/sample")
    page = owner.get("/?region=1&compare=year").get_data(as_text=True)
    assert "data-more-filters" in page and "more-filters-button" in page
    assert '<span class="filter-count">2</span>' in page  # a region and a comparison that isn't the usual
    plain = owner.get("/").get_data(as_text=True)
    assert "filter-count" not in plain
    assert ".more-filters::details-content" in CSS and "max-width: 39.99rem" in CSS


def test_the_demo_is_a_badge_and_its_welcome_a_message_that_fades(client):
    response = post(client, "/demo")
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    # The welcome pops up once, as a message that fades by itself (with a close button).
    assert 'class="toast"' in page and "Welcome!" in page and "data-toast-close" in page
    # After that, only the small badge in the menu; tapping it tells what the demo is.
    assert "demo-bar" not in page and 'class="demo-badge"' in page and "deleted 2 hours" in page
    page = client.get("/settings").get_data(as_text=True)
    assert "Welcome!" not in page and 'class="demo-badge"' in page
    # Good news fades; an error stays until it is closed.
    assert "toast-out 0.3s ease-in 8s forwards" in CSS
    assert ".toast-error { border-left-color: var(--danger); animation: toast-in 0.2s ease-out; }" in CSS


def test_the_owner_has_no_demo_badge(owner):
    page = owner.get("/").get_data(as_text=True)
    assert "demo-badge" not in page.split("</aside>")[0]


def test_on_a_phone_top_products_fit_without_scrolling_sideways(app, owner):
    post(owner, "/import/sample")
    for url in ("/", "/products"):
        page = owner.get(url).get_data(as_text=True)
        # Each number carries its own label, shown above it on a phone (the header row is hidden there).
        assert 'class="rank-table rank-list"' in page and 'data-label="Units"' in page
        assert 'data-label="Gross profit"' in page and 'class="num rank-revenue"' in page
    assert ".rank-list { min-width: 0; }" in CSS
    assert ".rank-list .rank-extra::before { content: attr(data-label);" in CSS


def test_sorting_products_opens_the_page_at_the_list(app, owner):
    post(owner, "/import/sample")
    page = owner.get("/products").get_data(as_text=True)
    assert 'id="product-list"' in page
    assert page.count('#product-list"') == 4  # Revenue, Gross profit, Units, Orders


def test_titles_have_no_line_of_text_under_them(app, owner):
    post(owner, "/import/sample")
    for url in ("/", "/products", "/customers", "/reports", "/reports/cohorts", "/reports/pareto", "/goals",
                "/alerts", "/import", "/records", "/settings"):
        assert 'class="lead"' not in owner.get(url).get_data(as_text=True), url
    assert ".lead" not in CSS


def test_facts_that_were_under_titles_moved_into_the_boxes(app, owner):
    post(owner, "/import/sample")
    records = owner.get("/records").get_data(as_text=True)
    order_no = re.search(r'href="/orders/([^"?]+)', records).group(1)
    order = owner.get(f"/orders/{order_no}").get_data(as_text=True)
    assert "<span>Date</span>" in order
    imports = owner.get("/import").get_data(as_text=True)
    import_id = re.search(r'href="/imports/(\d+)"', imports).group(1)
    assert "<span>Imported</span>" in owner.get(f"/imports/{import_id}").get_data(as_text=True)
    customers = owner.get("/customers?range=all").get_data(as_text=True)
    customer = re.search(r'href="(/customers/\d+)[^"]*"', customers).group(1)
    page = owner.get(customer).get_data(as_text=True)
    for words in ("Customer since", "Orders in all", "Spent in all", "Last order"):
        assert words in page, words

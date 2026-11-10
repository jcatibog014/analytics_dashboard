"""The look and the words from the UI review: logo colours, labels, the theme button, Settings."""
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
